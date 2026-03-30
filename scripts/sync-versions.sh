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

# Validate version format: semver (0.6.0-beta.1) or PEP 440 pre-release (0.6.0b1)
if ! echo "$NEW_VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+(-(alpha|beta|rc)\.[0-9]+)?$' && \
   ! echo "$NEW_VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+(a|b|rc)[0-9]+$'; then
    error "Invalid version format: $NEW_VERSION"
    echo "Expected format: X.Y.Z, X.Y.Z-beta.N (semver), or X.Y.ZbN (PEP 440)"
    echo "Pre-release types: alpha, beta, rc"
    exit 1
fi

cd "$PROJECT_ROOT"

# Compute per-ecosystem versions.
# User can pass either format; we derive the correct one for each ecosystem.
PY_VERSION=$(to_pep440 "$NEW_VERSION")
SEMVER_VERSION=$(to_semver_prerelease "$NEW_VERSION")

# Get current versions
CURRENT_PY=$(get_python_version)
CURRENT_NODE=$(get_node_version)
CURRENT_RUST=$(get_rust_version)

info "Current versions:"
echo "  Python:  $CURRENT_PY"
echo "  Node.js: $CURRENT_NODE"
echo "  Rust:    $CURRENT_RUST"
echo ""
info "New versions:"
echo "  Python (PEP 440): $PY_VERSION"
echo "  Node.js (semver):  $SEMVER_VERSION"
echo "  Rust (semver):     $SEMVER_VERSION"
echo ""

if [ "$DRY_RUN" = true ]; then
    info "DRY RUN - No files will be modified"
    echo ""
    echo "Would update:"
    echo "  $PYPROJECT  -> $PY_VERSION"
    echo "  $PACKAGE_JSON -> $SEMVER_VERSION"
    echo "  $CARGO_TOML -> $SEMVER_VERSION"
    if [ "$CREATE_COMMIT" = true ]; then
        echo ""
        echo "Would create commit: chore: bump version to $SEMVER_VERSION"
    fi
    if [ "$CREATE_TAG" = true ]; then
        echo "Would create tag: v$SEMVER_VERSION"
    fi
    exit 0
fi

# Update pyproject.toml (PEP 440 format)
info "Updating $PYPROJECT..."
sed_inplace "s/^version = \"${CURRENT_PY}\"/version = \"${PY_VERSION}\"/" "$PYPROJECT"

# Update package.json (semver format)
info "Updating $PACKAGE_JSON..."
sed_inplace "s/\"version\": \"${CURRENT_NODE}\"/\"version\": \"${SEMVER_VERSION}\"/" "$PACKAGE_JSON"

# Update Cargo.toml (semver format)
info "Updating $CARGO_TOML..."
sed_inplace "s/^version = \"${CURRENT_RUST}\"/version = \"${SEMVER_VERSION}\"/" "$CARGO_TOML"

# Verify updates
echo ""
info "Verifying updates..."
NEW_PY=$(get_python_version)
NEW_NODE=$(get_node_version)
NEW_RUST=$(get_rust_version)

ERRORS=0
if [ "$NEW_PY" != "$PY_VERSION" ]; then
    error "Python version update failed (expected $PY_VERSION, got $NEW_PY)"
    ERRORS=1
fi
if [ "$NEW_NODE" != "$SEMVER_VERSION" ]; then
    error "Node.js version update failed (expected $SEMVER_VERSION, got $NEW_NODE)"
    ERRORS=1
fi
if [ "$NEW_RUST" != "$SEMVER_VERSION" ]; then
    error "Rust version update failed (expected $SEMVER_VERSION, got $NEW_RUST)"
    ERRORS=1
fi

if [ $ERRORS -ne 0 ]; then
    exit 1
fi

success "All versions updated (Python: $PY_VERSION, npm/Rust: $SEMVER_VERSION)"

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
    git commit -m "chore: bump version to $SEMVER_VERSION"
    success "Created commit"
fi

# Create tag if requested
if [ "$CREATE_TAG" = true ]; then
    echo ""
    TAG="v$SEMVER_VERSION"
    info "Creating git tag: $TAG"
    git tag "$TAG"
    success "Created tag: $TAG"
    echo ""
    warn "Don't forget to push the tag: git push origin $TAG"
fi

echo ""
success "Version sync complete!"
