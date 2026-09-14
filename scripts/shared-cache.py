#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Opt an application into shared Mesa caching, preserving its local Fossilize reads."""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


def link_directory(link, target):
    if link.is_symlink():
        if link.resolve() != target.resolve():
            raise ValueError("Cache view has a different target: " + str(link))
    elif link.exists():
        raise ValueError("Cache view path already contains data: " + str(link))
    else:
        try:
            link.symlink_to(target, target_is_directory=True)
        except FileExistsError:
            # Another launcher may prepare this same view concurrently.
            if not link.is_symlink() or link.resolve() != target.resolve():
                raise


def prepare(environment, shared_root=None):
    env = dict(environment)
    if env.get("MESA_SHADER_CACHE_DISABLE", "").lower() in {"true", "1", "yes"}:
        raise ValueError("MESA_SHADER_CACHE_DISABLE explicitly disables caching")
    user_cache = Path(env.get("XDG_CACHE_HOME") or Path.home() / ".cache").expanduser()
    if not user_cache.is_absolute():
        raise ValueError("XDG_CACHE_HOME must be absolute")
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
    view = shared / "views" / identity
    if old_root.resolve().is_relative_to(shared.resolve()):
        raise ValueError("Original cache root must be outside the shared cache")
    view.mkdir(parents=True, exist_ok=True, mode=0o700)
    database = shared / "mesa_shader_cache_db"
    database.mkdir(exist_ok=True, mode=0o700)
    link_directory(view / "mesa_shader_cache_db", database)
    fossilize = old_root / "mesa_shader_cache_sf"
    if fossilize.is_dir():
        link_directory(view / "mesa_shader_cache_sf", fossilize)
    # Builtin helper shaders are small. Keep their existing per-game cache.
    builtin = old_root / "radv_builtin_shaders"
    if builtin.is_dir():
        link_directory(view / "radv_builtin_shaders", builtin)
    readonly = [
        name for name in env.get("MESA_DISK_CACHE_READ_ONLY_FOZ_DBS", "").split(",") if name
    ]
    if "foz_cache" not in readonly and len(readonly) < 8:
        readonly.append("foz_cache")
    env.update(
        MESA_SHADER_CACHE_DIR=str(view),
        MESA_DISK_CACHE_SINGLE_FILE="0",
        MESA_DISK_CACHE_DATABASE="1",
        MESA_DISK_CACHE_COMBINE_RW_WITH_RO_FOZ="1",
        MESA_DISK_CACHE_READ_ONLY_FOZ_DBS=",".join(readonly),
        BC250_FSR4_CACHE_ORIGINAL_ROOT=str(old_root),
    )
    env.setdefault("MESA_SHADER_CACHE_MAX_SIZE", "10G")
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-dir", type=Path, help="Shared storage; defaults to XDG cache/bc250-fsr4"
    )
    parser.add_argument("--show", action="store_true", help="Prepare and show only cache settings")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command and not args.show:
        parser.error("Supply -- followed by the original launch command")
    try:
        env = prepare(os.environ, args.cache_dir)
    except (OSError, ValueError) as error:
        if args.show:
            raise SystemExit("Shared cache unavailable: " + str(error)) from error
        print(
            "Shared cache unavailable; launching with original settings: " + str(error),
            file=sys.stderr,
        )
        env = dict(os.environ)
    if args.show:
        keys = [
            key for key in env if key.startswith("MESA_") or key == "BC250_FSR4_CACHE_ORIGINAL_ROOT"
        ]
        print(json.dumps({key: env[key] for key in sorted(keys)}, indent=2))
        return
    os.execvpe(command[0], command, env)


if __name__ == "__main__":
    main()
