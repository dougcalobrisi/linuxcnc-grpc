#!/bin/bash
# Publish Python package to PyPI
# Usage: ./scripts/publish-python.sh [--test] [--dry-run]
#   --test     Publish to TestPyPI instead of PyPI
#   --dry-run  Validate without uploading

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

# Parse arguments
USE_TEST_PYPI=false
parse_common_args "$@"

for arg in "$@"; do
    case $arg in
        --test)
            USE_TEST_PYPI=true
            ;;
        --help|-h)
            echo "Usage: ./scripts/publish-python.sh [--test] [--dry-run]"
            echo "  --test     Publish to TestPyPI instead of PyPI"
            echo "  --dry-run  Validate without uploading"
            exit 0
            ;;
    esac
done

cd "$PROJECT_ROOT"

# Check for dist files
if [ ! -d "dist" ] || [ -z "$(ls -A dist/ 2>/dev/null)" ]; then
    error "No distribution files found in dist/"
    echo "  Run: make build-python"
    exit 1
fi

# Check for twine (via python -m)
if ! python3 -c "import twine" 2>/dev/null; then
    error "Python 'twine' module not found."
    echo "  Install with: pip install twine"
    exit 1
fi

# Validate packages
info "Validating packages..."
python3 -m twine check dist/*

if [ "$DRY_RUN" = true ]; then
    success "Dry run complete - packages validated successfully"
    echo ""
    echo "Would upload:"
    ls -la dist/
    exit 0
fi

# Upload
if [ "$USE_TEST_PYPI" = true ]; then
    info "Publishing to TestPyPI..."
    python3 -m twine upload --repository testpypi dist/*
    echo ""
    success "Published to TestPyPI!"
    echo "  View at: https://test.pypi.org/project/linuxcnc-grpc/"
else
    info "Publishing to PyPI..."
    python3 -m twine upload dist/*
    echo ""
    success "Published to PyPI!"
    echo "  View at: https://pypi.org/project/linuxcnc-grpc/"
fi
