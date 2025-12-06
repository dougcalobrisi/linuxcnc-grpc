# linuxcnc-grpc-server Makefile

.PHONY: all install install-dev proto proto-go proto-rust proto-node proto-all clean lint test test-cov run run-debug dist help

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
	./generate_protos.sh

# Generate protobuf code (Python + Go)
proto-go:
	./generate_protos.sh --go

# Generate protobuf code (Python + Rust)
proto-rust:
	./generate_protos.sh --rust

# Generate protobuf code (Python + Node.js/TypeScript)
proto-node:
	./generate_protos.sh --node

# Generate protobuf code (all languages)
proto-all:
	./generate_protos.sh --go --rust --node

# Clean generated and build artifacts
clean:
	rm -rf build/ dist/ *.egg-info/
	rm -rf src/linuxcnc_grpc_server/_generated/*_pb2*.py
	rm -rf src/linuxcnc_grpc_server/__pycache__/
	rm -rf src/linuxcnc_grpc_server/_generated/__pycache__/
	rm -rf gen/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete

# Check Python syntax
lint:
	$(PYTHON) -m py_compile src/linuxcnc_grpc_server/*.py
	$(PYTHON) -m py_compile examples/*.py
	$(PYTHON) -m py_compile tests/*.py
	@echo "Syntax check passed"

# Run tests
test:
	$(PYTHON) -m pytest tests/ -v

# Run tests with coverage
test-cov:
	$(PYTHON) -m pytest tests/ -v --cov=src/linuxcnc_grpc_server --cov-report=term-missing

# Run server (requires LinuxCNC environment)
run:
	$(PYTHON) -m linuxcnc_grpc_server.server

# Run server with debug logging
run-debug:
	$(PYTHON) -m linuxcnc_grpc_server.server --debug

# Build distribution packages
dist: clean proto
	$(PYTHON) -m build

# Show help
help:
	@echo "linuxcnc-grpc-server Makefile"
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
	@echo "  test         Run tests"
	@echo "  test-cov     Run tests with coverage report"
	@echo "  run          Run the server"
	@echo "  run-debug    Run the server with debug logging"
	@echo "  dist         Build distribution packages"
	@echo "  help         Show this help"
