from __future__ import annotations

from flask import Flask, jsonify


app = Flask(__name__)


@app.get("/")
def home():
    return jsonify(
        {
            "status": "ok",
            "project": "Hacim Radarı V30",
            "version": "V30",
            "message": "V30 çalışıyor.",
            "mode": "backtest-first",
            "endpoints": [
                "/api/health",
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


if __name__ == "__main__":
    import os

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
