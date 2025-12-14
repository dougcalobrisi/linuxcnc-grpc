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

# Check npm login status
if [ "$DRY_RUN" = false ]; then
    if ! npm whoami &>/dev/null; then
        error "Not logged in to npm"
        echo "  Run: npm login"
        exit 1
    fi
    info "Logged in as: $(npm whoami)"
fi

# Build publish command
PUBLISH_CMD="npm publish --access public"
if [ -n "$NPM_TAG" ]; then
    PUBLISH_CMD="$PUBLISH_CMD --tag $NPM_TAG"
fi

if [ "$DRY_RUN" = true ]; then
    info "Dry run - validating package..."
    npm publish --dry-run --access public
    echo ""
    success "Dry run complete - package validated successfully"
    exit 0
fi

# Publish
info "Publishing to npm..."
$PUBLISH_CMD

echo ""
success "Published to npm!"
echo "  View at: https://www.npmjs.com/package/linuxcnc-grpc"
