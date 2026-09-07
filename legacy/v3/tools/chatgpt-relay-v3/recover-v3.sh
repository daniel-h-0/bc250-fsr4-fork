#!/usr/bin/env bash
set -euo pipefail
UNIT="$HOME/.config/systemd/user/bc250-relay-v3.service"
BIN="$HOME/.local/bin/bc250-relay-v3"
[[ -x "$BIN" ]] || { echo "Missing $BIN" >&2; exit 1; }
mkdir -p "$(dirname "$UNIT")"
cat > "$UNIT" <<'EOF'
[Unit]
Description=BC-250 Relay v3 recovery
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=%h/.local/bin/bc250-relay-v3 run
Restart=always
RestartSec=3
KillMode=control-group
TimeoutStopSec=8

[Install]
WantedBy=default.target
EOF
systemctl --user disable --now bc250-relay-v3-watchdog.timer >/dev/null 2>&1 || true
systemctl --user daemon-reload
systemctl --user enable bc250-relay-v3.service >/dev/null
systemctl --user restart bc250-relay-v3.service
sleep 2
printf 'SERVICE: '
systemctl --user is-active bc250-relay-v3.service
printf 'HEALTH: '
curl -fsS http://127.0.0.1:8765/health; echo
