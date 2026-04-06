---
title: "Server Setup"
weight: 5
---

How to install and run the linuxcnc-grpc server on your LinuxCNC machine.

## Prerequisites

- A Linux machine with **LinuxCNC** installed (physical or simulation)
- **Python 3.9+** (included with most LinuxCNC installations)
- Network access if you want to connect from a remote machine

## Install

SSH into your LinuxCNC machine (or open a terminal on it) and install:

```bash
pip install linuxcnc-grpc
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv pip install linuxcnc-grpc
```

This installs both the gRPC server and the Python client library.

## Start the Server

LinuxCNC must already be running before you start the gRPC server.

```bash
# Start LinuxCNC (if not already running)
linuxcnc /path/to/your/machine.ini

# Start the gRPC server
linuxcnc-grpc --host 0.0.0.0 --port 50051
```

`--host 0.0.0.0` makes the server listen on all network interfaces so remote clients can connect. Use `127.0.0.1` to restrict to local-only access.

You should see:

```
2024-01-15 10:30:45 [INFO] linuxcnc_grpc.server: Server configured on 0.0.0.0:50051
2024-01-15 10:30:45 [INFO] linuxcnc_grpc.server: LinuxCNC + HAL gRPC Server
```

## Verify It's Working

### From the LinuxCNC machine

```bash
python3 -c "
import grpc
from linuxcnc_pb import linuxcnc_pb2, linuxcnc_pb2_grpc

channel = grpc.insecure_channel('localhost:50051')
stub = linuxcnc_pb2_grpc.LinuxCNCServiceStub(channel)
status = stub.GetStatus(linuxcnc_pb2.GetStatusRequest())
print(f'Connected! LinuxCNC version: {status.version}')
print(f'Machine state: {linuxcnc_pb2.TaskState.Name(status.task.task_state)}')
channel.close()
"
```

### From a remote machine

If you have [grpcurl](https://github.com/fullstorydev/grpcurl) installed:

```bash
grpcurl -plaintext LINUXCNC_IP:50051 list
```

This should return:

```
linuxcnc.HalService
linuxcnc.LinuxCNCService
```

Or test network connectivity:

```bash
nc -zv LINUXCNC_IP 50051
```

If this fails, check your firewall settings — see [Server Configuration](server.md#firewall).

## Auto-Start with LinuxCNC

For production use, you'll want the gRPC server to start automatically when LinuxCNC launches. The simplest approach is to add it to your machine's HAL file:

```hal
loadusr linuxcnc-grpc --host 0.0.0.0 --port 50051
```

See [Server Configuration](server.md#auto-start-with-linuxcnc) for all auto-start methods (HAL file, dedicated HAL file, systemd service).

## What's Next

- [Client Quickstart](getting-started.md) — Install a client library and make your first API calls
- [Server Configuration](server.md) — Advanced options, TLS, firewall, performance tuning
- [Tutorial](tutorial.md) — Full walkthrough of connecting from a remote development machine
