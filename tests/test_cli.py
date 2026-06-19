"""Tests for CLI entry point."""

import subprocess
import sys


def test_cli_help():
    """CLI --help should exit 0 and print usage."""
    result = subprocess.run(
        [sys.executable, "-m", "pptracer", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "Usage:" in result.stdout or "usage:" in result.stdout.lower()


def test_cli_version():
    """CLI --version should exit 0."""
    result = subprocess.run(
        [sys.executable, "-m", "pptracer", "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"


def test_cli_run_help():
    """CLI subcommand --help should work."""
    result = subprocess.run(
        [sys.executable, "-m", "pptracer", "run", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
