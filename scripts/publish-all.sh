#!/bin/bash
# Publish all packages to their respective registries
# Usage: ./scripts/publish-all.sh [--dry-run] [--yes] [--tag] [--allow-dirty]
#   --dry-run      Validate without uploading
#   --yes          Skip confirmation prompt
#   --tag          Create and push git tag for Go module (only if every
#                  package was published)
#   --allow-dirty  Publish even though the working tree has uncommitted
#                  changes (Python and npm; cargo still refuses)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

# Parse arguments
SKIP_CONFIRM=false
CREATE_TAG=false
ALLOW_DIRTY=false
parse_common_args "$@"

for arg in "$@"; do
    case $arg in
        --yes|-y)
            SKIP_CONFIRM=true
            ;;
        --tag)
            CREATE_TAG=true
            ;;
        --allow-dirty)
            ALLOW_DIRTY=true
            ;;
        --help|-h)
            echo "Usage: ./scripts/publish-all.sh [--dry-run] [--yes] [--tag] [--allow-dirty]"
            echo "  --dry-run      Validate without uploading"
            echo "  --yes          Skip confirmation prompt"
            echo "  --tag          Create and push git tag for Go module (only if every"
            echo "                 package was published)"
            echo "  --allow-dirty  Publish even though the working tree has uncommitted"
            echo "                 changes (Python and npm; cargo still refuses)"
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

TAG="v$VERSION"

# Print the commit a tag on the remote points at (nothing if the remote has no
# such tag). Fails if the remote cannot be asked, so that "could not check" is
# never read as "no such tag".
remote_tag_commit() {
    local tag_name="$1"
    local remote_refs
    remote_refs=$(git ls-remote --tags origin "refs/tags/$tag_name" "refs/tags/$tag_name^{}") || return 1
    # An annotated tag is listed twice; the "^{}" line names the commit.
    echo "$remote_refs" | awk '
        $2 ~ /\^\{\}$/ { peeled_commit = $1 }
        { tag_object = $1 }
        END { if (peeled_commit != "") print peeled_commit; else if (tag_object != "") print tag_object }
    '
}

# Checks before anything is built or published. A dry run skips them: it
# publishes nothing and is meant to work during development.
if [ "$DRY_RUN" = false ]; then
    # What gets published must be what is committed (and, with --tag, tagged).
    UNCOMMITTED_CHANGES=$(git status --porcelain)
    if [ -n "$UNCOMMITTED_CHANGES" ]; then
        if [ "$ALLOW_DIRTY" = true ]; then
            warn "Working tree has uncommitted changes (continuing because of --allow-dirty):"
            echo "$UNCOMMITTED_CHANGES"
            echo ""
        else
            error "Working tree has uncommitted changes; refusing to publish"
            echo "$UNCOMMITTED_CHANGES"
            echo "  Commit or remove them, or pass --allow-dirty"
            exit 1
        fi
    fi

    # An existing tag is only acceptable if it already names this commit.
    if [ "$CREATE_TAG" = true ]; then
        HEAD_COMMIT=$(git rev-parse HEAD)
        if LOCAL_TAG_COMMIT=$(git rev-parse --verify --quiet "refs/tags/$TAG^{commit}"); then
            if [ "$LOCAL_TAG_COMMIT" != "$HEAD_COMMIT" ]; then
                error "Tag $TAG already exists locally and points at $LOCAL_TAG_COMMIT, not HEAD ($HEAD_COMMIT)"
                echo "  Bump the version, or check out the tagged commit"
                exit 1
            fi
            info "Tag $TAG already exists locally and points at HEAD"
        fi
        if ! REMOTE_TAG_COMMIT=$(remote_tag_commit "$TAG"); then
            error "Could not ask the remote 'origin' whether tag $TAG exists"
            exit 1
        fi
        if [ -n "$REMOTE_TAG_COMMIT" ] && [ "$REMOTE_TAG_COMMIT" != "$HEAD_COMMIT" ]; then
            error "Tag $TAG already exists on origin and points at $REMOTE_TAG_COMMIT, not HEAD ($HEAD_COMMIT)"
            echo "  Bump the version, or check out the tagged commit"
            exit 1
        fi
    fi
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
        echo "  - Go: Create and push tag $TAG"
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

# Handle Go (via git tag). The tag is what makes the Go module version
# exist, and it marks the release: it is only made when every package above
# was published.
echo "=========================================="
echo "Go -> GitHub (git tag)"
echo "=========================================="
STATUS_SKIPPED=2
if [ "$CREATE_TAG" != true ]; then
    info "Skipped - use --tag to create git tag $TAG"
    GO_STATUS=0
elif [ $PYTHON_STATUS -ne 0 ] || [ $NODE_STATUS -ne 0 ] || [ $RUST_STATUS -ne 0 ]; then
    error "Not creating tag $TAG: not every package was published"
    echo "  Fix the failed publish and publish that package again, then tag this commit:"
    echo "    git tag $TAG && git push origin $TAG"
    GO_STATUS=$STATUS_SKIPPED
elif [ "$DRY_RUN" = true ]; then
    info "Would create and push tag: $TAG"
    GO_STATUS=0
else
    HEAD_COMMIT=$(git rev-parse HEAD)
    GO_STATUS=0
    if LOCAL_TAG_COMMIT=$(git rev-parse --verify --quiet "refs/tags/$TAG^{commit}"); then
        if [ "$LOCAL_TAG_COMMIT" != "$HEAD_COMMIT" ]; then
            error "Tag $TAG points at $LOCAL_TAG_COMMIT, not HEAD ($HEAD_COMMIT)"
            GO_STATUS=1
        else
            info "Tag $TAG already exists and points at HEAD"
        fi
    elif git tag "$TAG"; then
        info "Created tag: $TAG"
    else
        error "Failed to create tag $TAG"
        GO_STATUS=1
    fi
    if [ $GO_STATUS -eq 0 ]; then
        if [ "${REMOTE_TAG_COMMIT:-}" = "$HEAD_COMMIT" ]; then
            info "Tag $TAG is already on origin and points at HEAD"
        elif git push origin "refs/tags/$TAG"; then
            success "Pushed tag: $TAG"
        else
            error "Failed to push tag"
            GO_STATUS=1
        fi
    fi
fi
echo ""

# Summary
echo "=========================================="
echo "Publish Summary"
echo "=========================================="

print_status() {
    if [ "$2" -eq 0 ]; then
        echo -e "  $1: ${GREEN}OK${NC}"
    elif [ "$2" -eq "$STATUS_SKIPPED" ]; then
        echo -e "  $1: ${YELLOW}NOT DONE${NC}"
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
