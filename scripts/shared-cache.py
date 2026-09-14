#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Opt an application into shared Mesa caching, preserving its local Fossilize reads."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import time
from pathlib import Path

TOOL_API = 1
# Keep the two-file launcher self-contained when the download is discarded.
TOOL_LICENSE = 'MIT License\n\nCopyright (c) 2026 Daniel (daniel-h-0)\n\nApplies only to newly authored v4 build/install/test tooling carrying the\nSPDX-License-Identifier: MIT marker. See THIRD_PARTY.md for other material.\n\nPermission is hereby granted, free of charge, to any person obtaining a copy\nof this software and associated documentation files (the "Software"), to deal\nin the Software without restriction, including without limitation the rights\nto use, copy, modify, merge, publish, distribute, sublicense, and/or sell\ncopies of the Software, and to permit persons to whom the Software is\nfurnished to do so, subject to the following conditions:\n\nThe above copyright notice and this permission notice shall be included in all\ncopies or substantial portions of the Software.\n\nTHE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\nIMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\nFITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\nAUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\nLIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\nOUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\nSOFTWARE.\n'
SOURCE = Path(__file__).resolve().parent


def xdg(environment, name, fallback):
    home = Path(environment.get("HOME") or Path.home()).expanduser()
    value = Path(environment.get(name) or home / fallback).expanduser()
    if not value.is_absolute():
        value = home / fallback
    if not value.is_absolute():
        raise ValueError("The user's directory must be absolute")
    return value


def default_install(environment):
    return xdg(environment, "XDG_DATA_HOME", ".local/share") / "bc250-fsr4-cache"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".cache-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def probe_write(directory):
    """Check an actual write, including an existing directory or full filesystem."""
    fd, temporary = tempfile.mkstemp(prefix=".bc250-write-", dir=directory)
    try:
        try:
            data = b"BC250 cache write check\n"
            if os.write(fd, data) != len(data):
                raise OSError("Incomplete cache write check")
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        os.unlink(temporary)


def link_directory(link, target):
    try:
        link.symlink_to(target, target_is_directory=True)
    except FileExistsError:
        # Create first, then inspect an existing entry. Separate exists checks
        # race with another launcher preparing the same view.
        if not link.is_symlink():
            raise ValueError("Cache view path already contains data: " + str(link)) from None
        if link.resolve() != target.resolve():
            raise ValueError("Cache view has a different target: " + str(link)) from None


def inside(path, parent):
    """Path.is_relative_to without requiring Python 3.9."""
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def plan(environment, shared_root=None, backend="multi-file"):
    """Compute a cache view without creating directories, links or records."""
    env = dict(environment)
    disabled = env.get("MESA_SHADER_CACHE_DISABLE", env.get("MESA_GLSL_CACHE_DISABLE", ""))
    if disabled.strip().lower() in {"true", "1", "yes", "on"}:
        raise ValueError("MESA_SHADER_CACHE_DISABLE explicitly disables caching")
    if backend not in {"multi-file", "database"}:
        raise ValueError("Unsupported cache backend")
    user_cache = xdg(env, "XDG_CACHE_HOME", ".cache")
    shared = Path(shared_root or user_cache / "bc250-fsr4").expanduser().absolute()
    old_root = (
        Path(env.get("MESA_SHADER_CACHE_DIR") or env.get("MESA_GLSL_CACHE_DIR") or user_cache)
        .expanduser()
        .absolute()
    )
    # A second wrapper must not recursively build a view of its own view.
    marker = env.get("BC250_FSR4_CACHE_ORIGINAL_ROOT")
    if marker:
        old_root = Path(marker).expanduser().absolute()
    identity = hashlib.sha256(os.fsencode(old_root)).hexdigest()[:24]
    view = shared / "views-v2" / backend / identity
    if inside(old_root.resolve(), shared.resolve()):
        raise ValueError("Original cache root must be outside the shared cache")
    cache_name = "mesa_shader_cache_db" if backend == "database" else "mesa_shader_cache"
    database = shared / cache_name
    fossilize = old_root / "mesa_shader_cache_sf"
    # No private SF directory is created while the original is absent. A later
    # Steam download can then be linked without replacing anything in this view.
    builtin = shared / "radv_builtin_shaders"
    readonly = [
        name for name in env.get("MESA_DISK_CACHE_READ_ONLY_FOZ_DBS", "").split(",") if name
    ]
    # The dynamic list shares the eight-file limit; do not take a slot from it.
    if (
        "foz_cache" not in readonly
        and len(readonly) < 8
        and not env.get("MESA_DISK_CACHE_READ_ONLY_FOZ_DBS_DYNAMIC_LIST")
    ):
        readonly.append("foz_cache")
    env.update(
        MESA_SHADER_CACHE_DIR=str(view),
        MESA_DISK_CACHE_SINGLE_FILE="0",
        MESA_DISK_CACHE_DATABASE="1" if backend == "database" else "0",
        MESA_DISK_CACHE_MULTI_FILE="1",
        MESA_DISK_CACHE_COMBINE_RW_WITH_RO_FOZ="1" if fossilize.is_dir() else "0",
        MESA_DISK_CACHE_READ_ONLY_FOZ_DBS=",".join(readonly),
        BC250_FSR4_CACHE_ORIGINAL_ROOT=str(old_root),
    )
    env.setdefault("MESA_SHADER_CACHE_MAX_SIZE", env.get("MESA_GLSL_CACHE_MAX_SIZE") or "10G")
    return {
        "environment": env,
        "shared": shared,
        "view": view,
        "store": database,
        "builtin": builtin,
        "fossilize": fossilize,
        "original": old_root,
        "backend": backend,
    }


