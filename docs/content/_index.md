---
title: "LinuxCNC gRPC Documentation"
weight: 1
geekdocNav: false
geekdocAlign: center
---

API documentation and guides for the LinuxCNC gRPC interface.

## Contents

| Document | Description |
|----------|-------------|
| [Server Setup](server-setup.md) | Install and run the gRPC server on your LinuxCNC machine |
| [Client Quickstart](getting-started.md) | Install a client library and make your first API calls |
| [Tutorial](tutorial.md) | Step-by-step guide to connect from a remote machine |
| [Server Configuration](server.md) | Advanced server options, auto-start, TLS, and tuning |
| [API Reference](api-reference.md) | Complete API documentation for both services |
| [Examples Guide](examples.md) | Walkthrough of example code in all languages |

## Overview

LinuxCNC gRPC provides a network interface to LinuxCNC, enabling:

- **Remote control** - Monitor and control CNC machines from any device
- **Multi-language support** - Python, Go, Node.js/TypeScript, Rust clients
- **Real-time streaming** - Subscribe to status updates instead of polling
- **HAL introspection** - Query HAL pins, signals, and parameters

## Architecture

```
┌─────────────────┐     gRPC      ┌──────────────────┐
│  Client App     │◄────────────►│  gRPC Server     │
│  (any language) │   :50051     │  (Python)        │
└─────────────────┘               └────────┬─────────┘
                                           │
                                           ▼
                                  ┌──────────────────┐
                                  │  LinuxCNC        │
                                  │  (local machine) │
                                  └──────────────────┘
```

The gRPC server runs on the LinuxCNC machine and translates gRPC calls to LinuxCNC's native Python API. Clients can connect from anywhere on the network.

## Services

### LinuxCNCService

Main service for machine control:

- **GetStatus** - Poll current machine status
- **SendCommand** - Execute commands (jog, MDI, state changes, etc.)
- **WaitComplete** - Wait for command completion
- **StreamStatus** - Subscribe to real-time status updates
- **StreamErrors** - Subscribe to error messages

### HalService

HAL (Hardware Abstraction Layer) introspection:

- **GetSystemStatus** - Get complete HAL system state
- **GetValue** - Get a single pin/signal/parameter value
- **QueryPins/Signals/Params/Components** - Query with glob patterns
- **StreamStatus** - Subscribe to HAL status updates
- **WatchValues** - Watch specific values for changes

## Quick Links

- [Installation](getting-started.md#installation)
- [Python Quickstart](getting-started.md#python)
- [Go Quickstart](getting-started.md#go)
- [Node.js Quickstart](getting-started.md#nodejs--typescript)
- [Rust Quickstart](getting-started.md#rust)
- [LinuxCNCService API](api-reference.md#linuxcncservice)
- [HalService API](api-reference.md#halservice)
