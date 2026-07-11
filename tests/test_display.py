"""Tests for display system (§7.2)."""



from ppt.display import (
    Color,
    cmd_hint,
    currency_badge,
    dev_change,
    dev_string,
    dev_tone,
    display_width,
    mini_trend,
    note,
    pad_left,
    pad_right,
    price_str,
    progress_bar,
    status_badge,
    ticker_display,
    ticker_unit,
)

# ── Color tokens exist ───────────────────────────────────────────────────────


class TestColorTokens:
    def test_all_present(self):
        for attr in [
            "fg_strong", "fg_default", "fg_muted", "fg_dim",
            "accent", "profit", "loss", "warn", "info",
            "border_dim", "border_ok", "border_warn", "border_crit", "border_info",
        ]:
            assert hasattr(Color, attr)


# ── Badges ───────────────────────────────────────────────────────────────────


class TestBadges:
    def test_status_ok(self):
        s = status_badge("ok")
        assert "OK" in s
        assert "green" in s

    def test_status_warn(self):
        s = status_badge("warn")
        assert "WARN" in s
        assert "yellow" in s

    def test_status_crit(self):
        s = status_badge("crit")
        assert "CRIT" in s
        assert "red" in s

    def test_currency_usd(self):
        s = currency_badge("SPYM")
        assert "USD" in s

    def test_currency_cny(self):
        s = currency_badge("518880.SS")
        assert "CNY" in s


# ── Progress bar ─────────────────────────────────────────────────────────────


class TestProgressBar:
    def test_at_target(self):
        bar = progress_bar(0.25, 0.25, L=0.10, U=0.40)
        assert "│" in bar
        assert "+0.0%" in bar

    def test_over_target(self):
        bar = progress_bar(0.35, 0.25, L=0.10, U=0.40)
        assert "█" in bar
        assert "↑" in bar

    def test_under_target(self):
        bar = progress_bar(0.15, 0.25, L=0.10, U=0.40)
        assert "↓" in bar


# ── Deviation helpers ────────────────────────────────────────────────────────


class TestDeviation:
    def test_tone_ok(self):
        assert dev_tone(0.002, tol=0.005) == "ok"

    def test_tone_warn(self):
        assert dev_tone(0.05, tol=0.005, upper=0.35) == "warn"

    def test_tone_crit(self):
        assert dev_tone(0.15, tol=0.005, upper=0.35) == "crit"

    def test_dev_string_within_tol(self):
        s = dev_string(0.002, tol=0.005)
        assert "—" in s

    def test_dev_string_positive(self):
        s = dev_string(0.05, tol=0.005)
        assert "↑" in s

    def test_change_improved(self):
        s = dev_change(0.12, 0.08)
        assert "↓" in s
        assert "green" in s

    def test_change_worsened(self):
        s = dev_change(0.08, 0.12)
        assert "↑" in s
        assert "red" in s


# ── Ticker helpers ───────────────────────────────────────────────────────────


class TestTickerHelpers:
    def test_display_removes_ss(self):
        assert ticker_display("518880.SS") == "518880"

    def test_display_usd_unchanged(self):
        assert ticker_display("SPYM") == "SPYM"

    def test_unit_usd(self):
        assert ticker_unit("SPYM") == "股"

    def test_unit_cny(self):
        assert ticker_unit("518880.SS") == "份"

    def test_price_usd(self):
        s = price_str("SPYM", 72.50)
        assert "$72.50" in s

    def test_price_cny(self):
        s = price_str("518880.SS", 5.50)
        assert "¥5.50" in s


# ── CJK width ────────────────────────────────────────────────────────────────


class TestDisplayWidth:
    def test_ascii(self):
        assert display_width("hello") == 5

    def test_cjk(self):
        assert display_width("中文") == 4

    def test_mixed(self):
        assert display_width("SPYM 股票") > 4

    def test_pad_left(self):
        result = pad_left("hi", 5)
        assert len(result) >= 3  # at least 3 leading spaces
        assert display_width(result) >= 5

    def test_pad_right(self):
        result = pad_right("hi", 5)
        assert result.startswith("hi")


# ── Mini trend ───────────────────────────────────────────────────────────────


class TestMiniTrend:
    def test_basic(self):
        values = [100 + i for i in range(20)]
        t = mini_trend(values)
        assert len(t) <= 8
        assert all(c in "▁▂▃▄▅▆▇█" for c in t)

    def test_flat(self):
        t = mini_trend([50, 50, 50, 50])
        assert len(t) <= 8


# ── Other helpers ────────────────────────────────────────────────────────────


class TestNote:
    def test_dim_text(self):
        n = note("这是一条备注")
        assert "grey" in n.lower() or "dim" in n.lower()


class TestCmdHint:
    def test_format(self):
        h = cmd_hint("ppt buy SPYM#10@72.50")
        assert "ppt buy" in h
