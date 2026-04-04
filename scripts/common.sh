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

# Convert a semver pre-release version to PEP 440 format for Python.
# Examples:
#   0.6.0-alpha.1 -> 0.6.0a1
#   0.6.0-beta.2  -> 0.6.0b2
#   0.6.0-rc.1    -> 0.6.0rc1
#   0.6.0         -> 0.6.0 (unchanged)
to_pep440() {
    local ver="$1"
    echo "$ver" | sed -E \
        -e 's/-alpha\.?/a/' \
        -e 's/-beta\.?/b/' \
        -e 's/-rc\.?/rc/'
}

# Convert a PEP 440 pre-release version to semver format for npm/Rust.
# Examples:
#   0.6.0a1  -> 0.6.0-alpha.1
#   0.6.0b2  -> 0.6.0-beta.2
#   0.6.0rc1 -> 0.6.0-rc.1
#   0.6.0    -> 0.6.0 (unchanged)
to_semver_prerelease() {
    local ver="$1"
    echo "$ver" | sed -E \
        -e 's/([0-9])a([0-9])/\1-alpha.\2/' \
        -e 's/([0-9])b([0-9])/\1-beta.\2/' \
        -e 's/([0-9])rc([0-9])/\1-rc.\2/'
}

# Extract major.minor from a version string for use in documentation examples.
# Examples:
#   0.6.0           -> 0.6
#   0.6.0-beta.5    -> 0.6
#   0.6.0b5         -> 0.6
#   1.2.3-rc.1      -> 1.2
to_doc_version() {
    local ver="$1"
    echo "$ver" | sed -E 's/^([0-9]+\.[0-9]+).*/\1/'
}

# Normalize a version to a canonical form for comparison.
# Converts both PEP 440 and semver pre-release formats to semver.
normalize_version() {
    local ver="$1"
    # First try PEP 440 -> semver, then pass through
    to_semver_prerelease "$ver"
}

# Check if all package versions match (accounting for format differences).
# Python uses PEP 440 (0.6.0b1), npm/Rust use semver (0.6.0-beta.1).
check_version_consistency() {
    local py_ver node_ver rust_ver
    py_ver=$(get_python_version)
    node_ver=$(get_node_version)
    rust_ver=$(get_rust_version)

    local py_norm node_norm rust_norm
    py_norm=$(normalize_version "$py_ver")
    node_norm=$(normalize_version "$node_ver")
    rust_norm=$(normalize_version "$rust_ver")

    if [ "$py_norm" = "$node_norm" ] && [ "$node_norm" = "$rust_norm" ]; then
        echo "$node_ver"
        return 0
    else
        error "Version mismatch detected:"
        echo "  Python:  $py_ver (normalized: $py_norm)"
        echo "  Node.js: $node_ver (normalized: $node_norm)"
        echo "  Rust:    $rust_ver (normalized: $rust_norm)"
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
