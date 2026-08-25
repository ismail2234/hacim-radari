from __future__ import annotations

import os


def _float(name: str, default: float) -> float:
    try:
        return float(
            os.getenv(name, default)
        )
    except (TypeError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(
            os.getenv(name, default)
        )
    except (TypeError, ValueError):
        return default


# WebSocket
WS_RECONNECT_DELAY = _float(
    "V29_WS_RECONNECT_DELAY",
    3.0,
)

# Volume Spike
VOLUME_WINDOW_SECONDS = _int(
    "V29_VOLUME_WINDOW_SECONDS",
    10,
)

VOLUME_BASELINE_SECONDS = _int(
    "V29_VOLUME_BASELINE_SECONDS",
    60,
)

VOLUME_SPIKE_THRESHOLD = _float(
    "V29_VOLUME_SPIKE_THRESHOLD",
    3.0,
)

VOLUME_STRONG_THRESHOLD = _float(
    "V29_VOLUME_STRONG_THRESHOLD",
    5.0,
)

VOLUME_EXTREME_THRESHOLD = _float(
    "V29_VOLUME_EXTREME_THRESHOLD",
    10.0,
)

# Price velocity
PRICE_VELOCITY_WINDOW = _int(
    "V29_PRICE_VELOCITY_WINDOW",
    5,
)

# Order book
ORDERBOOK_DEPTH_LEVELS = _int(
    "V29_ORDERBOOK_DEPTH_LEVELS",
    10,
)

# Signal thresholds
EARLY_SCORE_THRESHOLD = _int(
    "V29_EARLY_SCORE_THRESHOLD",
    65,
)

CONFIRMATION_SCORE_THRESHOLD = _int(
    "V29_CONFIRMATION_SCORE_THRESHOLD",
    70,
)

BUY_SCORE_THRESHOLD = _int(
    "V29_BUY_SCORE_THRESHOLD",
    78,
)

FAKEOUT_MAX_PENALTY = _int(
    "V29_FAKEOUT_MAX_PENALTY",
    35,
)

# Scanner
SCAN_INTERVAL_SECONDS = _float(
    "V29_SCAN_INTERVAL_SECONDS",
    5.0,
)

# Erkenlik hedefi
EARLY_LOOKAHEAD_CANDLES = _int(
    "V29_EARLY_LOOKAHEAD_CANDLES",
    3,
)
