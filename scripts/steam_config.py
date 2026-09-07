# SPDX-License-Identifier: MIT
"""Plan narrow native-Steam configuration edits without writing any Steam file.

This module contains an original KeyValues text parser. Existing bytes outside
the selected value spans are retained. Callers own stopped-Steam checks,
transaction persistence, atomic writes and backups. No third-party parser is
required, and no account secrets are returned by discovery.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable, Mapping, Sequence
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


def _shell_words(text: str) -> list[tuple[int, int, str]]:
    """Retain source spans for plain shell words; reject compound shell programs."""
    if "\n" in text or "\r" in text or "\0" in text:
        raise SteamConfigError("Multiline launch commands need manual reconciliation.")
    result = []
    index = 0
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        start = index
        quote = None
        while index < len(text):
            char = text[index]
            if quote == "'":
                if char == "'":
                    quote = None
                index += 1
                continue
            if char == "\\":
                index += 2
                continue
            if char == "`" or text.startswith("$(", index):
                raise SteamConfigError("Launch command substitutions need manual reconciliation.")
            if quote == '"':
                if char == '"':
                    quote = None
                index += 1
                continue
            if char in "'\"":
                quote = char
            elif char.isspace():
                break
            elif char in ";&|<>()" or (char == "#" and index == start):
                raise SteamConfigError("Compound launch commands need manual reconciliation.")
            index += 1
        raw = text[start:index]
        try:
            values = shlex.split(raw)
        except ValueError as error:
            raise SteamConfigError("Unbalanced quoting in Steam launch options.") from error
        if len(values) != 1:
            raise SteamConfigError("Ambiguous Steam launch word.")
        result.append((start, index, values[0]))
    return result


def _wine_overrides(current: str, requested: str) -> str:
    def clauses(value: str) -> list[tuple[list[str], str]]:
        result = []
        for clause in filter(None, (part.strip() for part in value.split(";"))):
            names, separator, mode = clause.partition("=")
            group = [name.strip() for name in names.split(",")]
            if not separator or not all(group):
                raise SteamConfigError("Existing Wine DLL overrides need manual reconciliation.")
            result.append((group, mode.strip()))
        return result

    def normalized(name: str) -> str:
        return name.casefold().lstrip("*").removesuffix(".dll")

    desired = clauses(requested)
    names = {normalized(name) for group, _ in desired for name in group}
    retained = []
    for group, mode in clauses(current):
        remaining = [name for name in group if normalized(name) not in names]
        if remaining:
            retained.append(",".join(remaining) + "=" + mode)
    return ";".join(retained + [",".join(group) + "=" + mode for group, mode in desired])


def compose_launch_options(
    current: str,
    environment: Mapping[str, str],
    arguments: Iterable[str] = (),
    remove_environment: Iterable[str] = (),
) -> str:
    """Merge selected assignments while retaining unrelated launch syntax/arguments."""
    for key in environment:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise SteamConfigError("Invalid managed launch environment name.")
    words = _shell_words(current)
    commands = [index for index, (_, _, word) in enumerate(words) if word == "%command%"]
    if current.count("%command%") != len(commands) or len(commands) > 1:
        raise SteamConfigError("Expected one standalone %command% in Steam launch options.")
    if commands:
        start, end, _ = words[commands[0]]
        if current[start:end] != "%command%":
            raise SteamConfigError("Quoted command placeholders need manual reconciliation.")
    if not commands:
        if words and re.match(r"[A-Za-z_][A-Za-z0-9_]*=", words[0][2]):
            raise SteamConfigError(
                "Launch environment assignments without %command% need manual reconciliation."
            )
        current = "%command%" + (" " + current if current else "")
        words = _shell_words(current)
        commands = [0]
    command_index = commands[0]
    prefix = words[:command_index]
    if any(
        word in ("-i", "--ignore-environment", "-u", "--unset") or word.startswith("--unset=")
        for _, _, word in prefix
    ):
        raise SteamConfigError(
            "Launch wrappers that clear environment variables need manual reconciliation."
        )
    changes = []
    seen = set()
    remove = set(remove_environment) - set(environment)
    for start, end, word in prefix:
        key, separator, value = word.partition("=")
        if not separator or key not in set(environment) | remove:
            continue
        if key in seen:
            raise SteamConfigError(
                "Duplicate managed launch assignments need manual reconciliation."
            )
        seen.add(key)
        if key in remove:
            changes.append((start, end, ""))
            continue
        desired = environment[key]
        if key == "WINEDLLOVERRIDES":
            if "$" in current[start:end]:
                raise SteamConfigError("Dynamic Wine override values need manual reconciliation.")
            desired = _wine_overrides(value, desired)
        if desired != value or not current[start:end].startswith(key + "="):
            changes.append((start, end, key + "=" + shlex.quote(desired)))
    suffix = {word for _, _, word in words[command_index + 1 :]}
    appended = "".join(
        " " + shlex.quote(argument) for argument in arguments if argument not in suffix
    )
    for start, end, replacement in reversed(changes):
        current = current[:start] + replacement + current[end:]
    missing = [
        key + "=" + shlex.quote(value) for key, value in environment.items() if key not in seen
    ]
    return (" ".join(missing) + " " if missing else "") + current + appended


def discover_roots(home: Path | None = None) -> list[Path]:
    home = home or Path.home()
    result = []
    for relative in (".steam/root", ".local/share/Steam", ".steam/steam"):
        root = (home / relative).resolve()
        if root not in result and (root / "steamapps").is_dir() and (root / "config").is_dir():
            result.append(root)
    return result


_LOCAL_APPS = ("UserLocalConfigStore", "Software", "Valve", "Steam", "apps")
_GLOBAL_STEAM = ("InstallConfigStore", "Software", "Valve", "Steam")


def discover_accounts(root: Path) -> list[dict]:
    users = {}
    login = root / "config/loginusers.vdf"
    if login.is_file():
        document = Document(login.read_bytes())
        for steam_id in document.keys(("users",)):
            if not steam_id.isdigit():
                continue
            path = ("users", steam_id)
            users[str(int(steam_id) & 0xFFFFFFFF)] = {
                "label": document.get((*path, "PersonaName"))
                or document.get((*path, "AccountName")),
                "most_recent": document.get((*path, "MostRecent")) == "1",
                "auto_login": document.get((*path, "AutoLogin")) == "1",
            }
    accounts = []
    for path in sorted((root / "userdata").glob("*/config/localconfig.vdf")):
        account_id = path.parent.parent.name
        if not account_id.isdigit() or account_id == "0":
            continue
        user = users.get(account_id, {})
        accounts.append(
            {
                "id": account_id,
                "label": user.get("label") or "Steam account " + account_id,
                "preferred": False,
                "localconfig": path,
                "most_recent": user.get("most_recent", False),
                "auto_login": user.get("auto_login", False),
            }
        )
    for marker in ("most_recent", "auto_login"):
        preferred = [account for account in accounts if account[marker]]
        if len(preferred) == 1:
            preferred[0]["preferred"] = True
            break
    if len(accounts) == 1:
        accounts[0]["preferred"] = True
    for account in accounts:
        account.pop("most_recent")
        account.pop("auto_login")
    return accounts


def discover_games(root: Path, profiles: Sequence[Mapping]) -> list[dict]:
    libraries = [root.resolve()]
    folders = root / "steamapps/libraryfolders.vdf"
    if folders.is_file():
        document = Document(folders.read_bytes())
        for number in document.keys(("libraryfolders",)):
            if not number.isdigit():
                continue
            path = ("libraryfolders", number)
            raw = document.get((*path, "path")) if document.has_object(path) else document.get(path)
            if raw:
                if not Path(raw).is_absolute():
                    raise SteamConfigError("Steam library paths must be absolute.")
                library = Path(raw).expanduser().resolve()
                if library not in libraries:
                    libraries.append(library)
    games = []
    for profile in profiles:
        appid = str(profile["appid"])
        matches = []
        for library in libraries:
            manifest = library / "steamapps" / ("appmanifest_" + appid + ".acf")
            if not manifest.is_file():
                continue
            document = Document(manifest.read_bytes())
            if document.get(("AppState", "appid")) != appid:
                raise SteamConfigError("Steam appmanifest ID does not match its filename.")
            flags = document.get(("AppState", "StateFlags"), "0")
            if not flags.isdigit() or not (int(flags) & 4):
                continue
            relative = Path(document.get(("AppState", "installdir"), ""))
            common = (library / "steamapps/common").resolve()
            game = (common / relative).resolve()
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not game.is_relative_to(common)
                or game == common
            ):
                raise SteamConfigError("Unsafe Steam installation directory in appmanifest.")
            if not (game / profile["executable"]).is_file():
                continue
            matches.append(
                {
                    "profile": profile["id"],
                    "appid": appid,
                    "title": profile["title"],
                    "game": game,
                    "library": library,
                    "manifest": manifest,
                }
            )
        if len({str(item["game"]) for item in matches}) > 1:
            raise SteamConfigError(
                "Multiple installed copies of a supported game were found. Resolve the duplicate installation in Steam's Storage settings, then run setup again."
            )
        games.extend(matches[:1])
    return games


def plan_change(data: bytes, updates: Mapping[tuple[str, ...], str]) -> dict:
    """Describe a field-level rollback alongside exact original/replacement bytes."""
    original = Document(data)
    fields = []
    created = set()
    changed = data
    for keys, after in updates.items():
        before = original.get(keys)
        if before == after:
            continue
        fields.append({"keys": list(keys), "before": before, "after": after})
        for count in range(1, len(keys)):
            if not original.has_object(keys[:count]):
                created.add(keys[:count])
        changed = Document(changed).set(keys, after)
    return {
        "before_hex": data.hex(),
        "after_hex": changed.hex(),
        "fields": fields,
        "created_objects": [
            list(keys) for keys in sorted(created, key=lambda keys: (len(keys), keys))
        ],
    }


def plan_settings(
    root: Path,
    account_ids: Sequence[str],
    appid: str,
    environment: Mapping[str, str],
    proton: str,
    arguments: Iterable[str] = (),
    remove_environment: Iterable[str] = (),
) -> list[dict]:
    """Prepare selected-account launch edits and the selected AppID's global Proton map."""
    root = root.expanduser().resolve()
    if ".var" in root.parts and "com.valvesoftware.Steam" in root.parts:
        raise SteamConfigError("Automatic Flatpak Steam configuration is not qualified.")
    if not appid.isdigit() or int(appid) < 1:
        raise SteamConfigError(
            "Select a real game AppID; the global AppID 0 mapping is never edited."
        )
    if not proton:
        raise SteamConfigError("Select an installed Proton compatibility tool.")
    known = {account["id"]: account for account in discover_accounts(root)}
    if not account_ids or any(account not in known for account in account_ids):
        raise SteamConfigError("Select an existing Steam account with a local configuration.")
    changes = []
    arguments = tuple(arguments)
    remove_environment = tuple(remove_environment)
    for account_id in dict.fromkeys(account_ids):
        path = known[account_id]["localconfig"]
        before = read_config(path)
        keys = (*_LOCAL_APPS, appid, "LaunchOptions")
        current = Document(before).get(keys, "")
        desired = compose_launch_options(current, environment, arguments, remove_environment)
        change = plan_change(before, {keys: desired})
        if change["fields"]:
            changes.append({"path": str(path), **change})
    path = root / "config/config.vdf"
    mapping = (*_GLOBAL_STEAM, "CompatToolMapping", appid)
    change = plan_change(
        read_config(path),
        {(*mapping, "name"): proton, (*mapping, "config"): "", (*mapping, "priority"): "250"},
    )
    if change["fields"]:
        changes.append({"path": str(path), **change})
    return changes


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
