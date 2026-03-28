# linuxcnc-grpc Makefile

.PHONY: all install install-dev proto proto-go proto-rust proto-node proto-all clean lint test test-cov test-go test-node test-all run run-debug dist help build build-python build-node build-rust build-all publish publish-python publish-node publish-rust publish-all publish-dry-run sync-version

PYTHON ?= python3
PIP ?= pip

all: proto

# Install package
install:
	$(PIP) install .

# Install in editable/development mode
install-dev:
	$(PIP) install -e ".[dev]"

# Generate protobuf code (Python only)
proto:
	./scripts/generate-protos.sh

# Generate protobuf code (Python + Go)
proto-go:
	./scripts/generate-protos.sh --go

# Generate protobuf code (Python + Rust)
proto-rust:
	./scripts/generate-protos.sh --rust

# Generate protobuf code (Python + Node.js/TypeScript)
proto-node:
	./scripts/generate-protos.sh --node

# Generate protobuf code (all languages)
proto-all:
	./scripts/generate-protos.sh --all

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
	$(PYTHON) -m py_compile src/linuxcnc_grpc/*.py
	$(PYTHON) -m py_compile examples/python/*.py
	$(PYTHON) -m py_compile tests/*.py
	@echo "Syntax check passed"

# Run Python tests
test:
	$(PYTHON) -m pytest tests/ -v

# Run Python tests with coverage
test-cov:
	$(PYTHON) -m pytest tests/ -v --cov=src/linuxcnc_grpc --cov-report=term-missing

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
	$(PYTHON) -m linuxcnc_grpc.server

# Run server with debug logging
run-debug:
	$(PYTHON) -m linuxcnc_grpc.server --debug

# Build distribution packages
dist: clean proto
	$(PYTHON) -m build

# Show help
help:
	@echo "linuxcnc-grpc Makefile"
	@echo ""
	@echo "Targets:"
	@echo "  all          Generate protobuf code (default)"
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
	./scripts/build-python.sh

build-node:
	./scripts/build-node.sh

build-rust:
	./scripts/build-rust.sh

build-all:
	./scripts/build-all.sh

# --- Publish targets ---

publish: publish-python

publish-python:
	./scripts/publish-python.sh

publish-node:
	./scripts/publish-node.sh

publish-rust:
	./scripts/publish-rust.sh

publish-all:
	./scripts/publish-all.sh

publish-dry-run:
	DRY_RUN=1 ./scripts/publish-all.sh

# --- Version management ---

sync-version:
	@test -n "$(VERSION)" || (echo "Usage: make sync-version VERSION=x.y.z" && exit 1)
	./scripts/sync-versions.sh $(VERSION)
