#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$DIR/libvulkan_radeon.so"
ICD="$DIR/radv-bc250-fsr4-v3.json"
status=0

if [[ ! -f "$LIB" ]]; then
  echo "FAIL: missing $LIB"
  echo "      Use ./install-v3.sh or build with ./build-anywhere.sh."
  exit 1
fi

if command -v ldd >/dev/null 2>&1; then
  if ldd "$LIB" | grep -q 'not found'; then
    echo "FAIL: missing runtime dependencies:"
    ldd "$LIB" | grep 'not found' || true
    status=1
  else
    echo "OK: runtime dependencies resolved"
  fi
fi

"$DIR/setup.sh" >/dev/null
echo "OK: ICD generated at $ICD"

if command -v python3 >/dev/null 2>&1; then
  python3 -m json.tool "$ICD" >/dev/null
  echo "OK: ICD JSON is valid"
fi

cat <<EOF

Runtime verification:
  VK_DRIVER_FILES="$ICD" vulkaninfo --summary

Expected driver: RADV / Mesa 26.2.0 on AMD BC-250 (GFX1013).
EOF

exit "$status"
