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

TICKER_WHITELIST: FrozenSet[str] = frozenset(
    {"SPYM", "AVUV", "VGIT", "GLDM", "518880.SS", "SGOV", "511360.SS"}
)

TICKER_MARKET: Dict[str, str] = {
    "SPYM": "US",
    "AVUV": "US",
    "VGIT": "US",
    "GLDM": "US",
    "SGOV": "US",
    "518880.SS": "A",
    "511360.SS": "A",
}

TICKER_LOT_SIZE: Dict[str, int] = {
    "SPYM": 1,
    "AVUV": 1,
    "VGIT": 1,
    "GLDM": 1,
    "SGOV": 1,
    "518880.SS": 100,
    "511360.SS": 100,
}

TICKER_CURRENCY: Dict[str, str] = {
    "SPYM": "USD",
    "AVUV": "USD",
    "VGIT": "USD",
    "GLDM": "USD",
    "SGOV": "USD",
    "518880.SS": "CNY",
    "511360.SS": "CNY",
}

USD_TICKERS: FrozenSet[str] = frozenset(t for t, m in TICKER_MARKET.items() if m == "US")
CNY_TICKERS: FrozenSet[str] = frozenset(t for t, m in TICKER_MARKET.items() if m == "A")
A_SHARE_TICKERS: FrozenSet[str] = frozenset({"518880.SS", "511360.SS"})

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
YFINANCE_TICKERS: Tuple[str, ...] = (
    "SPYM", "AVUV", "VGIT", "GLDM", "SGOV",
    "518880.SS", "511360.SS",
    "CNY=X",
)
