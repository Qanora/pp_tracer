"""Terminal display design system (§7.2).

Atomic component layer — generic, no business semantics.
All terminal output MUST go through these components.
"""

from typing import List, Optional, Tuple, Union

from rich import box
from rich.console import Console
from rich.panel import Panel as RichPanel
from rich.table import Table

console = Console()


def get_console(file=None) -> Console:
    """Return a Console instance — injectable for testing.

    Pass file= to redirect output (e.g., StringIO for tests).
    Uses module-level ``console`` when no override is given.
    """
    if file is not None:
        return Console(file=file)
    return console


# ═══════════════════════════════════════════════════════════════════════════════
# Design Tokens (§7.2 设计 token)
# ═══════════════════════════════════════════════════════════════════════════════

MAX_WIDTH = 100
GUTTER = 2
PAD_X = 1
PAD_Y = 0
NUM_WIDTH = 8
BAR_WIDTH = 14

# ── Color Palette (§7.2 色板) ────────────────────────────────────────────────


class Color:
    """Semantic color tokens."""

    # Data intensity
    fg_strong = "bold white"
    fg_default = "white"
    fg_muted = "grey50"
    fg_dim = "grey35"

    # Semantic
    accent = "bold cyan"
    profit = "bold green"
    loss = "bold red"
    warn = "yellow"
    info = "bold magenta"

    # Structure
    border_dim = "grey23"
    border_ok = "green"
    border_warn = "yellow"
    border_crit = "red"
    border_info = "cyan"


# ═══════════════════════════════════════════════════════════════════════════════
# Atomic Components (§7.2 必备的原子组件类型)
# ═══════════════════════════════════════════════════════════════════════════════


# ── Rule / Header ────────────────────────────────────────────────────────────


def rule(title: str, style: str = Color.accent) -> None:
    """Top-level rule with centered title."""
    console.rule(f"[{style}]{title}[/]", style=Color.fg_muted)


# ── Panel (card) ─────────────────────────────────────────────────────────────


def panel(
    title: str,
    content: Union[str, List[str]],
    accent: str = Color.accent,
    border: str = Color.border_ok,
) -> None:
    """Generic card container with colored left border and title."""
    if isinstance(content, list):
        body = "\n".join(content)
    else:
        body = content
    rp = RichPanel(
        body,
        title=f"[{accent}]{title}[/]",
        border_style=border,
        box=box.ROUNDED,
        padding=(PAD_Y, PAD_X),
    )
    console.print(rp)


# ── KPI ───────────────────────────────────────────────────────────────────────


def kpi(
    label: str,
    value: str,
    sub: Optional[str] = None,
    value_style: str = Color.fg_strong,
) -> None:
    """Single KPI: small dim label above big bold value, optional sub-text."""
    lines = [f"[{Color.fg_muted}]{label}[/]"]
    lines.append(f"[{value_style}]{value}[/]")
    if sub:
        lines.append(f"[{Color.fg_dim}]{sub}[/]")
    console.print("\n".join(lines))


def kpi_row(kpis: List[Tuple[str, str, str]]) -> None:
    """Horizontal KPI row. Each tuple: (label, value, value_style)."""
    cells = []
    for label, value, style in kpis:
        cells.append(f"[{Color.fg_muted}]{label}[/]\n[{style}]{value}[/]")
    # Use table for alignment
    t = Table(show_header=False, box=None, padding=(0, GUTTER))
    for _ in kpis:
        t.add_column(justify="center")
    t.add_row(*cells)
    console.print(t)


# ── KV pair / KV table ───────────────────────────────────────────────────────


def kv(label: str, value: str) -> str:
    """Return a dim-label bright-value line (for use inside panels)."""
    return f"[{Color.fg_muted}]{label}[/]: [{Color.fg_default}]{value}[/]"


def kv_table(pairs: List[Tuple[str, str]]) -> None:
    """Multi-row KV table."""
    t = Table(show_header=False, box=None, padding=(0, GUTTER))
    t.add_column(style=Color.fg_muted, justify="right")
    t.add_column(style=Color.fg_default)
    for label, value in pairs:
        t.add_row(label, value)
    console.print(t)


# ── Badges ───────────────────────────────────────────────────────────────────


def badge(text: str, style: str = Color.accent) -> str:
    """Single status badge: ` OK ` / ` WARN ` etc."""
    return f"[{style}]{text}[/]"


def status_badge(status: str) -> str:
    """Semantic badge from status string."""
    mapping = {
        "ok": (" ✓ OK ", Color.profit),
        "warn": (" ⚠ WARN ", Color.warn),
        "crit": (" ● CRIT ", Color.loss),
        "info": (" ℹ INFO ", Color.info),
    }
    text, style = mapping.get(status, (f" {status} ", Color.fg_muted))
    return badge(text, style)


def currency_badge(ticker: str) -> str:
    """Currency badge: USD / CNY, auto-detected."""
    if ticker.endswith(".SS"):
        return badge(" CNY ", "bold yellow")
    return badge(" USD ", "bold green")


# ── Progress bar ─────────────────────────────────────────────────────────────


def progress_bar(
    actual: float,
    target: float,
    L: float,
    U: float,
    width: int = BAR_WIDTH,
) -> str:
    """Weight deviation progress bar with target marker.

    █████│░░░░ 12.5% ↑
    """
    # Map [L, U] to [0, width]
    bar_range = U - L
    if bar_range <= 0:
        bar_range = 0.01
    pos = int((actual - L) / bar_range * width)
    pos = max(0, min(width, pos))
    target_pos = int((target - L) / bar_range * width)
    target_pos = max(0, min(width, target_pos))

    bar = ""
    for i in range(width):
        if i == target_pos:
            bar += "│"
        elif i < pos:
            bar += "█"
        else:
            bar += "░"

    dev = actual - target
    if abs(dev) < 0.005:
        tail = "  —  "
    elif dev > 0:
        tail = f" {dev:+.1%} ↑"
    else:
        tail = f" {dev:+.1%} ↓"

    return bar + tail


