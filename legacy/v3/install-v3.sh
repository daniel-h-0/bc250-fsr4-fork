#!/usr/bin/env bash
set -euo pipefail

VERSION="v3.0.0"
REPO="dmorazasanchez/bc250-fsr4"
ASSET="bc250-fsr4-v3-cachyos-arch-llvm22-x86_64.tar.gz"
BASE_URL="https://github.com/${REPO}/releases/download/${VERSION}"
PREFIX="${BC250_FSR4_PREFIX:-$HOME/.local/share/bc250-fsr4/v3}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

die() { echo "ERROR: $*" >&2; exit 1; }

[[ "$(uname -m)" == "x86_64" ]] || die "The precompiled V3 release is x86_64-only."

if command -v lspci >/dev/null 2>&1; then
  if ! lspci -Dn | grep -qiE '1002:13fe'; then
    if [[ "${BC250_FSR4_FORCE:-0}" != "1" ]]; then
      die "AMD BC-250 PCI ID 1002:13FE was not detected. Set BC250_FSR4_FORCE=1 only if you know this is the correct machine."
    fi
  fi
else
  echo "WARN: lspci not found; BC-250 PCI ID check skipped."
fi

if command -v curl >/dev/null 2>&1; then
  curl -fL --retry 3 --connect-timeout 15 "$BASE_URL/$ASSET" -o "$TMP/$ASSET"
  curl -fL --retry 3 --connect-timeout 15 "$BASE_URL/$ASSET.sha256" -o "$TMP/$ASSET.sha256"
elif command -v wget >/dev/null 2>&1; then
  wget -O "$TMP/$ASSET" "$BASE_URL/$ASSET"
  wget -O "$TMP/$ASSET.sha256" "$BASE_URL/$ASSET.sha256"
else
  die "curl or wget is required."
fi

(
  cd "$TMP"
  sha256sum -c "$ASSET.sha256"
)

rm -rf "$PREFIX"
mkdir -p "$PREFIX"
tar -xzf "$TMP/$ASSET" -C "$PREFIX"

LIB="$PREFIX/libvulkan_radeon.so"
ICD="$PREFIX/radv-bc250-fsr4-v3.json"
[[ -f "$LIB" ]] || die "Release archive did not contain libvulkan_radeon.so."

if command -v ldd >/dev/null 2>&1 && ldd "$LIB" | grep -q 'not found'; then
  echo
  echo "The precompiled CachyOS/Arch binary is not ABI-compatible with this system."
  echo "Missing dependencies:"
  ldd "$LIB" | grep 'not found' || true
  echo
  echo "Use the source/Docker build path from the V3 README on this distribution."
  exit 1
fi

python3 - "$LIB" "$ICD" <<'PY'
import json, sys
lib, icd = sys.argv[1], sys.argv[2]
with open(icd, "w") as f:
    json.dump({
        "file_format_version": "1.0.0",
        "ICD": {"library_path": lib, "api_version": "1.4.0"}
    }, f, indent=2)
    f.write("\n")
PY

cat > "$PREFIX/STEAM-LAUNCH.txt" <<EOF
VK_DRIVER_FILES="$ICD" %command%
EOF

echo
echo "Installed BC-250 FSR4 V3 to:"
echo "  $PREFIX"
echo
echo "Steam Launch Options:"
cat "$PREFIX/STEAM-LAUNCH.txt"

if command -v vulkaninfo >/dev/null 2>&1; then
  echo
  echo "Validating driver..."
  if VK_DRIVER_FILES="$ICD" vulkaninfo --summary >"$TMP/vulkaninfo.txt" 2>&1; then
    grep -E 'deviceName|deviceID|driverName|driverInfo' "$TMP/vulkaninfo.txt" | head -12 || true
  else
    echo "WARN: vulkaninfo could not initialize the V3 driver."
    cat "$TMP/vulkaninfo.txt"
    exit 1
  fi
else
  echo
echo "vulkaninfo is not installed; runtime validation skipped."
fi

echo
echo "No system Mesa files were replaced."
echo "Uninstall with:"
echo "  curl -fsSL https://raw.githubusercontent.com/${REPO}/v3/uninstall-v3.sh | bash"
