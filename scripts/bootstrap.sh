#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
python3 -m venv "$root/.venv"
"$root/.venv/bin/python" -m pip install -r "$root/requirements-build.txt"
printf 'Build tools ready in %s/.venv/bin\n' "$root"
