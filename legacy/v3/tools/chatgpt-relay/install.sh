#!/usr/bin/env bash
set -euo pipefail

RELAY_URL="https://raw.githubusercontent.com/dmorazasanchez/bc250-fsr4/v2/tools/chatgpt-relay/bc250_relay.py"
CONTROL_REPO="dmorazasanchez/hola"
CONTROL_ISSUE="1"
BIN="$HOME/.local/bin/bc250-relay"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/bc250-chatgpt-relay.service"

echo "== BC-250 ChatGPT relay installer =="

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required." >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required." >&2
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "Installing GitHub CLI..."
  sudo pacman -S --needed github-cli
fi

if ! gh auth status >/dev/null 2>&1; then
  echo
  echo "GitHub authentication is required once. A browser/device login will open now."
  gh auth login --hostname github.com --git-protocol https --web
fi

mkdir -p "$HOME/.local/bin" "$UNIT_DIR"
curl -fsSL "$RELAY_URL" -o "$BIN"
chmod 0755 "$BIN"

# init refuses a public control repository, generates a random relay token,
# stores it mode 0600, and posts the token only to the private control issue.
"$BIN" init --repo "$CONTROL_REPO" --issue "$CONTROL_ISSUE"

cat > "$UNIT" <<'EOF'
[Unit]
Description=BC-250 ChatGPT GitHub relay
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=%h/.local/bin/bc250-relay run
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now bc250-chatgpt-relay.service
sleep 2

echo
"$BIN" check

echo
echo "Relay service status:"
systemctl --user --no-pager --full status bc250-chatgpt-relay.service || true

echo
echo "Installation complete. The relay token was posted to the private control issue."
echo "Remote safe mode blocks sudo/pacman/systemctl/reboot/rm and other destructive commands."
