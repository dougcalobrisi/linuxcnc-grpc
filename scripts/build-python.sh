#!/bin/bash
# Build Python package (wheel and sdist)
# Usage: ./scripts/build-python.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

info "Building Python package..."

cd "$PROJECT_ROOT"

# Clean previous build artifacts
info "Cleaning previous build artifacts..."
rm -rf dist/ build/ *.egg-info/ src/*.egg-info/

# Verify build module is available
if ! python3 -c "import build" 2>/dev/null; then
    error "Python 'build' module not found."
    echo "  Install with: pip install build"
    exit 1
fi

# Build
info "Running python -m build..."
python3 -m build

# List artifacts
echo ""
success "Build complete! Artifacts:"
ls -la dist/
