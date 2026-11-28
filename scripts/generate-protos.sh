#!/bin/bash
# Generate protobuf and gRPC code from .proto files
# Usage: ./scripts/generate-protos.sh [--go] [--rust] [--node] [--all]
#   --go    Generate Go code (requires protoc-gen-go, protoc-gen-go-grpc)
#   --rust  Generate Rust code (requires protoc-gen-prost, protoc-gen-tonic)
#   --node  Generate Node.js/TypeScript code (requires ts-proto)
#   --all   Generate all languages

set -euo pipefail

# Get project root (parent of scripts directory)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PROTO_DIR="$PROJECT_ROOT/proto"
PYTHON_OUT_DIR="$PROJECT_ROOT/packages/python/linuxcnc_pb"
GO_OUT_DIR="$PROJECT_ROOT/packages/go"
RUST_OUT_DIR="$PROJECT_ROOT/packages/rust/src"
NODE_OUT_DIR="$PROJECT_ROOT/packages/node/src"

# Parse arguments
GENERATE_GO=false
GENERATE_RUST=false
GENERATE_NODE=false

for arg in "$@"; do
    case $arg in
        --go)
            GENERATE_GO=true
            ;;
        --rust)
            GENERATE_RUST=true
            ;;
        --node)
            GENERATE_NODE=true
            ;;
        --all)
            GENERATE_GO=true
            GENERATE_RUST=true
            GENERATE_NODE=true
            ;;
        --help|-h)
            echo "Usage: ./scripts/generate-protos.sh [--go] [--rust] [--node] [--all]"
            echo "  --go    Generate Go code (requires protoc-gen-go, protoc-gen-go-grpc)"
            echo "  --rust  Generate Rust code (requires protoc-gen-prost, protoc-gen-tonic)"
            echo "  --node  Generate Node.js/TypeScript code (requires ts-proto)"
            echo "  --all   Generate all languages"
            echo ""
            echo "Python code is always generated."
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg"
            echo "Use --help for usage information."
            exit 1
            ;;
    esac
done

echo "Generating protobuf code..."
echo "Proto source: $PROTO_DIR"

# Detect platform for sed compatibility
PLATFORM="$(uname -s)"

# Cross-platform sed in-place
sed_inplace() {
    if [ "$PLATFORM" = "Darwin" ]; then
        sed -i '' "$@"
    else
        sed -i "$@"
    fi
}

# --- Python (always generated) ---
echo ""
echo "=== Python ==="
mkdir -p "$PYTHON_OUT_DIR"

python3 -m grpc_tools.protoc \
    -I"$PROTO_DIR" \
    --python_out="$PYTHON_OUT_DIR" \
    --grpc_python_out="$PYTHON_OUT_DIR" \
    "$PROTO_DIR/linuxcnc.proto" \
    "$PROTO_DIR/hal.proto"

# Fix imports in generated grpc files to use relative imports
sed_inplace 's/^import linuxcnc_pb2/from . import linuxcnc_pb2/' "$PYTHON_OUT_DIR/linuxcnc_pb2_grpc.py"
sed_inplace 's/^import hal_pb2/from . import hal_pb2/' "$PYTHON_OUT_DIR/hal_pb2_grpc.py"

# Create/update __init__.py for the generated package
cat > "$PYTHON_OUT_DIR/__init__.py" << 'EOF'
"""Generated protobuf and gRPC code for LinuxCNC and HAL services."""

from .linuxcnc_pb2 import *
from .linuxcnc_pb2_grpc import *
from .hal_pb2 import *
from .hal_pb2_grpc import *
EOF

echo "Generated Python code in $PYTHON_OUT_DIR"

# --- Go (optional) ---
if [ "$GENERATE_GO" = true ]; then
    echo ""
    echo "=== Go ==="

    # Check for required plugins
    if ! command -v protoc-gen-go &> /dev/null; then
        echo "Error: protoc-gen-go not found. Install with:"
        echo "  go install google.golang.org/protobuf/cmd/protoc-gen-go@latest"
        exit 1
    fi
    if ! command -v protoc-gen-go-grpc &> /dev/null; then
        echo "Error: protoc-gen-go-grpc not found. Install with:"
        echo "  go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest"
        exit 1
    fi

    # Generate Go code in packages/go (subpackage of main module)
    mkdir -p "$GO_OUT_DIR"
    protoc \
        -I"$PROTO_DIR" \
        --go_out="$GO_OUT_DIR" \
        --go_opt=paths=source_relative \
        --go-grpc_out="$GO_OUT_DIR" \
        --go-grpc_opt=paths=source_relative \
        "$PROTO_DIR/linuxcnc.proto" \
        "$PROTO_DIR/hal.proto"

    echo "Generated Go code in $GO_OUT_DIR"
