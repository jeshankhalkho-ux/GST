import os, json, logging, time, base64, re
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
CAPTCHA_API_KEY = os.getenv("CAPTCHA_API_KEY", "")

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
    return ok({"msg": "GST API is running", "endpoints": ["/api/gst/search?gstin=...", "/api/gst/search?pan=...", "/api/gst/returns?gstin=...", "/api/gst/search-by-name?name=..."]})

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

# ──────────────────────────────────────────
# CAPTCHA solver via capsolver
# ──────────────────────────────────────────
def solve_captcha(base64_image):
    """Send CAPTCHA image to capsolver and return solution text."""
    if not CAPTCHA_API_KEY:
        logger.warning("No CAPTCHA_API_KEY set, cannot solve CAPTCHA")
        return None

    try:
        # Create task
        create_resp = req.post("https://api.capsolver.com/createTask", json={
            "clientKey": CAPTCHA_API_KEY,
            "task": {
                "type": "ImageToTextTask",
                "body": base64_image,
                "CapKey": "EAED8C78-814D-4A18-B2E2-B03C02043297",
            }
        }, timeout=30)
        create_data = create_resp.json()

        if create_data.get("errorId"):
            logger.error("capsolver createTask error: %s", create_data.get("errorDescription"))
            return None

        task_id = create_data.get("taskId")
        if not task_id:
            return None

        # Poll for result
        for _ in range(30):
            time.sleep(3)
            result_resp = req.post("https://api.capsolver.com/getTaskResult", json={
                "clientKey": CAPTCHA_API_KEY,
                "taskId": task_id,
            }, timeout=30)
            result_data = result_resp.json()

            if result_data.get("status") == "ready":
                solution = result_data.get("solution", {}).get("text")
                logger.info("CAPTCHA solved: %s", solution)
                return solution

        logger.error("capsolver timeout waiting for result")
        return None
    except Exception as e:
        logger.error("CAPTCHA solver error: %s", e)
        return None

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

    # --- Source 1: expressgst.com with CAPTCHA solving ---
    try:
        sess = req.Session()
        sess.headers.update({
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Mobile Safari/537.36",
        })

        # Step 1: Get CAPTCHA
        captcha_resp = sess.get(
            "https://appnw.expressgst.com/api/v1/public/gstportal/public-search/captcha",
            timeout=REQUEST_TIMEOUT,
        )
        captcha_resp.raise_for_status()
        captcha_data = captcha_resp.json()
        gpc_id = captcha_data.get("data", {}).get("gpc_id")
        captcha_image_b64 = captcha_data.get("data", {}).get("captcha_image")

        if not gpc_id or not captcha_image_b64:
            raise Exception("Failed to get CAPTCHA from expressgst")

        logger.info("expressgst gpc_id: %s", gpc_id)

        # Step 2: Solve CAPTCHA
        captcha_text = solve_captcha(captcha_image_b64)
        if not captcha_text:
            raise Exception("CAPTCHA solving failed")

        # Step 3: Search with solved CAPTCHA
        resp = sess.get(
            "https://appnw.expressgst.com/api/v1/public/gstportal/public-search-by-gstin/detail",
            params={"gpc_id": gpc_id, "captcha_text": captcha_text, "gst_number": gstin},
            headers={
                "Accept": "*/*",
                "Origin": "https://www.expressgst.com",
                "Referer": "https://www.expressgst.com/",
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if data and not data.get("error"):
            elapsed = time.time() - t0
            monitor_log("/api/gst/search", 200, elapsed, "expressgst")
            logger.info("expressgst OK for %s (%.1fs)", gstin, elapsed)
            return jsonify({"rs": "S", "rc": "GSTN0000", "rd": "Success", "pd": data}), 200
        logger.info("expressgst no data for %s, trying Cleartax", gstin)
    except Exception as e:
        logger.warning("expressgst failed: %s", e)

    # --- Source 2: Cleartax (fallback) ---
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
        logger.info("Cleartax result for %s: sts=%s (%.1fs)", gstin, data.get("taxpayerInfo", {}).get("sts"), elapsed)
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

    # --- Source 1: GST Portal ---
    try:
        url = "https://services.gst.gov.in/services/api/search/taxpayerByGstin"
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://services.gst.gov.in/",
        }
        logger.info("Fetching returns for %s (FY %s) via GST Portal", gstin, fy)
        resp = req.get(url, params={"gstin": gstin}, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        elapsed = time.time() - t0
        monitor_log("/api/gst/returns", 200, elapsed, "gst_portal")
        logger.info("GST Portal returns OK for %s (%.1fs)", gstin, elapsed)
        return jsonify({"rs": "S", "rc": "GSTN0000", "rd": "Success", "pd": data}), 200
    except req.Timeout:
        elapsed = time.time() - t0
        monitor_log("/api/gst/returns", 503, elapsed, "timeout")
        return err("GSTN0503", "GST Portal timed out", 503)
    except req.ConnectionError:
        elapsed = time.time() - t0
        monitor_log("/api/gst/returns", 502, elapsed, "connection_error")
        return err("GSTN0502", "Cannot connect to GST Portal", 502)
    except req.HTTPError:
        sc = resp.status_code
        elapsed = time.time() - t0
        monitor_log("/api/gst/returns", 502, elapsed, "http_error")
        return err(f"GSTN0{sc}", f"GST Portal returned HTTP {sc}", 502)
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
