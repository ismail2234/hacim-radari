from __future__ import annotations

import os

from flask import Flask, jsonify

from backtest import V30Backtester
from binance_client import BinanceTRClient
from scanner import V30Scanner
from worker import start_worker


app = Flask(__name__)

client = BinanceTRClient()
scanner = V30Scanner(client=client)
backtester = V30Backtester()


# ============================================================
# V30 WORKER BAŞLAT
# ============================================================

try:
    start_worker()
except Exception as exc:
    print(
        f"[V30] Worker başlatılamadı: {exc}",
        flush=True,
    )


# ============================================================
# ANA SAYFA
# ============================================================

@app.get("/")
def home():

    return jsonify(
        {
            "status": "ok",
            "project": "Hacim Radarı V30",
            "version": "V30",
            "mode": "live-worker",
            "endpoints": [
                "/api/health",
                "/api/symbols",
                "/api/scan",
                "/api/buy-signals",
                "/api/backtest/<SYMBOL>",
            ],
        }
    )


# ============================================================
# HEALTH
# ============================================================

@app.get("/api/health")
def health():

    try:

        server_time = client.get_server_time()

        return jsonify(
            {
                "status": "ok",
                "project": "Hacim Radarı V30",
                "version": "V30",
                "binance_tr": "connected",
                "worker": "running",
                "server_time": server_time,
            }
        )

    except Exception as exc:

        return jsonify(
            {
                "status": "error",
                "project": "Hacim Radarı V30",
                "binance_tr": "error",
                "error": str(exc),
            }
        ), 500


# ============================================================
# BINANCE TR TRY PARİTELERİ
# ============================================================

@app.get("/api/symbols")
def symbols():

    try:

        result = scanner.get_try_symbols()

        return jsonify(
            {
                "status": "ok",
                "count": len(result),
                "symbols": result,
            }
        )

    except Exception as exc:

        return jsonify(
            {
                "status": "error",
                "error": str(exc),
            }
        ), 500


# ============================================================
# TÜM PİYASAYI TARA
# ============================================================

@app.get("/api/scan")
def scan():

    try:

        results = scanner.scan_all()

        return jsonify(
            {
                "status": "ok",
                "count": len(results),
                "results": results,
            }
        )

    except Exception as exc:

        return jsonify(
            {
                "status": "error",
                "error": str(exc),
            }
        ), 500


# ============================================================
# SADECE BUY ADAYLARI
# ============================================================

@app.get("/api/buy-signals")
def buy_signals():

    try:

        results = scanner.buy_candidates()

        return jsonify(
            {
                "status": "ok",
                "count": len(results),
                "results": results,
            }
        )

    except Exception as exc:

        return jsonify(
            {
                "status": "error",
                "error": str(exc),
            }
        ), 500


# ============================================================
# TEK COIN BACKTEST
# ============================================================

@app.get("/api/backtest/<symbol>")
def backtest_symbol(symbol: str):

    try:

        symbol = symbol.upper().strip()

        klines = client.get_klines(
            symbol=symbol,
            interval="5m",
            limit=1000,
        )

        if not klines:

            return jsonify(
                {
                    "status": "error",
                    "symbol": symbol,
                    "error": "Mum verisi alınamadı.",
                }
            ), 404

        result = backtester.run(
            symbol,
            klines,
        )

        return jsonify(
            {
                "status": "ok",
                "result": result,
            }
        )

    except Exception as exc:

        return jsonify(
            {
                "status": "error",
                "symbol": symbol,
                "error": str(exc),
            }
        ), 500


# ============================================================
# LOCAL ÇALIŞTIRMA
# ============================================================

if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "5000",
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
    )
