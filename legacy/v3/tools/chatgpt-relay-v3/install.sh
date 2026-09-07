#!/usr/bin/env bash
set -euo pipefail

BIN="$HOME/.local/bin/bc250-relay-v3"
CFG="$HOME/.config/bc250-relay-v3/config.json"
UNIT_DIR="$HOME/.config/systemd/user"
URL="https://raw.githubusercontent.com/dmorazasanchez/bc250-fsr4/v2/tools/chatgpt-relay-v3/bc250_relay_v3.py"

command -v gh >/dev/null || { echo 'github-cli (gh) is required'; exit 1; }
gh auth status >/dev/null 2>&1 || { echo 'Authenticate GitHub first with: gh auth login'; exit 1; }
mkdir -p "$HOME/.local/bin" "$UNIT_DIR"
curl -fsSL "$URL" -o "$BIN"
chmod +x "$BIN"
python3 -m py_compile "$BIN"

if [[ ! -f "$CFG" ]]; then
  roots=()
  for d in "$HOME"; do
    [[ -e "$d" ]] && roots+=(--root "$d")
  done
  "$BIN" init --repo dmorazasanchez/hola "${roots[@]}"
else
  echo "Keeping existing config: $CFG"
fi

# Reuse the upgrade script to install/update service + watchdog without changing token/roots.
curl -fsSL https://raw.githubusercontent.com/dmorazasanchez/bc250-fsr4/v2/tools/chatgpt-relay-v3/upgrade-v3.1.sh | bash
