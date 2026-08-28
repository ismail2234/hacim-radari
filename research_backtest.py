"""
🐋 HACİM RADARI — V29 RESEARCH BACKTEST
=======================================

Amaç:
V29'un geçmişte gerçekten para kazandırıp kazandırmadığını
geleceği görmeden test etmek.

ÖNEMLİ:
- Her sinyal anında sadece geçmiş veriler kullanılır.
- Gelecek mumlar yalnızca sonucu ölçmek için kullanılır.
- Cooldown kullanılmaz.
- BUY ve WATCH ayrı tutulur.
- TP / SL / MFE / MAE hesaplanır.
- Sonuç CSV olarak kaydedilir.

Kullanım:
    python research_backtest.py

Beklenen CSV:
    backtest_results.csv
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any

import pandas as pd

from v29_engine import V29Engine


# ============================================================
# AYARLAR
# ============================================================

SYMBOL = "SENT_TRY"

INITIAL_DATA = 60

TP_LEVELS = [0.02, 0.03, 0.05]
SL_LEVELS = [-0.01, -0.015, -0.02]

LOOKAHEAD = 12

FEE_RATE_ONE_WAY = 0.001


# ============================================================
# YARDIMCI
# ============================================================

def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)

        if not math.isfinite(value):
            return default

        return value

    except (TypeError, ValueError):
        return default


def pct_change(entry: float, price: float) -> float:
    if entry <= 0:
        return 0.0

    return ((price - entry) / entry) * 100.0


# ============================================================
# İŞLEM KAYDI
# ============================================================

@dataclass
class TradeResult:

    symbol: str
    signal_time: str
    signal: str
    score: float
    confirmation: str

    entry_price: float

    result: str
    exit_price: float
    exit_time: str

    gross_return_pct: float
    net_return_pct: float

    mfe_pct: float
    mae_pct: float

    plus_1_pct: float
    plus_3_pct: float
    plus_6_pct: float
    plus_12_pct: float

    tp_2_hit: bool
    tp_3_hit: bool
    tp_5_hit: bool

    sl_1_hit: bool
    sl_15_hit: bool
    sl_2_hit: bool

    candles_to_tp3: int


# ============================================================
# DATAFRAME ZAMAN
# ============================================================

def get_time(df: pd.DataFrame, index: int) -> str:

    if "open_time" in df.columns:

        value = df.iloc[index]["open_time"]

        return str(value)

    if "timestamp" in df.columns:

        value = df.iloc[index]["timestamp"]

        return str(value)

    if isinstance(df.index, pd.DatetimeIndex):

        return str(df.index[index])

    return str(index)


# ============================================================
# V29 BACKTEST
# ============================================================

def run_backtest(
    df: pd.DataFrame,
    symbol: str,
) -> list[TradeResult]:

    if df is None or df.empty:
        raise ValueError("Veri boş.")

    data = df.copy().reset_index(drop=True)

    engine = V29Engine()

    results: list[TradeResult] = []

    max_signal_index = len(data) - LOOKAHEAD - 1

    if max_signal_index < INITIAL_DATA:
        raise ValueError(
            "Backtest için yeterli mum yok."
        )

    for i in range(
        INITIAL_DATA,
        max_signal_index + 1,
    ):

        # ----------------------------------------------------
        # KRİTİK:
        # V29'A SADECE O ANA KADAR OLAN VERİ GİDER.
        # ----------------------------------------------------

        history = data.iloc[
            : i + 1
        ].copy()

        try:

            result = engine.calculate(
                symbol,
                history,
            )

        except Exception as exc:

            print(
                f"[WARN] {symbol} "
                f"mum={i} V29 hatası: {exc}"
            )

            continue

        signal = str(
            getattr(
                result,
                "signal",
                "",
            )
        ).upper()

        # ----------------------------------------------------
        # SADECE BUY / WATCH KAYDET
        # ----------------------------------------------------

        if signal not in (
            "AL",
            "BUY",
            "WATCH",
        ):
            continue

        entry_price = safe_float(
            getattr(
                result,
                "price",
                history.iloc[-1]["close"],
            )
        )

        if entry_price <= 0:
            continue

        future = data.iloc[
            i + 1 :
            i + LOOKAHEAD + 1
        ].copy()

        if future.empty:
            continue

        # ----------------------------------------------------
        # GELECEK HAREKET
        # ----------------------------------------------------

        highs = pd.to_numeric(
            future["high"],
            errors="coerce",
        ).dropna()

        lows = pd.to_numeric(
            future["low"],
            errors="coerce",
        ).dropna()

        closes = pd.to_numeric(
            future["close"],
            errors="coerce",
        ).dropna()

        if highs.empty or lows.empty:
            continue

        # ----------------------------------------------------
        # MFE / MAE
        # ----------------------------------------------------

        mfe_pct = (
            (highs.max() - entry_price)
            / entry_price
        ) * 100.0

        mae_pct = (
            (lows.min() - entry_price)
            / entry_price
        ) * 100.0

        # ----------------------------------------------------
        # TP / SL
        # ----------------------------------------------------

        tp_2_hit = False
        tp_3_hit = False
        tp_5_hit = False

        sl_1_hit = False
        sl_15_hit = False
        sl_2_hit = False

        candles_to_tp3 = 0

        exit_price = safe_float(
            closes.iloc[-1],
            entry_price,
        )

        exit_index = LOOKAHEAD - 1

        trade_result = "TIMEOUT"

        # ----------------------------------------------------
        # MUM MUM İLERLE
        # ----------------------------------------------------

        for j in range(len(future)):

            candle = future.iloc[j]

            high = safe_float(
                candle["high"],
                entry_price,
            )

            low = safe_float(
                candle["low"],
                entry_price,
            )

            # -----------------------------
            # HEDEFLER
            # -----------------------------

            if (
                high >= entry_price * 1.02
            ):
                tp_2_hit = True

            if (
                high >= entry_price * 1.03
                and not tp_3_hit
            ):
                tp_3_hit = True

                candles_to_tp3 = j + 1

            if (
                high >= entry_price * 1.05
            ):
                tp_5_hit = True

            # -----------------------------
            # STOPLAR
            # -----------------------------

            if (
                low <= entry_price * 0.99
            ):
                sl_1_hit = True

            if (
                low <= entry_price * 0.985
            ):
                sl_15_hit = True

            if (
                low <= entry_price * 0.98
            ):
                sl_2_hit = True

            # ------------------------------------------------
            # İLK OLASILIK:
            # TP +3 / SL -1.5
            #
            # Aynı mumda ikisi birden görünürse
            # KÖTÜMSER SENARYO = STOP
            # ------------------------------------------------

            hit_tp = (
                high >= entry_price * 1.03
            )

            hit_sl = (
                low <= entry_price * 0.985
            )

            if hit_tp and hit_sl:

                trade_result = "SL"

                exit_price = (
                    entry_price * 0.985
                )

                exit_index = j

                break

            if hit_sl:

                trade_result = "SL"

                exit_price = (
                    entry_price * 0.985
                )

                exit_index = j

                break

            if hit_tp:

                trade_result = "TP"

                exit_price = (
                    entry_price * 1.03
                )

                exit_index = j

                break

        else:

            last_close = safe_float(
                closes.iloc[-1],
                entry_price,
            )

            exit_price = last_close

            exit_index = len(future) - 1

        # ----------------------------------------------------
        # GETİRİ
        # ----------------------------------------------------

        gross_return_pct = pct_change(
            entry_price,
            exit_price,
        )

        # Alış + satış maliyeti.
        total_fee = (
            FEE_RATE_ONE_WAY * 2
        )

        net_return_pct = (
            (
                exit_price / entry_price
            )
            * (1 - total_fee)
            - 1
        ) * 100.0

        # ----------------------------------------------------
        # 1 / 3 / 6 / 12 MUM
        # ----------------------------------------------------

        def future_close_pct(
            candles: int,
        ) -> float:

            if len(closes) < candles:
                return 0.0

            price = safe_float(
                closes.iloc[candles - 1],
                entry_price,
            )

            return pct_change(
                entry_price,
                price,
            )

        # ----------------------------------------------------
        # ZAMAN
        # ----------------------------------------------------

        signal_time = get_time(
            data,
            i,
        )

        exit_time = get_time(
            data,
            i + 1 + exit_index,
        )

        results.append(
            TradeResult(

                symbol=symbol,

                signal_time=signal_time,

                signal=signal,

                score=safe_float(
                    getattr(
                        result,
                        "score",
                        0,
                    )
                ),

                confirmation=str(
                    getattr(
                        result,
                        "confirmation",
                        "",
                    )
                ),

                entry_price=entry_price,

                result=trade_result,

                exit_price=exit_price,

                exit_time=exit_time,

                gross_return_pct=(
                    gross_return_pct
                ),

                net_return_pct=(
                    net_return_pct
                ),

                mfe_pct=mfe_pct,

                mae_pct=mae_pct,

                plus_1_pct=future_close_pct(
                    1
                ),

                plus_3_pct=future_close_pct(
                    3
                ),

                plus_6_pct=future_close_pct(
                    6
                ),

                plus_12_pct=future_close_pct(
                    12
                ),

                tp_2_hit=tp_2_hit,

                tp_3_hit=tp_3_hit,

                tp_5_hit=tp_5_hit,

                sl_1_hit=sl_1_hit,

                sl_15_hit=sl_15_hit,

                sl_2_hit=sl_2_hit,

                candles_to_tp3=(
                    candles_to_tp3
                ),
            )
        )

    return results


# ============================================================
# RAPOR
# ============================================================

def print_report(
    results: list[TradeResult],
) -> None:

    if not results:

        print()
        print("=" * 60)
        print("SONUÇ")
        print("=" * 60)
        print("Hiç BUY/WATCH sinyali bulunamadı.")
        return

    df = pd.DataFrame(
        [asdict(x) for x in results]
    )

    total = len(df)

    wins = (
        df["result"] == "TP"
    ).sum()

    losses = (
        df["result"] == "SL"
    ).sum()

    timeout = (
        df["result"] == "TIMEOUT"
    ).sum()

    win_rate = (
        wins / total * 100
        if total
        else 0
    )

    avg_return = df[
        "net_return_pct"
    ].mean()

    median_return = df[
        "net_return_pct"
    ].median()

    total_return = (
        df["net_return_pct"].sum()
    )

    avg_mfe = df[
        "mfe_pct"
    ].mean()

    avg_mae = df[
        "mae_pct"
    ].mean()

    profit_sum = df.loc[
        df["net_return_pct"] > 0,
        "net_return_pct",
    ].sum()

    loss_sum = abs(
        df.loc[
            df["net_return_pct"] < 0,
            "net_return_pct",
        ].sum()
    )

    profit_factor = (
        profit_sum / loss_sum
        if loss_sum > 0
        else float("inf")
    )

    print()
    print("=" * 60)
    print("🐋 V29 RESEARCH BACKTEST")
    print("=" * 60)

    print(
        f"Toplam sinyal : {total}"
    )

    print(
        f"TP            : {wins}"
    )

    print(
        f"SL            : {losses}"
    )

    print(
        f"Timeout       : {timeout}"
    )

    print(
        f"Win Rate      : {win_rate:.2f}%"
    )

    print(
        f"Ort. Net      : {avg_return:.3f}%"
    )

    print(
        f"Medyan Net    : {median_return:.3f}%"
    )

    print(
        f"Toplam Net    : {total_return:.3f}%"
    )

    print(
        f"Profit Factor : {profit_factor:.3f}"
    )

    print(
        f"Ort. MFE      : {avg_mfe:.3f}%"
    )

    print(
        f"Ort. MAE      : {avg_mae:.3f}%"
    )

    print()
    print("HEDEF GERÇEKLEŞME")
    print("-" * 60)

    print(
        f"+2% : "
        f"{df['tp_2_hit'].mean() * 100:.2f}%"
    )

    print(
        f"+3% : "
        f"{df['tp_3_hit'].mean() * 100:.2f}%"
    )

    print(
        f"+5% : "
        f"{df['tp_5_hit'].mean() * 100:.2f}%"
    )

    print()
    print("1 / 3 / 6 / 12 MUM")
    print("-" * 60)

    print(
        f"+1 mum  : "
        f"{df['plus_1_pct'].mean():.3f}%"
    )

    print(
        f"+3 mum  : "
        f"{df['plus_3_pct'].mean():.3f}%"
    )

    print(
        f"+6 mum  : "
        f"{df['plus_6_pct'].mean():.3f}%"
    )

    print(
        f"+12 mum : "
        f"{df['plus_12_pct'].mean():.3f}%"
    )

    print()
    print("=" * 60)


# ============================================================
# CSV
# ============================================================

def save_results(
    results: list[TradeResult],
    filename: str = "backtest_results.csv",
) -> None:

    if not results:
        return

    df = pd.DataFrame(
        [asdict(x) for x in results]
    )

    df.to_csv(
        filename,
        index=False,
        encoding="utf-8-sig",
    )

    print()
    print(
        f"CSV kaydedildi: {filename}"
    )


# ============================================================
# ÖRNEK VERİ YÜKLEYİCİ
# ============================================================

def load_csv(
    filename: str,
) -> pd.DataFrame:

    df = pd.read_csv(
        filename
    )

    required = {
        "open",
        "high",
        "low",
        "close",
    }

    missing = (
        required
        - set(df.columns)
    )

    if missing:

        raise ValueError(
            "CSV eksik kolonlar: "
            + ", ".join(
                sorted(missing)
            )
        )

    return df


# ============================================================
# ANA
# ============================================================

def main() -> None:

    print("=" * 60)
    print("🐋 V29 RESEARCH BACKTEST")
    print("=" * 60)

    print()
    print(
        "Bu dosya geçmiş CSV verisi üzerinde çalışır."
    )

    print()
    print(
        "Önce CSV dosyanı hazırla:"
    )

    print(
        "    SENT_TRY_5m.csv"
    )

    print()
    print(
        "CSV kolonları en az:"
    )

    print(
        "    open, high, low, close"
    )

    print()

    filename = (
        f"{SYMBOL}_5m.csv"
    )

    try:

        df = load_csv(
            filename
        )

    except FileNotFoundError:

        print(
            f"CSV bulunamadı: {filename}"
        )

        print()
        print(
            "Bu aşamada kod hata vermesin diye "
            "veri çekme işlemini ayrı tutuyoruz."
        )

        return

    print(
        f"Veri: {len(df)} mum"
    )

    results = run_backtest(
        df,
        SYMBOL,
    )

    print_report(
        results
    )

    save_results(
        results
    )


if __name__ == "__main__":
    main()