def prepare(environment, shared_root=None, backend="multi-file"):
    proposed = plan(environment, shared_root, backend)
    view = proposed["view"]
    view.mkdir(parents=True, exist_ok=True, mode=0o700)
    for directory in (proposed["store"], proposed["builtin"]):
        directory.mkdir(exist_ok=True, mode=0o700)
        probe_write(directory)
        link_directory(view / directory.name, directory)
    # The view also needs to accept Mesa's own bookkeeping files.
    probe_write(view)
    fossilize = proposed["fossilize"]
    if fossilize.is_dir():
        link_directory(view / "mesa_shader_cache_sf", fossilize)
    return proposed["environment"]


def last_launch_path(environment):
    return xdg(environment, "XDG_STATE_HOME", ".local/state") / "bc250-fsr4-cache/last-launch.json"


def launch_environment(environment, shared_root=None, backend="multi-file", enabled=True):
    """Cache setup is optional: preserve the caller's complete environment on failure."""
    original = dict(environment)
    record = {"time": time.time(), "pid": os.getpid(), "scope": "Preparation before exec"}
    app = original.get("SteamAppId")
    if app and app.isdigit():
        record["steam_appid"] = app
    if not enabled:
        result = original
        record.update(result="disabled", reason="Shared caching disabled for this launch")
    else:
        try:
            result = prepare(original, shared_root, backend)
            record.update(
                result="prepared",
                write_probe="passed",
                original=result["BC250_FSR4_CACHE_ORIGINAL_ROOT"],
                view=result["MESA_SHADER_CACHE_DIR"],
                backend=backend,
            )
        except (OSError, ValueError, RuntimeError) as error:
            result = original
            record.update(result="fallback", reason=str(error))
            print(
                "Shared cache unavailable; launching with original settings: " + str(error),
                file=sys.stderr,
                flush=True,
            )
    try:
        write_json(last_launch_path(original), record)
    except (OSError, ValueError, RuntimeError):
        # Recording a diagnostic must never prevent a game launch.
        pass
    return result


def inspect(environment, shared_root=None, backend="multi-file"):
    report = {"scope": "This environment; no game cache hits have been measured"}
    try:
        proposed = plan(environment, shared_root, backend)
        report.update(
            available=True,
            directory=str(proposed["shared"]),
            original_directory=str(proposed["original"]),
            backend=backend,
            size_limit=proposed["environment"]["MESA_SHADER_CACHE_MAX_SIZE"],
            store_exists=proposed["store"].is_dir(),
            steam_cache_present=proposed["fossilize"].is_dir(),
            existing_ordinary_cache_imported=False,
        )
    except (OSError, ValueError, RuntimeError) as error:
        report.update(available=False, reason=str(error))
    try:
        record = last_launch_path(environment)
        if record.is_file():
            report["last_launch"] = json.loads(record.read_text())
    except (OSError, ValueError, RuntimeError) as error:
        report["record_error"] = str(error)
    return report


def print_status(report):
    if report.get("available"):
        print(
            "Shared cache: configured"
            if report["store_exists"]
            else "Shared cache: not created yet"
        )
        print("Storage: " + report["directory"])
        print("Limit: " + report["size_limit"] + " per architecture (all enrolled Mesa shaders)")
        print(
            "Existing Steam cache: "
            + ("present" if report["steam_cache_present"] else "not found in this environment")
        )
    else:
        print("Shared cache unavailable: " + report["reason"])
    previous = report.get("last_launch")
    if isinstance(previous, dict):
        stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(previous.get("time", 0)))
        print("Last launch preparation: " + str(previous.get("result", "unknown")) + " at " + stamp)
        if previous.get("reason"):
            print("Reason: " + str(previous["reason"]))
        if previous.get("view"):
            print("Last prepared view: " + str(previous["view"]))
    print("Preparation in this environment does not establish cache hits inside the game.")


