---
title: "Server Configuration"
weight: 30
---

The linuxcnc-grpc server runs on your LinuxCNC machine and exposes both LinuxCNCService and HalService over gRPC.

> **Security warning: the server has no authentication and no TLS.**
> Anyone who can reach the port can command the machine: jog it, start the
> spindle, run or upload a program.
>
> - **Preferred:** bind to localhost with `--host 127.0.0.1` and reach it from
>   other machines through an SSH tunnel
>   (`ssh -N -L 50051:127.0.0.1:50051 user@cnc-machine`).
> - **Otherwise:** only use `--host 0.0.0.0` on a trusted, isolated LAN.
>   Never expose the port to the internet or to a shared network.
>
> The examples below use `--host 0.0.0.0` for brevity, and it is the default.
> See [Security Considerations](#security-considerations).

## Requirements

- Linux with LinuxCNC 2.9 or newer installed (it does not have to be running when the server starts)
- Python 3.10+
- Network access to the machine (for remote clients)

LinuxCNC 2.8 is not enough for the server as shipped. The HAL service lists
pins, signals and parameters with `hal.get_info_pins`, `hal.get_info_signals`
and `hal.get_info_params`, which 2.8's `hal` module does not have, and the
server does not start without the HAL service. The LinuxCNC service on its own
(status, commands, machine configuration) also works with 2.8's bindings, which
matters only if you build a gRPC server from `LinuxCNCServiceServicer`
yourself. On 2.8 the status fields that LinuxCNC does not report there
(`ini_filename`, `num_extrajoints`, `tool_from_pocket`, `misc_error`) are left
at their defaults.

## Installation

```bash
pip install linuxcnc-grpc
# or with uv
uv pip install linuxcnc-grpc
```

## Basic Usage

Start LinuxCNC first, then start the gRPC server:

```bash
linuxcnc-grpc --host 0.0.0.0 --port 50051
```

Or run as a Python module:

```bash
python -m linuxcnc_grpc.server --host 0.0.0.0 --port 50051
```

## Command Line Options

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | `0.0.0.0` | Address to bind to. Use `127.0.0.1` for local-only access |
| `--port` | `50051` | Port number to listen on |
| `--nc-files` | `/home/linuxcnc/linuxcnc/nc_files` | Directory for G-code file operations |
| `--error-channel` | `on-demand` | When to read LinuxCNC's error channel: `on-demand`, `always` or `off`. See [Error messages and the GUI](#error-messages-and-the-gui) |
| `--require-linuxcnc` | off | Exit with an error if LinuxCNC is not running at startup, as versions up to 1.0 did. See [Starting before LinuxCNC](#starting-before-linuxcnc) |
| `--in-process-commands` | off | Send commands to LinuxCNC from the server's own process, as versions up to 1.0 did, instead of from its command helper process. See [A LinuxCNC task that does not respond](#a-linuxcnc-task-that-does-not-respond) |
| `--debug` | off | Enable debug logging |

## Auto-Start with LinuxCNC

### Method 1: HAL File

Add to your machine's HAL file (e.g., `your_machine.hal`):

```hal
# Start gRPC server
loadusr linuxcnc-grpc --host 0.0.0.0 --port 50051
```

### Method 2: Dedicated HAL File

Create a separate `grpc-server.hal` file:

```hal
# grpc-server.hal
loadusr linuxcnc-grpc --host 0.0.0.0 --port 50051
```

Reference it in your INI file:

```ini
[HAL]
POSTGUI_HALFILE = grpc-server.hal
```

Using `POSTGUI_HALFILE` ensures the server starts after the GUI, which is useful for certain configurations.

### Method 3: systemd Service

Create `/etc/systemd/system/linuxcnc-grpc.service`:

```ini
[Unit]
Description=LinuxCNC gRPC Server

[Service]
Type=simple
ExecStart=/usr/local/bin/linuxcnc-grpc --host 0.0.0.0 --port 50051
Restart=always
RestartSec=2
User=your-username

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable linuxcnc-grpc
sudo systemctl start linuxcnc-grpc
```

The service can start at boot, before LinuxCNC: the server waits for LinuxCNC
and connects when it comes up. Use `Restart=always`, not `on-failure`: when
LinuxCNC shuts down it stops the server too, with a normal exit, and systemd
has to start it again for the next LinuxCNC session (see
[Starting before LinuxCNC](#starting-before-linuxcnc)). Run it as the same
user that runs LinuxCNC.

The server runs one child process, its command helper (see
[A LinuxCNC task that does not respond](#a-linuxcnc-task-that-does-not-respond)).
The unit needs nothing for it. When systemd stops the service it sends
`SIGTERM` to every process of the service; the helper ignores it and ends as
soon as the server has closed the pipe to it, or once it has finished a send
that was under way.

## Starting before LinuxCNC

The server no longer needs LinuxCNC to be running when it starts. If LinuxCNC
is not there, the server starts anyway and waits:

- The gRPC health service reports `NOT_SERVING` for `""` and
  `linuxcnc.LinuxCNCService`.
- RPCs that need LinuxCNC (`GetStatus`, `SendCommand`, `WaitComplete`,
  `StreamStatus`, `StreamErrors`, `ResolveFile`, `GetMachineConfig`) fail with
  `UNAVAILABLE`. Two of them check something else first: `StreamErrors` on a
  server started with `--error-channel off` fails with `FAILED_PRECONDITION`,
  and `ResolveFile` with an invalid file name fails with `INVALID_ARGUMENT`.
- The file RPCs (`UploadFile`, `ListFiles`, `ReadFile`, `DeleteFile`) work, so
  programs can be uploaded before LinuxCNC is up.
- `GetServerInfo` works and reports `linuxcnc_connected = false`, so a client
  can tell a server that is waiting for LinuxCNC from one that is not there.
- The HAL service answers, with no pins until LinuxCNC has loaded its HAL
  configuration.

The server looks for LinuxCNC after 1, 2 and 4 seconds and then every
5 seconds, connects as soon as it is there, and health turns to `SERVING`.
On the LinuxCNC 2.9 simulator the server connected within a few seconds of
LinuxCNC starting, and the full end-to-end test suite passed against a server
that had been started first.

Pass `--require-linuxcnc` to get the old behaviour: the server exits with
status 1 if LinuxCNC is not running. Use it where something else is watching
for that exit.

**The server exits with an error if it finds it can no longer connect.** If
LinuxCNC goes away in the moment between the server's check and its
connection attempt, the attempt fails, and after that LinuxCNC's Python
module cannot connect again from the same process, even once LinuxCNC is
back. The server tries 3 times in all, then logs an error, stops and exits
with status 1. A supervisor such as systemd with `Restart=always` starts a
fresh process, which connects normally. Without a supervisor, start the
server again by hand.

The server's command helper process (see
[A LinuxCNC task that does not respond](#a-linuxcnc-task-that-does-not-respond))
makes its own connection check when it starts. If it fails while the
server's check passes (LinuxCNC went away in between), the server process
itself has tried nothing and is not affected: it stays disconnected and tries
again at the usual intervals, also at startup. After 10 such failures in a
row it gives up in the same way as above, with an error that says the
command helper could not be started, so the server cannot send commands.
For `create_server()` this is the same `on_unrecoverable` call, with the same
caveat: it can cross with a shutdown.

A program that builds the server with `create_server()` instead of running
`linuxcnc-grpc` gets the error in the log, and can pass
`on_unrecoverable=callback` to be told: the callback is called once, from a
background thread, with a description. The server stays up and `NOT_SERVING`
until the program stops it; only a new process can connect. The callback is
not called once the server is being stopped, but the two can cross: a call
can arrive at the moment the program stops the server, or just after. Ignore
it once you have decided to stop.

`create_server()` binds the port as its last step, so if it raises, no port
is held. A server it returned keeps its port until it has been started and
stopped, or the process exits: stopping a gRPC server that was never started
does not release the port.

### What happens when LinuxCNC stops

**A normal LinuxCNC shutdown stops the server too.** The server owns a HAL
component (`grpc-hal-service`), and LinuxCNC unloads every HAL component when
it shuts down, which signals the server to exit. That was already the case in
1.0. So a server meant to outlive LinuxCNC sessions needs a supervisor that
starts it again, such as systemd with `Restart=always`. A server started with
`loadusr` from a HAL file simply starts and stops with LinuxCNC.

**A crashed LinuxCNC is not detected.** Measured on the 2.9 simulator by
killing the task processes with `kill -9`: LinuxCNC's status memory stays in
place, so the server keeps running, keeps returning the last status, and
health stays `SERVING`. A command sent in that state blocks for about 5
seconds and then reports `RCS_DONE` with `acknowledged` false (LinuxCNC's own
send timeout does not raise an error; see
[A LinuxCNC task that does not respond](#a-linuxcnc-task-that-does-not-respond));
a `WaitComplete` on it never completes and returns `RCS_EXEC` at its timeout. LinuxCNC's Python interface exposes no heartbeat,
so the server has no cheap way to tell a dead task from an idle one. Clients
that need to know should watch for a `WaitComplete` that times out on a
command that should be quick. The next start of LinuxCNC after a crash cleans
up HAL, which stops the server just as a normal shutdown does.

**Once connected, the server does not watch LinuxCNC.** It keeps the
connection it made. On the simulator there was nothing for a watcher to do:
a normal shutdown stops the server, a crash cannot be told from an idle
machine, and the LinuxCNC start after a crash stops the server before it
could reconnect. If a status poll does raise an error, the RPC that hit it
reconnects, as in 1.0.

While waiting for LinuxCNC, each check starts a short-lived Python process
(the LinuxCNC Python module cannot be asked safely from inside the server
while LinuxCNC is down). It took 7 ms on the simulator; the cost on a slow
controller PC has not been measured. The checks stop once the server has
connected.

## A LinuxCNC task that does not respond

LinuxCNC's task process (`milltask`) takes the commands that clients send. If
it stops answering (it hangs, is stopped in a debugger, or does not get the
CPU), commands behave as described here. Everything in this section was
measured on the LinuxCNC 2.9.4 simulator with the task process frozen by
`SIGSTOP`; the numbers are from that machine.

**A command waits up to 5 seconds per LinuxCNC message.** LinuxCNC's Python
call that sends a command writes it to LinuxCNC's command queue and then waits
for the task to confirm it, for at most 5 seconds (a fixed value built into
LinuxCNC). After that it returns without an error. So `SendCommand` answers
after about 5 seconds (5.01 s measured), or 10 seconds for a command sent as
two LinuxCNC messages such as `coolant` (10.02 s), with `RCS_DONE`, a
`linuxcnc_serial` and `acknowledged` false.

**The command is not withdrawn.** It stays in LinuxCNC's queue and runs when
the task runs again: a mode change sent to the frozen task took effect within
about 35 ms of the task being continued, and so did two commands queued behind
each other, in order. A client deadline does not withdraw a command either. A
`SendCommand` that has reached its turn is carried out even if its client has
given up meanwhile; only a command still waiting for its turn is dropped when
its client goes away (unless it only stops something, see below).

**Commands are sent one at a time, a stop included.** The server sends one
command, with all its messages, before it starts the next. A command that
arrives while another waits for the task waits for its turn: a feed override
sent 0.5 s after a mode change returned after 9.5 s, both with `acknowledged`
false. That applies to an E-stop or an abort sent through the server as well.
A command waiting its turn is answered `RCS_ERROR` with `not_sent` true,
without being sent, if its client goes away or the server stops, except a
command that only stops something (E-stop, machine off, program abort and
pause, jog stop, spindle off), which always waits and is sent. Every waiting
command occupies one of the server's worker threads (10 by default). Do not
make the server the machine's only way to stop: the E-stop chain of a machine
belongs in its hardware and HAL configuration.

**The rest of the server keeps answering.** LinuxCNC's send call waits
without letting any other Python thread of the process run. A server that made
that call itself stalled completely: status, health, file and HAL calls were
all answered about 4.5 seconds late. The server therefore sends commands from
a separate process, its command helper, and waits for the helper's answer in a
way that lets the other threads run. With a send blocked for 5 seconds,
`GetStatus`, `GetServerInfo`, the health check, `ListFiles` and HAL
`QueryPins` answered in 3 to 16 ms, and `StreamStatus` at 100 ms kept sending
an update every 0.10 to 0.11 s. A `WaitComplete` with a 2-second timeout
made meanwhile waited for the command being sent and answered `RCS_EXEC`
after 2.0 s, as for any command still in progress (see
[`WaitComplete`](#waitcomplete)). The same scenario
without the helper (`--in-process-commands`): every one of those calls
answered when the send returned, 4.5 s late, and the status stream sent
nothing for 3 seconds. The helper costs a command a few milliseconds at most:
the medians of 100 commands with and without it were 50 and 49 ms for a mode
change and 29 and 24 ms for a feed override.

**What `acknowledged` tells.** `CommandResponse.acknowledged` is set on every
command that was dispatched with LinuxCNC bindings that report serials (2.8,
2.9 and master do):

- `true`: the task had echoed the command's serial when the send returned. It
  has taken the command; it may still be executing it.
- `false`: the command was written to LinuxCNC's queue, but the task had not
  taken it after LinuxCNC's 5-second wait. The task is not answering. The
  command runs whenever the task does; read the status
  (`task.echo_serial_number` reaching `linuxcnc_serial`, then the state you
  expect) before you rely on it or send it again. The server logs a warning.
- unset: the server cannot tell (nothing was dispatched, the bindings have
  no serial, or the status poll right after the send failed).

`RCS_DONE` keeps its meaning, "dispatched". For a command sent as several
LinuxCNC messages, `acknowledged` describes the last one.

**The command helper process.** The server starts it when it connects to
LinuxCNC. It shows in `ps` as a child of the server:

```text
/path/to/python /path/to/site-packages/linuxcnc_grpc/command_helper_process.py
```

(the same Python interpreter as the server).

It runs in a session of its own, so a Ctrl+C in the server's terminal does not
reach it. It ignores `SIGTERM` and `SIGINT`, so that a signal sent to a whole
group of processes cannot stop it in the middle of a write to LinuxCNC. It
ends when the server closes the pipe to it, which the server does when it
stops and which also happens when the server dies. If it is in a send at that
moment it finishes the send first, which can take up to 5 seconds per message.
It is not left behind.

A command that is under way when the server stops is completed, all its
messages, and the server process exits once it is done. Measured with
`SIGTERM` to the server 1 s into a blocked single-message command: the server
exited 4 s later, when the send returned, and the helper with it. 1 s into a
blocked `coolant` command (two messages): the client's call ended with
`UNAVAILABLE` when the server's 5-second grace period ran out, the server
still sent the second message and exited 9 s after the signal, and no helper
was left. A supervisor that kills the server sooner than that cuts the command
short; systemd's default stop timeout is 90 seconds.

If the helper dies, or the pipe to it breaks, the server starts another one
before the next command. A command the helper had already been given is
answered `RCS_ERROR` with a message that it may have been sent: the helper
writes to LinuxCNC before it answers, so the server cannot know. Check the
status before sending it again. Health stays `SERVING` meanwhile, because the
server's connection to LinuxCNC is unchanged; if no helper can be started,
commands are answered `RCS_ERROR` with `not_sent` true.

**Turning it off.** `--in-process-commands` (or
`create_server(command_helper=False)`) sends commands from the server's own
process, as versions up to 1.0 did. There is no child process then, and a
command sent to a task that does not respond stalls every RPC of the server
for as long as the send waits. A `LinuxCNCServiceServicer` built directly in
Python sends from its own process unless it is given `command_helper=True`.

## Network Configuration

### Firewall

Allow incoming connections on the gRPC port:

```bash
# UFW (Ubuntu)
sudo ufw allow 50051/tcp

# firewalld (Fedora/CentOS)
sudo firewall-cmd --permanent --add-port=50051/tcp
sudo firewall-cmd --reload

# iptables
sudo iptables -A INPUT -p tcp --dport 50051 -j ACCEPT
```

### Binding to Specific Interface

To bind to a specific network interface:

```bash
# Bind to localhost only (no remote access)
linuxcnc-grpc --host 127.0.0.1

# Bind to specific IP
linuxcnc-grpc --host 192.168.1.100
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LINUXCNC_NC_FILES` | `/home/linuxcnc/linuxcnc/nc_files` | Directory for G-code file operations (UploadFile, ListFiles, ReadFile, DeleteFile, ResolveFile, program open) |

### NC Files Directory

All file management RPCs (`UploadFile`, `ListFiles`, `ReadFile`, `DeleteFile`, `ResolveFile`) and the `program open` command operate within a single allowed directory. By default this is `/home/linuxcnc/linuxcnc/nc_files`.

To change it, use the `--nc-files` flag:

```bash
linuxcnc-grpc --host 0.0.0.0 --nc-files /home/user/nc_programs
```

Or set the `LINUXCNC_NC_FILES` environment variable:

```bash
export LINUXCNC_NC_FILES=/home/user/nc_programs
linuxcnc-grpc --host 0.0.0.0
```

Priority: `--nc-files` flag > `LINUXCNC_NC_FILES` env var > default.

Or in a systemd service file:

```ini
[Service]
Environment=LINUXCNC_NC_FILES=/home/user/nc_programs
ExecStart=/usr/local/bin/linuxcnc-grpc --host 0.0.0.0 --port 50051
```

Path traversal outside this directory is rejected — filenames like `../../etc/passwd` will return `INVALID_ARGUMENT`. The server resolves symlinks and validates the canonical path stays within the allowed directory. A name that runs into a symbolic link loop is also `INVALID_ARGUMENT`.

## Security Considerations

The server has **no authentication and no encryption**. Every RPC is open to
anyone who can connect to the port, and that includes motion, spindle, program
and file-upload commands. The default bind address is `0.0.0.0` (all
interfaces), so decide deliberately where it listens.

### Recommended: localhost plus an SSH tunnel

Bind the server to localhost on the LinuxCNC machine:

```bash
linuxcnc-grpc --host 127.0.0.1 --port 50051
```

Then open a tunnel from each client machine and point the client at
`localhost:50051`:

```bash
ssh -N -L 50051:127.0.0.1:50051 user@cnc-machine
```

SSH supplies the authentication and encryption the server lacks, and no
firewall port needs opening.

### Trusted LAN only

Binding to `0.0.0.0` or a LAN address is acceptable only on a network where
you trust every device, such as an isolated machine network. In that case the
insecure connection is suitable for:

- Local development
- Trusted private networks
- Testing and evaluation

### Production Security

For production use on untrusted networks:

1. **Use TLS** - gRPC supports TLS encryption. Configure your server with certificates.
2. **Use a VPN** - Run LinuxCNC on a private network accessible only via VPN.
3. **Firewall rules** - Restrict access to known client IPs.
4. **Authentication** - Implement gRPC interceptors for authentication.

### TLS Configuration (Advanced)

To enable TLS, modify the server code or use a gRPC proxy like Envoy:

```python
# Example TLS configuration (requires code modification)
import grpc

with open('server.key', 'rb') as f:
    private_key = f.read()
with open('server.crt', 'rb') as f:
    certificate_chain = f.read()

server_credentials = grpc.ssl_server_credentials(
    [(private_key, certificate_chain)]
)
server.add_secure_port('[::]:50051', server_credentials)
```

## Command and Error Semantics

A few behaviours that are easy to get wrong from a client.

### `SendCommand` returns when the command is dispatched

`SendCommand` answers `RCS_DONE` as soon as LinuxCNC has accepted the command.
**`RCS_DONE` here means "dispatched", not "finished"**: an MDI move or a
homing sequence is usually still running when the response arrives. To know
when it has finished, call `WaitComplete` or watch the status
(`task.exec_state`, `task.interp_state`, `task.task_mode`).

### `RCS_ERROR` from `SendCommand`

`SendCommand` answers `RCS_ERROR`, with the reason in `error_message`, in
these cases:

- **The server rejected the command.** It checks some values itself: a joint
  or spindle index, an MDI command that is empty or too long, the file name of
  a program to open, an enum value it does not know. A command that fails a
  check is not sent, and `error_message` is the server's own text, for example
  `MDI command must not be empty`.
- **A LinuxCNC call failed.** `error_message` is the text of that error.
- **The command helper process was lost with the command.** The text says the
  command may have been sent; check the status before sending it again (see
  [A LinuxCNC task that does not respond](#a-linuxcnc-task-that-does-not-respond)).
- **LinuxCNC did not take the command.** If the write to LinuxCNC's command
  channel fails, LinuxCNC's Python bindings report no error. The server
  notices because LinuxCNC's command serial did not change, and answers
  `RCS_ERROR` with a message that the command was not accepted by LinuxCNC.
  Nothing was sent. This check needs LinuxCNC bindings that expose
  `command.serial`; those of 2.8, 2.9 and master do.

Most commands are one LinuxCNC message. Four are sent as several messages, one
after the other:

| Command | Messages, in order |
|---------|--------------------|
| `coolant` | Mist, flood |
| `set_limit` | Minimum limit, maximum limit |
| `override_config` | Feed override, spindle override, feed hold, adaptive feed |
| `program_options` | Optional stop, block delete |

If one of these messages fails, the ones before it have been sent and stay in
effect, and the ones after it are not sent. The answer is `RCS_ERROR` all the
same, and the response does not say how many messages went out.

The server's own checks all run before the first message (see
[Values the server rejects](api-reference.md#values-the-server-rejects)), so a
rejected command is never partly sent. Only a LinuxCNC call that fails after
the first message leaves one that way.

The check for a command LinuxCNC did not take looks at the command as a whole.
If LinuxCNC takes none of these messages, the answer is `RCS_ERROR` and
nothing was sent. If it takes some and silently drops another, the answer is
`RCS_DONE`. The same holds for opening a program, which LinuxCNC itself sends
as two messages (close the current program, open the new one).

After `RCS_ERROR` from one of these four commands, read the status before you
send the command again: part of it may have taken effect.

**`not_sent` says when a retry is safe.** With `RCS_ERROR`,
`CommandResponse.not_sent` is true when the server is certain that nothing
was written to LinuxCNC for the request: the server rejected it, it was
dropped while waiting its turn (shutdown, client gone), no command helper
could be started, or LinuxCNC took none of its messages. Sending it again
cannot run it twice. False means the server cannot rule out that something
was written: a LinuxCNC call that failed during a send, a command of several
messages that failed part-way, a helper lost with the command, or bindings
without a serial. A 1.0 server never sets it.

### The response serial is the server's, not LinuxCNC's

`CommandResponse.serial` is the `serial` you set on the request or, if you
left it at 0, a number from the gRPC server's own counter. It is **not**
LinuxCNC's command serial, so don't compare it with
`status.task.echo_serial_number`. It is only a label for your own
bookkeeping: the server does not use it to identify a command later, and
`WaitComplete` ignores it. The server's counter wraps to 1 after 2147483647
(the field is an int32); the HAL service's counter does the same.

### `linuxcnc_serial` identifies the command to LinuxCNC

`CommandResponse.linuxcnc_serial` is LinuxCNC's own serial number for the
command. It is set whenever the command was dispatched:

- **It identifies the command; it does not prove the task received it.**
  LinuxCNC's send call normally returns once the task has echoed the command,
  so `task.echo_serial_number` is normally at or past this value by the time
  you get the response. But if the task does not answer, the send gives up
  after about 5 seconds without an error and the serial is returned all the
  same (see [A LinuxCNC task that does not respond](#a-linuxcnc-task-that-does-not-respond)).
  The server looks at the echo once, right after the send, and reports it in
  `acknowledged`. To confirm later that the command was received, call
  `WaitComplete` with the value or compare it with `task.echo_serial_number`
  in the status.
- **It says nothing about the outcome.** LinuxCNC can still refuse the command
  (an MDI line in the wrong mode, for example) or fail part-way. Pass the
  value to `WaitComplete` to get `RCS_DONE` or `RCS_ERROR` for it, within the
  limits described [below](#waitcomplete).
- **For a command sent as several LinuxCNC messages** (see the table
  [above](#rcs_error-from-sendcommand)) it is the serial of the last message
  LinuxCNC took, so a wait on it covers that message only.
- **It is unset** whenever the answer is `RCS_ERROR`, also when part of the
  command was sent (see [`RCS_ERROR` from `SendCommand`](#rcs_error-from-sendcommand)),
  and when the LinuxCNC Python bindings don't expose `command.serial`.
  LinuxCNC 2.8, 2.9 and master do.
- Serials are only meaningful for one run of LinuxCNC. Don't keep them across
  a restart of LinuxCNC.

### `WaitComplete`

`WaitComplete` waits until **the most recent command sent through this
server** has finished. The `serial` in the request is ignored, as it always
has been, and is only echoed back in the response. After sending A and then B,
`WaitComplete` reports on B whichever serial you pass.

With several clients on one server, "most recent" means the most recent from
any of them, so a plain `WaitComplete` cannot tell you about your own command
if another client has sent one since.

**To wait on one command's serial, set `linuxcnc_serial`** in the request to
the value `SendCommand` returned for it. The wait then applies LinuxCNC's own
serial rule (the one its `wait_complete` uses) to that serial instead of the
latest one:

- If the task is on that command, the result is `RCS_DONE` or `RCS_ERROR` when
  it stops executing.
- If LinuxCNC has echoed a later command, the result is `RCS_DONE` at once,
  whatever became of the earlier one.
- If the value is not a serial this server returned from `SendCommand` on its
  current connection to LinuxCNC, the result is `RCS_ERROR` at once with a
  message saying so. That covers a made-up serial, one from before the server
  last reconnected (unless the new connection has by chance issued the same
  number), and one older than the last 4096 commands. An explicit 0 is
  treated like any other value: it is refused unless this server issued 0.
  Copy the field only when the response has it set (`HasField` in Python),
  because an unset field reads as 0.
- If the server reconnects to LinuxCNC during the wait, the result is
  `RCS_ERROR` with a message saying the outcome is unknown: LinuxCNC may have
  restarted and begun counting serials again. A reconnect in the instant
  between the wait's last look at the status and its answer is not noticed,
  and the answer can then still be `RCS_DONE`.

**What the answer proves.** A wait with `linuxcnc_serial` is exact as long as
nothing else is sent to LinuxCNC meanwhile. Once a later command has been
echoed, sent through this server or by any other client of the same LinuxCNC
(the GUI, for example, or a feed override sent while a long MDI move runs),
the answer is `RCS_DONE` and the wait does not say how the earlier command
ended: it may still be executing, or it may have failed. A client that needs
to know a motion has finished should also read the status:
`task.interp_state` back at `INTERP_IDLE`, `trajectory.inpos` true and
`trajectory.current_vel` at zero, as fits the command.

The rest of this section describes the wait without `linuxcnc_serial`.

| Status | Meaning |
|--------|---------|
| `RCS_DONE` | The command finished |
| `RCS_ERROR` | The command failed (`error_message` is `"Command failed"`), or LinuxCNC could not be read |
| `RCS_EXEC` | The timeout expired and the command is still running |

- A `timeout` of 0 means the default of 5 seconds.
- The timeout covers the whole call, including the time it waits for the
  server's internal lock (and the connection check at the start). If another
  RPC holds the lock past the deadline, the answer is `RCS_EXEC`, as for any
  timeout.
- The command it waits on is the latest one dispatched when the call arrives.
  A command sent while the wait is in progress does not change its target.
- A command whose messages are still being sent when the call arrives (a
  `SendCommand` waiting for a task that does not respond, say) is the latest
  one. The wait goes on until its send is over and then applies the rules
  below to it; while the send lasts it answers `RCS_EXEC` at its timeout, as
  for any command still in progress. That is also what versions up to 1.0
  answered, only later: there the wait could not even start until the send
  had returned. If the server's command helper process is lost with that
  command, what became of it is not known; the wait then reports on the
  command before it, and the `SendCommand` itself says the command may have
  been sent.
- A wait with `linuxcnc_serial` is not held up by a command being sent. After
  `acknowledged` false it waits for the task to take the command, and answers
  `RCS_EXEC` at its timeout while the task does not answer.
- The command counts as finished when LinuxCNC has picked it up and is no
  longer executing it; the result is then `RCS_DONE` or `RCS_ERROR`.
- If LinuxCNC has already moved on to a later command (one sent during the
  wait, or from the GUI or anything else), the result is `RCS_DONE` straight
  away, even while that later command is running or has failed. The later
  command's outcome is not this command's.
- If the server has no command to wait for, it returns `RCS_DONE` immediately,
  whatever the machine is doing. That is the case when nothing has been sent
  through this server yet, and after the server has reconnected to LinuxCNC,
  which makes it forget the command it last sent.
- If the server reconnects to LinuxCNC while a wait is in progress, the wait
  switches to the most recent command sent since the reconnect, or returns
  `RCS_DONE` if there is none. Check the status rather than rely on
  `WaitComplete` across a LinuxCNC restart.
- The server polls status about every 50 ms while it waits. A wait in
  progress does not hold the server's internal lock, so an estop sent from
  another client or thread is not queued behind it. (Up to 1.0.0 it was:
  every other RPC waited until the `WaitComplete` returned.)
- Each waiting `WaitComplete` still occupies one of the server's worker
  threads (10 by default) until it returns. Don't park many long waits at
  once, or new RPCs will have to queue for a free thread.

### Error messages and the GUI

LinuxCNC delivers operator and NML error messages through its NML error
channel. That channel is **a single queue, and reading a message removes
it**. The LinuxCNC Python module offers no way to look at a message without
taking it. So each message goes to exactly one reader: the machine's GUI
(AXIS, gmoccapy and so on) or this server, never both.

We checked this on the LinuxCNC 2.9 simulator, with a second error-channel
reader standing in for the GUI:

- A second reader in the same process, or in another process, gets nothing
  once the first has read the message.
- A reader that connects after the message was posted still finds it, as long
  as nobody has read it yet.
- Putting a message back for the GUI is not possible either. The only way to
  post one is `operator_message`, which adds a *new* message to the same
  queue, where the server would read it again.

The server therefore cannot make its reading invisible to the GUI. What it
can do is read in one place, share what it read with every client, and let
you choose whether it reads at all. That is the `--error-channel` option:

| Mode | The server reads the channel | GUI sees messages | `StreamErrors` | `status.errors` |
|------|------------------------------|-------------------|----------------|-----------------|
| `on-demand` (default) | Only while at least one `StreamErrors` client is connected | Yes; while a client is streaming, only the ones it polls before the server does | Works | Messages read during streams |
| `always` | From startup | Only the ones it polls before the server does | Works, and `replay_buffered` returns messages from before the client connected | Always the most recent messages |
| `off` | Never | Yes, always | Refused with `FAILED_PRECONDITION` | Always empty |

Measured on the simulator: in `on-demand` with no stream open, and in `off`,
the stand-in GUI reader received every message and `status.errors` stayed
empty. In `on-demand` with a stream open, and in `always`, the stream
received every message and the stand-in GUI reader received none. That is
what was observed, not a guarantee: nothing reserves a message for the server,
so a GUI that polls the queue first gets that message and the server never
sees it.

Which to use:

- **A machine with an operator at a GUI:** keep `on-demand` and don't call
  `StreamErrors` unless your client is meant to take over error display, or
  use `off` to make sure no client can. Detect trouble from the status
  instead: `task.exec_state`, `task.task_state` (estop),
  `task.interpreter_errcode`, limit status and joint faults.
- **A headless machine, or one driven entirely over gRPC:** use `always`.
  Every client then sees every message the server read, and
  `replay_buffered` returns the newest 256 from before the client connected.

Details:

- While the server reads the channel and a GUI is running too, both are
  polling the same queue and each message goes to whichever polls first. The
  server polls about every 50 ms.
- In `on-demand` mode, a message that was waiting in the queue when the first
  stream starts is read and delivered. When the last stream ends, the server
  stops reading; a poll already under way at that moment may still take one
  more message, which then appears in `status.errors` and in later replays.
- The server keeps the most recent 256 messages. A `StreamErrors` client
  that falls more than 256 messages behind loses the older ones. There is no
  error; the only sign is a jump in `sequence`, which clients built against
  1.0 cannot see.
- `status.errors` carries the newest 20 messages in every `GetStatus` and
  `StreamStatus` message, and they stay there until newer ones push them out.
  Use `ErrorMessage.sequence` to tell a new message from one you have already
  handled. If more than 20 messages arrive between two status reads, the
  ones in between never appear there, so a gap in `sequence` between polls
  is normal; use `StreamErrors` if you need every message.
- `timestamp` is when the server read the message, not when LinuxCNC raised it.

## Using with Claude (MCP)

`linuxcnc-mcp` is an MCP (Model Context Protocol) server built on this API.
It lets AI assistants such as Claude monitor and diagnose a machine and, when
you opt in, operate it. See [Using with Claude (MCP)](using-with-claude.md)
for what it does and how it uses this server.

## Logging

### Log Levels

- **INFO** (default) - Normal operation, connection events, and one line for each command sent to LinuxCNC with its main values. For an MDI command that line has the full command text, and for an operator message the message text
- **DEBUG** - Also one line per RPC with the method name, and for most RPCs a line with its main request fields (command type, file name, query pattern). It adds no other request contents and no responses, with one exception: the full text of every MDI command is logged when it arrives, including one the server then rejects

Enable debug logging:

```bash
linuxcnc-grpc --debug
```

### Log Format

```
2024-01-15 10:30:45 [INFO] linuxcnc_grpc.server: Server configured on 0.0.0.0:50051
2024-01-15 10:30:45 [INFO] linuxcnc_grpc.server: LinuxCNC + HAL gRPC Server
```

## Connection Handling

### Reconnection

The server automatically handles LinuxCNC connection issues:

- Starts without LinuxCNC and connects when it comes up
- Stops with LinuxCNC on a normal shutdown (run it under a supervisor to come back)
- Does not detect a crashed LinuxCNC (see "What happens when LinuxCNC stops")
- Returns `UNAVAILABLE` to clients and `NOT_SERVING` from the health service
  while disconnected
- Reconnects if a status poll raises an error (rare: see below)

See [Starting before LinuxCNC](#starting-before-linuxcnc) for the details and
for what a LinuxCNC shutdown does to the server.

### Thread Pool

The server uses a thread pool for handling concurrent requests:

- Default: 10 worker threads
- Sufficient for most use cases
- Can be customized in code if needed

Every stream that is open (`StreamStatus`, `StreamErrors`, the HAL streams)
occupies one worker thread for as long as it lasts. A stream gives its thread
back as soon as the client cancels or disconnects, whatever interval it asked
for. So does every `SendCommand` that waits for its turn behind a command the
LinuxCNC task is not answering (see
[A LinuxCNC task that does not respond](#a-linuxcnc-task-that-does-not-respond));
a waiting command whose client has gone gives its thread back within about a
tenth of a second, unless it only stops something.

## Troubleshooting

### RPCs return `UNAVAILABLE` ("LinuxCNC is not running…")

The server is up but not connected to LinuxCNC. Start LinuxCNC; the server
connects within a few seconds:

```bash
linuxcnc /path/to/your/machine.ini
```

If LinuxCNC is running and the server still does not connect, check that both
run as the same user and that the server's Python can import the `linuxcnc`
module (a venv needs `--system-site-packages`). With `--require-linuxcnc` the
server exits at startup instead of waiting.

### "Address already in use"

Another process is using the port:

```bash
# Find what's using port 50051
sudo lsof -i :50051

# Use a different port
linuxcnc-grpc --port 50052
```

### "Connection refused" from client

1. Verify the server is running
2. Check firewall rules
3. Verify the host/port match
4. Check network connectivity: `nc -zv hostname 50051`

### Debug Mode

Enable debug logging to see detailed information:

```bash
linuxcnc-grpc --debug
```

This adds, for every RPC, a line with the method name, and for most RPCs a
line with the main request fields. Responses are not logged. Commands that
were sent to LinuxCNC (including the full text of each MDI command) and
connection changes are logged at INFO, so they appear without `--debug`.

## Performance Tuning

### Streaming Intervals

Status streaming defaults to 100ms intervals. Adjust based on your needs:

- **10-50ms** - High-frequency updates (UI animation)
- **100ms** - Standard monitoring
- **500ms-1000ms** - Low-bandwidth connections

Clients specify the interval when calling `StreamStatus`:

```python
request = linuxcnc_pb2.StreamStatusRequest(interval_ms=50)
```

The longest interval is 60 seconds: a larger `interval_ms` is treated as
60000. The same default and limit apply to the HAL service's `StreamStatus`
and `WatchValues`.

### Network Latency

gRPC performs well over high-latency connections. For best results:

- Use streaming instead of polling for real-time data
- Batch multiple queries when possible
- Consider compression for slow connections
