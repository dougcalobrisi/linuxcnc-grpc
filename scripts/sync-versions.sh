#!/bin/bash
# Synchronize version across all package manifests
# Usage: ./scripts/sync-versions.sh <version> [--commit] [--tag] [--dry-run]
#   <version>  New version (e.g., 0.6.0)
#   --commit   Create git commit with version bump
#   --tag      Also create git tag (for Go module); needs --commit
#   --dry-run  Show what would change without modifying files
#
# Lock files: only the project's own version entry changes. uv.lock is edited
# in place (no re-resolve, so no dependency moves) and then checked with
# "uv lock --check". If any lock file cannot be updated the script reports
# which, exits non-zero and creates no commit or tag.

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
            echo "  --tag      Also create git tag (for Go module); needs --commit"
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

# A tag made without the commit would point at the commit before the bump.
if [ "$CREATE_TAG" = true ] && [ "$CREATE_COMMIT" = false ]; then
    error "--tag needs --commit: without it the tag would point at the commit before the version bump"
    echo "  Use --commit --tag, or commit the bump yourself and tag that commit"
    exit 1
fi

cd "$PROJECT_ROOT"

# Example projects: they carry the same version as the packages they show.
EXAMPLES_CARGO_TOML="$PROJECT_ROOT/examples/rust/Cargo.toml"
EXAMPLES_PACKAGE_JSON="$PROJECT_ROOT/examples/node/package.json"
EXAMPLES_PACKAGE_LOCK="$PROJECT_ROOT/examples/node/package-lock.json"

# Set the version recorded for one package in a TOML lock file (uv.lock,
# Cargo.lock): the "version = ..." line directly after 'name = "<package>"'.
# Nothing else in the file changes. Fails if there is no such entry.
# Usage: set_locked_package_version <lock file> <package name> <version>
set_locked_package_version() {
    local lock_file="$1"
    local package_name="$2"
    local package_version="$3"
    local updated_lock_file="${lock_file}.version-sync"
    if awk -v name_line="name = \"${package_name}\"" -v version_line="version = \"${package_version}\"" '
            after_name && /^version = "/ { print version_line; after_name = 0; entries_changed++; next }
            { after_name = ($0 == name_line); print }
            END { exit entries_changed == 1 ? 0 : 1 }
        ' "$lock_file" > "$updated_lock_file"; then
        mv "$updated_lock_file" "$lock_file"
    else
        rm -f "$updated_lock_file"
        return 1
    fi
}

# Set the first top-level "version" in a manifest, whatever it is now.
# Usage: set_cargo_manifest_version <Cargo.toml> <version>
#        set_package_json_version <package.json> <version>
set_cargo_manifest_version() {
    local manifest="$1"
    local manifest_version="$2"
    local updated_manifest="${manifest}.version-sync"
    if awk -v version_line="version = \"${manifest_version}\"" '
            !entries_changed && /^version[[:space:]]*=/ { print version_line; entries_changed = 1; next }
            { print }
            END { exit entries_changed ? 0 : 1 }
        ' "$manifest" > "$updated_manifest"; then
        mv "$updated_manifest" "$manifest"
    else
        rm -f "$updated_manifest"
        return 1
    fi
}

set_package_json_version() {
    local manifest="$1"
    local manifest_version="$2"
    local updated_manifest="${manifest}.version-sync"
    if awk -v new_version="$manifest_version" '
            !entries_changed && /^  "version":[[:space:]]*"[^"]*"/ {
                sub(/"version":[[:space:]]*"[^"]*"/, "\"version\": \"" new_version "\"")
                entries_changed = 1
            }
            { print }
            END { exit entries_changed ? 0 : 1 }
        ' "$manifest" > "$updated_manifest"; then
        mv "$updated_manifest" "$manifest"
    else
        rm -f "$updated_manifest"
        return 1
    fi
}

# Compute per-ecosystem versions.
# User can pass either format; we derive the correct one for each ecosystem.
PY_VERSION=$(to_pep440 "$NEW_VERSION")
SEMVER_VERSION=$(to_semver_prerelease "$NEW_VERSION")
DOC_VERSION=$(to_doc_version "$SEMVER_VERSION")

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
echo "  Docs (major.minor): $DOC_VERSION"
echo ""

