from flask import Flask, jsonify
import os

app = Flask(__name__)


@app.get("/")
def home():
    return jsonify(
        {
            "status": "ok",
            "project": "Hacim Radarı",
            "message": "Hacim Radarı çalışıyor."
        }
    )


@app.get("/health")
def health():
    return jsonify(
        {
            "status": "healthy"
        }
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
