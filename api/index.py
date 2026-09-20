import os, json, logging, time, re
from flask import Flask, request, jsonify
import requests as req

# ──────────────────────────────────────────
# Config
# ──────────────────────────────────────────
MONITOR_URL = os.getenv("MONITOR_URL", "https://api-monitor-teal.vercel.app/api/log")
API_KEY     = os.getenv("API_KEY", "743386f24c7c22419b39bd1595b03efc")
CORS_ALLOW  = os.getenv("CORS_ALLOW", "*")
LOG_FILE    = os.getenv("LOG_FILE", "gst.log")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))

# Free API keys (sign up at these sites to get your own)
APPYFLOW_KEY    = os.getenv("APPYFLOW_KEY", "")      # https://appyflow.in/verify-gst/
GSTINCHECK_KEY  = os.getenv("GSTINCHECK_KEY", "")    # https://gstincheck.co.in/

app = Flask(__name__)
logger = logging.getLogger("gst-api")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.FileHandler(LOG_FILE))
    logger.addHandler(logging.StreamHandler())

# ──────────────────────────────────────────
# CORS
# ──────────────────────────────────────────
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
    return ok({"msg": "GST API is running", "endpoints": ["/api/gst/search?gstin=...", "/api/gst/returns?gstin=...", "/api/gst/search-by-name?name=..."]})

# ──────────────────────────────────────────
# Monitor logging
# ──────────────────────────────────────────
def monitor_log(endpoint, status_code, response_time, source="gst", error=None):
    try:
        req.post(MONITOR_URL, json={
            "api": "GST", "endpoint": endpoint, "source": source,
            "status_code": status_code, "response_time": response_time, "error": error,
        }, timeout=5)
    except Exception:
        pass

# ──────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────
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

def flatten_cleartax(data):
    """Flatten Cleartax response to standard format"""
    info = data.get("taxpayerInfo", {})
    return {
        "gstin": info.get("gstin", ""),
        "legal_name": info.get("lgnm", ""),
        "trade_name": info.get("tradeNam", ""),
        "gstin_status": info.get("sts", ""),
        "taxpayer_type": info.get("dty", ""),
        "constitution_of_business": info.get("ctb", ""),
        "registration_date": info.get("rgdt", ""),
        "cancellation_date": info.get("cxdt", ""),
        "state": info.get("stj", ""),
        "pan": info.get("pan", ""),
        "filing": data.get("filing", []),
    }

# ──────────────────────────────────────────
# Routes
# ──────────────────────────────────────────
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

    # --- Source 1: AppyFlow (free, no CAPTCHA) ---
    if APPYFLOW_KEY:
        try:
            url = f"https://appyflow.in/api/verifyGST?gstNo={gstin}&key_secret={APPYFLOW_KEY}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36",
                "Accept": "application/json",
            }
            logger.info("Fetching GSTIN: %s via AppyFlow", gstin)
            resp = req.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if data.get("taxpayerInfo"):
                elapsed = time.time() - t0
                monitor_log("/api/gst/search", 200, elapsed, "appyflow")
                logger.info("AppyFlow OK for %s (%.1fs)", gstin, elapsed)
                return jsonify({"rs": "S", "rc": "GSTN0000", "rd": "Success", "pd": data}), 200
        except Exception as e:
            logger.warning("AppyFlow failed: %s", e)

    # --- Source 2: GSTINCheck (free, no CAPTCHA) ---
    if GSTINCHECK_KEY:
        try:
            url = f"https://sheet.gstincheck.co.in/check/{GSTINCHECK_KEY}/{gstin}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36",
                "Accept": "application/json",
            }
            logger.info("Fetching GSTIN: %s via GSTINCheck", gstin)
            resp = req.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if data.get("data"):
                elapsed = time.time() - t0
                monitor_log("/api/gst/search", 200, elapsed, "gstincheck")
                logger.info("GSTINCheck OK for %s (%.1fs)", gstin, elapsed)
                return jsonify({"rs": "S", "rc": "GSTN0000", "rd": "Success", "pd": data}), 200
        except Exception as e:
            logger.warning("GSTINCheck failed: %s", e)

    # --- Source 3: Cleartax (fallback) ---
    try:
        url = f"https://api.cleartax.in/f/compliance-report/{gstin}/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://www.cleartax.in/gst-number-search/",
        }
        logger.info("Fetching GSTIN: %s via Cleartax", gstin)
        resp = req.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        elapsed = time.time() - t0
        monitor_log("/api/gst/search", 200, elapsed, "cleartax")
        logger.info("Cleartax OK for %s (%.1fs)", gstin, elapsed)
        return jsonify({"rs": "S", "rc": "GSTN0000", "rd": "Success", "pd": data}), 200
    except req.Timeout:
        elapsed = time.time() - t0
        monitor_log("/api/gst/search", 503, elapsed, "timeout")
        return err("GSTN0503", "All upstream APIs timed out", 503)
    except req.ConnectionError:
        elapsed = time.time() - t0
        monitor_log("/api/gst/search", 502, elapsed, "connection_error")
        return err("GSTN0502", "Cannot connect to upstream GST APIs", 502)
    except req.HTTPError:
        sc = resp.status_code
        elapsed = time.time() - t0
        monitor_log("/api/gst/search", 502, elapsed, "http_error")
        return err(f"GSTN0{sc}", f"Upstream returned HTTP {sc}", 502)
    except ValueError:
        elapsed = time.time() - t0
        monitor_log("/api/gst/search", 502, elapsed, "json_error")
        return err("GSTN0503", "Upstream returned invalid JSON", 502)
    except Exception as e:
        elapsed = time.time() - t0
        monitor_log("/api/gst/search", 500, elapsed, "error", str(e))
        return err("GSTN0500", f"Server error: {e}", 500)