if [ "$DRY_RUN" = true ]; then
    info "DRY RUN - No files will be modified"
    echo ""
    echo "Would update:"
    echo "  $PYPROJECT  -> $PY_VERSION"
    echo "  $PACKAGE_JSON -> $SEMVER_VERSION"
    echo "  $CARGO_TOML -> $SEMVER_VERSION"
    echo "  src/linuxcnc_grpc/__init__.py -> $PY_VERSION"
    echo "  examples/rust/Cargo.toml -> $SEMVER_VERSION"
    echo "  examples/node/package.json -> $SEMVER_VERSION"
    echo "  uv.lock (project version entry only, then checked)"
    echo "  packages/node/package-lock.json (re-locked)"
    echo "  packages/rust/Cargo.lock (re-locked)"
    echo "  examples/rust/Cargo.lock (re-locked)"
    echo "  examples/node/package-lock.json (re-locked)"
    echo "  *.md files containing linuxcnc-grpc version -> \"$DOC_VERSION\""
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

# Update __init__.py __version__ (PEP 440 format)
INIT_PY="$PROJECT_ROOT/src/linuxcnc_grpc/__init__.py"
if [ -f "$INIT_PY" ]; then
    info "Updating $INIT_PY..."
    sed_inplace "s/__version__ = \".*\"/__version__ = \"${PY_VERSION}\"/" "$INIT_PY"
fi

# Update package.json (semver format)
info "Updating $PACKAGE_JSON..."
sed_inplace "s/\"version\": \"${CURRENT_NODE}\"/\"version\": \"${SEMVER_VERSION}\"/" "$PACKAGE_JSON"

# Update Cargo.toml (semver format)
info "Updating $CARGO_TOML..."
sed_inplace "s/^version = \"${CURRENT_RUST}\"/version = \"${SEMVER_VERSION}\"/" "$CARGO_TOML"

# Update the example projects' own versions (semver format). These are not
# published, so a failure here is reported with the lock files below rather
# than stopping before the packages are verified.
UPDATE_FAILURES=()
if [ -f "$EXAMPLES_CARGO_TOML" ]; then
    info "Updating $EXAMPLES_CARGO_TOML..."
    set_cargo_manifest_version "$EXAMPLES_CARGO_TOML" "$SEMVER_VERSION" \
        || UPDATE_FAILURES+=("examples/rust/Cargo.toml: no version line found")
fi
if [ -f "$EXAMPLES_PACKAGE_JSON" ]; then
    info "Updating $EXAMPLES_PACKAGE_JSON..."
    set_package_json_version "$EXAMPLES_PACKAGE_JSON" "$SEMVER_VERSION" \
        || UPDATE_FAILURES+=("examples/node/package.json: no version line found")
fi

# Update version strings in documentation (Rust Cargo.toml examples use major.minor).
# Restrict updates to tracked Markdown files so local build/output artifacts are not modified.
DOC_UPDATED_FILES=()
info "Updating doc version strings to $DOC_VERSION..."
while IFS= read -r -d '' doc_file; do
    doc_path="$PROJECT_ROOT/$doc_file"
    if grep -q 'linuxcnc-grpc = "[0-9]' "$doc_path"; then
        sed_inplace "s/linuxcnc-grpc = \"[0-9]*\.[0-9]*\"/linuxcnc-grpc = \"${DOC_VERSION}\"/" "$doc_path"
        DOC_UPDATED_FILES+=("$doc_path")
    fi