def steam_command(original, prefix):
    """Insert immediately before Steam's placeholder; preserve assignments and wrappers."""
    if any(character in original for character in "\0\r\n"):
        raise ValueError("Steam launch options must be a single line")
    tokens = shlex.split(original)
    if original.count("%command%") != 1 or tokens.count("%command%") != 1:
        raise ValueError("Keep exactly one standalone %command% in the existing launch options")
    matches = list(re.finditer(r"(?<!\S)%command%(?!\S)", original))
    if len(matches) != 1:
        raise ValueError("Use an unquoted standalone %command% placeholder")
    if str(prefix[0]) in tokens:
        return original
    position = matches[0].start()
    return (
        original[:position]
        + " ".join(shlex.quote(str(arg)) for arg in prefix)
        + " "
        + original[position:]
    )


def existing_steam_options(supplied):
    if supplied is not None:
        return supplied or "%command%"
    if sys.stdin.isatty():
        return input("Current Steam launch options (Enter if empty): ") or "%command%"
    return "%command%"


def verify_tools(directory):
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Installed tools are not a regular directory")
    manifest = json.loads((directory / "tool-set.json").read_text())
    if manifest.get("schema") != 1:
        raise ValueError("Unknown installed tool format")
    if {p.name for p in directory.iterdir()} != set(manifest["files"]) | {"tool-set.json"}:
        raise ValueError("Installed tools contain missing or unexpected files")
    for name, expected in manifest["files"].items():
        path = directory / name
        if Path(name).name != name or path.is_symlink() or not path.is_file():
            raise ValueError("Invalid installed tool file")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Installed tool was changed: " + name)
    return manifest


def stage_tools(prefix, names):
    """Stage an immutable complete tool set before any launcher points at it."""
    payload = {"LICENSE.new-code": TOOL_LICENSE.encode()}
    for name in names:
        path = SOURCE / name
        if path.is_symlink() or not path.is_file() or Path(name).name != name:
            raise ValueError("Missing regular installation source: " + name)
        payload[name] = path.read_bytes()
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(payload.items())}
    identity = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:24]
    tools = prefix / "launcher-tools"
    if tools.is_symlink():
        raise ValueError("The installed tool directory must not be a symlink")
    tools.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = tools / identity
    if destination.exists():
        if verify_tools(destination)["files"] != hashes:
            raise ValueError("Existing tool set differs")
    else:
        with tempfile.TemporaryDirectory(prefix=".stage-", dir=tools) as temporary:
            staged = Path(temporary) / identity
            staged.mkdir()
            for name, data in payload.items():
                path = staged / name
                path.write_bytes(data)
                path.chmod(0o755 if name.endswith(".sh") else 0o644)
            write_json(staged / "tool-set.json", {"schema": 1, "files": hashes})
            os.rename(staged, destination)
    return destination


def installed(prefix):
    current = prefix / "current"
    launcher = prefix / "bc250-fsr4-cache"
    if not current.is_symlink():
        if current.exists():
            raise ValueError("Installation selection contains unrelated data")
        return None
    target = Path(os.readlink(current))
    if target.parts[:1] != ("launcher-tools",) or len(target.parts) != 2 or ".." in target.parts:
        raise ValueError("Unexpected installed tool selection")
    verify_tools(prefix / target)
    if not launcher.is_symlink() or os.readlink(launcher) != "current/shared-cache.sh":
        raise ValueError("Installed launcher was changed")
    return launcher