fi

# --- Rust (optional) ---
if [ "$GENERATE_RUST" = true ]; then
    echo ""
    echo "=== Rust ==="

    # Check for required plugins
    if ! command -v protoc-gen-prost &> /dev/null; then
        echo "Error: protoc-gen-prost not found. Install with:"
        echo "  cargo install protoc-gen-prost"
        exit 1
    fi
    if ! command -v protoc-gen-tonic &> /dev/null; then
        echo "Error: protoc-gen-tonic not found. Install with:"
        echo "  cargo install protoc-gen-tonic"
        exit 1
    fi

    mkdir -p "$RUST_OUT_DIR"

    protoc \
        -I"$PROTO_DIR" \
        --prost_out="$RUST_OUT_DIR" \
        --tonic_out="$RUST_OUT_DIR" \
        "$PROTO_DIR/linuxcnc.proto" \
        "$PROTO_DIR/hal.proto"

    echo "Generated Rust code in $RUST_OUT_DIR"
fi

# --- Node.js/TypeScript (optional) ---
if [ "$GENERATE_NODE" = true ]; then
    echo ""
    echo "=== Node.js/TypeScript ==="

    # Check for ts-proto plugin
    TS_PROTO_PLUGIN=""
    if [ -x "$PROJECT_ROOT/node_modules/.bin/protoc-gen-ts_proto" ]; then
        TS_PROTO_PLUGIN="$PROJECT_ROOT/node_modules/.bin/protoc-gen-ts_proto"
    elif [ -x "$PROJECT_ROOT/packages/node/node_modules/.bin/protoc-gen-ts_proto" ]; then
        TS_PROTO_PLUGIN="$PROJECT_ROOT/packages/node/node_modules/.bin/protoc-gen-ts_proto"
    elif command -v protoc-gen-ts_proto &> /dev/null; then
        TS_PROTO_PLUGIN="$(command -v protoc-gen-ts_proto)"
    else
        echo "Error: protoc-gen-ts_proto not found. Install with:"
        echo "  cd packages/node && npm install"
        echo "  # or globally: npm install -g ts-proto"
        exit 1
    fi

    mkdir -p "$NODE_OUT_DIR"

    protoc \
        -I"$PROTO_DIR" \
        --plugin="$TS_PROTO_PLUGIN" \
        --ts_proto_out="$NODE_OUT_DIR" \
        --ts_proto_opt=outputServices=grpc-js \
        --ts_proto_opt=esModuleInterop=true \
        --ts_proto_opt=exportCommonSymbols=false \
        "$PROTO_DIR/linuxcnc.proto" \
        "$PROTO_DIR/hal.proto"

    echo "Generated Node.js/TypeScript code in $NODE_OUT_DIR"
fi

# --- Post-process Node.js: how 64-bit integers become JavaScript numbers ---
#
# The TypeScript type of every int64/uint64 field is `number`, and that must
# not change in 1.x (bigint or string would break every TypeScript user).
# ts-proto decodes all of them with one helper, longToNumber, which throws
# for anything outside the safe integer range (beyond +-2^53). Two kinds of
# field go through it and they need different handling:
#
#   - Timestamps are sent in nanoseconds, which are always beyond 2^53. They
#     are scaled to milliseconds, which fit a number and suit `new Date()`.
#     1.0 clients rely on that, so it stays exactly as it was: a value beyond
#     the safe range is divided by 1e6, a smaller one is passed through.
#   - Everything else (HAL s64/u64 values, sizes, offsets, sequences) is a
#     value and must not be scaled. It is decoded to the nearest number, so
#     beyond 2^53 it loses its low digits, as any JavaScript number does.
#     1.0 scaled these too, which made a large HAL value wrong by a factor
#     of a million.
#
# So the step below gives timestamp fields their own helper and makes
# longToNumber a plain conversion. A timestamp field is an int64 field whose
# name is `timestamp` or ends in `_timestamp` (`timestamp`, `...Timestamp` in
# the generated code). Name a new time field that way, or extend the pattern.
#
# The step does nothing to a file that is already patched (it also runs when
# --node was not given) and fails on a file it does not recognise, so a new
# ts-proto that generates different code cannot slip through unpatched.
echo ""
echo "=== Post-processing Node.js generated code ==="
for TS_FILE in "$NODE_OUT_DIR/linuxcnc.ts" "$NODE_OUT_DIR/hal.ts"; do
    if [ -f "$TS_FILE" ]; then
        python3 - "$TS_FILE" << 'PATCH_NODE_INT64'
