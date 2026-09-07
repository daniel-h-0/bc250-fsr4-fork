#!/usr/bin/env bash
set -euo pipefail
PREFIX="${BC250_FSR4_PREFIX:-$HOME/.local/share/bc250-fsr4/v3}"

if [[ -d "$PREFIX" ]]; then
  rm -rf "$PREFIX"
  echo "Removed BC-250 FSR4 V3 from $PREFIX"
else
  echo "BC-250 FSR4 V3 is not installed at $PREFIX"
fi

echo "If you added VK_DRIVER_FILES to a game's Steam Launch Options, remove it to return to the system RADV driver."
