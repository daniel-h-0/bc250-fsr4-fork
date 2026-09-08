# SPDX-License-Identifier: MIT
"""Restore only Steam fields recorded by the retired setup tool."""
from __future__ import annotations
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

class SteamConfigError(RuntimeError):
    """The requested change cannot safely be inferred from the supplied files."""


@dataclass
class _Token:
    value: str
    start: int
    end: int
    kind: str = "string"


@dataclass
class _Entry:
    key: _Token
    value: _Token | _Object


@dataclass
class _Object:
    entries: list[_Entry]
    start: int
    end: int
    closing: int


def _tokens(text: str) -> list[_Token]:
    tokens = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace() or (index == 0 and char == "\ufeff"):
            index += 1
            continue
        if text.startswith("//", index):
            end = text.find("\n", index)
            index = len(text) if end == -1 else end + 1
            continue
        if text.startswith("/*", index):
            raise SteamConfigError("Block comments are unsupported in Steam configuration.")
        start = index
        if char in "{}":
            tokens.append(_Token(char, start, start + 1, char))
            index += 1
            continue
        if char == '"':
            index += 1
            value = []
            while index < len(text):
                char = text[index]
                index += 1
                if char == '"':
                    tokens.append(_Token("".join(value), start, index))
                    break
                if char == "\\":
                    if index >= len(text):
                        raise SteamConfigError("Unterminated escape in Steam configuration.")
                    escaped = text[index]
                    index += 1
                    value.append(
                        {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}.get(
                            escaped, "\\" + escaped
                        )
                    )
                else:
                    value.append(char)
            else:
                raise SteamConfigError("Unterminated quoted Steam configuration value.")
            continue
        while index < len(text) and not text[index].isspace() and text[index] not in '{}"':
            index += 1
        value = text[start:index]
        if not value or value.startswith(("#", "[")):
            raise SteamConfigError(
                "Conditional/directive syntax is unsupported in Steam configuration."
            )
        tokens.append(_Token(value, start, index))
    return tokens


def _quoted(value: str) -> str:
    if "\0" in value:
        raise SteamConfigError("Steam values cannot contain NUL bytes.")
    return (
        '"'
        + value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        + '"'
    )


