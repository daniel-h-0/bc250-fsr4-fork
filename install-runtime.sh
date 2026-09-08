#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ $# == 0 || ( $1 == --* && $1 != --help ) ]]; then
    set -- install "$@"
fi
exec python3 -B "$root/scripts/runtime.py" "$@"
