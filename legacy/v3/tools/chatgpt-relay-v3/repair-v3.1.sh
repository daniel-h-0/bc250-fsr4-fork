#!/usr/bin/env bash
set -euo pipefail

BIN3="$HOME/.local/bin/bc250-relay-v3"
BIN31="$HOME/.local/bin/bc250-relay-v3.1"
CFG="$HOME/.config/bc250-relay-v3/config.json"
UNIT_DIR="$HOME/.config/systemd/user"
SERVICE="$UNIT_DIR/bc250-relay-v3.service"
WATCHDOG_BIN="$HOME/.local/bin/bc250-relay-v3-watchdog"
WATCHDOG_SERVICE="$UNIT_DIR/bc250-relay-v3-watchdog.service"
WATCHDOG_TIMER="$UNIT_DIR/bc250-relay-v3-watchdog.timer"
URL31="https://raw.githubusercontent.com/dmorazasanchez/bc250-fsr4/v2/tools/chatgpt-relay-v3/bc250_relay_v3_1.py"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

[[ -x "$BIN3" ]] || { echo "Missing working v3 base binary: $BIN3"; exit 1; }
[[ -f "$CFG" ]] || { echo "Missing config: $CFG"; exit 1; }

curl -fsSL "$URL31" -o "$TMP"
python3 - "$TMP" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); s=p.read_text()
old="""spec=importlib.util.spec_from_file_location('bc250_relay_v3_base',BASE_PATH)\nif not spec or not spec.loader: raise SystemExit(f'cannot load {BASE_PATH}')\nbase=importlib.util.module_from_spec(spec); spec.loader.exec_module(base)"""
new="""from importlib.machinery import SourceFileLoader\n_loader=SourceFileLoader('bc250_relay_v3_base',str(BASE_PATH))\nspec=importlib.util.spec_from_loader(_loader.name,_loader)\nif not spec or not spec.loader: raise SystemExit(f'cannot load {BASE_PATH}')\nbase=importlib.util.module_from_spec(spec); spec.loader.exec_module(base)"""
if old not in s:
    raise SystemExit('Expected v3.1 loader block not found; refusing unsafe patch')
p.write_text(s.replace(old,new,1))
PY
python3 -m py_compile "$TMP"
install -m 0755 "$TMP" "$BIN31"

python3 - "$CFG" <<'PY'
import json,secrets,sys
from pathlib import Path
p=Path(sys.argv[1]); d=json.loads(p.read_text())
d.setdefault('protocol','BC250_RELAY_V3')
d.setdefault('queue_prefix','relay-v3')
d.setdefault('dashboard_key',secrets.token_urlsafe(18))
d['poll_seconds']=min(int(d.get('poll_seconds',5)),5)
d.setdefault('max_timeout',1800)
d.setdefault('http_host','127.0.0.1')
d.setdefault('http_port',8765)
p.write_text(json.dumps(d,indent=2)+'\n')
PY

cat > "$SERVICE" <<'EOF'
[Unit]
Description=BC-250 Relay v3.1
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=%h/.local/bin/bc250-relay-v3.1
Restart=always
RestartSec=3
KillMode=control-group
TimeoutStopSec=8

[Install]
WantedBy=default.target
EOF

cat > "$WATCHDOG_BIN" <<'EOF'
#!/usr/bin/env bash
set -u
if python3 - <<'PY'
import json,sys,urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:8765/health',timeout=5) as r:
        d=json.load(r)
    age=(d.get('transport') or {}).get('last_ok_age_s')
    ok=bool(d.get('ok')) and (age is None or age < 180)
except Exception:
    ok=False
sys.exit(0 if ok else 1)
PY
then exit 0; fi
systemctl --user restart bc250-relay-v3.service
EOF
chmod +x "$WATCHDOG_BIN"

cat > "$WATCHDOG_SERVICE" <<'EOF'
[Unit]
Description=BC-250 Relay v3.1 health watchdog
After=bc250-relay-v3.service

[Service]
Type=oneshot
ExecStart=%h/.local/bin/bc250-relay-v3-watchdog
EOF

cat > "$WATCHDOG_TIMER" <<'EOF'
[Unit]
Description=Check BC-250 Relay v3.1 every minute

[Timer]
OnBootSec=90
OnUnitActiveSec=60
AccuracySec=10
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable bc250-relay-v3.service >/dev/null
systemctl --user restart bc250-relay-v3.service

healthy=0
for _ in {1..12}; do
  if python3 - <<'PY'
import json,sys,urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:8765/health',timeout=2) as r: d=json.load(r)
    sys.exit(0 if d.get('ok') and d.get('relay_version')=='3.1' else 1)
except Exception: sys.exit(1)
PY
  then healthy=1; break; fi
  sleep 1
done

if [[ "$healthy" != 1 ]]; then
  echo 'v3.1 failed health validation; rolling back to v3.' >&2
  cat > "$SERVICE" <<'EOF'
[Unit]
Description=BC-250 Relay v3 (rollback)
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
  systemctl --user daemon-reload
  systemctl --user restart bc250-relay-v3.service
  echo 'Rollback complete. Recent relay log:' >&2
  journalctl --user -u bc250-relay-v3 -n 30 --no-pager >&2 || true
  exit 1
fi

systemctl --user enable --now bc250-relay-v3-watchdog.timer >/dev/null
KEY="$(python3 - "$CFG" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))['dashboard_key'])
PY
)"

echo
echo 'BC-250 Relay v3.1 repaired and healthy.'
echo 'Health:'
curl -fsS http://127.0.0.1:8765/health; echo
echo "Dashboard: http://127.0.0.1:8765/?key=$KEY"
echo 'Watchdog: systemctl --user status bc250-relay-v3-watchdog.timer --no-pager'
