"""
GST Lookup Flask API
Proxies upstream GST verification service.
Response format mirrors GSTN official API structure.
"""

import os
import re
import time
import logging

import requests
from flask import Flask, g, jsonify, request
from flask_cors import CORS

# ── App ───────────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ── Config ────────────────────────────────────────────────────────────────────
GST_API_URL        = os.getenv("GST_API_URL", "")
GST_API_KEY        = os.getenv("GST_API_KEY", "")
UMANG_TKN          = os.getenv("UMANG_TKN", "")
UMANG_TRKR         = os.getenv("UMANG_TRKR", "")
UMANG_USRID        = os.getenv("UMANG_USRID", "")
UMANG_DEPTID       = os.getenv("UMANG_DEPTID", "63")
UMANG_SRVID        = os.getenv("UMANG_SRVID", "559")
PORT               = int(os.getenv("PORT", 5000))
DEBUG              = os.getenv("DEBUG", "false").lower() == "true"
MOCK_MODE          = os.getenv("MOCK_MODE", "false").lower() == "true"
API_AUTH_KEY       = os.getenv("API_AUTH_KEY", "")
REQUEST_TIMEOUT    = int(os.getenv("REQUEST_TIMEOUT", 15))

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("gst-api")

# ── GSTIN Validation ──────────────────────────────────────────────────────────
GSTIN_RE = re.compile(
    r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$"
)

VALID_STATE_CODES = {
    "01","02","03","04","05","06","07","08","09","10",
    "11","12","13","14","15","16","17","18","19","20",
    "21","22","23","24","25","26","27","28","29","30",
    "31","32","33","34","35","36","37","38","96","97","99",
}

def validate_gstin(gstin: str) -> tuple[bool, str]:
    if len(gstin) != 15:
        return False, "GSTIN must be exactly 15 characters"
    if not GSTIN_RE.match(gstin):
        return False, "Invalid GSTIN format"
    if gstin[:2] not in VALID_STATE_CODES:
        return False, f"Invalid state code '{gstin[:2]}'"
    return True, ""

# ── Response Helpers ──────────────────────────────────────────────────────────
def ok(pd: dict, http: int = 200):
    return jsonify({"rs": "S", "rc": "GSTN0000", "rd": "Success", "pd": pd}), http

def err(rc: str, rd: str, http: int = 400):
    return jsonify({"rs": "E", "rc": rc, "rd": rd, "pd": None}), http

# ── Mock Payload ──────────────────────────────────────────────────────────────
MOCK_DECODED = {
    "stjCd": "JH017",
    "lgnm": "DEMO TAXPAYER PRIVATE LIMITED",
    "stj": "Jamshedpur",
    "dty": "Regular",
    "adadr": [],
    "cxdt": "",
    "gstin": "20AABCP1234C1ZX",
    "nba": ["Retail Business"],
    "lstupdt": "01/01/2024",
    "rgdt": "01/01/2020",
    "ctb": "Private Limited Company",
    "pradr": {
        "addr": {
            "bnm": "DEMO BUILDING",
            "st": "MAIN ROAD",
            "loc": "BISTUPUR JAMSHEDPUR",
            "bno": "1",
            "dst": "East Singhbhum",
            "lt": "86.2548449",
            "locality": "Bistupur",
            "pncd": "831001",
            "landMark": "",
            "stcd": "Jharkhand",
            "geocodelvl": "NA",
            "flno": "",
            "lg": "22.8046927",
        },
        "ntr": "Retail Business",
    },
    "tradeNam": "DEMO ENTERPRISES",
    "ctjCd": "XX0405",
    "sts": "Active",
    "ctj": "BISTUPUR RANGE",
    "einvoiceStatus": "No",
}

MOCK_PD = {
    "status_cd": "1",
    "data": None,
    "rek": "",
    "hmac": "",
    "error": None,
    "decodedData": MOCK_DECODED,
}

# ── Middleware ────────────────────────────────────────────────────────────────
@app.before_request
def _auth():
    if request.method == "OPTIONS":
        return
    if request.path in ("/", "/health"):
        return
    if API_AUTH_KEY:
        key = request.headers.get("x-api-key", "")
        if key != API_AUTH_KEY:
            return err("GSTN0401", "Invalid or missing API key", 401)

