---
title: "Using with Claude (MCP)"
weight: 45
---

[linuxcnc-mcp](https://github.com/dougcalobrisi/linuxcnc-mcp) is an [MCP](https://modelcontextprotocol.io) server and Claude Code plugin built on this API. It lets Claude:
- **monitor and diagnose** a machine: status, a derived problem list, HAL pins, signals and params, and value watches;
- **operate it**, if you opt in: homing, capped incremental jogs, MDI, programs, overrides and files.

```
Claude Code ──MCP/stdio──▶ linuxcnc-mcp ──gRPC (SSH tunnel)──▶ linuxcnc-grpc ──▶ LinuxCNC + HAL
```

## Quick start
1. In Claude Code:
   ```text
   /plugin marketplace add dougcalobrisi/linuxcnc-mcp
   /plugin install linuxcnc@linuxcnc-mcp
   ```
2. Run `/linuxcnc:setup-server user@cnc-host`. It installs this server on the machine with linuxcnc-mcp's `install-server.sh` (a venv with `--system-site-packages`, started with LinuxCNC through a HAL `loadusr` line or systemd) and sets up an SSH tunnel.
3. Ask Claude: *"What's the machine status?"*

## Safety model
- **Read-only by default.** Control tiers (`operate`, `full`) are opt-in, and in Claude Code, by default, every control call asks for approval.
- **Software stops** (`estop`, `abort`, `pause`, `jog_stop`) bypass queued writes. The physical e-stop is still the only real e-stop.
- **This server has no authentication or TLS yet.** Bind it to `127.0.0.1` and use an SSH tunnel (see [Server]({{< ref "server" >}})).

## How linuxcnc-mcp uses this API
- **Completion is detected by polling `GetStatus`.** It snapshots `task.echo_serial_number` before sending and waits for it to advance, never comparing it with `CommandResponse.serial`. It never calls `WaitComplete`.
- **Errors come from derived status by default** (task, exec and interp state, joint faults, limits). StreamErrors is opt-in, because it consumes the NML error queue the GUI also reads.
- **The HAL tools are read-only,** as `HalService` is.
