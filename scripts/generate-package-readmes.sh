#!/bin/bash
# Generate package-specific READMEs for npm and crates.io from the root README.
# These are auto-generated so registry pages show proper documentation.
# Usage: ./scripts/generate-package-readmes.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

REPO_URL="https://github.com/dougcalobrisi/linuxcnc-grpc"
BLOB_URL="$REPO_URL/blob/main"

README="$PROJECT_ROOT/README.md"

# Extract badges (lines starting with [![)
get_badges() {
    sed -n '/^\[!\[/p' "$README"
}

# Extract the intro paragraph and Why gRPC section
get_intro() {
    sed -n '/^gRPC interface/,/^## Running the Server/{ /^## Running the Server/d; p; }' "$README"
}

# Common tail for all package READMEs
write_tail() {
    local lang_name="$1"
    local examples_dir="$2"
    cat << EOF

## Examples

See the [examples directory]($BLOB_URL/examples/$examples_dir) for complete $lang_name examples:

- \`get_status\` — Poll machine status
- \`stream_status\` — Real-time status streaming
- \`jog_axis\` — Jog axes with keyboard
- \`mdi_command\` — Execute G-code via MDI
- \`hal_query\` — Query HAL pins/signals
- \`upload_file\` — Upload, list, delete G-code files

## Services

- **LinuxCNCService** — Machine control: status, jogging, MDI, program execution, file management
- **HalService** — HAL introspection: query pins, signals, parameters (read-only)

## Documentation

See the [full documentation]($BLOB_URL/docs) for server setup, API reference, and tutorials.

## Safety Warning

This package communicates with a gRPC server that controls real CNC machinery. Ensure proper safety measures:

- Use only on trusted networks
- Implement authentication in production (gRPC supports TLS/mTLS)
- Never leave machines unattended during remote operation
- Verify E-stop and safety systems are functional

## License

[MIT]($BLOB_URL/LICENSE)
EOF
}

# --- Node.js README ---
{
    echo '<!-- AUTO-GENERATED from root README.md — do not edit directly -->'
    echo '<!-- Regenerate with: ./scripts/generate-package-readmes.sh -->'
    echo ''
    echo '# linuxcnc-grpc'
    echo ''
    get_badges
    echo ''
    get_intro
    cat << 'EOF'
## Installation

```bash
npm install linuxcnc-grpc
```

> [npm package](https://www.npmjs.com/package/linuxcnc-grpc)

EOF
    # Extract the TypeScript code example from the README
    sed -n '/^```typescript/,/^```$/p' "$README"
    write_tail "Node.js/TypeScript" "node"
} > "$PROJECT_ROOT/packages/node/README.md"

# --- Rust README ---
{
    echo '<!-- AUTO-GENERATED from root README.md — do not edit directly -->'
    echo '<!-- Regenerate with: ./scripts/generate-package-readmes.sh -->'
    echo ''
    echo '# linuxcnc-grpc'
    echo ''
    get_badges
    echo ''
    get_intro
    cat << 'EOF'
## Installation

Add to `Cargo.toml`:

> [crates.io](https://crates.io/crates/linuxcnc-grpc)

EOF
    # Extract the Rust toml block
    sed -n '/^```toml/,/^```$/p' "$README"
    echo ''
    # Extract the Rust code block
    sed -n '/^```rust/,/^```$/p' "$README"
    write_tail "Rust" "rust"
} > "$PROJECT_ROOT/packages/rust/README.md"

echo "Generated packages/node/README.md"
echo "Generated packages/rust/README.md"
