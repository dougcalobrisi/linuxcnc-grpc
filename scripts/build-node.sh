#!/bin/bash
# Build Node.js/TypeScript package
# Usage: ./scripts/build-node.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

NODE_DIR="$PROJECT_ROOT/packages/node"

info "Building Node.js package..."

cd "$NODE_DIR"

# Install dependencies if needed
if [ ! -d "node_modules" ]; then
    info "Installing dependencies..."
    npm ci
fi

# Clean previous build
info "Cleaning previous build..."
rm -rf dist/

# Build
info "Running npm run build..."
npm run build

# Verify build succeeded
if [ ! -d "dist" ]; then
    error "Build failed - dist/ directory not created"
    exit 1
fi

echo ""
success "Build complete! Artifacts:"
ls -la dist/