import re
import sys
from pathlib import Path

generated_file = Path(sys.argv[1])
source = generated_file.read_text(encoding="utf-8")

HELPER_AS_GENERATED = """\
function longToNumber(int64: { toString(): string }): number {
  const num = globalThis.Number(int64.toString());
  if (num > globalThis.Number.MAX_SAFE_INTEGER) {
    throw new globalThis.Error("Value is larger than Number.MAX_SAFE_INTEGER");
  }
  if (num < globalThis.Number.MIN_SAFE_INTEGER) {
    throw new globalThis.Error("Value is smaller than Number.MIN_SAFE_INTEGER");
  }
  return num;
}
"""

HELPERS_AS_PATCHED = """\
/**
 * A 64-bit integer that is a value, not a timestamp. Beyond 2^53 the result
 * is the nearest number, so the low digits are lost; it is never scaled.
 * (Patched in by scripts/generate-protos.sh.)
 */
function longToNumber(int64: { toString(): string }): number {
  return globalThis.Number(int64.toString());
}

/**
 * A timestamp. The server sends nanoseconds, which do not fit a number, so a
 * value beyond the safe integer range is scaled to milliseconds.
 * (Patched in by scripts/generate-protos.sh.)
 */
function timestampToNumber(int64: { toString(): string }): number {
  const num = globalThis.Number(int64.toString());
  if (num > globalThis.Number.MAX_SAFE_INTEGER) {
    return Math.floor(num / 1e6);
  }
  if (num < globalThis.Number.MIN_SAFE_INTEGER) {
    return Math.ceil(num / 1e6);
  }
  return num;
}
"""

# message.timestamp = longToNumber(...), message.modifiedTimestamp = ...
TIMESTAMP_FIELD_DECODE = re.compile(
    r"(message\.(?:timestamp|[A-Za-z0-9]+Timestamp) = )longToNumber\("
)

if HELPERS_AS_PATCHED in source:
    patched = source
elif source.count(HELPER_AS_GENERATED) == 1:
    patched = source.replace(HELPER_AS_GENERATED, HELPERS_AS_PATCHED)
    patched, timestamp_fields_patched = TIMESTAMP_FIELD_DECODE.subn(
        r"\1timestampToNumber(", patched
    )
    if timestamp_fields_patched == 0:
        sys.exit(f"{generated_file.name}: found no timestamp field to patch")
else:
    sys.exit(
        f"{generated_file.name}: longToNumber is not as ts-proto is known to "
        "generate it, nor already patched. Update the post-processing in "
        "scripts/generate-protos.sh."
    )

if TIMESTAMP_FIELD_DECODE.search(patched):
    sys.exit(f"{generated_file.name}: a timestamp field is still decoded unscaled")

if patched != source:
    generated_file.write_text(patched, encoding="utf-8")
PATCH_NODE_INT64
        echo "Patched 64-bit integer decoding in $(basename "$TS_FILE")"
    fi
done

# --- Sync proto files to Rust package (build.rs reads from there) ---
echo ""
echo "=== Syncing proto files to Rust package ==="
RUST_PROTO_DIR="$PROJECT_ROOT/packages/rust/proto"
mkdir -p "$RUST_PROTO_DIR"
cp "$PROTO_DIR/linuxcnc.proto" "$RUST_PROTO_DIR/"
cp "$PROTO_DIR/hal.proto" "$RUST_PROTO_DIR/"
echo "Synced proto files to $RUST_PROTO_DIR"

echo ""
echo "Done!"
