#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
engine=${BC250_CONTAINER_ENGINE:-}
if [[ -z "$engine" ]]; then
    if command -v podman >/dev/null 2>&1; then engine=podman
    elif command -v docker >/dev/null 2>&1; then engine=docker
    else printf 'Install and start Docker or Podman, or use scripts/build-native.sh.\n' >&2; exit 1
    fi
fi
"$engine" info >/dev/null
"$engine" build --platform linux/amd64 -t bc250-fsr4-v4-builder "$root"
# The container writes as the caller; build results stay owned by the caller.
userns=()
if [[ "${engine##*/}" == podman ]]; then userns=(--userns=keep-id); fi
"$engine" run --rm --platform linux/amd64 "${userns[@]}" --user "$(id -u):$(id -g)" \
    -v "$root:/workspace:Z" bc250-fsr4-v4-builder \
    --work /workspace/.work/container "$@"
