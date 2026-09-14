#!/bin/sh
# SPDX-License-Identifier: MIT
# Launch even when a minimal/Flatpak runtime has no Python interpreter.
if command -v python3 >/dev/null 2>&1; then
    case "$0" in
        */*) bc250_cache_script=$0 ;;
        *) bc250_cache_script=$(command -v "$0") ;;
    esac
    if command -v readlink >/dev/null 2>&1; then
        bc250_cache_resolved=$(readlink -f -- "$bc250_cache_script" 2>/dev/null) || bc250_cache_resolved=
        [ -z "$bc250_cache_resolved" ] || bc250_cache_script=$bc250_cache_resolved
    fi
    bc250_cache_script_dir=${bc250_cache_script%/*}
    if [ -f "$bc250_cache_script_dir/shared-cache.py" ]; then
        exec python3 "$bc250_cache_script_dir/shared-cache.py" "$@"
    fi
fi
while [ "$#" -gt 0 ]; do
    case "$1" in
        --) shift; break ;;
        --show|--help|-h|install|uninstall|status|doctor|steam)
            printf '%s\n' 'Python 3 is required to inspect or configure shared caching.' >&2
            exit 127 ;;
        --cache-dir|--backend) shift; [ "$#" -gt 0 ] || exit 2; shift ;;
        --cache-dir=*|--backend=*) shift ;;
        --disable) shift ;;
        *) break ;;
    esac
done
if [ "$#" -eq 0 ]; then
    printf '%s\n' 'Supply -- followed by the original launch command.' >&2
    exit 2
fi
printf '%s\n' 'Shared-cache helper unavailable; launching with original cache settings.' >&2
exec "$@"
