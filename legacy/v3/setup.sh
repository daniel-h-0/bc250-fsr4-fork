#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$DIR/libvulkan_radeon.so"
ICD="$DIR/radv-bc250-fsr4-v3.json"

json_escape() {
  local s=${1//\\/\\\\}
  s=${s//\"/\\\"}
  printf '%s' "$s"
}

if [[ ! -f "$LIB" ]]; then
  echo "Error: missing $LIB" >&2
  echo "Use ./install-v3.sh for the precompiled release, or build V3 with ./build-anywhere.sh." >&2
  exit 1
fi

LIB_JSON="$(json_escape "$LIB")"

cat > "$ICD" <<EOF
{
  "file_format_version": "1.0.0",
  "ICD": {
    "library_path": "$LIB_JSON",
    "api_version": "1.4.0"
  }
}
EOF

echo "Created: $ICD"
echo "Steam launch option:"
echo "VK_DRIVER_FILES=\"$ICD\" %command%"
