#!/usr/bin/env python3
"""BC-250 <-> ChatGPT relay using a private GitHub issue as the transport.

The daemon is intentionally dumb: it executes only structured JOB comments with
an exact relay token and posts structured RESULT comments back to the same issue.
No model/API key is used locally.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROTOCOL = "BC250_RELAY_V1"
JOB_PREFIX = "BC250_JOB_V1\n"
RESULT_PREFIX = "BC250_RESULT_V1\n"
HELLO_PREFIX = "BC250_HELLO_V1\n"
CONFIG_DIR = Path.home() / ".config" / "bc250-chatgpt-relay"
CONFIG_PATH = CONFIG_DIR / "config.json"
STATE_PATH = CONFIG_DIR / "state.json"
LOG_PATH = CONFIG_DIR / "relay.log"

BLOCKED_PATTERNS = [
    r"(^|[;&|()]|\s)(sudo|doas)(\s|$)",
    r"(^|[;&|()]|\s)su\s+-",
    r"(^|[;&|()]|\s)(pacman|yay|paru)(\s|$)",
    r"(^|[;&|()]|\s)systemctl(\s|$)",
    r"(^|[;&|()]|\s)(reboot|shutdown|poweroff|halt)(\s|$)",
    r"(^|[;&|()]|\s)(mkfs|fdisk|parted|mount|umount)(\s|$)",
    r"(^|[;&|()]|\s)rm(\s|$)",
    r"\bgit\s+clean\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+(checkout|restore)\s+.*(?:--\s+)?\.\s*$",
    r"\bdd\s+[^\n]*\bof=/dev/",
    r"(curl|wget)[^\n|]*\|\s*(sh|bash|zsh|fish)\b",
]


def log(msg: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(cmd: list[str], *, input_text: str | None = None, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def require_gh() -> None:
    if not shutil.which("gh"):
        raise RuntimeError("GitHub CLI (gh) is not installed. On Arch/CachyOS: sudo pacman -S github-cli")
    p = run(["gh", "auth", "status"], timeout=20)
    if p.returncode != 0:
        raise RuntimeError("gh is not authenticated. Run: gh auth login")


def gh_json(endpoint: str) -> Any:
    p = run(["gh", "api", endpoint], timeout=30)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or f"gh api failed: {endpoint}")
    return json.loads(p.stdout)


def post_comment(repo: str, issue: int, body: str) -> None:
    p = run(
        ["gh", "api", "--method", "POST", f"repos/{repo}/issues/{issue}/comments", "-f", f"body={body}"],
        timeout=30,
    )
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or "failed to post GitHub issue comment")


def save_json(path: Path, data: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, mode)
    tmp.replace(path)


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise RuntimeError(f"No config found at {CONFIG_PATH}. Run: {Path(sys.argv[0]).name} init ...")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def expand_path(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


def allowed_roots(cfg: dict[str, Any]) -> list[Path]:
    return [expand_path(x) for x in cfg.get("allowed_roots", [])]


def ensure_allowed(path_value: str, cfg: dict[str, Any], *, must_exist: bool = False, directory: bool = False) -> Path:
    p = expand_path(path_value)
    roots = allowed_roots(cfg)
    if not roots:
        raise RuntimeError("allowed_roots is empty")
    ok = any(p == root or root in p.parents for root in roots)
    if not ok:
        raise PermissionError(f"Path is outside allowed_roots: {p}")
    if must_exist and not p.exists():
        raise FileNotFoundError(str(p))
    if directory and p.exists() and not p.is_dir():
        raise NotADirectoryError(str(p))
    return p


def command_is_blocked(command: str, cfg: dict[str, Any]) -> str | None:
    if cfg.get("allow_destructive", False):
        return None
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, command, flags=re.IGNORECASE | re.MULTILINE):
            return pattern
    return None


def clip(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    head = limit * 2 // 3
    tail = limit - head
    return text[:head] + "\n\n... [relay output truncated] ...\n\n" + text[-tail:], True


def shell_job(job: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
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
    try:
        p = subprocess.run(
            [cfg.get("shell", "/bin/bash"), "-lc", command],
            cwd=str(cwd),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        timed_out = False
        rc = p.returncode
        stdout, stderr = p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        timed_out = True
        rc = 124
        stdout = e.stdout or ""
        stderr = (e.stderr or "") + f"\nTimed out after {timeout}s"
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
    return {
        "exit_code": rc,
        "timed_out": timed_out,
        "duration_s": round(time.monotonic() - started, 3),
        "cwd": str(cwd),
        "stdout": stdout,
        "stderr": stderr,
    }


def read_file_job(job: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    p = ensure_allowed(str(job.get("path", "")), cfg, must_exist=True)
    if not p.is_file():
        raise ValueError("path is not a regular file")
    data = p.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, int(job.get("start_line", 1)))
    end = min(len(data), int(job.get("end_line", start + 399)))
    if end < start:
        end = start
    text = "\n".join(f"{i}: {data[i-1]}" for i in range(start, end + 1))
    return {"path": str(p), "start_line": start, "end_line": end, "content": text}


def write_file_job(job: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    p = ensure_allowed(str(job.get("path", "")), cfg)
    content = str(job.get("content", ""))
    if len(content) > int(cfg.get("max_write_chars", 250_000)):
        raise ValueError("write_file payload too large")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".relay-tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(p)
    return {"path": str(p), "bytes": len(content.encode("utf-8"))}


def patch_job(job: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    cwd = ensure_allowed(str(job.get("cwd", "")), cfg, must_exist=True, directory=True)
    patch = str(job.get("patch", ""))
    if not patch.strip():
        raise ValueError("patch job requires patch")
    if len(patch) > int(cfg.get("max_write_chars", 250_000)):
        raise ValueError("patch payload too large")
    check = run(["git", "-C", str(cwd), "apply", "--check", "-"], input_text=patch, timeout=60)
    if check.returncode != 0:
        return {"exit_code": check.returncode, "applied": False, "stdout": check.stdout, "stderr": check.stderr}
    apply = run(["git", "-C", str(cwd), "apply", "-"], input_text=patch, timeout=60)
    return {"exit_code": apply.returncode, "applied": apply.returncode == 0, "stdout": apply.stdout, "stderr": apply.stderr}


def git_job(job: dict[str, Any], cfg: dict[str, Any], kind: str) -> dict[str, Any]:
    cwd = ensure_allowed(str(job.get("cwd", "")), cfg, must_exist=True, directory=True)
    if kind == "git_status":
        args = ["git", "-C", str(cwd), "status", "--short", "--branch"]
    else:
        args = ["git", "-C", str(cwd), "diff", "--no-ext-diff", "--unified=3"]
        if job.get("staged"):
            args.append("--staged")
    p = run(args, timeout=60)
    return {"exit_code": p.returncode, "cwd": str(cwd), "stdout": p.stdout, "stderr": p.stderr}


def list_files_job(job: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    root = ensure_allowed(str(job.get("path", "")), cfg, must_exist=True, directory=True)
    max_entries = min(int(job.get("max_entries", 400)), 2000)
    depth = min(int(job.get("max_depth", 3)), 8)
    out: list[str] = []
    base_parts = len(root.parts)
    for current, dirs, files in os.walk(root):
        cur = Path(current)
        if len(cur.parts) - base_parts >= depth:
            dirs[:] = []
        dirs[:] = [d for d in dirs if d not in {".git", ".cache"}]
        for name in sorted(dirs):
            out.append(str((cur / name).relative_to(root)) + "/")
            if len(out) >= max_entries:
                return {"path": str(root), "entries": out, "truncated": True}
        for name in sorted(files):
            out.append(str((cur / name).relative_to(root)))
            if len(out) >= max_entries:
                return {"path": str(root), "entries": out, "truncated": True}
    return {"path": str(root), "entries": out, "truncated": False}


def execute_job(job: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    op = str(job.get("op", ""))
    if op == "ping":
        return {"pong": True, "host": socket.gethostname(), "time": int(time.time())}
    if op == "shell":
        return shell_job(job, cfg)
    if op == "read_file":
        return read_file_job(job, cfg)
    if op == "write_file":
        return write_file_job(job, cfg)
    if op == "patch":
        return patch_job(job, cfg)
    if op in {"git_status", "git_diff"}:
        return git_job(job, cfg, op)
    if op == "list_files":
        return list_files_job(job, cfg)
    raise ValueError(f"Unsupported op: {op}")


def make_result(job: dict[str, Any], comment_id: int, cfg: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    try:
        payload = execute_job(job, cfg)
        status = "ok"
        error = None
    except Exception as e:
        payload = {}
        status = "error"
        error = f"{type(e).__name__}: {e}"
    result = {
        "protocol": PROTOCOL,
        "job_id": str(job.get("job_id", "")),
        "job_comment_id": comment_id,
        "status": status,
        "error": error,
        "host": socket.gethostname(),
        "elapsed_s": round(time.monotonic() - started, 3),
        "result": payload,
    }
    limit = int(cfg.get("max_comment_chars", 52000))
    # Clip large textual fields while keeping JSON valid.
    for key in ("stdout", "stderr", "content"):
        if key in payload and isinstance(payload[key], str):
            payload[key], was = clip(payload[key], max(1000, limit // 3))
            if was:
                payload[f"{key}_truncated"] = True
    return result


def fetch_comments(repo: str, issue: int) -> list[dict[str, Any]]:
    data = gh_json(f"repos/{repo}/issues/{issue}/comments?per_page=100")
    if not isinstance(data, list):
        raise RuntimeError("Unexpected comments response")
    return data


def save_state(last_seen: int) -> None:
    save_json(STATE_PATH, {"last_seen_comment_id": last_seen})


def load_state() -> int | None:
    if not STATE_PATH.exists():
        return None
    try:
        return int(json.loads(STATE_PATH.read_text(encoding="utf-8")).get("last_seen_comment_id", 0))
    except Exception:
        return None


def daemon(cfg: dict[str, Any]) -> None:
    require_gh()
    repo = str(cfg["repo"])
    issue = int(cfg["issue"])
    token = str(cfg["token"])
    interval = max(1.0, float(cfg.get("poll_seconds", 3)))

    comments = fetch_comments(repo, issue)
    last_seen = load_state()
    if last_seen is None:
        last_seen = max((int(x["id"]) for x in comments), default=0)
        save_state(last_seen)
        log(f"first start: ignoring existing comments through id={last_seen}")

    log(f"relay started repo={repo} issue={issue} roots={[str(x) for x in allowed_roots(cfg)]}")
    while True:
        try:
            comments = fetch_comments(repo, issue)
            for c in sorted(comments, key=lambda x: int(x["id"])):
                cid = int(c["id"])
                if cid <= last_seen:
                    continue
                body = str(c.get("body") or "")
                if body.startswith(JOB_PREFIX):
                    try:
                        job = json.loads(body[len(JOB_PREFIX):])
                        if job.get("protocol") != PROTOCOL:
                            raise ValueError("wrong protocol")
                        if not secrets.compare_digest(str(job.get("token", "")), token):
                            raise PermissionError("relay token mismatch")
                        job_id = str(job.get("job_id", ""))
                        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", job_id):
                            raise ValueError("invalid job_id")
                        log(f"job {job_id} op={job.get('op')} comment={cid}")
                        result = make_result(job, cid, cfg)
                    except Exception as e:
                        result = {
                            "protocol": PROTOCOL,
                            "job_id": "unknown",
                            "job_comment_id": cid,
                            "status": "error",
                            "error": f"{type(e).__name__}: {e}",
                            "host": socket.gethostname(),
                            "result": {},
                        }
                    post_comment(repo, issue, RESULT_PREFIX + json.dumps(result, ensure_ascii=False))
                last_seen = cid
                save_state(last_seen)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log(f"poll error: {type(e).__name__}: {e}")
        time.sleep(interval)


def default_roots() -> list[str]:
    home = Path.home()
    candidates = [
        home / "bc250-fsr4-v2-test",
        home / "fsr4-custom",
        home / "mesa-26.2",
        home / "mesa",
        home / "bc250-mesh-test",
    ]
    existing = [str(x.resolve()) for x in candidates if x.exists()]
    return existing or [str((home / "bc250-fsr4-v2-test").resolve()), str((home / "fsr4-custom").resolve())]


def init_config(args: argparse.Namespace) -> None:
    require_gh()
    repo_info = gh_json(f"repos/{args.repo}")
    if not bool(repo_info.get("private")) and not args.allow_public_control_repo:
        raise RuntimeError("Refusing to use a public repository as the control channel")
    issue_info = gh_json(f"repos/{args.repo}/issues/{args.issue}")
    if "pull_request" in issue_info:
        raise RuntimeError("control target must be an issue, not a pull request")

    roots = [str(expand_path(x)) for x in (args.root or default_roots())]
    token = secrets.token_urlsafe(32)
    cfg = {
        "protocol": PROTOCOL,
        "repo": args.repo,
        "issue": args.issue,
        "token": token,
        "allowed_roots": roots,
        "poll_seconds": 3,
        "shell": "/bin/bash",
        "default_timeout": 120,
        "max_timeout": 900,
        "max_comment_chars": 52000,
        "max_write_chars": 250000,
        "allow_destructive": False,
    }
    save_json(CONFIG_PATH, cfg)
    if STATE_PATH.exists():
        STATE_PATH.unlink()
    hello = {
        "protocol": PROTOCOL,
        "token": token,
        "host": socket.gethostname(),
        "allowed_roots": roots,
        "safe_mode": True,
    }
    post_comment(args.repo, args.issue, HELLO_PREFIX + json.dumps(hello, ensure_ascii=False))
    print(f"Wrote {CONFIG_PATH}")
    print("Posted private HELLO/token to the control issue.")
    print("Next: bc250-relay run")


def check(cfg: dict[str, Any]) -> None:
    require_gh()
    repo = str(cfg["repo"])
    issue = int(cfg["issue"])
    info = gh_json(f"repos/{repo}")
    issue_info = gh_json(f"repos/{repo}/issues/{issue}")
    print(f"GitHub auth: OK")
    print(f"Control repo: {repo} ({'private' if info.get('private') else 'PUBLIC'})")
    print(f"Issue: #{issue} {issue_info.get('title')}")
    print("Allowed roots:")
    for root in allowed_roots(cfg):
        print(f"  - {root} {'[exists]' if root.exists() else '[missing]'}")
    print(f"Safe mode: {'ON' if not cfg.get('allow_destructive') else 'OFF'}")


def main() -> int:
    parser = argparse.ArgumentParser(description="BC-250 ChatGPT GitHub issue relay")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init", help="create local config and post private HELLO token")
    p_init.add_argument("--repo", required=True, help="private owner/repo used as the control channel")
    p_init.add_argument("--issue", required=True, type=int)
    p_init.add_argument("--root", action="append", help="allowed workspace root; repeatable")
    p_init.add_argument("--allow-public-control-repo", action="store_true")
    sub.add_parser("run", help="run foreground relay daemon")
    sub.add_parser("check", help="check auth/config/control issue")
    args = parser.parse_args()
    try:
        if args.cmd == "init":
            init_config(args)
        elif args.cmd == "run":
            daemon(load_config())
        else:
            check(load_config())
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
