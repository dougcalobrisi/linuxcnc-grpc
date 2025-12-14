# Server Configuration

The linuxcnc-grpc server runs on your LinuxCNC machine and exposes both LinuxCNCService and HalService over gRPC.

## Requirements

- Linux with LinuxCNC installed and running
- Python 3.8+
- Network access to the machine (for remote clients)

## Installation

```bash
pip install linuxcnc-grpc
```

This installs the `linuxcnc-grpc-server` command-line tool.

## Basic Usage

Start LinuxCNC first, then start the gRPC server:

```bash
linuxcnc-grpc-server --host 0.0.0.0 --port 50051
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
| `--debug` | off | Enable debug logging |

## Auto-Start with LinuxCNC

### Method 1: HAL File

Add to your machine's HAL file (e.g., `your_machine.hal`):

```hal
# Start gRPC server
# -W makes LinuxCNC wait for the server to be ready
loadusr -W linuxcnc-grpc-server --host 0.0.0.0 --port 50051
```

### Method 2: Dedicated HAL File

Create a separate `grpc-server.hal` file:

```hal
# grpc-server.hal
loadusr -W linuxcnc-grpc-server --host 0.0.0.0 --port 50051
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
After=linuxcnc.service

[Service]
Type=simple
ExecStart=/usr/local/bin/linuxcnc-grpc-server --host 0.0.0.0 --port 50051
Restart=on-failure
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
linuxcnc-grpc-server --host 127.0.0.1

# Bind to specific IP
linuxcnc-grpc-server --host 192.168.1.100
```

## Security Considerations

The default configuration uses insecure (unencrypted) connections. This is suitable for:

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

## Logging

### Log Levels

- **INFO** (default) - Normal operation, connection events
- **DEBUG** - Detailed request/response logging

Enable debug logging:

```bash
linuxcnc-grpc-server --debug
```

### Log Format

```
2024-01-15 10:30:45 [INFO] linuxcnc_grpc.server: Server configured on 0.0.0.0:50051
2024-01-15 10:30:45 [INFO] linuxcnc_grpc.server: LinuxCNC + HAL gRPC Server
```

## Connection Handling

### Reconnection

The server automatically handles LinuxCNC connection issues:

- Detects when LinuxCNC disconnects
- Returns `UNAVAILABLE` status to clients during disconnection
- Automatically reconnects when LinuxCNC becomes available

### Thread Pool

The server uses a thread pool for handling concurrent requests:

- Default: 10 worker threads
- Sufficient for most use cases
- Can be customized in code if needed

## Troubleshooting

### "LinuxCNC not running"

The server requires LinuxCNC to be running first:

```bash
# Start LinuxCNC
linuxcnc /path/to/your/machine.ini

# Then start the gRPC server
linuxcnc-grpc-server
```

### "Address already in use"

Another process is using the port:

```bash
# Find what's using port 50051
sudo lsof -i :50051

# Use a different port
linuxcnc-grpc-server --port 50052
```

### "Connection refused" from client

1. Verify the server is running
2. Check firewall rules
3. Verify the host/port match
4. Check network connectivity: `nc -zv hostname 50051`

### Debug Mode

Enable debug logging to see detailed information:

```bash
linuxcnc-grpc-server --debug
```

This shows:
- All incoming requests
- Response details
- Connection events
- Internal state changes

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

### Network Latency

gRPC performs well over high-latency connections. For best results:

- Use streaming instead of polling for real-time data
- Batch multiple queries when possible
- Consider compression for slow connections
