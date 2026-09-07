#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ ! -x "$root/.venv/bin/meson" ]]; then
    printf 'First run: %s/scripts/bootstrap.sh\n' "$root" >&2
    exit 1
fi
export PATH="$root/.venv/bin:$PATH"
exec python "$root/scripts/build.py" "$@"
