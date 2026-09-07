# BC-250 Relay v3

Relay v3 replaces the GitHub-issue comment queue with a file-addressed queue in the private control repository.

## Why v3

- No issue pagination failure.
- No comment-thread growth.
- Every job has a deterministic result path.
- Process-group timeouts: a timed-out game/build kills its whole process group.
- Full stdout/stderr is saved locally as an artifact; GitHub gets a compact result.
- `cancel` operation for running jobs.
- Heartbeat and health endpoints.
- Local HTTP API on `127.0.0.1:8765` for direct/local/Tailscale access.
- GitHub remains the ChatGPT-compatible transport; Tailscale Funnel can expose the HTTP endpoint for humans or future direct integrations.

## Transport layout

Private repo `dmorazasanchez/hola`:

- `relay-v3/jobs/<job_id>.json` — ChatGPT creates one file per job.
- `relay-v3/results/<job_id>.json` — daemon writes exactly one result file.
- `relay-v3/status/hello.json` — current token, host, workspaces, version.
- `relay-v3/status/heartbeat.json` — liveness and active jobs.

There is no need to scan or poll an issue thread. Once ChatGPT creates `relay-v3/jobs/foo.json`, it fetches only `relay-v3/results/foo.json` until available.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/dmorazasanchez/bc250-fsr4/v2/tools/chatgpt-relay-v3/install.sh | bash
```

The installer keeps an existing v3 config, auto-detects known BC-250 workspaces on first install, installs a user service, and starts it.

## Operations

- `ping`
- `shell`
- `cancel`
- `read_file`
- `write_file`
- `git_status`
- `git_diff`
- `list_files`

Example job file:

```json
{
  "protocol": "BC250_RELAY_V3",
  "token": "TOKEN_FROM_STATUS_HELLO",
  "job_id": "fsr4-probe-001",
  "op": "shell",
  "cwd": "/home/david/bc250-fsr4-v2-test",
  "command": "git status --short --branch",
  "timeout": 30
}
```

Result path is then exactly:

`relay-v3/results/fsr4-probe-001.json`

## Local HTTP API

Health does not require a token:

```bash
curl http://127.0.0.1:8765/health
```

Authenticated jobs use `X-Relay-Token`:

```bash
curl -H "X-Relay-Token: $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"op":"ping","job_id":"local-ping"}' \
  http://127.0.0.1:8765/
```

The API is intentionally bound to localhost. If desired, expose it through Tailscale Funnel:

```bash
tailscale funnel --bg 8765
```

Tailscale Funnel is a separate network transport. Current ChatGPT sessions still use the GitHub-file queue because ChatGPT does not expose a generic arbitrary HTTP POST tool here. The direct API is useful for the user's phone/browser tooling and for a future MCP/custom connector.

## Chat instruction for v3

Use the private repo `dmorazasanchez/hola` as the BC-250 Relay v3 queue. Read `relay-v3/status/hello.json` first and use its token. Create each job as a new JSON file under `relay-v3/jobs/<unique_job_id>.json`, then read only `relay-v3/results/<same_job_id>.json` for the result. Do not use issue #1 for v3 jobs. Use bounded timeouts. Large stdout/stderr is persisted locally and the result contains the artifact path. If a job needs human interaction, stop and ask the user rather than assuming the outcome.