def install(prefix, original="%command%"):
    launcher = prefix / "bc250-fsr4-cache"
    command = steam_command(original, [launcher, "--"])
    if prefix.is_symlink() or prefix in (Path("/"), Path.home(), Path("/tmp")):
        raise ValueError("Choose a dedicated tool installation directory")
    prefix.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (prefix / ".install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        installed(prefix)
        if launcher.exists() or launcher.is_symlink():
            if not launcher.is_symlink() or os.readlink(launcher) != "current/shared-cache.sh":
                raise ValueError("The launcher path contains unrelated data")
        payload = stage_tools(prefix, ["shared-cache.py", "shared-cache.sh"])
        if not launcher.is_symlink():
            launcher.symlink_to("current/shared-cache.sh")
        temporary = prefix / (".current-" + str(os.getpid()))
        try:
            temporary.symlink_to(payload.relative_to(prefix))
            os.replace(temporary, prefix / "current")
        finally:
            if temporary.is_symlink():
                temporary.unlink()
    print("Cache launcher installed. The original download can now be moved or deleted.")
    print("Steam launch options:\n" + command)
    print("Status: " + shlex.quote(str(launcher)) + " status")
    print("First use may compile shaders. Existing caches are preserved, not imported.")
    return launcher


def uninstall(prefix):
    if not prefix.exists():
        return
    with (prefix / ".install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if installed(prefix) is None:
            return
        payloads = [
            path
            for path in (prefix / "launcher-tools").iterdir()
            if re.fullmatch(r"[0-9a-f]{24}", path.name)
        ]
        for directory in payloads:
            verify_tools(directory)
        (prefix / "bc250-fsr4-cache").unlink()
        (prefix / "current").unlink()
        for directory in payloads:
            shutil.rmtree(directory)
        if not any((prefix / "launcher-tools").iterdir()):
            (prefix / "launcher-tools").rmdir()
    print("Launcher removed. Shader caches are retained; remove the wrapper from launch options.")


def management(action, arguments):
    parser = argparse.ArgumentParser(prog="bc250-fsr4-cache " + action)
    prefix_default = (
        SOURCE.parent.parent
        if SOURCE.parent.name == "launcher-tools"
        else default_install(os.environ)
    )
    parser.add_argument("--prefix", type=Path, default=prefix_default)
    if action in {"status", "doctor"}:
        parser.add_argument("--cache-dir", type=Path)
        parser.add_argument("--backend", choices=("multi-file", "database"), default="multi-file")
        parser.add_argument("--json", action="store_true")
    if action in {"install", "steam"}:
        parser.add_argument(
            "--launch-options",
            default=None,
            help="Existing Steam launch-option text to preserve",
        )
    args = parser.parse_args(arguments)
    prefix = args.prefix.expanduser().absolute()
    if action == "install":
        install(prefix, existing_steam_options(args.launch_options))
    elif action == "uninstall":
        uninstall(prefix)
    elif action == "steam":
        launcher = installed(prefix)
        if launcher is None:
            raise ValueError("Run shared-cache.sh install first")
        print(steam_command(existing_steam_options(args.launch_options), [launcher, "--"]))
    else:
        report = inspect(os.environ, args.cache_dir, args.backend)
        try:
            launcher = installed(prefix)
            report["launcher"] = str(launcher) if launcher else None
        except (OSError, ValueError, RuntimeError) as error:
            report["installation_error"] = str(error)
        if action == "doctor":
            try:
                prepare(os.environ, args.cache_dir, args.backend)
                report.update(
                    **inspect(os.environ, args.cache_dir, args.backend),
                    write_probe="passed",
                )
                report["scope"] = "Write check in this environment; game cache hits unmeasured"
            except (OSError, ValueError, RuntimeError) as error:
                report.update(write_probe="failed", reason=str(error), available=False)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(
                "Launcher: "
                + (report.get("launcher") or report.get("installation_error") or "not installed")
            )
            print_status(report)
            if action == "doctor":
                print("Write check: " + report["write_probe"])
        return 0 if report.get("available") else 1
    return 0


def main():
    arguments = sys.argv[1:]
    actions = {"install", "uninstall", "status", "doctor", "steam"}
    if not arguments or arguments[0] in actions:
        return management(arguments[0] if arguments else "status", arguments[1:])
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Setup: shared-cache.sh install. Inspect: status. Test writes: doctor. Generate Steam options: steam.",
    )
    parser.add_argument("--cache-dir", type=Path, help="Advanced: choose shared storage")
    parser.add_argument(
        "--show", action="store_true", help="Show the proposed environment without creating paths"
    )
    parser.add_argument(
        "--disable", action="store_true", help="Use the original cache settings for this launch"
    )
    parser.add_argument(
        "--backend",
        choices=("multi-file", "database"),
        default="multi-file",
        help="Advanced: cache storage format",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(arguments)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if args.show:
        env = plan(os.environ, args.cache_dir, args.backend)["environment"]
        print(
            json.dumps(
                {
                    key: env[key]
                    for key in sorted(env)
                    if key.startswith("MESA_") or key == "BC250_FSR4_CACHE_ORIGINAL_ROOT"
                },
                indent=2,
            )
        )
        return 0
    if not command:
        parser.error("Supply -- followed by the original launch command")
    env = launch_environment(os.environ, args.cache_dir, args.backend, not args.disable)
    os.execvpe(command[0], command, env)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit("Shared cache: " + str(error)) from error
