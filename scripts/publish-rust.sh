#!/bin/bash
# Publish Rust crate to crates.io
# Usage: ./scripts/publish-rust.sh [--dry-run]
#   --dry-run  Validate without uploading

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

RUST_DIR="$PROJECT_ROOT/packages/rust"

# Parse arguments
parse_common_args "$@"

for arg in "$@"; do
    case $arg in
        --help|-h)
            echo "Usage: ./scripts/publish-rust.sh [--dry-run]"
            echo "  --dry-run  Validate without uploading"
            exit 0
            ;;
    esac
done

cd "$RUST_DIR"

# Verify Cargo.toml version matches what we expect to publish
EXPECTED_RUST_VERSION=$(get_rust_version)
PACKED_VERSION=$(cargo metadata --no-deps --format-version 1 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['packages'][0]['version'])")
if [ "$PACKED_VERSION" != "$EXPECTED_RUST_VERSION" ]; then
    error "Package version mismatch: cargo metadata says $PACKED_VERSION but Cargo.toml says $EXPECTED_RUST_VERSION"
    echo "  Run: make build-rust"
    exit 1
fi

# For actual publishing, we validate strictly (no dirty files)
# For dry-run mode, allow dirty to enable testing during development
if [ "$DRY_RUN" = true ]; then
    info "Validating package (dry-run, allowing uncommitted changes)..."
    cargo publish --dry-run --allow-dirty
    echo ""
    success "Dry run complete - package validated successfully"
    exit 0
fi

# Always do a strict dry-run first to validate before actual publish
info "Validating package..."
cargo publish --dry-run

# Publish
info "Publishing to crates.io..."
cargo publish

echo ""
success "Published to crates.io!"
echo "  View at: https://crates.io/crates/linuxcnc-grpc"
