#!/bin/bash
# Build Rust crate
# Usage: ./scripts/build-rust.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

RUST_DIR="$PROJECT_ROOT/packages/rust"

info "Building Rust crate..."

cd "$RUST_DIR"

# Build release
info "Running cargo build --release..."
cargo build --release

# Run tests
info "Running cargo test..."
cargo test

# Preview publishable files (allow dirty for development builds)
info "Package contents:"
cargo package --list --allow-dirty

echo ""
success "Build complete!"
