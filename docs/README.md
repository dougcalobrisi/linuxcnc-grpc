# Documentation

This directory contains the Hugo source for the LinuxCNC gRPC documentation site.

**Published site:** https://dougcalobrisi.github.io/linuxcnc-grpc/

## Local Development

```bash
# From the repo root:
make docs-serve
```

This downloads the Hugo theme (if needed) and starts a local server with live reload at http://localhost:1313.

## Structure

- `hugo.toml` - Hugo configuration
- `content/` - Markdown documentation pages
- `static/` - Static assets (images, etc.)
- `themes/` - Hugo theme (downloaded at build time, gitignored)
