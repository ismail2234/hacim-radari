from __future__ import annotations

import os

from flask import Flask, jsonify

from scanner import V30Scanner


app = Flask(__name__)

scanner = V30Scanner()


@app.get("/")
def home():
    return jsonify(
        {
            "status": "ok",
            "project": "Hacim Radarı V30",
            "version": "V30",
            "mode": "live-scan",
            "endpoints": [
                "/api/health",
                "/api/symbols",
                "/api/scan",
                "/api/buy-signals",
            ],
        }
    )


@app.get("/api/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "project": "Hacim Radarı V30",
            "version": "V30",
        }
    )


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