done < <(git -C "$PROJECT_ROOT" ls-files -z -- '*.md')
if [ ${#DOC_UPDATED_FILES[@]} -gt 0 ]; then
    info "Updated ${#DOC_UPDATED_FILES[@]} doc file(s)"
fi

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

# Update lock files. Each failure is recorded and reported below; none is
# hidden, and a failure stops the script before any commit or tag.

# uv.lock: change only the project's own version entry. Re-locking (and
# "uv lock --upgrade" in particular) lets a newer uv re-resolve every
# dependency, which changes the generated code CI checks against.
if [ -f "$PROJECT_ROOT/uv.lock" ]; then
    info "Updating uv.lock (project version only)..."
    if ! set_locked_package_version "$PROJECT_ROOT/uv.lock" "linuxcnc-grpc" "$PY_VERSION"; then
        UPDATE_FAILURES+=("uv.lock: no single 'linuxcnc-grpc' package entry to update")
    elif ! command -v uv &> /dev/null; then
        UPDATE_FAILURES+=("uv.lock: version entry updated, but uv is not installed to check the file")
    elif ! (cd "$PROJECT_ROOT" && uv lock --check); then
        UPDATE_FAILURES+=("uv.lock: 'uv lock --check' failed after the version entry was updated (run 'uv lock' and review its diff)")
    fi
fi

# Run a lock-file command in a directory, showing its output only on failure.
# Usage: run_lock_update <label for the report> <directory> <command...>
run_lock_update() {
    local lock_label="$1"
    local lock_directory="$2"
    shift 2
    local command_output
    info "Updating $lock_label..."
    if ! command -v "$1" &> /dev/null; then
        UPDATE_FAILURES+=("$lock_label: $1 is not installed")
        return 0
    fi
    if ! command_output=$(cd "$lock_directory" && "$@" 2>&1); then
        echo "$command_output" >&2
        UPDATE_FAILURES+=("$lock_label: '$*' failed")
    fi
    return 0
}

if [ -f "$PROJECT_ROOT/packages/rust/Cargo.lock" ]; then
    run_lock_update "packages/rust/Cargo.lock" "$PROJECT_ROOT/packages/rust" \
        cargo update --package linuxcnc-grpc
fi

if [ -f "$PROJECT_ROOT/examples/rust/Cargo.lock" ]; then
    run_lock_update "examples/rust/Cargo.lock" "$PROJECT_ROOT/examples/rust" \
        cargo update --package linuxcnc-grpc --package linuxcnc-grpc-examples
fi

if [ -f "$PROJECT_ROOT/packages/node/package-lock.json" ]; then
    run_lock_update "packages/node/package-lock.json" "$PROJECT_ROOT/packages/node" \
        npm install --package-lock-only
fi

if [ -f "$EXAMPLES_PACKAGE_LOCK" ]; then
    run_lock_update "examples/node/package-lock.json" "$PROJECT_ROOT/examples/node" \
        npm install --package-lock-only
fi

if [ ${#UPDATE_FAILURES[@]} -gt 0 ]; then
    echo ""
    error "The version files were updated, but these updates failed:"
    for update_failure in "${UPDATE_FAILURES[@]}"; do
        echo "  - $update_failure" >&2
    done
    echo "" >&2
    echo "The working tree now holds a partial version bump. Fix the cause and run the" >&2
    echo "script again (it is safe to repeat), or update the listed files by hand." >&2
    if [ "$CREATE_COMMIT" = true ]; then
        echo "No commit or tag was created." >&2
    fi
    exit 1
fi

# Create commit if requested
if [ "$CREATE_COMMIT" = true ]; then
    echo ""
    info "Creating git commit..."
    # Build explicit file list so we only commit version files,
    # regardless of other staged changes in the working tree.
    COMMIT_FILES=("$PYPROJECT" "$PACKAGE_JSON" "$CARGO_TOML" "$INIT_PY")
    if [ ${#DOC_UPDATED_FILES[@]} -gt 0 ]; then
        COMMIT_FILES+=("${DOC_UPDATED_FILES[@]}")
    fi
    for example_file in "$EXAMPLES_CARGO_TOML" "$EXAMPLES_PACKAGE_JSON" "$EXAMPLES_PACKAGE_LOCK"; do
        if [ -f "$example_file" ]; then
            COMMIT_FILES+=("$example_file")
        fi
    done
    if [ -f "$PROJECT_ROOT/uv.lock" ]; then
        COMMIT_FILES+=("$PROJECT_ROOT/uv.lock")
    fi
    if [ -f "$PROJECT_ROOT/packages/rust/Cargo.lock" ]; then
        COMMIT_FILES+=("$PROJECT_ROOT/packages/rust/Cargo.lock")
    fi
    if [ -f "$PROJECT_ROOT/examples/rust/Cargo.lock" ]; then
        COMMIT_FILES+=("$PROJECT_ROOT/examples/rust/Cargo.lock")
    fi
    if [ -f "$PROJECT_ROOT/packages/node/package-lock.json" ]; then
        COMMIT_FILES+=("$PROJECT_ROOT/packages/node/package-lock.json")
    fi
    if git commit --only "${COMMIT_FILES[@]}" -m "chore: bump version to $SEMVER_VERSION"; then
        success "Created commit"
    else
        error "git commit failed — files are modified but not committed"
        exit 1
    fi
fi

# Create tag if requested
if [ "$CREATE_TAG" = true ]; then
    echo ""
    TAG="v$SEMVER_VERSION"
    info "Creating git tag: $TAG"
    if git tag "$TAG"; then
        success "Created tag: $TAG"
    else
        error "git tag failed — tag $TAG may already exist"
        exit 1
    fi
    echo ""
    warn "Don't forget to push the tag: git push origin $TAG"
fi

echo ""
success "Version sync complete!"
