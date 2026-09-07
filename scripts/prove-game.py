#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Record native FSR4 engagement from a live Linux game, without tracing it."""

import argparse
import configparser
import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
import driver


def mapped(pid, name, proc_root=Path("/proc")):
    matches = []
    for line in (proc_root / str(pid) / "maps").read_text().splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) != 6 or not fields[5].endswith("/" + name):
            continue
        namespace_path = proc_root / str(pid) / "root" / fields[5].lstrip("/")
        stat = namespace_path.stat()
        major, minor = (int(v, 16) for v in fields[3].split(":"))
        if stat.st_ino != int(fields[4]):
            raise RuntimeError(
                "Mapped file no longer matches its namespace inode: " + str(namespace_path)
            )
        # A container may have no equivalent host pathname. Hash the process's
        # actual namespace file; report a host path only when it is the same file.
        host = Path(fields[5].removeprefix("/run/host"))
        try:
            samefile = host.samefile(namespace_path)
        except OSError:
            samefile = False
        item = {
            "name": name,
            "host_path": str(host) if samefile else None,
            "mapped_path": fields[5],
            "namespace_path": str(namespace_path),
            "sha256": driver.digest(namespace_path),
            "inode": stat.st_ino,
            "mapping_verified": True,
            "namespace_samefile": samefile,
            "maps_device": [major, minor],
            "stat_device": [os.major(stat.st_dev), os.minor(stat.st_dev)],
        }
        if item not in matches:
            matches.append(item)
    if len(matches) != 1:
        raise RuntimeError("Expected one mapped " + name + ", found " + str(len(matches)))
    return matches[0]


def process_identity(pid, proc_root=Path("/proc")):
    # The comm field can itself contain spaces and parentheses.
    fields = (proc_root / str(pid) / "stat").read_text().rsplit(") ", 1)[1].split()
    start_ticks = int(fields[19])
    boot = next(
        int(line.split()[1])
        for line in (proc_root / "stat").read_text().splitlines()
        if line.startswith("btime ")
    )
    return {
        "start_ticks": start_ticks,
        "started_epoch": boot + start_ticks / os.sysconf("SC_CLK_TCK"),
    }


def initialization_log(path, process):
    metadata = path.stat()
    # btime has one-second granularity. This rejects clearly stale files, but
    # appending to an old log cannot associate an individual line with this PID.
    if metadata.st_mtime < process["started_epoch"] - 1:
        raise RuntimeError(
            "The supplied engine log predates this game process; select its current launch log."
        )
    content = path.read_bytes()
    lines = [
        line
        for line in content.decode(errors="replace").splitlines()
        if "Successfully initialized FSR Upscaling provider using version '4.1.1'" in line
    ]
    if not lines:
        raise RuntimeError(
            "No successful native FSR 4.1.1 initialization in the supplied game log."
        )
    return {
        "initialization": lines[-2:],
        "engine_log_sha256": hashlib.sha256(content).hexdigest(),
        "engine_log": str(path.resolve()),
        "engine_log_mtime_epoch": metadata.st_mtime,
        "initialization_scope": "Successful line in the supplied log; an appended log may retain earlier launches. Confirm the line belongs to this launch.",
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pid", type=int, required=True)
    p.add_argument("--release-manifest", type=Path, required=True)
    p.add_argument("--engine-log", type=Path, required=True)
    p.add_argument("--config", type=Path, required=True, help="The game-side OptiScaler.ini")
    args = p.parse_args()
    process = process_identity(args.pid)
    release = json.loads(args.release_manifest.read_text())
    policy = json.loads((Path(__file__).resolve().parents[1] / "v4/games.json").read_text())
    environment = dict(
        item.split(b"=", 1)
        for item in Path(f"/proc/{args.pid}/environ").read_bytes().split(b"\0")
        if b"=" in item
    )
    overrides = {
        k.decode(): v.decode(errors="replace")
        for k, v in environment.items()
        if k.startswith(b"BC250_FSR4_") and k != b"BC250_FSR4_PREFIX"
    }
    if overrides:
        raise RuntimeError(
            "Remove research overrides for a production-default proof: " + json.dumps(overrides)
        )
    library = mapped(args.pid, "libvulkan_radeon.so")
    provider = mapped(args.pid, "amdxcffx64.dll")
    opti = (
        mapped(args.pid, "OptiScaler.dll")
        if any(
            "OptiScaler.dll" in line
            for line in Path(f"/proc/{args.pid}/maps").read_text().splitlines()
        )
        else mapped(args.pid, "dxgi.dll")
    )
    if library["sha256"] != release["driver_sha256"]:
        raise RuntimeError("The game is not using the selected v4 release binary.")
    if (
        provider["sha256"] != policy["provider_sha256"]
        or opti["sha256"] != policy["optiscaler"]["dll_sha256"]
    ):
        raise RuntimeError("FSR provider or model-hook binary differs from the qualified input.")
    ini = configparser.ConfigParser(interpolation=None, strict=False)
    ini.read(args.config)
    if (
        ini.get("FSR", "Fsr4ForceModel", fallback="") != "2"
        or ini.get("FrameGen", "Enabled", fallback="").lower() != "false"
    ):
        raise RuntimeError("Expected INT8 model 2 and disabled frame generation.")
    evidence = initialization_log(args.engine_log, process)
    if process_identity(args.pid)["start_ticks"] != process["start_ticks"]:
        raise RuntimeError(
            "The game PID was reused while collecting evidence; retry with the current process."
        )
    print(
        json.dumps(
            {
                "observed_epoch": time.time(),
                "pid": args.pid,
                "process": process,
                "release": release["version"],
                "driver": library,
                "provider": provider,
                "model_hook": opti,
                "model": "INT8 (2)",
                "frame_generation": False,
                "production_overrides": overrides,
                **evidence,
                "config_sha256": driver.digest(args.config),
                "scope": "Native-route live mappings, supplied configuration and log evidence; inspect a current rendered frame and confirm the initialization line belongs to this launch. No per-frame tracing or performance claim.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (
        RuntimeError,
        OSError,
        ValueError,
        KeyError,
        IndexError,
        StopIteration,
        configparser.Error,
    ) as error:
        raise SystemExit("ERROR: " + str(error))