# ── Deviation helpers ────────────────────────────────────────────────────────


def dev_tone(dev: float, tol: float = 0.005, upper: float = 0.35) -> str:
    """Map deviation to OK/WARN/CRIT."""
    adev = abs(dev)
    if adev < tol:
        return "ok"
    if adev < upper - 0.25:
        return "warn"
    return "crit"


def dev_string(dev: float, tol: float = 0.005) -> str:
    """Formatted deviation string: ↑2.5% / ↓0.5% / —."""
    if abs(dev) < tol:
        return "  —  "
    arrow = "↑" if dev > 0 else "↓"
    return f"[{Color.profit if dev < 0 else Color.loss}]{arrow}{abs(dev):.1%}[/]"


def dev_change(old_dev: float, new_dev: float) -> str:
    """Deviation change display: 12.3% → 8.1% ↓."""
    arrow = "↓" if abs(new_dev) < abs(old_dev) else "↑"
    style = Color.profit if abs(new_dev) < abs(old_dev) else Color.loss
    return (
        f"[{Color.fg_muted}]{abs(old_dev):.1%} → {abs(new_dev):.1%}[/] "
        f"[{style}]{arrow}[/]"
    )


# ── Mini trend ───────────────────────────────────────────────────────────────


def mini_trend(values: List[float], width: int = 8) -> str:
    """Mini sparkline: ▁▂▃▄▅▆▇█."""
    blocks = "▁▂▃▄▅▆▇█"
    if not values or max(values) - min(values) < 1e-9:
        return blocks[0] * width
    span = max(values) - min(values)
    result = ""
    step = max(1, len(values) // width)
    for i in range(0, len(values), step):
        idx = int((values[i] - min(values)) / span * (len(blocks) - 1))
        idx = max(0, min(len(blocks) - 1, idx))
        result += blocks[idx]
        if len(result) >= width:
            break
    return result[:width]


# ── Ticker helpers ───────────────────────────────────────────────────────────


def ticker_display(ticker: str) -> str:
    """Remove .SS suffix for display."""
    return ticker.replace(".SS", "")


def ticker_unit(ticker: str) -> str:
    """Return unit: 份 for A-share, 股 for USD."""
    return "份" if ticker.endswith(".SS") else "股"


def price_str(ticker: str, price: float) -> str:
    """Formatted price: $72.50 or ¥5.00."""
    if ticker.endswith(".SS"):
        return f"¥{price:.2f}"
    return f"${price:.2f}"


# ── CJK width helpers ───────────────────────────────────────────────────────


def display_width(s: str) -> int:
    """Calculate display width (CJK chars = 2 cols)."""
    w = 0
    for ch in s:
        if "ᄀ" <= ch <= "ᅟ" or \
           "⺀" <= ch <= "｠" or \
           "￠" <= ch <= "￦" or \
           "　" <= ch <= "〿" or \
           "！" <= ch <= "｠" or \
           "￠" <= ch <= "￦":
            w += 2
        else:
            w += 1
    return w


def pad_left(s: str, width: int) -> str:
    """Left-pad to display width."""
    dw = display_width(s)
    if dw >= width:
        return s
    return " " * (width - dw) + s


def pad_right(s: str, width: int) -> str:
    """Right-pad to display width."""
    dw = display_width(s)
    if dw >= width:
        return s
    return s + " " * (width - dw)


def pad_center(s: str, width: int) -> str:
    """Center-pad to display width."""
    dw = display_width(s)
    if dw >= width:
        return s
    left = (width - dw) // 2
    right = width - dw - left
    return " " * left + s + " " * right


def cols(*specs: Tuple[str, int, str]) -> str:
    """Format aligned columns with CJK-aware visual widths.

    Each spec: (value, visual_width, align) where align is 'left'/'right'/'center'.
    Columns are separated by a single space.

    Example: cols(('代码', 8, 'left'), ('100股', 9, 'right'))
    """
    parts = []
    for val, width, align in specs:
        if align == 'right':
            parts.append(pad_left(val, width))
        elif align == 'center':
            parts.append(pad_center(val, width))
        else:
            parts.append(pad_right(val, width))
    return " ".join(parts)


# ── Structured messages ──────────────────────────────────────────────────────


def note(text: str) -> str:
    """Dimmed remark line."""
    return f"[{Color.fg_dim}]{text}[/]"


def success_banner(text: str) -> None:
    """Green success banner."""
    console.print(f"[{Color.profit}]✅ {text}[/]")


def warn_card(title: str, body: Optional[str] = None, icon: str = "⚠") -> None:
    """Warning/error card."""
    content = f"[{Color.warn}]{icon} {title}[/]"
    if body:
        content += f"\n[{Color.fg_muted}]{body}[/]"
    console.print(content)


def confirm_card(title: str, preview: str, prompt: str = "确认? (y/N)") -> None:
    """Confirmation prompt with preview."""
    panel(
        title,
        f"{preview}\n\n[{Color.fg_muted}]{prompt}[/]",
        accent=Color.warn,
        border=Color.border_warn,
    )


def cmd_hint(cmd: str) -> str:
    """Command hint line: ❯ ppt sell ..."""
    return f"[{Color.fg_dim}] ❯ [/][{Color.accent}]{cmd}[/]"


def empty_state(icon: str = "📭", message: str = "暂无数据") -> None:
    """Empty state card."""
    panel(icon, f"[{Color.fg_dim}]{message}[/]", border=Color.border_dim)
