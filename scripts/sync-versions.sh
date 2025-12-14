#!/bin/bash
# Synchronize version across all package manifests
# Usage: ./scripts/sync-versions.sh <version> [--commit] [--tag] [--dry-run]
#   <version>  New version (e.g., 0.6.0)
#   --commit   Create git commit with version bump
#   --tag      Also create git tag (for Go module)
#   --dry-run  Show what would change without modifying files

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

# Parse arguments
NEW_VERSION=""
CREATE_COMMIT=false
CREATE_TAG=false
parse_common_args "$@"

for arg in "$@"; do
    case $arg in
        --commit)
            CREATE_COMMIT=true
            ;;
        --tag)
            CREATE_TAG=true
            ;;
        --help|-h)
            echo "Usage: ./scripts/sync-versions.sh <version> [--commit] [--tag] [--dry-run]"
            echo "  <version>  New version (e.g., 0.6.0)"
            echo "  --commit   Create git commit with version bump"
            echo "  --tag      Also create git tag (for Go module)"
            echo "  --dry-run  Show what would change without modifying files"
            exit 0
            ;;
        --*)
            # Skip flags
            ;;
        *)
            if [ -z "$NEW_VERSION" ]; then
                NEW_VERSION="$arg"
            fi
            ;;
    esac
done

# Validate version argument
if [ -z "$NEW_VERSION" ]; then
    error "Version argument required"
    echo "Usage: ./scripts/sync-versions.sh <version>"
    exit 1
fi

# Validate semver format (basic check)
if ! echo "$NEW_VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?$'; then
    error "Invalid version format: $NEW_VERSION"
    echo "Expected format: X.Y.Z or X.Y.Z-suffix"
    exit 1
fi

cd "$PROJECT_ROOT"

# Get current versions
CURRENT_PY=$(get_python_version)
CURRENT_NODE=$(get_node_version)
CURRENT_RUST=$(get_rust_version)

info "Current versions:"
echo "  Python:  $CURRENT_PY"
echo "  Node.js: $CURRENT_NODE"
echo "  Rust:    $CURRENT_RUST"
echo ""
info "New version: $NEW_VERSION"
echo ""

if [ "$DRY_RUN" = true ]; then
    info "DRY RUN - No files will be modified"
    echo ""
    echo "Would update:"
    echo "  $PYPROJECT"
    echo "  $PACKAGE_JSON"
    echo "  $CARGO_TOML"
    if [ "$CREATE_COMMIT" = true ]; then
        echo ""
        echo "Would create commit: chore: bump version to $NEW_VERSION"
    fi
    if [ "$CREATE_TAG" = true ]; then
        echo "Would create tag: v$NEW_VERSION"
    fi
    exit 0
fi

# Update pyproject.toml
info "Updating $PYPROJECT..."
sed_inplace "s/^version = \"$CURRENT_PY\"/version = \"$NEW_VERSION\"/" "$PYPROJECT"

# Update package.json
info "Updating $PACKAGE_JSON..."
sed_inplace "s/\"version\": \"$CURRENT_NODE\"/\"version\": \"$NEW_VERSION\"/" "$PACKAGE_JSON"

# Update Cargo.toml
info "Updating $CARGO_TOML..."
sed_inplace "s/^version = \"$CURRENT_RUST\"/version = \"$NEW_VERSION\"/" "$CARGO_TOML"

# Verify updates
echo ""
info "Verifying updates..."
NEW_PY=$(get_python_version)
NEW_NODE=$(get_node_version)
NEW_RUST=$(get_rust_version)

ERRORS=0
if [ "$NEW_PY" != "$NEW_VERSION" ]; then
    error "Python version update failed (got $NEW_PY)"
    ERRORS=1
fi
if [ "$NEW_NODE" != "$NEW_VERSION" ]; then
    error "Node.js version update failed (got $NEW_NODE)"
    ERRORS=1
fi
if [ "$NEW_RUST" != "$NEW_VERSION" ]; then
    error "Rust version update failed (got $NEW_RUST)"
    ERRORS=1
fi

if [ $ERRORS -ne 0 ]; then
    exit 1
fi

success "All versions updated to $NEW_VERSION"

# Update Cargo.lock if it exists
if [ -f "$PROJECT_ROOT/packages/rust/Cargo.lock" ]; then
    info "Updating Cargo.lock..."
    (cd "$PROJECT_ROOT/packages/rust" && cargo update --package linuxcnc-grpc 2>/dev/null || true)
fi

# Create commit if requested
if [ "$CREATE_COMMIT" = true ]; then
    echo ""
    info "Creating git commit..."
    git add "$PYPROJECT" "$PACKAGE_JSON" "$CARGO_TOML"
    if [ -f "$PROJECT_ROOT/packages/rust/Cargo.lock" ]; then
        git add "$PROJECT_ROOT/packages/rust/Cargo.lock"
    fi
    git commit -m "chore: bump version to $NEW_VERSION"
    success "Created commit"
fi

# Create tag if requested
if [ "$CREATE_TAG" = true ]; then
    echo ""
    TAG="v$NEW_VERSION"
    info "Creating git tag: $TAG"
    git tag "$TAG"
    success "Created tag: $TAG"
    echo ""
    warn "Don't forget to push the tag: git push origin $TAG"
fi

echo ""
success "Version sync complete!"
