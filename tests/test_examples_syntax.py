"""
Test that all example scripts have valid Python syntax.

These tests verify that the examples can be parsed and compiled,
without actually running them (which would require a live gRPC server).
"""

import py_compile
import sys
from pathlib import Path

import pytest


EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def get_example_files():
    """Get all Python files in the examples/python directory."""
    return sorted(EXAMPLES_DIR.glob("python/*.py"))


@pytest.mark.parametrize("example_file", get_example_files(), ids=lambda p: p.name)
def test_example_syntax(example_file):
    """Verify example file has valid Python syntax."""
    try:
        py_compile.compile(str(example_file), doraise=True)
    except py_compile.PyCompileError as e:
        pytest.fail(f"Syntax error in {example_file.name}: {e}")


def test_examples_exist():
    """Verify that example files exist."""
    examples = get_example_files()
    assert len(examples) > 0, "No example files found in examples/python/"


def test_examples_have_docstrings():
    """Verify all examples have module docstrings."""
    for example_file in get_example_files():
        content = example_file.read_text()
        # Check for docstring (triple-quoted string near start)
        has_docstring = '"""' in content[:500] or "'''" in content[:500]
        assert has_docstring, f"{example_file.name} is missing a docstring"


def test_examples_have_main_guard():
    """Verify examples use if __name__ == '__main__' guard."""
    for example_file in get_example_files():
        content = example_file.read_text()
        has_main_guard = '__name__' in content and '__main__' in content
        assert has_main_guard, f"{example_file.name} is missing __name__ == '__main__' guard"


def test_examples_use_argparse():
    """Verify examples use argparse for CLI arguments."""
    for example_file in get_example_files():
        content = example_file.read_text()
        uses_argparse = "import argparse" in content or "from argparse" in content
        assert uses_argparse, f"{example_file.name} should use argparse for CLI arguments"
