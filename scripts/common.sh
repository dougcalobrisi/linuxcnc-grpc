#!/bin/bash
# Common functions and variables for build/publish scripts
# Source this file: source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

set -euo pipefail

# Project root (parent of scripts directory)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Package manifest locations
PYPROJECT="$PROJECT_ROOT/pyproject.toml"
PACKAGE_JSON="$PROJECT_ROOT/packages/node/package.json"
CARGO_TOML="$PROJECT_ROOT/packages/rust/Cargo.toml"

# Detect platform
detect_platform() {
    case "$(uname -s)" in
        Darwin*) PLATFORM="macos" ;;
        Linux*)  PLATFORM="linux" ;;
        *)       PLATFORM="unknown" ;;
    esac
}
detect_platform

# Color output (if terminal supports it)
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    BLUE='\033[0;34m'
    NC='\033[0m' # No Color
else
    RED=''
    GREEN=''
    YELLOW=''
    BLUE=''
    NC=''
fi

# Logging functions
info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $*"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $*"
}

error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

# Cross-platform sed in-place
# Usage: sed_inplace 's/foo/bar/' file.txt
sed_inplace() {
    if [ "$PLATFORM" = "macos" ]; then
        sed -i '' "$@"
    else
        sed -i "$@"
    fi
}

# Check if a command exists
require_command() {
    local cmd="$1"
    local install_hint="${2:-}"
    if ! command -v "$cmd" &> /dev/null; then
        error "$cmd not found."
        if [ -n "$install_hint" ]; then
            echo "  Install with: $install_hint"
        fi
        exit 1
    fi
}

# Get version from pyproject.toml
get_python_version() {
    grep -E '^version[[:space:]]*=' "$PYPROJECT" | head -1 | sed 's/.*=[[:space:]]*"\([^"]*\)".*/\1/'
}

# Get version from package.json
get_node_version() {
    grep -E '"version"' "$PACKAGE_JSON" | head -1 | sed 's/.*:[[:space:]]*"\([^"]*\)".*/\1/'
}

# Get version from Cargo.toml
get_rust_version() {
    grep -E '^version[[:space:]]*=' "$CARGO_TOML" | head -1 | sed 's/.*=[[:space:]]*"\([^"]*\)".*/\1/'
}

# Check if all package versions match
check_version_consistency() {
    local py_ver node_ver rust_ver
    py_ver=$(get_python_version)
    node_ver=$(get_node_version)
    rust_ver=$(get_rust_version)

    if [ "$py_ver" = "$node_ver" ] && [ "$node_ver" = "$rust_ver" ]; then
        echo "$py_ver"
        return 0
    else
        error "Version mismatch detected:"
        echo "  Python: $py_ver"
        echo "  Node.js: $node_ver"
        echo "  Rust: $rust_ver"
        return 1
    fi
}

# Parse common arguments
# Normalize DRY_RUN to "true" or "false" (accepts 1, true, yes)
_dry_run_val="${DRY_RUN:-false}"
if [ "$_dry_run_val" = "1" ] || [ "$_dry_run_val" = "true" ] || [ "$_dry_run_val" = "yes" ]; then
    DRY_RUN=true
else
    DRY_RUN=false
fi

parse_common_args() {
    for arg in "$@"; do
        case $arg in
            --dry-run)
                DRY_RUN=true
                ;;
        esac
    done
}
