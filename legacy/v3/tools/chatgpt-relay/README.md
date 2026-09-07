# BC-250 ChatGPT relay

A deliberately simple execution bridge between a ChatGPT conversation and the BC-250. The local machine does **no AI reasoning**. GitHub is only the transport.

## Architecture

`ChatGPT -> private GitHub issue -> bc250-relay -> stdout/stderr -> private GitHub issue -> ChatGPT`

The control issue is `dmorazasanchez/hola#1`, which is private. The executable client lives in this public repository because the client itself contains no credentials.

## Install on CachyOS / Arch

```bash
curl -fsSL https://raw.githubusercontent.com/dmorazasanchez/bc250-fsr4/v2/tools/chatgpt-relay/install.sh | bash
```

The installer:

1. Installs `github-cli` with pacman if it is missing.
2. Runs the GitHub device/browser login once if `gh` is not authenticated.
3. Installs `bc250-relay` to `~/.local/bin/`.
4. Generates a random relay token in `~/.config/bc250-chatgpt-relay/config.json` (mode 0600).
5. Posts that token only to the private control issue as a `BC250_HELLO_V1` message.
6. Installs and starts `bc250-chatgpt-relay.service` as a **user** systemd service.

## Default allowed workspaces

The client automatically allows existing directories among:

- `~/bc250-fsr4-v2-test`
- `~/fsr4-custom`
- `~/mesa-26.2`
- `~/mesa`
- `~/bc250-mesh-test`

Edit `~/.config/bc250-chatgpt-relay/config.json` if another Mesa checkout needs to be added, then restart the user service.

## Supported remote operations

- `ping`
- `shell`
- `read_file`
- `write_file`
- `patch` (checked with `git apply --check` first)
- `git_status`
- `git_diff`
- `list_files`

## Safe mode

Safe mode is on by default. Remote shell jobs are rejected if they contain high-risk commands such as `sudo`, `doas`, `pacman`, `yay`, `paru`, `systemctl`, reboot/shutdown tools, disk/filesystem tools, `rm`, `git clean`, or `git reset --hard`. Shell jobs must also start from an allowlisted workspace and have bounded timeouts.

This is a guardrail, not a hardened security sandbox. The control repository must remain private and the relay token must not be published.

## Local control

```bash
bc250-relay check
systemctl --user status bc250-chatgpt-relay
journalctl --user -u bc250-chatgpt-relay -f
systemctl --user restart bc250-chatgpt-relay
systemctl --user stop bc250-chatgpt-relay
```

## Wire format

Jobs are private issue comments beginning with `BC250_JOB_V1`, followed by one JSON object containing `protocol`, `token`, `job_id`, `op`, and operation-specific fields.

Results begin with `BC250_RESULT_V1` and contain the job id, status, host, elapsed time, and returned data/stdout/stderr.
