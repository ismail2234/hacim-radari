from __future__ import annotations

from flask import Blueprint, jsonify

from scanner import MarketScanner


api = Blueprint("api", __name__)

scanner = MarketScanner()


@api.get("/api/health")
def health():
    return jsonify(
        {
            "status": "healthy",
            "service": "Hacim Radarı",
        }
    )


@api.get("/api/symbols")
def symbols():
    try:
        symbols = scanner.get_try_symbols()

        return jsonify(
            {
                "count": len(symbols),
                "symbols": symbols,
            }
        )

    except Exception as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 500


@api.get("/api/scan")
def scan():
    try:
        results = scanner.scan_all()

        return jsonify(
            {
                "count": len(results),
                "results": results,
            }
        )

    except Exception as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 500


@api.get("/api/buy-signals")
def buy_signals():
    try:
        results = scanner.get_buy_signals()

        return jsonify(
            {
                "count": len(results),
                "results": results,
            }
        )

    except Exception as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 500
