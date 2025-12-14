#!/bin/bash
# Publish all packages to their respective registries
# Usage: ./scripts/publish-all.sh [--dry-run] [--yes] [--tag]
#   --dry-run  Validate without uploading
#   --yes      Skip confirmation prompt
#   --tag      Create and push git tag for Go module

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

# Parse arguments
SKIP_CONFIRM=false
CREATE_TAG=false
parse_common_args "$@"

for arg in "$@"; do
    case $arg in
        --yes|-y)
            SKIP_CONFIRM=true
            ;;
        --tag)
            CREATE_TAG=true
            ;;
        --help|-h)
            echo "Usage: ./scripts/publish-all.sh [--dry-run] [--yes] [--tag]"
            echo "  --dry-run  Validate without uploading"
            echo "  --yes      Skip confirmation prompt"
            echo "  --tag      Create and push git tag for Go module"
            exit 0
            ;;
    esac
done

cd "$PROJECT_ROOT"

# Check version consistency
info "Checking version consistency..."
VERSION=$(check_version_consistency) || exit 1
success "All packages at version: $VERSION"
echo ""

if [ "$DRY_RUN" = true ]; then
    info "DRY RUN MODE - No packages will be published"
    echo ""
fi

# Build all packages first
echo "=========================================="
echo "Building packages..."
echo "=========================================="
"$SCRIPT_DIR/build-all.sh" || { error "Build failed"; exit 1; }
echo ""

# Confirmation
if [ "$SKIP_CONFIRM" = false ] && [ "$DRY_RUN" = false ]; then
    echo "This will publish the following packages:"
    echo "  - Python (linuxcnc-grpc) to PyPI"
    echo "  - Node.js (linuxcnc-grpc) to npm"
    echo "  - Rust (linuxcnc-grpc) to crates.io"
    if [ "$CREATE_TAG" = true ]; then
        echo "  - Go: Create and push tag v$VERSION"
    fi
    echo ""
    read -p "Continue? [y/N] " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 1
    fi
    echo ""
fi

PYTHON_STATUS=0
NODE_STATUS=0
RUST_STATUS=0
GO_STATUS=0

# Build dry-run flag
DR_FLAG=""
if [ "$DRY_RUN" = true ]; then
    DR_FLAG="--dry-run"
fi

# Publish Python
echo "=========================================="
echo "Python -> PyPI"
echo "=========================================="
if "$SCRIPT_DIR/publish-python.sh" $DR_FLAG; then
    PYTHON_STATUS=0
else
    PYTHON_STATUS=1
fi
echo ""

# Publish Node.js
echo "=========================================="
echo "Node.js -> npm"
echo "=========================================="
if "$SCRIPT_DIR/publish-node.sh" $DR_FLAG; then
    NODE_STATUS=0
else
    NODE_STATUS=1
fi
echo ""

# Publish Rust
echo "=========================================="
echo "Rust -> crates.io"
echo "=========================================="
if "$SCRIPT_DIR/publish-rust.sh" $DR_FLAG; then
    RUST_STATUS=0
else
    RUST_STATUS=1
fi
echo ""

# Handle Go (via git tag)
echo "=========================================="
echo "Go -> GitHub (git tag)"
echo "=========================================="
if [ "$CREATE_TAG" = true ]; then
    TAG="v$VERSION"
    if [ "$DRY_RUN" = true ]; then
        info "Would create and push tag: $TAG"
        GO_STATUS=0
    else
        if git tag "$TAG" 2>/dev/null; then
            info "Created tag: $TAG"
            if git push origin "$TAG"; then
                success "Pushed tag: $TAG"
                GO_STATUS=0
            else
                error "Failed to push tag"
                GO_STATUS=1
            fi
        else
            warn "Tag $TAG already exists"
            GO_STATUS=0
        fi
    fi
else
    info "Skipped - use --tag to create git tag v$VERSION"
    GO_STATUS=0
fi
echo ""

# Summary
echo "=========================================="
echo "Publish Summary"
echo "=========================================="

print_status() {
    if [ "$2" -eq 0 ]; then
        echo -e "  $1: ${GREEN}OK${NC}"
    else
        echo -e "  $1: ${RED}FAILED${NC}"
    fi
}

print_status "Python (PyPI)" $PYTHON_STATUS
print_status "Node.js (npm)" $NODE_STATUS
print_status "Rust (crates.io)" $RUST_STATUS
print_status "Go (git tag)" $GO_STATUS

# Exit with error if any failed
if [ $PYTHON_STATUS -ne 0 ] || [ $NODE_STATUS -ne 0 ] || [ $RUST_STATUS -ne 0 ] || [ $GO_STATUS -ne 0 ]; then
    exit 1
fi

echo ""
if [ "$DRY_RUN" = true ]; then
    success "Dry run complete - all packages validated!"
else
    success "All packages published successfully!"
fi
