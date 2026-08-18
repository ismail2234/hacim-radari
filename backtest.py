"""
V26 - backtest.py
Gecmis veride sistemin urettigi sinyallerin sonrasinda ne oldugunu olcer.
Basit versiyon burada; walk-forward / out-of-sample test icin
train/test bolme mantigi ayrica eklenmelidir.
"""

import pandas as pd

from scoring import score_candidate
from indicators import compute_all


def run_backtest(df: pd.DataFrame, score_threshold: int = 70, lookahead: int = 10) -> pd.DataFrame:
    """
    Her mumda (yeterli gecmis veri biriktiginde) skor hesaplar,
    esigin ustunde sinyal olustugunda sonraki 'lookahead' mumda
    max kazanc / max kayip ne olmus bakar.
    """
    df = compute_all(df.copy())
    df.dropna(inplace=True)
    df.reset_index(inplace=True)

    results = []
    for i in range(50, len(df) - lookahead):
        window = df.iloc[: i + 1]
        try:
            result = score_candidate(window)
        except Exception:
            continue

        if result["total"] >= score_threshold:
            entry = df["close"].iloc[i]
            future = df["close"].iloc[i + 1: i + 1 + lookahead]
            if len(future) == 0:
                continue

            max_gain = (future.max() - entry) / entry * 100
            max_loss = (future.min() - entry) / entry * 100

            results.append({
                "index": i,
                "date": df["ts"].iloc[i] if "ts" in df.columns else i,
                "score": result["total"],
                "entry_price": entry,
                "max_gain_%": round(max_gain, 2),
                "max_loss_%": round(max_loss, 2),
            })

    return pd.DataFrame(results)


def summarize_backtest(results: pd.DataFrame) -> dict:
    """Win rate, ortalama kazanc/kayip, basit performans ozeti."""
    if results.empty:
        return {"signal_count": 0}

    wins = results[results["max_gain_%"] > abs(results["max_loss_%"])]
    win_rate = len(wins) / len(results) * 100

    return {
        "signal_count": len(results),
        "win_rate_%": round(win_rate, 1),
        "avg_max_gain_%": round(results["max_gain_%"].mean(), 2),
        "avg_max_loss_%": round(results["max_loss_%"].mean(), 2),
        "worst_drawdown_%": round(results["max_loss_%"].min(), 2),
    }


def walk_forward_split(df: pd.DataFrame, train_ratio: float = 0.7):
    """Veriyi egitim (gorulen) ve test (gorulmemis / out-of-sample) olarak boler."""
    split_idx = int(len(df) * train_ratio)
    return df.iloc[:split_idx].copy(), df.iloc[split_idx:].copy()
    
