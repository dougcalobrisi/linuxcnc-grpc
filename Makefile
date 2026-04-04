# linuxcnc-grpc Makefile

.PHONY: all setup install install-dev proto proto-go proto-rust proto-node proto-all clean lint test test-cov test-go test-node test-all run run-debug dist help build build-python build-node build-rust build-all publish publish-python publish-node publish-rust publish-all publish-dry-run sync-version readme

all: proto

# Install all development and build dependencies (creates .venv automatically)
setup:
	uv sync --extra dev --extra build

# Install for production on a LinuxCNC machine (includes system site-packages
# so the linuxcnc Python module is accessible inside the venv)
install:
	uv venv --system-site-packages --allow-existing
	uv sync --no-dev

# Install in editable/development mode
install-dev:
	uv sync --extra dev

# Generate protobuf code (Python only — needs dev extras for grpcio-tools)
proto:
	uv run ./scripts/generate-protos.sh

# Generate protobuf code (Python + Go)
proto-go:
	uv run ./scripts/generate-protos.sh --go

# Generate protobuf code (Python + Rust)
proto-rust:
	uv run ./scripts/generate-protos.sh --rust

# Generate protobuf code (Python + Node.js/TypeScript)
proto-node:
	uv run ./scripts/generate-protos.sh --node

# Generate protobuf code (all languages)
proto-all:
	uv run ./scripts/generate-protos.sh --all

# Clean generated and build artifacts
clean:
	rm -rf build/ dist/ *.egg-info/
	rm -rf src/linuxcnc_grpc/_generated/*_pb2*.py
	rm -rf src/linuxcnc_grpc/__pycache__/
	rm -rf src/linuxcnc_grpc/_generated/__pycache__/
	rm -f *.pb.go
	rm -rf packages/node/dist/
	rm -rf packages/node/node_modules/
	rm -rf packages/rust/target/
	rm -rf packages/rust/src/linuxcnc/
	rm -rf packages/rust/src/hal/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete

# Check Python syntax
lint:
	uv run python -m py_compile src/linuxcnc_grpc/*.py
	uv run python -m py_compile examples/python/*.py
	uv run python -m py_compile tests/*.py
	@echo "Syntax check passed"

# Run Python tests
test:
	uv run python -m pytest tests/ -v

# Run Python tests with coverage
test-cov:
	uv run python -m pytest tests/ -v --cov=src/linuxcnc_grpc --cov-report=term-missing

# Run Go tests (requires mock server)
test-go:
	go test -v -count=1

# Run Node.js tests (requires mock server)
test-node:
	cd packages/node && npm test

# Run all language tests
test-all: test test-go test-node
	@echo ""
	@echo "All tests passed!"

# Run server (requires LinuxCNC environment)
run:
	uv run python -m linuxcnc_grpc.server

# Run server with debug logging
run-debug:
	uv run python -m linuxcnc_grpc.server --debug

# Build distribution packages
dist: clean proto
	uv run python -m build

# Show help
help:
	@echo "linuxcnc-grpc Makefile"
	@echo ""
	@echo "Targets:"
	@echo "  all          Generate protobuf code (default)"
	@echo "  setup        Install all dev + build dependencies (creates .venv)"
	@echo "  install      Install package"
	@echo "  install-dev  Install in editable mode with dev dependencies"
	@echo "  proto        Generate protobuf/gRPC code (Python only)"
	@echo "  proto-go     Generate protobuf/gRPC code (Python + Go)"
	@echo "  proto-rust   Generate protobuf/gRPC code (Python + Rust)"
	@echo "  proto-node   Generate protobuf/gRPC code (Python + Node.js/TypeScript)"
	@echo "  proto-all    Generate protobuf/gRPC code (all languages)"
	@echo "  clean        Remove generated and build artifacts"
	@echo "  lint         Check Python syntax"
	@echo "  test         Run Python tests"
	@echo "  test-cov     Run Python tests with coverage report"
	@echo "  test-go      Run Go client tests"
	@echo "  test-node    Run Node.js client tests"
	@echo "  test-all     Run all language tests (Python + Go + Node.js)"
	@echo "  run          Run the server"
	@echo "  run-debug    Run the server with debug logging"
	@echo "  dist         Build distribution packages"
	@echo ""
	@echo "Build & Publish:"
	@echo "  build-python   Build Python package (wheel + sdist)"
	@echo "  build-node     Build Node.js package"
	@echo "  build-rust     Build Rust crate"
	@echo "  build-all      Build all packages"
	@echo "  publish-python Publish to PyPI"
	@echo "  publish-node   Publish to npm"
	@echo "  publish-rust   Publish to crates.io"
	@echo "  publish-all    Publish all packages"
	@echo "  publish-dry-run Test publish without uploading"
	@echo "  sync-version   Sync version across packages (VERSION=x.y.z)"
	@echo ""
	@echo "  help         Show this help"

# --- Build targets ---

# Build Python package
build: build-python

build-python:
	uv run ./scripts/build-python.sh

build-node:
	./scripts/build-node.sh

build-rust:
	./scripts/build-rust.sh

build-all:
	uv run ./scripts/build-all.sh

# --- Publish targets ---

publish: publish-python

publish-python:
	uv run ./scripts/publish-python.sh

publish-node:
	./scripts/publish-node.sh

publish-rust:
	./scripts/publish-rust.sh

publish-all:
	uv run ./scripts/publish-all.sh

publish-dry-run:
	DRY_RUN=1 uv run ./scripts/publish-all.sh

# --- README generation ---

# Generate package-specific READMEs for npm and crates.io
readme:
	./scripts/generate-package-readmes.sh

# --- Version management ---

sync-version:
	@test -n "$(VERSION)" || (echo "Usage: make sync-version VERSION=x.y.z" && exit 1)
	./scripts/sync-versions.sh $(VERSION)
