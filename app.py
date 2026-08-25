from flask import Flask, jsonify

from routes import api
from worker import start_worker


app = Flask(__name__)

app.register_blueprint(api)


_worker_started = False


def start_v29_worker():
    global _worker_started

    if _worker_started:
        return

    _worker_started = True
    start_worker()


# Gunicorn import ettiğinde worker başlar.
start_v29_worker()


@app.get("/")
def home():
    return jsonify(
        {
            "status": "ok",
            "project": "Hacim Radarı",
            "message": "V29 Hacim Radarı çalışıyor.",
            "version": "V29",
            "endpoints": [
                "/api/health",
                "/api/symbols",
                "/api/scan",
                "/api/buy-signals",
            ],
        }
    )


if __name__ == "__main__":
    import os

    port = int(os.getenv("PORT", "5000"))

    app.run(
        host="0.0.0.0",
        port=port,
    )
