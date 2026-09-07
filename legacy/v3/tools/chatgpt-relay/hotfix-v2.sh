#!/usr/bin/env bash
set -euo pipefail

TARGET="$HOME/.local/bin/bc250-relay"
if [[ ! -f "$TARGET" ]]; then
  echo "Relay not found at $TARGET" >&2
  exit 1
fi

cp -a "$TARGET" "$TARGET.bak.$(date +%Y%m%d-%H%M%S)"

python3 - "$TARGET" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
s = p.read_text(encoding='utf-8')

# 1) Add signal import for process-group termination.
if 'import signal\n' not in s:
    s = s.replace('import secrets\n', 'import secrets\nimport signal\n')

# 2) Replace shell_job with a process-group-safe implementation.
start = s.index('def shell_job(job: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:')
end = s.index('\n\ndef read_file_job(', start)
new_shell = r'''def shell_job(job: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    cwd = ensure_allowed(str(job.get("cwd", "")), cfg, must_exist=True, directory=True)
    command = str(job.get("command", ""))
    if not command.strip():
        raise ValueError("shell job requires command")
    blocked = command_is_blocked(command, cfg)
    if blocked:
        raise PermissionError(f"Command blocked by safe-mode policy (pattern: {blocked})")
    requested = int(job.get("timeout", cfg.get("default_timeout", 120)))
    timeout = max(1, min(requested, int(cfg.get("max_timeout", 900))))
    env = os.environ.copy()
    for k, v in (job.get("env") or {}).items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(k)):
            raise ValueError(f"Invalid environment variable name: {k}")
        env[str(k)] = str(v)

    started = time.monotonic()
    proc = subprocess.Popen(
        [cfg.get("shell", "/bin/bash"), "-lc", command],
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = proc.communicate()
        rc = 124
        stderr = (stderr or "") + f"\nTimed out after {timeout}s; process group terminated"

    return {
        "exit_code": rc,
        "timed_out": timed_out,
        "duration_s": round(time.monotonic() - started, 3),
        "cwd": str(cwd),
        "stdout": stdout or "",
        "stderr": stderr or "",
    }
'''
s = s[:start] + new_shell + s[end:]

# 3) Replace fetch_comments so it reads ALL pages. This is the critical fix:
#    the old daemon stopped seeing new jobs once issue #1 exceeded 100 comments.
old = '''def fetch_comments(repo: str, issue: int) -> list[dict[str, Any]]:\n    data = gh_json(f"repos/{repo}/issues/{issue}/comments?per_page=100")\n    if not isinstance(data, list):\n        raise RuntimeError("Unexpected comments response")\n    return data\n'''
new = '''def fetch_comments(repo: str, issue: int) -> list[dict[str, Any]]:\n    endpoint = f"repos/{repo}/issues/{issue}/comments?per_page=100"\n    p = run(["gh", "api", "--paginate", "--slurp", endpoint], timeout=60)\n    if p.returncode != 0:\n        raise RuntimeError(p.stderr.strip() or "failed to fetch paginated comments")\n    pages = json.loads(p.stdout)\n    if not isinstance(pages, list):\n        raise RuntimeError("Unexpected paginated comments response")\n    out: list[dict[str, Any]] = []\n    for page in pages:\n        if isinstance(page, list):\n            out.extend(page)\n    return out\n'''
if old not in s:
    raise SystemExit('Could not locate fetch_comments block; aborting without modifying relay')
s = s.replace(old, new)

# 4) Reduce default polling pressure if config did not override it deliberately.
s = s.replace('interval = max(1.0, float(cfg.get("poll_seconds", 3)))',
              'interval = max(2.0, float(cfg.get("poll_seconds", 5)))')

p.write_text(s, encoding='utf-8')
PY

chmod 755 "$TARGET"

# Keep existing token, roots and permissions; only nudge polling to 5s if currently 3.
python3 - <<'PY'
import json
from pathlib import Path
p = Path.home()/'.config/bc250-chatgpt-relay/config.json'
d = json.loads(p.read_text())
if float(d.get('poll_seconds', 3)) <= 3:
    d['poll_seconds'] = 5
p.write_text(json.dumps(d, indent=2) + '\n')
PY

systemctl --user daemon-reload
systemctl --user restart bc250-chatgpt-relay
sleep 2
systemctl --user --no-pager --full status bc250-chatgpt-relay | sed -n '1,18p'

echo
echo "Hotfix installed. Backup saved next to $TARGET."
