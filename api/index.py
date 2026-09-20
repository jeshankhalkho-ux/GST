import os, json, logging, time, re
from flask import Flask, request, jsonify
import requests as req

MONITOR_URL = os.getenv("MONITOR_URL", "https://api-monitor-teal.vercel.app/api/log")
CORS_ALLOW  = os.getenv("CORS_ALLOW", "*")
LOG_FILE    = os.getenv("LOG_FILE", "gst.log")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))
APPYFLOW_KEY    = os.getenv("APPYFLOW_KEY", "")
GSTINCHECK_KEY  = os.getenv("GSTINCHECK_KEY", "")

app = Flask(__name__)
logger = logging.getLogger("gst-api")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.FileHandler(LOG_FILE))
    logger.addHandler(logging.StreamHandler())

@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = CORS_ALLOW
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Api-Key"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp

@app.route("/", defaults={"path": ""}, methods=["GET", "POST", "OPTIONS"])
@app.route("/<path:path>", methods=["GET", "POST", "OPTIONS"])
def catch_all(path):
    if request.method == "OPTIONS":
        return "", 204
    return jsonify({"msg": "GST API is running"}), 200

def monitor_log(endpoint, status_code, response_time, source="gst", error=None):
    try:
        req.post(MONITOR_URL, json={
            "api": "GST", "endpoint": endpoint, "source": source,
            "status_code": status_code, "response_time": response_time, "error": error,
        }, timeout=5)
    except Exception:
        pass

def ok(pd):
    return jsonify({"rs": "S", "rc": "GSTN0000", "rd": "Success", "pd": pd}), 200

def err(rc, rd, status=400):
    return jsonify({"rs": "E", "rc": rc, "rd": rd, "pd": None}), status

def validate_gstin(gstin):
    if len(gstin) != 15:
        return False, "GSTIN must be 15 characters"
    if not re.match(r'^\d{2}[A-Z]{5}\d{4}[A-Z]\dZ[A-Z\d]$', gstin):
        return False, "Invalid GSTIN format"
    return True, ""

@app.route("/api/gst/search", methods=["GET", "POST"])
def gst_search():
    t0 = time.time()
    if request.method == "POST":
        raw = (request.get_json(silent=True) or {}).get("gstin", "").strip()
    else:
        raw = request.args.get("gstin", "").strip()

    gstin = raw.upper()
    if not gstin:
        return err("GSTN0001", "GSTIN is required")
    valid, msg = validate_gstin(gstin)
    if not valid:
        return err("GSTN0002", msg)

    if APPYFLOW_KEY:
        try:
            url = f"https://appyflow.in/api/verifyGST?gstNo={gstin}&key_secret={APPYFLOW_KEY}"
            resp = req.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if data.get("taxpayerInfo"):
                elapsed = time.time() - t0
                monitor_log("/api/gst/search", 200, elapsed, "appyflow")
                return jsonify({"rs": "S", "rc": "GSTN0000", "rd": "Success", "pd": data}), 200
        except Exception as e:
            logger.warning("AppyFlow failed: %s", e)

    if GSTINCHECK_KEY:
        try:
            url = f"https://sheet.gstincheck.co.in/check/{GSTINCHECK_KEY}/{gstin}"
            resp = req.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if data.get("data"):
                elapsed = time.time() - t0
                monitor_log("/api/gst/search", 200, elapsed, "gstincheck")
                return jsonify({"rs": "S", "rc": "GSTN0000", "rd": "Success", "pd": data}), 200
        except Exception as e:
            logger.warning("GSTINCheck failed: %s", e)

    try:
        url = f"https://api.cleartax.in/f/compliance-report/{gstin}/"
        resp = req.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        elapsed = time.time() - t0
        monitor_log("/api/gst/search", 200, elapsed, "cleartax")
        return jsonify({"rs": "S", "rc": "GSTN0000", "rd": "Success", "pd": data}), 200
    except Exception as e:
        elapsed = time.time() - t0
        monitor_log("/api/gst/search", 500, elapsed, "error", str(e))
        return err("GSTN0500", f"All sources failed: {e}", 500)

@app.route("/api/gst/returns", methods=["GET"])
def gst_returns():
    t0 = time.time()
    gstin = request.args.get("gstin", "").strip().upper()
    fy = request.args.get("fy", "").strip()
    if not gstin:
        return err("GSTN0001", "GSTIN is required")
    if not fy:
        return err("GSTN0003", "Financial year (fy) is required")

    if APPYFLOW_KEY:
        try:
            url = f"https://appyflow.in/api/verifyGST?gstNo={gstin}&key_secret={APPYFLOW_KEY}"
            resp = req.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            filing = data.get("filing", [])
            elapsed = time.time() - t0
            monitor_log("/api/gst/returns", 200, elapsed, "appyflow")
            return jsonify({"rs": "S", "rc": "GSTN0000", "rd": "Success", "pd": {"filing": filing}}), 200
        except Exception as e:
            logger.warning("AppyFlow returns failed: %s", e)

    try:
        url = f"https://api.cleartax.in/f/compliance-report/{gstin}/"
        resp = req.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        filing = data.get("filing", [])
        elapsed = time.time() - t0
        monitor_log("/api/gst/returns", 200, elapsed, "cleartax")
        return jsonify({"rs": "S", "rc": "GSTN0000", "rd": "Success", "pd": {"filing": filing}}), 200
    except Exception as e:
        elapsed = time.time() - t0
        monitor_log("/api/gst/returns", 500, elapsed, "error", str(e))
        return err("GSTN0500", f"Server error: {e}", 500)

@app.route("/api/gst/search-by-name", methods=["GET"])
def gst_search_by_name():
    t0 = time.time()
    name = request.args.get("name", "").strip()
    if not name:
        return err("GSTN0004", "Business name is required")

    try:
        url = "https://mastergst.com/api/public/search/gstin"
        resp = req.get(url, params={"q": name}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        elapsed = time.time() - t0
        monitor_log("/api/gst/search-by-name", 200, elapsed, "masters_india")
        return jsonify({"rs": "S", "rc": "GSTN0000", "rd": "Success", "pd": data}), 200
    except Exception as e:
        elapsed = time.time() - t0
        monitor_log("/api/gst/search-by-name", 500, elapsed, "error", str(e))
        return err("GSTN0500", f"Server error: {e}", 500)

@app.route("/api/health")
def health():
    return jsonify({"status": "ok"}), 200
