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

# Always do a dry-run first to validate
info "Validating package..."
cargo publish --dry-run

if [ "$DRY_RUN" = true ]; then
    echo ""
    success "Dry run complete - package validated successfully"
    exit 0
fi

# Publish
info "Publishing to crates.io..."
cargo publish

echo ""
success "Published to crates.io!"
echo "  View at: https://crates.io/crates/linuxcnc-grpc"
