"""Asset configuration and fixed constants (§1, §6, §9)."""

import os
from typing import Dict, FrozenSet, Tuple

# ── §1 资产配置 ──────────────────────────────────────────────────────────────

BUCKETS: FrozenSet[str] = frozenset({"stock", "bond", "gold", "cash"})

BUCKET_TICKERS: Dict[str, Tuple[str, ...]] = {
    "stock": ("SPYM", "AVUV"),
    "bond": ("VGIT",),
    "gold": ("GLDM", "518880.SS"),
    "cash": ("SGOV", "511360.SS"),
}

PRIMARY_TICKER: Dict[str, str] = {
    "stock": "SPYM",
    "bond": "VGIT",
    "gold": "GLDM",
    "cash": "SGOV",
}

# Centralised per-ticker metadata — single source of truth.
# Adding a new ticker: add to _TICKER_META + add to BUCKET_TICKERS above.
_TICKER_META: dict = {
    "SPYM":      {"market": "US", "lot": 1,   "currency": "USD"},
    "AVUV":      {"market": "US", "lot": 1,   "currency": "USD"},
    "VGIT":      {"market": "US", "lot": 1,   "currency": "USD"},
    "GLDM":      {"market": "US", "lot": 1,   "currency": "USD"},
    "SGOV":      {"market": "US", "lot": 1,   "currency": "USD"},
    "518880.SS": {"market": "A",  "lot": 100, "currency": "CNY"},
    "511360.SS": {"market": "A",  "lot": 100, "currency": "CNY"},
}

TICKER_WHITELIST: FrozenSet[str] = frozenset(_TICKER_META.keys())

TICKER_MARKET: Dict[str, str] = {
    t: m["market"] for t, m in _TICKER_META.items()
}

TICKER_LOT_SIZE: Dict[str, int] = {
    t: m["lot"] for t, m in _TICKER_META.items()
}

TICKER_CURRENCY: Dict[str, str] = {
    t: m["currency"] for t, m in _TICKER_META.items()
}

USD_TICKERS: FrozenSet[str] = frozenset(t for t, m in _TICKER_META.items() if m["market"] == "US")
CNY_TICKERS: FrozenSet[str] = frozenset(t for t, m in _TICKER_META.items() if m["market"] == "A")
A_SHARE_TICKERS: FrozenSet[str] = frozenset(t for t, m in _TICKER_META.items() if m["market"] == "A")

# ── 固定常量 (§6 末尾固定常量表) ─────────────────────────────────────────────

# 波动率
VOL_WINDOW: int = 60               # 滚动窗口 (天)
PRICE_HISTORY_MAX: int = 120        # 历史保留 (天)
VOL_FALLBACK: Dict[str, float] = {  # 经验默认波动率
    "stock": 0.15,
    "bond": 0.10,
    "gold": 0.16,
    "cash": 0.02,
}
VOL_FLOOR: float = 0.005            # 波动率下限 (防除零)

# 走廊
CORRIDOR_HMIN: float = 0.03         # 最小半宽
CORRIDOR_HARD_CAP: float = 0.40     # 硬顶
CORRIDOR_HARD_FLOOR: float = 0.10   # 硬底

# 趋势
TREND_S: int = 10                   # 短期均线
TREND_L: int = 20                   # 长期均线

# 相关性
CORR_MIN_DAYS: int = 30             # 最小数据天数
CORR_WARN_THRESHOLD: float = 0.7    # 预警阈值
STOCK_BOND_REVERSAL_THRESHOLD: float = 0.3

# 桶内再均衡
INTRA_BUCKET_THRESHOLD: float = 0.60
INTRA_BUCKET_TARGET: float = 0.50

# 交易
MIN_TRADE_AMOUNT: float = 500.0     # 最小交易额 (¥)
OVERSPOOT_PROTECTION_FLOOR: float = 10000.0  # 过冲保护最低金额：<此额跳过保护
FX_SPREAD: float = 0.003            # 汇兑安全垫

# 数值
EPSILON: float = 1e-9               # 浮点比较容差

# OSS 路径 (可由环境变量 PP_OSS_BUCKET 覆盖)
_oss_bucket = os.environ.get("PP_OSS_BUCKET", "pp-tracer")
OSS_HOLDINGS_PATH: str = f"oss://{_oss_bucket}/pp_holdings.json"
OSS_BACKUP_PATH: str = f"oss://{_oss_bucket}/pp_holdings.backup.json"
OSS_PRICE_HISTORY_PATH: str = f"oss://{_oss_bucket}/pp_price_history.json"

# 本地目录
LOCAL_DIR: str = "~/.pp/"

# yfinance
YFINANCE_TICKERS: Tuple[str, ...] = tuple(list(_TICKER_META.keys()) + ["CNY=X"])
