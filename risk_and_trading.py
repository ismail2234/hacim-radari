"""
V26 - risk_and_trading.py  (5/8)
Stop-loss + pozisyon buyuklugu hesaplari VE gercek para kullanmadan
sanal islem takibi (paper trading) bu dosyada birlesik.
"""

import json
import os
from datetime import datetime

from config import CONFIG

LOG_FILE = "paper_trades.json"


# ============================================================
# RISK YONETIMI
# ============================================================

def calculate_stop_loss(df, entry_price: float) -> float:
    """
    Stop, tek bir kurala (orn. sadece MA7 altina) kor sekilde baglanmaz;
    ATR bazli, son dip ve MA30 destegi birlikte degerlendirilir.
    """
    cfg = CONFIG["risk"]
    last = df.iloc[-1]

    atr_stop = entry_price - cfg["atr_stop_mult"] * last["atr"]
    recent_low = df["low"].iloc[-cfg["recent_low_window"]:].min()
    ma_stop = last["ma30"] * cfg["ma_stop_buffer"]

    stop = max(min(atr_stop, recent_low), ma_stop)
    return round(float(stop), 6)


def position_size(capital: float, entry: float, stop: float, risk_pct: float = None) -> float:
    """Risk yuzdesine gore ne kadarlik pozisyon acilmali (taban birim)."""
    risk_pct = risk_pct or CONFIG["risk"]["risk_pct_per_trade"]
    risk_amount = capital * risk_pct
    risk_per_unit = abs(entry - stop)
    if risk_per_unit == 0:
        return 0.0
    return round(risk_amount / risk_per_unit, 4)


def staged_entries(total_size: float, num_stages: int = 3) -> list:
    """Kademeli giris icin toplam pozisyonu esit parcalara boler."""
    if num_stages <= 0:
        return [total_size]
    portion = round(total_size / num_stages, 4)
    return [portion] * num_stages


# ============================================================
# PAPER TRADING (sanal islem takibi)
# ============================================================

def _load_log() -> list:
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_log(log: list):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def record_signal(symbol: str, score: float, entry_price: float, stop_loss: float):
    """Yeni bir sanal islem kaydeder (henuz kapanmamis)."""
    log = _load_log()
    log.append({
        "symbol": symbol,
        "score": score,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "opened_at": datetime.utcnow().isoformat(),
        "status": "open",
        "closed_at": None,
        "close_price": None,
        "result_%": None,
    })
    _save_log(log)


def close_signal(symbol: str, close_price: float, opened_at: str = None):
    """Acik olan sanal islemi kapatir ve sonucu hesaplar."""
    log = _load_log()
    for trade in log:
        if trade["symbol"] == symbol and trade["status"] == "open":
            if opened_at and trade["opened_at"] != opened_at:
                continue
            trade["status"] = "closed"
            trade["closed_at"] = datetime.utcnow().isoformat()
            trade["close_price"] = close_price
            trade["result_%"] = round(
                (close_price - trade["entry_price"]) / trade["entry_price"] * 100, 2
            )
            break
    _save_log(log)


def performance_summary() -> dict:
    """Kapanmis sanal islemlerin performans ozetini cikarir."""
    log = _load_log()
    closed = [t for t in log if t["status"] == "closed"]
    if not closed:
        return {"closed_trades": 0}

    wins = [t for t in closed if t["result_%"] > 0]
    win_rate = len(wins) / len(closed) * 100
    avg_result = sum(t["result_%"] for t in closed) / len(closed)

    return {
        "closed_trades": len(closed),
        "win_rate_%": round(win_rate, 1),
        "avg_result_%": round(avg_result, 2),
        "best_%": max(t["result_%"] for t in closed),
        "worst_%": min(t["result_%"] for t in closed),
    }
    
