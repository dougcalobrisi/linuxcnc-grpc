#!/bin/bash
# Generate protobuf and gRPC code from .proto files
# Usage: ./scripts/generate-protos.sh [--go] [--rust] [--node] [--all]
#   --go    Generate Go code (requires protoc-gen-go, protoc-gen-go-grpc)
#   --rust  Generate Rust code (requires protoc-gen-prost, protoc-gen-tonic)
#   --node  Generate Node.js/TypeScript code (requires ts-proto)
#   --all   Generate all languages

set -e

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

echo ""
echo "Done!"
