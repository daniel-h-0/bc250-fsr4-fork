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


def prepare(environment, shared_root=None, backend="multi-file"):
    env = dict(environment)
    disabled = env.get("MESA_SHADER_CACHE_DISABLE", env.get("MESA_GLSL_CACHE_DISABLE", ""))
    if disabled.strip().lower() in {"true", "1", "yes", "on"}:
        raise ValueError("MESA_SHADER_CACHE_DISABLE explicitly disables caching")
    if backend not in {"multi-file", "database"}:
        raise ValueError("Unsupported cache backend")
    user_home = Path(env.get("HOME") or Path.home())
    user_cache = Path(env.get("XDG_CACHE_HOME") or user_home / ".cache").expanduser()
    if not user_cache.is_absolute():
        # XDG requires relative cache locations to be ignored.
        user_cache = user_home / ".cache"
    if not user_cache.is_absolute():
        raise ValueError("The user's cache directory must be absolute")
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
    view.mkdir(parents=True, exist_ok=True, mode=0o700)
    cache_name = "mesa_shader_cache_db" if backend == "database" else "mesa_shader_cache"
    database = shared / cache_name
    database.mkdir(exist_ok=True, mode=0o700)
    link_directory(view / cache_name, database)
    fossilize = old_root / "mesa_shader_cache_sf"
    if fossilize.is_dir():
        link_directory(view / "mesa_shader_cache_sf", fossilize)
    # No private SF directory is created while the original is absent. A later
    # Steam download can then be linked without replacing anything in this view.
    builtin = shared / "radv_builtin_shaders"
    builtin.mkdir(exist_ok=True, mode=0o700)
    link_directory(view / "radv_builtin_shaders", builtin)
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
    env.setdefault("MESA_SHADER_CACHE_MAX_SIZE", "10G")
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-dir", type=Path, help="Shared storage; defaults to XDG cache/bc250-fsr4"
    )
    parser.add_argument("--show", action="store_true", help="Prepare and show only cache settings")
    parser.add_argument(
        "--backend",
        choices=("multi-file", "database"),
        default="multi-file",
        help="Portable multi-file cache by default; database requires Mesa-DB support",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command and not args.show:
        parser.error("Supply -- followed by the original launch command")
    try:
        env = prepare(os.environ, args.cache_dir, args.backend)
    except (OSError, ValueError, RuntimeError) as error:
        if args.show:
            raise SystemExit("Shared cache unavailable: " + str(error)) from error
        print(
            "Shared cache unavailable; launching with original settings: " + str(error),
            file=sys.stderr,
            flush=True,
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
