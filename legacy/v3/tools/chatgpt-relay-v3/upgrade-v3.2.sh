#!/usr/bin/env bash
set -euo pipefail
BIN3=$HOME/.local/bin/bc250-relay-v3
BIN32=$HOME/.local/bin/bc250-relay-v3.2
CFG=$HOME/.config/bc250-relay-v3/config.json
UNIT=$HOME/.config/systemd/user/bc250-relay-v3.service
WD=$HOME/.local/bin/bc250-relay-v3-watchdog
WDS=$HOME/.config/systemd/user/bc250-relay-v3-watchdog.service
WDT=$HOME/.config/systemd/user/bc250-relay-v3-watchdog.timer
URL=https://raw.githubusercontent.com/dmorazasanchez/bc250-fsr4/v2/tools/chatgpt-relay-v3/bc250_relay_v3_2.py
TMP=$(mktemp)
SIDELOG=$HOME/.local/share/bc250-relay-v3/v32-upgrade-sidecar.log
SIDE=
trap 'rm -f "$TMP"; [ -n "${SIDE:-}" ] && kill "$SIDE" 2>/dev/null || true' EXIT
[ -x "$BIN3" ] || { echo "Missing base relay: $BIN3" >&2; exit 1; }
[ -f "$CFG" ] || { echo "Missing config: $CFG" >&2; exit 1; }
mkdir -p $HOME/.local/bin $HOME/.config/systemd/user $HOME/.local/share/bc250-relay-v3
curl -fsSL "$URL" -o "$TMP"
python3 -m py_compile "$TMP"
chmod +x "$TMP"
python3 - "$CFG" <<'PY'
import json,pathlib,secrets,sys
p=pathlib.Path(sys.argv[1]); d=json.loads(p.read_text())
d.setdefault('dashboard_key',secrets.token_urlsafe(18)); d['poll_seconds']=min(int(d.get('poll_seconds',5)),5); d['heartbeat_seconds']=300; d['queue_prefix']='relay-v3'; d['http_port']=8765
p.write_text(json.dumps(d,indent=2)+'\n')
PY
KEY=$(python3 - "$CFG" <<'PY'
import json,sys; print(json.load(open(sys.argv[1]))['dashboard_key'])
PY
)
TOKEN=$(python3 - "$CFG" <<'PY'
import json,sys; print(json.load(open(sys.argv[1]))['token'])
PY
)
TESTPREFIX=relay-v32-selftest-$(date +%s)
nohup env BC250_RELAY_HTTP_PORT=8766 BC250_RELAY_QUEUE_PREFIX=$TESTPREFIX "$TMP" >"$SIDELOG" 2>&1 & SIDE=$!
ok=0
for i in $(seq 1 20); do
  if python3 - <<'PY'
import json,urllib.request,sys
try:
 d=json.load(urllib.request.urlopen('http://127.0.0.1:8766/health',timeout=2)); sys.exit(0 if d.get('ok') and d.get('relay_version')=='3.2' else 1)
except Exception: sys.exit(1)
PY
  then ok=1; break; fi
  sleep .5
done
[ "$ok" = 1 ] || { echo 'v3.2 sidecar health failed' >&2; tail -60 "$SIDELOG" >&2 || true; exit 1; }
curl -fsS "http://127.0.0.1:8766/?key=$KEY" | grep -q 'BC-250 Relay v3.2'
python3 - "$TOKEN" <<'PY'
import concurrent.futures,json,sys,time,urllib.request
token=sys.argv[1]
def one(s):
 req=urllib.request.Request('http://127.0.0.1:8766/',data=json.dumps({'session':s,'job_id':'upgrade-'+s,'op':'ping'}).encode(),headers={'Content-Type':'application/json','X-Relay-Token':token},method='POST')
 return json.load(urllib.request.urlopen(req,timeout=5))
t=time.monotonic()
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex: r=list(ex.map(one,['fsr4','ps5','vcn']))
assert all(x['result']['pong'] for x in r); assert time.monotonic()-t < 2.5
PY
kill "$SIDE" 2>/dev/null || true; wait "$SIDE" 2>/dev/null || true; SIDE=
install -m 0755 "$TMP" "$BIN32"
BACK=$UNIT.backup-v32-$(date +%Y%m%d-%H%M%S)
cp -f "$UNIT" "$BACK"
cat > "$UNIT" <<'EOF'
[Unit]
Description=BC-250 Relay v3.2
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
ExecStart=%h/.local/bin/bc250-relay-v3.2
Restart=always
RestartSec=3
KillMode=control-group
TimeoutStopSec=8
[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user restart bc250-relay-v3.service
ok=0
for i in $(seq 1 24); do
  if python3 - <<'PY'
import json,urllib.request,sys
try:
 d=json.load(urllib.request.urlopen('http://127.0.0.1:8765/health',timeout=2)); sys.exit(0 if d.get('ok') and d.get('relay_version')=='3.2' else 1)
except Exception: sys.exit(1)
PY
  then ok=1; break; fi
  sleep .5
done
if [ "$ok" != 1 ]; then
  echo 'Production health failed; rolling back service unit' >&2
  cp -f "$BACK" "$UNIT"; systemctl --user daemon-reload; systemctl --user restart bc250-relay-v3.service; exit 1
fi
cat > "$WD" <<'EOF'
#!/usr/bin/env bash
set -u
if python3 - <<'PY'
import json,urllib.request,sys
try:
 d=json.load(urllib.request.urlopen('http://127.0.0.1:8765/health',timeout=4)); age=(d.get('transport') or {}).get('last_ok_age_s'); ok=d.get('ok') and d.get('relay_version')=='3.2' and (age is None or age < 900)
except Exception: ok=False
sys.exit(0 if ok else 1)
PY
then exit 0; fi
systemctl --user restart bc250-relay-v3.service
EOF
chmod +x "$WD"
cat > "$WDS" <<'EOF'
[Unit]
Description=BC-250 Relay health watchdog
After=bc250-relay-v3.service
[Service]
Type=oneshot
ExecStart=%h/.local/bin/bc250-relay-v3-watchdog
EOF
cat > "$WDT" <<'EOF'
[Unit]
Description=Check BC-250 Relay every minute
[Timer]
OnBootSec=90
OnUnitActiveSec=60
AccuracySec=10
Persistent=true
[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now bc250-relay-v3-watchdog.timer >/dev/null
echo 'BC-250 Relay v3.2 healthy.'
echo "Dashboard: http://127.0.0.1:8765/?key=$KEY"
curl -fsS http://127.0.0.1:8765/health; echo
