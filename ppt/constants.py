"""Fixed portfolio metadata shared by the pure calculation layers."""

BUCKET_ORDER: tuple[str, ...] = ("stock", "bond", "gold", "cash")
BUCKET_TICKERS: dict[str, tuple[str, ...]] = {
    "stock": ("SPYM", "AVUV"),
    "bond": ("VGIT",),
    "gold": ("GLDM", "518880.SS"),
    "cash": ("SGOV", "511360.SS"),
}

TICKER_ORDER: tuple[str, ...] = tuple(
    ticker for bucket in BUCKET_ORDER for ticker in BUCKET_TICKERS[bucket]
)
TICKER_WHITELIST: frozenset[str] = frozenset(TICKER_ORDER)

_TICKER_META: dict[str, dict[str, str | int]] = {
    "SPYM": {"currency": "USD", "lot": 1},
    "AVUV": {"currency": "USD", "lot": 1},
    "VGIT": {"currency": "USD", "lot": 1},
    "GLDM": {"currency": "USD", "lot": 1},
    "SGOV": {"currency": "USD", "lot": 1},
    "518880.SS": {"currency": "CNY", "lot": 100},
    "511360.SS": {"currency": "CNY", "lot": 100},
}

TICKER_CURRENCY: dict[str, str] = {
    ticker: str(meta["currency"]) for ticker, meta in _TICKER_META.items()
}
TICKER_LOT_SIZE: dict[str, int] = {
    ticker: int(meta["lot"]) for ticker, meta in _TICKER_META.items()
}
USD_TICKERS: frozenset[str] = frozenset(
    ticker for ticker, currency in TICKER_CURRENCY.items() if currency == "USD"
)
CNY_TICKERS: frozenset[str] = TICKER_WHITELIST - USD_TICKERS

TARGET_BUCKET_WEIGHT = 0.25
CORRIDOR_LOWER = 0.15
CORRIDOR_UPPER = 0.35
INTRA_BUCKET_SELL_THRESHOLD = 0.60
TARGET_CURRENCY_WEIGHT = 0.50

TREND_SHORT_WINDOW = 10
TREND_LONG_WINDOW = 20
CORRELATION_MIN_POINTS = 30
CORRELATION_WARNING = 0.70
