#!/bin/bash
# Publish Node.js package to npm
# Usage: ./scripts/publish-node.sh [--dry-run] [--tag <tag>]
#   --dry-run      Validate without uploading
#   --tag <tag>    Publish with specific tag (e.g., beta)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

NODE_DIR="$PROJECT_ROOT/packages/node"

# Parse arguments
NPM_TAG=""
parse_common_args "$@"

while [[ $# -gt 0 ]]; do
    case $1 in
        --tag)
            if [ $# -lt 2 ] || [ -z "$2" ]; then
                error "--tag needs a value (e.g. --tag beta)"
                exit 1
            fi
            NPM_TAG="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: ./scripts/publish-node.sh [--dry-run] [--tag <tag>]"
            echo "  --dry-run      Validate without uploading"
            echo "  --tag <tag>    Publish with specific tag (e.g., beta)"
            exit 0
            ;;
        *)
            shift
            ;;
    esac
done

cd "$NODE_DIR"

# Check for dist directory
if [ ! -d "dist" ]; then
    error "No dist/ directory found"
    echo "  Run: make build-node"
    exit 1
fi

# Verify package.json version matches what we expect to publish
EXPECTED_NODE_VERSION=$(get_node_version)
PACKED_VERSION=$(node -p "require('./package.json').version")
if [ "$PACKED_VERSION" != "$EXPECTED_NODE_VERSION" ]; then
    error "Package version mismatch: package.json says $PACKED_VERSION but expected $EXPECTED_NODE_VERSION"
    echo "  Run: make build-node"
    exit 1
fi

# Check npm login status
if [ "$DRY_RUN" = false ]; then
    if ! npm whoami &>/dev/null; then
        error "Not logged in to npm"
        echo "  Run: npm login"
        exit 1
    fi
    info "Logged in as: $(npm whoami)"
fi

# Auto-detect npm tag from version if not explicitly set.
# Pre-release versions (e.g., 0.6.0-beta.1) default to their pre-release type
# so they don't become the "latest" tag on npm.
if [ -z "$NPM_TAG" ]; then
    NPM_TAG=$(npm_dist_tag_for_version "$EXPECTED_NODE_VERSION")
    if [ -n "$NPM_TAG" ]; then
        info "Auto-detected npm tag: $NPM_TAG (pre-release version)"
    elif [[ "$EXPECTED_NODE_VERSION" == *-* ]]; then
        # A pre-release of a kind this script does not know must not be
        # published as "latest" by default.
        error "Version $EXPECTED_NODE_VERSION is a pre-release, but no npm tag could be derived from it"
        echo "  Pass one explicitly: --tag <tag>"
        exit 1
    fi
fi

# Build the npm arguments. The array is never empty, so expanding it is safe
# under "set -u" in bash 3.2 (macOS) as well. The tag goes to the dry run too:
# npm refuses a pre-release version without --tag, also with --dry-run.
NPM_PUBLISH_ARGS=(publish --access public)
if [ -n "$NPM_TAG" ]; then
    NPM_PUBLISH_ARGS+=(--tag "$NPM_TAG")
fi

if [ "$DRY_RUN" = true ]; then
    info "Dry run - validating package..."
    npm "${NPM_PUBLISH_ARGS[@]}" --dry-run
    echo ""
    success "Dry run complete - package validated successfully"
    exit 0
fi

# Publish
info "Publishing to npm..."
npm "${NPM_PUBLISH_ARGS[@]}"

echo ""
success "Published to npm!"
echo "  View at: https://www.npmjs.com/package/linuxcnc-grpc"
