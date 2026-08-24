from flask import Flask, jsonify

from routes import api


app = Flask(__name__)

app.register_blueprint(api)


@app.get("/")
def home():
    return jsonify(
        {
            "status": "ok",
            "project": "Hacim Radarı",
            "message": "Hacim Radarı çalışıyor.",
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
