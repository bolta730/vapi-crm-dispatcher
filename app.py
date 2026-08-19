import os
from flask import Flask, jsonify

app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "service": "vapi-crm-dispatcher",
        "mode": "dry-run"
    })

@app.route("/health")
def health():
    return jsonify({
        "healthy": True
    })

@app.route("/config-check")
def config_check():
    lacrm_key = os.getenv("LACRM_API_KEY")
    vapi_key = os.getenv("VAPI_API_KEY")

    return jsonify({
        "lacrm_api_key_loaded": bool(lacrm_key),
        "vapi_api_key_loaded": bool(vapi_key),
        "safe": "No secret values are displayed."
    })
