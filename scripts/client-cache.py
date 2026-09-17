#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Reversible launcher enrollment for OptiScaler Client's optional shared cache.

Only launcher fields are owned here. The DLL installer has its own transaction.
The cache engine and original shader caches remain independent of either owner.
"""

from __future__ import annotations

import base64
import contextlib
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
sys.path.insert(0, str(SOURCE))
from client_cache_vdf import Document  # noqa: E402

spec = importlib.util.spec_from_file_location("client_shared_cache", SOURCE / "shared-cache.py")
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)
STEAM_APPS = ["UserLocalConfigStore", "Software", "Valve", "Steam", "apps"]
ABSENT = {"present": False, "value": None}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".client-cache-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            os.fchmod(out.fileno(), mode)
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save(path, value):
    atomic(path, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode())


def decode(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate configuration key: " + key)
            result[key] = value
        return result

    return json.loads(data, object_pairs_hook=unique)


def load(path):
    return decode(Path(path).read_bytes())


def regular(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_uid != os.getuid():
        raise ValueError("Use an existing configuration file owned by this user: " + str(path))
    return path.read_bytes()


def same_game(path, root):
    try:
        Path(root).resolve().relative_to(Path(path).resolve())
        return True
    except ValueError:
        return False


def yaml_location(data, keys):
    """Locate one simple block-mapping scalar without rewriting other YAML.

    Lutris writes this shape. Flow mappings, aliases and multiline target fields
    need manual setup rather than a guessed edit. Other YAML stays byte-for-byte.
    """
    text = data.decode()
    lines = text.splitlines(keepends=True)
    section, key = keys
    starts = [
        i for i, line in enumerate(lines) if re.match(r"^" + re.escape(section) + r"\s*:", line)
    ]
    if len(starts) > 1:
        raise ValueError("Duplicate Lutris section")
    if not starts:
        return lines, None, len(lines), None
    start = starts[0]
    if not re.fullmatch(re.escape(section) + r":\s*(?:#.*)?\r?\n?", lines[start]):
        raise ValueError("Use manual setup for this Lutris YAML section")
    end = next(
        (i for i in range(start + 1, len(lines)) if re.match(r"^[^\s#]", lines[i])), len(lines)
    )
    matches = [
        i for i in range(start + 1, end) if re.match(r"^\s+" + re.escape(key) + r"\s*:", lines[i])
    ]
    if len(matches) > 1:
        raise ValueError("Duplicate Lutris field")
    return lines, start, end, matches[0] if matches else None


def yaml_value(raw):
    raw = raw.strip()
    if raw.startswith('"'):
        decoder = json.JSONDecoder()
        value, end = decoder.raw_decode(raw)
        if raw[end:].strip() and not raw[end:].lstrip().startswith("#"):
            raise ValueError("Unsupported YAML scalar")
        return value
    if raw.startswith("'"):
        match = re.fullmatch(r"'((?:[^']|'')*)'\s*(?:#.*)?", raw)
        if not match:
            raise ValueError("Unsupported YAML quoting")
        return match[1].replace("''", "'")
    if raw.startswith(("&", "*", "!", "|", ">", "{", "[")):
        raise ValueError("Use manual setup for complex Lutris fields")
    raw = re.split(r"\s+#", raw)[0].rstrip()
    return None if raw in ("", "null", "~") else raw


def field(data, target):
    keys = target["keys"]
    if target["format"] == "vdf":
        value = Document(data).get(keys)
        return {"present": value is not None, "value": value}
    if target["format"] == "yaml":
        lines, _, _, index = yaml_location(data, keys)
        return (
            ABSENT.copy()
            if index is None
            else {"present": True, "value": yaml_value(lines[index].split(":", 1)[1])}
        )
    obj = decode(data)
    for key in keys[:-1]:
        obj = obj[key]
    return {"present": keys[-1] in obj, "value": copy.deepcopy(obj.get(keys[-1]))}


def replace(data, target, value):
    keys = target["keys"]
    if target["format"] == "vdf":
        return Document(data).set(keys, value["value"] if value["present"] else None)
    if target["format"] == "yaml":
        lines, section, end, index = yaml_location(data, keys)
        if not value["present"]:
            if index is not None:
                del lines[index]
            if section is not None and not any(
                s.strip() for s in lines[section + 1 : end - (index is not None)]
            ):
                del lines[section]
        else:
            line = "  " + keys[-1] + ": " + json.dumps(value["value"]) + "\n"
            if index is not None:
                indent = re.match(r"\s*", lines[index])[0]
                lines[index] = indent + line.lstrip()
            elif section is None:
                if lines and not lines[-1].endswith("\n"):
                    lines[-1] += "\n"
                lines.extend([keys[0] + ":\n", line])
            else:
                lines.insert(end, line)
        return "".join(lines).encode()
    obj = decode(data)
    node = obj
    for key in keys[:-1]:
        node = node[key]
    if value["present"]:
        node[keys[-1]] = value["value"]
    else:
        node.pop(keys[-1], None)
    return (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode()


def idle(kind, root):
    names = {
        "steam": {"steam", "steamwebhelper", "steam.exe"},
        "heroic": {"heroic", "heroic.exe"},
        "lutris": {"lutris"},
    }
    for proc in Path("/proc").glob("[0-9]*"):
        if proc.name == str(os.getpid()):
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            comm = (proc / "comm").read_text().strip().lower()
            args = (proc / "cmdline").read_bytes().split(b"\0")
            exe = Path(os.fsdecode(args[0])).name.lower() if args else ""
            script = Path(os.fsdecode(args[1])).name.lower() if len(args) > 1 else ""
            if (
                comm in names.get(kind, set())
                or exe in names.get(kind, set())
                or (exe.startswith("python") and script in names.get(kind, set()))
            ):
                raise ValueError(
                    "Close " + kind.title() + " fully, including its tray icon, then retry."
                )
            maps = (proc / "maps").read_text(errors="replace")
            if str(root) + "/" in maps:
                raise ValueError("Close the selected game before changing cache settings.")
        except (OSError, ProcessLookupError):
            pass


class Manager:
    def __init__(self, state, home=None, idle_check=idle, sandbox_check=None):
        self.home = Path(home or Path.home()).resolve()
        self.state = Path(state).absolute()
        self.tools = self.home / ".local/share/bc250-opticlient-cache"
        self.store = self.home / ".cache/bc250-fsr4"
        self.idle = idle_check
        self.sandbox_check = sandbox_check or self.check_sandbox

    def folder(self, game):
        return self.state / "games" / digest(os.fsencode(Path(game["root"]).resolve()))

    def discover(self, game):
        root = Path(game["root"]).resolve()
        found = []
        platform = str(game.get("platform", "Manual"))
        appid = str(game.get("appid", ""))
        for base in (
            (
                self.home / ".config/heroic",
                self.home / ".var/app/com.heroicgameslauncher.hgl/config/heroic",
            )
            if platform in ("Epic", "GOG", "Manual", "Custom")
            else ()
        ):
            for filename in (
                "gog_store/installed.json",
                "legendaryConfig/legendary/installed.json",
            ):
                if (platform == "GOG" and not filename.startswith("gog")) or (
                    platform == "Epic" and filename.startswith("gog")
                ):
                    continue
                manifest = base / filename
                if not manifest.is_file():
                    continue
                content = load(manifest)
                rows = (
                    content.get("installed", [])
                    if filename.startswith("gog")
                    else list(content.values())
                )
                for row in rows:
                    ident = row.get("appName") or row.get("app_name")
                    if not ident or not re.fullmatch(r"[A-Za-z0-9_.-]+", ident):
                        continue
                    if (
                        row.get("platform", "").lower() != "windows"
                        or not row.get("install_path")
                        or not same_game(row["install_path"], root)
                    ):
                        continue
                    if platform in ("Epic", "GOG") and appid != ident:
                        continue
                    path = base / "GamesConfig" / (ident + ".json")
                    if not path.is_file() or not isinstance(load(path).get(ident), dict):
                        continue
                    default = (
                        load(base / "config.json")
                        .get("defaultSettings", {})
                        .get("wrapperOptions", [])
                        if (base / "config.json").is_file()
                        else []
                    )
                    found.append(
                        {
                            "kind": "heroic",
                            "path": str(path),
                            "format": "json",
                            "keys": [ident, "wrapperOptions"],
                            "default": default,
                            "flatpak": "com.heroicgameslauncher.hgl"
                            if ".var/app/" in str(base)
                            else None,
                        }
                    )
        if platform == "Steam" and appid.isdigit():
            seen = set()
            for guess in (
                self.home / ".local/share/Steam",
                self.home / ".steam/steam",
                self.home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
            ):
                base = guess.resolve()
                if base in seen or not base.is_dir():
                    continue
                seen.add(base)
                libraries = [base]
                lib = base / "steamapps/libraryfolders.vdf"
                if lib.is_file():
                    doc = Document(lib.read_bytes())
                    libraries += [
                        Path(p)
                        for key in doc.keys(["libraryfolders"])
                        if (p := doc.get(["libraryfolders", key, "path"]))
                    ]
                matched = False
                for library in libraries:
                    manifest = library / "steamapps" / ("appmanifest_" + appid + ".acf")
                    if manifest.is_file():
                        directory = Document(manifest.read_bytes()).get(["AppState", "installdir"])
                        matched |= bool(
                            directory and same_game(library / "steamapps/common" / directory, root)
                        )
                if not matched:
                    continue
                users = sorted((base / "userdata").glob("*/config/localconfig.vdf"))
                login = base / "config/loginusers.vdf"
                if login.is_file():
                    doc = Document(login.read_bytes())
                    recent = [
                        u
                        for u in doc.keys(["users"])
                        if u.isdigit() and doc.get(["users", u, "MostRecent"]) == "1"
                    ]
                    if len(recent) == 1:
                        account = str(int(recent[0]) - 76561197960265728)
                        users = [p for p in users if p.parent.parent.name == account]
                for path in users:
                    if Document(path.read_bytes()).has_object(STEAM_APPS + [appid]):
                        found.append(
                            {
                                "kind": "steam",
                                "path": str(path),
                                "format": "vdf",
                                "keys": STEAM_APPS + [appid, "LaunchOptions"],
                                "default": "",
                                "flatpak": "com.valvesoftware.Steam"
                                if ".var/app/" in str(base)
                                else None,
                            }
                        )
        for base in (
            (
                self.home / ".local/share/lutris",
                self.home / ".var/app/net.lutris.Lutris/data/lutris",
            )
            if platform in ("Lutris", "Manual", "Custom")
            else ()
        ):
            for path in sorted((base / "games").glob("*.yml")):
                if platform == "Lutris" and appid != path.stem:
                    continue
                target = {"format": "yaml", "keys": ["game", "exe"]}
                try:
                    executable = field(path.read_bytes(), target)["value"]
                except ValueError:
                    if platform == "Lutris":
                        raise
                    continue
                if (
                    not executable
                    or not Path(executable).is_file()
                    or not same_game(root, Path(executable).parent)
                ):
                    continue
                if platform == "Lutris" and appid != path.stem:
                    continue
                default = ""
                config_base = self.home / (
                    ".var/app/net.lutris.Lutris/config/lutris"
                    if ".var/app/" in str(base)
                    else ".config/lutris"
                )
                for config in (
                    base / "system.yml",
                    base / "runners/wine.yml",
                    config_base / "system.yml",
                    config_base / "runners/wine.yml",
                ):
                    if (
                        config.is_file()
                        and field(
                            config.read_bytes(),
                            {"format": "yaml", "keys": ["system", "prefix_command"]},
                        )["value"]
                    ):
                        raise ValueError(
                            "This Lutris setup inherits a global command prefix. Use manual cache setup to preserve it."
                        )
                found.append(
                    {
                        "kind": "lutris",
                        "path": str(path),
                        "format": "yaml",
                        "keys": ["system", "prefix_command"],
                        "default": default,
                        "flatpak": "net.lutris.Lutris" if ".var/app/" in str(base) else None,
                    }
                )
        allowed = {"Steam": "steam", "Epic": "heroic", "GOG": "heroic", "Lutris": "lutris"}.get(
            platform
        )
        if allowed:
            found = [t for t in found if t["kind"] == allowed]
        if len(found) != 1:
            raise ValueError(
                "Launcher settings could not be uniquely identified. Use Prepare manual cache for launcher instructions."
            )
        return found[0]

    def check_sandbox(self, target, wrapper):
        if not target.get("flatpak"):
            return
        command = [
            "flatpak",
            "run",
            "--command=sh",
            target["flatpak"],
            "-c",
            'command -v python3 >/dev/null && test -r "$1" && test -x "$2" && bc250_probe=$(mktemp "$3/.client-check.XXXXXX") && rm -- "$bc250_probe"',
            "sh",
            str(self.tools / "current/shared-cache.py"),
            str(wrapper),
            str(self.store),
        ]
        result = subprocess.run(command, capture_output=True, timeout=25)
        if result.returncode:
            raise ValueError(
                "Flatpak needs Python 3 and access to "
                + str(self.tools)
                + " (read) and "
                + str(self.store)
                + " (write). Grant those folders to "
                + target["flatpak"]
                + " and retry; launcher settings are unchanged."
            )

    def wrapper(self, game):
        return self.tools / "entries" / (self.folder(game).name + ".sh")

    def install_tools(self, game):
        self.store.mkdir(parents=True, exist_ok=True)
        cache.probe_write(self.store)
        with contextlib.redirect_stdout(sys.stderr):
            launcher = cache.install(self.tools)
        wrapper = self.wrapper(game)
        record = self.store / ".client-status" / (self.folder(game).name + ".json")
        record.parent.mkdir(parents=True, exist_ok=True)
        text = (
            "#!/bin/sh\n# Managed by BC250 OptiScaler Client\nexport BC250_FSR4_CACHE_STATUS_FILE="
            + shlex.quote(str(record))
            + "\nif [ -x "
            + shlex.quote(str(launcher))
            + " ]; then\n  exec "
            + shlex.quote(str(launcher))
            + " --cache-dir "
            + shlex.quote(str(self.store))
            + ' -- "$@"\nfi\nexec "$@"\n'
        )
        if wrapper.is_symlink() or (wrapper.exists() and wrapper.read_text() != text):
            raise ValueError(
                "The managed cache entry was edited; preserve it and review before updating."
            )
        atomic(wrapper, text.encode(), 0o755)
        return wrapper

    def read_receipt(self, game):
        path = self.folder(game) / "receipt.json"
        if not path.is_file():
            return None
        receipt = load(path)
        if receipt.get("schema") != 1 or receipt.get("root") != str(Path(game["root"]).resolve()):
            raise ValueError("Cache installation record does not match this game.")
        self.validate_record(receipt)
        return receipt

    def validate_record(self, record):
        target = record["target"]
        kind = target["kind"]
        keys = target["keys"]
        expected = {"heroic": "json", "steam": "vdf", "lutris": "yaml"}
        if target["format"] != expected.get(kind):
            raise ValueError("Invalid launcher record format")
        if not Path(target["path"]).is_absolute():
            raise ValueError("Launcher record needs an absolute path")
        if not (
            kind == "heroic"
            and len(keys) == 2
            and keys[-1] == "wrapperOptions"
            or kind == "steam"
            and keys[:-2] == STEAM_APPS
            and keys[-1] == "LaunchOptions"
            or kind == "lutris"
            and keys == ["system", "prefix_command"]
        ):
            raise ValueError("Invalid managed launcher field")
        original = base64.b64decode(record["original_data"], validate=True)
        if field(original, target) != record["before"]:
            raise ValueError("Launcher backup does not match its record")

    def status(self, game):
        folder = self.folder(game)
        receipt = self.read_receipt(game)
        result = {
            "state": "off",
            "message": "Shared cache is off.",
            "wrapper": str(self.wrapper(game)),
            "store": str(self.store),
        }
        if (folder / "pending.json").exists():
            result.update(
                state="recovery", message="An interrupted cache change needs Disable / recover."
            )
        elif receipt:
            current = field(regular(receipt["target"]["path"]), receipt["target"])
            okay = current == receipt["after"]
            result.update(
                state="configured" if okay else "changed",
                message="Shared cache configured; next launch will check availability."
                if okay
                else "Launcher settings changed; review before changing cache enrollment.",
            )
        if result["state"] == "configured":
            try:
                if not self.wrapper(game).is_file() or not os.access(self.wrapper(game), os.X_OK):
                    raise ValueError("Missing game wrapper")
                cache.installed(self.tools)
            except (OSError, ValueError) as error:
                result.update(
                    state="repair",
                    message="Cache helper needs repair; use Enable shared cache again: "
                    + str(error),
                )
        record = self.store / ".client-status" / (folder.name + ".json")
        if record.is_file():
            result["last_launch"] = load(record)
        result["manual"] = (
            shlex.quote(str(self.wrapper(game)))
            + " %command% (Steam), or this entry as the launcher's wrapper / command prefix. Install the helper through Prepare manual cache first."
        )
        return result

    def apply(self, game, enabled):
        folder = self.folder(game)
        folder.mkdir(parents=True, exist_ok=True)
        self.state.mkdir(parents=True, exist_ok=True)
        with (self.state / "operation.lock").open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if (folder / "pending.json").exists():
                if enabled:
                    raise ValueError(
                        "Use Disable / recover for the interrupted cache change first."
                    )
                self.recover(game)
            receipt = self.read_receipt(game)
            if not enabled and not receipt:
                return self.status(game)
            target = receipt["target"] if receipt else self.discover(game)
            self.idle(target["kind"], Path(game["root"]).resolve())
            path = Path(target["path"])
            before_data = regular(path)
            before = field(before_data, target)
            if receipt and before != receipt["after"]:
                raise ValueError(
                    "The managed launcher field has later edits. Keep them and review before disabling/updating this entry."
                )
            if enabled:
                if not receipt:
                    value = before["value"] if before["present"] else target["default"]
                    if str(self.tools / "entries") + "/" in json.dumps(value):
                        raise ValueError(
                            "This launcher already has a client cache wrapper from another or moved entry. "
                            "Disable it from the original entry, or remove only that wrapper in the launcher before enrolling here."
                        )
                wrapper = self.install_tools(game)
                self.sandbox_check(target, wrapper)
                if receipt:
                    return self.status(game)
                value = before["value"] if before["present"] else target["default"]
                if target["kind"] == "heroic":
                    if not isinstance(value, list) or any(
                        not isinstance(v, dict)
                        or not isinstance(v.get("exe"), str)
                        or not isinstance(v.get("args", ""), str)
                        for v in value
                    ):
                        raise ValueError("Unrecognized Heroic wrapper settings")
                    after = {"present": True, "value": value + [{"exe": str(wrapper), "args": ""}]}
                elif target["kind"] == "steam":
                    after = {
                        "present": True,
                        "value": cache.steam_command(value or "%command%", [wrapper]),
                    }
                else:
                    if value and any(c in value for c in "\n\r\0"):
                        raise ValueError("Unsupported multiline Lutris command prefix")
                    shlex.split(value or "")
                    after = {
                        "present": True,
                        "value": ((value + " ") if value else "") + shlex.quote(str(wrapper)),
                    }
                final = {
                    "schema": 1,
                    "root": str(Path(game["root"]).resolve()),
                    "target": target,
                    "before": before,
                    "after": after,
                    "original_data": base64.b64encode(before_data).decode(),
                }
            else:
                after = receipt["before"]
                final = None
            after_data = replace(before_data, target, after)
            if not enabled:
                original = base64.b64decode(receipt["original_data"])
                # Restore exact formatting when no unrelated fields have changed.
                if replace(original, target, receipt["after"]) == before_data:
                    after_data = original
            pending = {
                "schema": 1,
                "root": str(Path(game["root"]).resolve()),
                "target": target,
                "before": before,
                "after": after,
                "original_data": base64.b64encode(before_data).decode(),
                "original_receipt": receipt,
                "final_receipt": final,
                "committed": False,
            }
            save(folder / "pending.json", pending)
            try:
                self.idle(target["kind"], Path(game["root"]).resolve())
                if regular(path) != before_data:
                    raise ValueError(
                        "Launcher configuration changed while preparing. Retry after closing the launcher."
                    )
                atomic(path, after_data, stat.S_IMODE(path.stat().st_mode))
                if regular(path) != after_data:
                    raise ValueError("Launcher configuration verification failed")
                if final:
                    save(folder / "receipt.json", final)
                else:
                    (folder / "receipt.json").unlink(missing_ok=True)
                pending["committed"] = True
                save(folder / "pending.json", pending)
            except Exception:
                self.recover(game)
                raise
            (folder / "pending.json").unlink()
            result = self.status(game)
            result["message"] = (
                "Shared cache enabled in "
                + target["kind"].title()
                + "; launch through that launcher as usual."
                if enabled
                else "Previous launcher settings restored. Shared cache files retained."
            )
            return result

    def recover(self, game):
        folder = self.folder(game)
        path = folder / "pending.json"
        pending = load(path)
        if pending.get("schema") != 1 or pending.get("root") != str(Path(game["root"]).resolve()):
            raise ValueError("Cache recovery record belongs to another game")
        self.validate_record(pending)
        if pending["committed"]:
            path.unlink()
            return
        target = pending["target"]
        self.idle(target["kind"], Path(game["root"]).resolve())
        current = regular(target["path"])
        value = field(current, target)
        if value not in (pending["before"], pending["after"]):
            raise ValueError(
                "Launcher field changed after interruption. Keep the recovery record and review it."
            )
        original = base64.b64decode(pending["original_data"])
        desired = (
            original
            if current in (original, replace(original, target, pending["after"]))
            else replace(current, target, pending["before"])
        )
        atomic(Path(target["path"]), desired, stat.S_IMODE(Path(target["path"]).stat().st_mode))
        if pending["original_receipt"]:
            save(folder / "receipt.json", pending["original_receipt"])
        else:
            (folder / "receipt.json").unlink(missing_ok=True)
        path.unlink()


def main():
    request = json.load(sys.stdin)
    manager = Manager(request["state"])
    game = request["game"]
    action = request["action"]
    if action in ("enable", "manual") and not Path(game["root"]).is_dir():
        raise ValueError("The game folder is unavailable")
    if action == "status":
        result = manager.status(game)
    elif action in ("enable", "disable"):
        result = manager.apply(game, action == "enable")
    elif action == "manual":
        manager.idle("", Path(game["root"]).resolve())
        manager.install_tools(game)
        result = manager.status(game)
        result["message"] = (
            "Helper ready. Add the wrapper shown below to your launcher; this manual change must also be removed there."
        )
    else:
        raise ValueError("Unknown cache action")
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"state": "error", "message": str(error)}))
        sys.exit(1)
