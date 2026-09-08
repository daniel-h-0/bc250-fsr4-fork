#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Launch the selected immutable runtime using GE-Proton's own prefix manager."""

import ctypes
import fcntl
import hashlib
import json
import os
import sys
import time
from pathlib import Path

HOST = Path("/run/host")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def environment(version, driver, inherited, *, game=True):
    lock = json.loads((version / "runtime-lock.json").read_text())
    if driver.get("source_manifest_sha256") != lock["driver"]["source_manifest_sha256"]:
        raise RuntimeError(
            "This runtime requires a different driver source. Select a matching driver first."
        )
    library = Path(driver["library"])
    exported = HOST / library.as_posix().lstrip("/")
    if driver.get("mode") == "system" and exported.is_file():
        library = exported
    if digest(library) != driver["sha256"]:
        raise RuntimeError("The selected driver changed. Reinstall or update the BC250 runtime.")
    if driver.get("mode") == "private":
        icd = Path(driver["environment"]["VK_DRIVER_FILES"])
        library = Path(json.loads(icd.read_text())["ICD"]["library_path"])
        if not library.is_absolute():
            library = icd.parent / library
        if library.resolve() != Path(driver["library"]).resolve():
            raise RuntimeError(
                "Private driver selection changed. Reinstall the runtime to rebind it."
            )
    env = dict(inherited)
    # The compatibility selection owns these settings. A stale game's launch
    # options cannot silently replace the pinned runtime with another version.
    for name in (
        "VK_DRIVER_FILES",
        "VK_ICD_FILENAMES",
        "VK_ADD_DRIVER_FILES",
        "PROTON_DLSS_UPGRADE",
        "PROTON_XESS_UPGRADE",
        "PROTON_FFX3_UPGRADE",
        "PROTON_FFX4_UPGRADE",
        "PROTON_FSR3_UPGRADE",
        "PROTON_MLFG_UPGRADE",
        "PROTON_OPTISCALER_NAME",
        "WINE_OPTISCALER_NAME",
        "WINE_UPSCALER_REPLACE",
        "PROTON_UPSCALER_MANIFEST",
        "PROTON_USE_OPTISCALER",
        "PROTON_FSR4_UPGRADE",
        "PROTON_OPTISCALER_CONFIG",
    ):
        env.pop(name, None)
    env.update(driver["environment"])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if not game:
        # Steam also invokes the tool for installers, path conversion and GPU
        # queries. These calls have no Steam game identity and need ordinary GE.
        return env
    preset = lock["preset"].copy()
    # OptiScaler advertises NVX extensions to unlock a game's DLSS input.
    # BC250 cannot use them in vkd3d's separate D3D12 device. Its creation
    # otherwise fails when Vulkan extension spoofing is enabled independently
    # of vendor spoofing. Preserve any additional caller exclusions.
    disabled = env.get("VKD3D_DISABLE_EXTENSIONS", "")
    env["VKD3D_DISABLE_EXTENSIONS"] = ";".join(
        part for part in (disabled, "VK_NVX_binary_import", "VK_NVX_image_view_handle") if part
    )
    if env.get("BC250_RUNTIME_DEBUG") == "1":
        preset.update({"Log.LogToFile": "true", "FSR.Fsr4EnableWatermark": "true"})
        env["PROTON_LOG"] = "1"
    env.update(
        {
            "PROTON_UPSCALER_MANIFEST": str(version / "ge/upscaler-manifest.json"),
            "PROTON_USE_OPTISCALER": lock["optiscaler"]["version"],
            "PROTON_OPTISCALER_NAME": lock["loader"]["proxy"],
            "PROTON_FSR4_UPGRADE": lock["provider"]["version"],
            "PROTON_MLFG_UPGRADE": "0",
            # Xalia inherits the global proxy and can keep a closed game alive.
            "PROTON_USE_XALIA": "0",
            "PROTON_OPTISCALER_CONFIG": ";".join(
                key + "=" + value for key, value in preset.items()
            ),
        }
    )
    return env


def main():
    invoked = Path(__file__).resolve()
    tool = invoked.parents[2]
    with (tool / ".runtime.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        version = (tool / "current").resolve(strict=True)
        if not version.is_relative_to(tool / "versions"):
            raise RuntimeError("The selected runtime is outside its owned versions directory.")
        # An update may win the lock after Steam read current/launch.py. Always
        # execute the selected version rather than combining two versions.
        if invoked != version / "launch.py":
            lock.close()
            os.execv(
                sys.executable, [sys.executable, "-B", str(version / "launch.py"), *sys.argv[1:]]
            )
        release = json.loads((version / "runtime-release.json").read_text())
        for name in release["critical_files"]:
            path = version / name
            expected = release["files"][name]
            if path.is_symlink() or not path.is_file() or digest(path) != expected["sha256"]:
                raise RuntimeError("Runtime file changed: " + name)
        selected = json.loads((tool / "driver.json").read_text())
        game = bool(os.environ.get("SteamAppId") or os.environ.get("SteamGameId"))
        env = environment(version, selected, os.environ, game=game)
        if game:
            library = Path(selected["library"])
            exported = HOST / library.as_posix().lstrip("/")
            if selected.get("mode") == "system" and exported.is_file():
                library = exported
            try:
                ctypes.CDLL(str(library), mode=os.RTLD_NOW | os.RTLD_LOCAL)
            except OSError as error:
                raise RuntimeError(
                    "Driver cannot load inside Steam's runtime: "
                    + str(error)
                    + ". Run bc250-fsr4 update, then bc250-fsr4 doctor."
                ) from error
        # Replace the wrapper so Steam keeps its original process and inherited
        # descriptors. Proton holds the shared version lock until it exits.
        os.set_inheritable(lock.fileno(), True)
        proton = str(version / "ge/proton")
        os.execve(proton, [proton, *sys.argv[1:]], env)


def failure(error):
    message = "BC250 runtime: " + str(error)
    try:
        directory = (
            Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "bc250-fsr4"
        )
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "last-launch-error.json"
        # A launch may fail before Proton has an opportunity to create its log.
        # O_NOFOLLOW avoids following a replaced log path outside this directory.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump({"time": time.time(), "error": str(error)}, stream, indent=2)
            stream.write("\n")
        message += "\nDetails: " + str(path)
    except OSError:
        pass
    return message


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        raise SystemExit(failure(error))
