#!/bin/bash
# Build all packages
# Usage: ./scripts/build-all.sh [--continue-on-error]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

# Parse arguments
CONTINUE_ON_ERROR=false
for arg in "$@"; do
    case $arg in
        --continue-on-error)
            CONTINUE_ON_ERROR=true
            ;;
        --help|-h)
            echo "Usage: ./scripts/build-all.sh [--continue-on-error]"
            echo "  --continue-on-error  Continue building other packages if one fails"
            exit 0
            ;;
    esac
done

info "Building all packages..."
echo ""

PYTHON_STATUS=0
NODE_STATUS=0
RUST_STATUS=0

# Build Python
echo "=========================================="
echo "Python"
echo "=========================================="
if "$SCRIPT_DIR/build-python.sh"; then
    PYTHON_STATUS=0
else
    PYTHON_STATUS=1
    if [ "$CONTINUE_ON_ERROR" = false ]; then
        exit 1
    fi
fi
echo ""

# Build Node.js
echo "=========================================="
echo "Node.js"
echo "=========================================="
if "$SCRIPT_DIR/build-node.sh"; then
    NODE_STATUS=0
else
    NODE_STATUS=1
    if [ "$CONTINUE_ON_ERROR" = false ]; then
        exit 1
    fi
fi
echo ""

# Build Rust
echo "=========================================="
echo "Rust"
echo "=========================================="
if "$SCRIPT_DIR/build-rust.sh"; then
    RUST_STATUS=0
else
    RUST_STATUS=1
    if [ "$CONTINUE_ON_ERROR" = false ]; then
        exit 1
    fi
fi
echo ""

# Summary
echo "=========================================="
echo "Build Summary"
echo "=========================================="

print_status() {
    if [ "$2" -eq 0 ]; then
        echo -e "  $1: ${GREEN}OK${NC}"
    else
        echo -e "  $1: ${RED}FAILED${NC}"
    fi
}

print_status "Python" $PYTHON_STATUS
print_status "Node.js" $NODE_STATUS
print_status "Rust" $RUST_STATUS
echo "  Go: (no build step - compile on use)"

# Exit with error if any failed
if [ $PYTHON_STATUS -ne 0 ] || [ $NODE_STATUS -ne 0 ] || [ $RUST_STATUS -ne 0 ]; then
    exit 1
fi

echo ""
success "All packages built successfully!"
