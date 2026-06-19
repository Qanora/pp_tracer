"""Integration tests for CLI commands (§5)."""

import subprocess
import sys


def cli(*args):
    """Run ppt CLI and return CompletedProcess."""
    return subprocess.run(
        [sys.executable, "-m", "ppt"] + list(args),
        capture_output=True, text=True, timeout=10,
    )


class TestCLIHelp:
    def test_help(self):
        r = cli("--help")
        assert r.returncode == 0
        assert "ppt" in r.stdout

    def test_plan_help(self):
        r = cli("plan", "--help")
        assert r.returncode == 0

    def test_status_help(self):
        r = cli("status", "--help")
        assert r.returncode == 0

    def test_buy_help(self):
        r = cli("buy", "--help")
        assert r.returncode == 0

    def test_sell_help(self):
        r = cli("sell", "--help")
        assert r.returncode == 0

    def test_undo_help(self):
        r = cli("undo", "--help")
        assert r.returncode == 0

    def test_history_help(self):
        r = cli("history", "--help")
        assert r.returncode == 0

    def test_config_help(self):
        r = cli("config", "--help")
        assert r.returncode == 0

    def test_init_help(self):
        r = cli("init", "--help")
        assert r.returncode == 0

    def test_custom_help(self):
        r = cli("help", "--help")
        assert r.returncode == 0


class TestCLIErrorHandling:
    def test_invalid_command(self):
        r = cli("nonexistent")
        assert r.returncode != 0

    def test_buy_no_args(self):
        r = cli("buy")
        assert r.returncode != 0

    def test_bad_format(self):
        # Requires initialized state first; format error caught at parse time
        r = cli("buy", "NOT#VALID")
        # Should fail because format is wrong (state isn't checked if args parse fails)
        assert "Error" in r.stderr or r.returncode != 0


class TestCLIStatusWithoutInit:
    """Status with no data → graceful empty state."""

    def test_status_no_data(self):
        # Global flags go before subcommand
        r = cli("--offline", "status")
        assert r.returncode in (0, 1)

    def test_plan_no_data(self):
        r = cli("--offline", "plan", "10000")
        assert r.returncode in (0, 1)