class Document:
    """Read text KeyValues or replace one case-insensitively selected scalar."""

    def __init__(self, data: bytes):
        if b"\0" in data:
            raise SteamConfigError("Expected text Steam configuration, not binary VDF.")
        self.data = data
        self.text = data.decode("utf-8", errors="surrogateescape")
        tokens = _tokens(self.text)
        index = 0

        def object_at(opening: _Token | None, depth: int) -> _Object:
            nonlocal index
            if depth > 64:
                raise SteamConfigError("Steam configuration nesting is too deep.")
            entries = []
            while index < len(tokens):
                key = tokens[index]
                index += 1
                if key.kind == "}":
                    if opening is None:
                        raise SteamConfigError("Unexpected closing brace in Steam configuration.")
                    return _Object(entries, opening.start, key.end, key.start)
                if key.kind != "string" or index == len(tokens):
                    raise SteamConfigError("Expected a Steam key followed by a value.")
                if key.value.casefold() in ("#base", "#include"):
                    raise SteamConfigError(
                        "Included Steam configuration files need manual reconciliation."
                    )
                value = tokens[index]
                index += 1
                if value.kind == "{":
                    entries.append(_Entry(key, object_at(value, depth + 1)))
                elif value.kind == "string":
                    entries.append(_Entry(key, value))
                else:
                    raise SteamConfigError("Missing Steam configuration value.")
            if opening is not None:
                raise SteamConfigError("Unclosed object in Steam configuration.")
            return _Object(entries, 0, len(self.text), len(self.text))

        self.root = object_at(None, 0)

    @staticmethod
    def _child(parent: _Object, key: str) -> _Entry | None:
        entries = [
            entry for entry in parent.entries if entry.key.value.casefold() == key.casefold()
        ]
        if len(entries) > 1:
            raise SteamConfigError("Duplicate target keys make this Steam edit ambiguous.")
        return entries[0] if entries else None

    def _object(self, path: Sequence[str]) -> _Object | None:
        node = self.root
        for key in path:
            entry = self._child(node, key)
            if entry is None:
                return None
            if not isinstance(entry.value, _Object):
                raise SteamConfigError(
                    "Expected an object at the requested Steam configuration path."
                )
            node = entry.value
        return node

    def keys(self, path: Sequence[str] = ()) -> list[str]:
        node = self._object(path)
        if node is None:
            return []
        keys = [entry.key.value for entry in node.entries]
        if len({key.casefold() for key in keys}) != len(keys):
            raise SteamConfigError("Duplicate keys make this Steam object ambiguous.")
        return keys

    def has_object(self, path: Sequence[str]) -> bool:
        if not path:
            return True
        parent = self._object(path[:-1])
        entry = self._child(parent, path[-1]) if parent is not None else None
        return entry is not None and isinstance(entry.value, _Object)

    def get(self, path: Sequence[str], default: str | None = None) -> str | None:
        if not path:
            raise SteamConfigError("Select a scalar Steam configuration path.")
        parent = self._object(path[:-1])
        entry = self._child(parent, path[-1]) if parent is not None else None
        if entry is None:
            return default
        if not isinstance(entry.value, _Token):
            raise SteamConfigError("Expected a scalar at the requested Steam configuration path.")
        return entry.value.value

    def _replace(self, start: int, end: int, replacement: str) -> bytes:
        return (self.text[:start] + replacement + self.text[end:]).encode(
            "utf-8", errors="surrogateescape"
        )

    def _remove(self, entry: _Entry) -> bytes:
        start, end = entry.key.start, entry.value.end
        line_start = self.text.rfind("\n", 0, start) + 1
        line_end = self.text.find("\n", end)
        line_end = len(self.text) if line_end == -1 else line_end
        if not self.text[line_start:start].strip() and not self.text[end:line_end].strip():
            start = line_start
            end = min(line_end + 1, len(self.text))
        return self._replace(start, end, "")

    def set(self, path: Sequence[str], value: str | None) -> bytes:
        """Return edited bytes; None removes the selected scalar if present."""
        if not path or any(not isinstance(key, str) or not key for key in path):
            raise SteamConfigError("Select a nonempty Steam configuration path.")
        parent = self.root
        for index, key in enumerate(path):
            entry = self._child(parent, key)
            if entry is None:
                if value is None:
                    return self.data
                return self._insert(parent, path[index:], value)
            if index == len(path) - 1:
                if not isinstance(entry.value, _Token):
                    raise SteamConfigError("Refusing to replace a Steam object with a scalar.")
                if value is None:
                    return self._remove(entry)
                if entry.value.value == value:
                    return self.data
                return self._replace(entry.value.start, entry.value.end, _quoted(value))
            if not isinstance(entry.value, _Object):
                raise SteamConfigError("Refusing to replace a Steam scalar with an object.")
            parent = entry.value
        raise AssertionError("Unreachable Steam edit path")

    def _insert(self, parent: _Object, path: Sequence[str], value: str) -> bytes:
        newline = "\r\n" if "\r\n" in self.text else "\n"
        closing_start = self.text.rfind("\n", 0, parent.closing) + 1
        closing_indent = self.text[closing_start : parent.closing]
        multiline = not closing_indent.strip()
        if not multiline:
            closing_indent = ""
        child_indent = closing_indent + ("\t" if parent is not self.root else "")
        if parent.entries:
            first = parent.entries[0].key.start
            line_start = self.text.rfind("\n", 0, first) + 1
            existing_indent = self.text[line_start:first]
            if not existing_indent.strip():
                child_indent = existing_indent
        unit = (
            child_indent[len(closing_indent) :] if child_indent.startswith(closing_indent) else "\t"
        )
        unit = unit or "\t"

        def branch(keys: Sequence[str], indent: str) -> str:
            if len(keys) == 1:
                return indent + _quoted(keys[0]) + "\t\t" + _quoted(value) + newline
            return (
                indent
                + _quoted(keys[0])
                + newline
                + indent
                + "{"
                + newline
                + branch(keys[1:], indent + unit)
                + indent
                + "}"
                + newline
            )

        insertion = branch(path, child_indent)
        if parent is self.root:
            prefix = "" if not self.text or self.text.endswith("\n") else newline
            return self._replace(len(self.text), len(self.text), prefix + insertion)
        if multiline:
            return self._replace(closing_start, closing_start, insertion)
        return self._replace(parent.closing, parent.closing, newline + insertion + closing_indent)

    def remove_empty(self, path: Sequence[str]) -> bytes:
        parent = self._object(path[:-1])
        entry = self._child(parent, path[-1]) if parent is not None else None
        if entry is None:
            return self.data
        if not isinstance(entry.value, _Object):
            raise SteamConfigError("Expected a created Steam object during restoration.")
        if entry.value.entries or self.text[entry.value.start + 1 : entry.value.closing].strip():
            return self.data
        return self._remove(entry)


def read_config(path: Path) -> bytes:
    """Read an editable regular Steam file without replacing symlink topology."""
    if path.is_symlink() or not path.is_file():
        raise SteamConfigError(
            "Steam configuration edits require an existing regular file, not a symlink or special file."
        )
    return path.read_bytes()


def restore_settings(current: bytes, change: Mapping, recover: bool = False) -> bytes:
    """Restore owned values while preserving later unrelated Steam/account changes."""
    if current == bytes.fromhex(change["after_hex"]):
        return bytes.fromhex(change["before_hex"])
    document = Document(current)
    for field in change["fields"]:
        value = document.get(field["keys"])
        allowed = (field["before"], field["after"]) if recover else (field["after"],)
        if value not in allowed:
            raise SteamConfigError(
                "A managed Steam field changed after setup; preserving the current configuration."
            )
    for field in change["fields"]:
        current = Document(current).set(field["keys"], field["before"])
    for keys in sorted(change["created_objects"], key=len, reverse=True):
        current = Document(current).remove_empty(keys)
    return current