@app.route("/api/gst/returns", methods=["GET"])
def gst_returns():
    t0 = time.time()
    gstin = request.args.get("gstin", "").strip().upper()
    fy = request.args.get("fy", "").strip()
    if not gstin:
        return err("GSTN0001", "GSTIN is required")
    if not fy:
        return err("GSTN0003", "Financial year (fy) is required, e.g. 2024-25")

    # --- Source 1: AppyFlow ---
    if APPYFLOW_KEY:
        try:
            url = f"https://appyflow.in/api/verifyGST?gstNo={gstin}&key_secret={APPYFLOW_KEY}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36",
                "Accept": "application/json",
            }
            logger.info("Fetching returns for %s (FY %s) via AppyFlow", gstin, fy)
            resp = req.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            filing = data.get("filing", [])
            elapsed = time.time() - t0
            monitor_log("/api/gst/returns", 200, elapsed, "appyflow")
            logger.info("AppyFlow returns OK for %s (%.1fs)", gstin, elapsed)
            return jsonify({"rs": "S", "rc": "GSTN0000", "rd": "Success", "pd": {"filing": filing}}), 200
        except Exception as e:
            logger.warning("AppyFlow returns failed: %s", e)

    # --- Source 2: Cleartax ---
    try:
        url = f"https://api.cleartax.in/f/compliance-report/{gstin}/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://www.cleartax.in/gst-number-search/",
        }
        logger.info("Fetching returns for %s (FY %s) via Cleartax", gstin, fy)
        resp = req.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        filing = data.get("filing", [])
        elapsed = time.time() - t0
        monitor_log("/api/gst/returns", 200, elapsed, "cleartax")
        logger.info("Cleartax returns OK for %s (%.1fs)", gstin, elapsed)
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

    # --- Source: Masters India ---
    try:
        url = "https://mastergst.com/api/public/search/gstin"
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://mastergst.com/",
        }
        logger.info("Searching by name: %s via Masters India", name)
        resp = req.get(url, params={"q": name}, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        elapsed = time.time() - t0
        monitor_log("/api/gst/search-by-name", 200, elapsed, "masters_india")
        logger.info("Masters India name search OK for '%s' (%.1fs)", name, elapsed)
        return jsonify({"rs": "S", "rc": "GSTN0000", "rd": "Success", "pd": data}), 200
    except req.Timeout:
        elapsed = time.time() - t0
        monitor_log("/api/gst/search-by-name", 503, elapsed, "timeout")
        return err("GSTN0503", "Masters India timed out", 503)
    except req.ConnectionError:
        elapsed = time.time() - t0
        monitor_log("/api/gst/search-by-name", 502, elapsed, "connection_error")
        return err("GSTN0502", "Cannot connect to Masters India", 502)
    except req.HTTPError:
        sc = resp.status_code
        elapsed = time.time() - t0
        monitor_log("/api/gst/search-by-name", 502, elapsed, "http_error")
        return err(f"GSTN0{sc}", f"Masters India returned HTTP {sc}", 502)
    except Exception as e:
        elapsed = time.time() - t0
        monitor_log("/api/gst/search-by-name", 500, elapsed, "error", str(e))
        return err("GSTN0500", f"Server error: {e}", 500)


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "3000")), debug=True)