@app.before_request
def _timer():
    g.t0 = time.perf_counter()

@app.after_request
def _log(response):
    ms = round((time.perf_counter() - g.get("t0", time.perf_counter())) * 1000, 1)
    logger.info("%s %s → %d [%sms]", request.method, request.path, response.status_code, ms)
    return response

# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/")
def index():
    return jsonify({
        "service": "GST Lookup API",
        "version": "1.0.0",
        "mock_mode": MOCK_MODE,
        "endpoints": {
            "root":       "GET  /",
            "health":     "GET  /health",
            "gst_search": "GET  /api/gst/search?gstin=<GSTIN>",
            "gst_post":   "POST /api/gst/search  {\"gstin\": \"<GSTIN>\"}",
        },
    })


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "gst-lookup",
        "upstream_configured": bool(GST_API_URL),
        "mock_mode": MOCK_MODE,
    })


@app.route("/api/gst/search", methods=["GET", "POST"])
def gst_search():

    # ── Parse input ──────────────────────────────────────────────────────────
    if request.method == "GET":
        raw = request.args.get("gstin", "").strip()
    else:
        body = request.get_json(silent=True) or {}
        raw  = body.get("gstin", "").strip()

    gstin = raw.upper()

    # ── Validate ─────────────────────────────────────────────────────────────
    if not gstin:
        return err("GSTN0001", "GSTIN is required")

    valid, msg = validate_gstin(gstin)
    if not valid:
        return err("GSTN0002", msg)

    # ── Mock mode ─────────────────────────────────────────────────────────────
    if MOCK_MODE:
        logger.info("[MOCK] %s", gstin)
        pd = {**MOCK_PD, "decodedData": {**MOCK_DECODED, "gstin": gstin}}
        return ok(pd)

    # ── Upstream check ────────────────────────────────────────────────────────
    if not GST_API_URL:
        return err("GSTN0099", "GST_API_URL is not configured", 500)

    # ── Call upstream ─────────────────────────────────────────────────────────
    try:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-api-key": GST_API_KEY,
            "deptid": UMANG_DEPTID,
            "srvid": UMANG_SRVID,
        }

        payload = {
            "tkn": UMANG_TKN,
            "trkr": UMANG_TRKR,
            "lang": "en",
            "usrid": UMANG_USRID,
            "mode": "web",
            "pltfrm": "web",
            "did": None,
            "deptid": UMANG_DEPTID,
            "srvid": UMANG_SRVID,
            "gstin": gstin,
        }

        logger.info("Fetching GSTIN: %s", gstin)

        upstream = requests.post(
            GST_API_URL,
            json=payload,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        upstream.raise_for_status()

        data = upstream.json()
        logger.info("Result: %s → rs=%s rc=%s", gstin, data.get("rs"), data.get("rc"))
        return jsonify(data), upstream.status_code

    except requests.Timeout:
        logger.warning("Timeout: %s", gstin)
        return err("GSTN0503", "Upstream API timed out", 503)

    except requests.ConnectionError:
        logger.error("Connection error to upstream")
        return err("GSTN0502", "Cannot connect to upstream GST API", 502)

    except requests.HTTPError:
        sc = upstream.status_code
        logger.error("Upstream HTTP %d for %s", sc, gstin)
        return err(f"GSTN0{sc}", f"Upstream returned HTTP {sc}", 502)

    except ValueError:
        logger.error("Non-JSON response from upstream for %s", gstin)
        return err("GSTN0503", "Upstream returned invalid JSON", 502)

    except Exception as e:
        logger.exception("Unhandled error for %s: %s", gstin, e)
        return err("GSTN0500", "Internal server error", 500)


# ── Error Handlers ────────────────────────────────────────────────────────────
@app.errorhandler(404)
def not_found(_):
    return err("GSTN0404", "Endpoint not found", 404)

@app.errorhandler(405)
def method_not_allowed(_):
    return err("GSTN0405", "Method not allowed", 405)

@app.errorhandler(500)
def internal(_):
    return err("GSTN0500", "Internal server error", 500)
