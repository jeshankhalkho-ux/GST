import os
import re
import time
import logging

import requests
from flask import Flask, g, jsonify, request, Response

try:
    from flask_cors import CORS
    _cors_available = True
except ImportError:
    _cors_available = False

app = Flask(__name__)
if _cors_available:
    CORS(app, resources={r"/api/*": {"origins": "*"}})

MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() == "true"

CLEARTAX_BASE      = os.getenv("CLEARTAX_BASE", "https://cleartax.in")
PORT               = int(os.getenv("PORT", 5000))
DEBUG              = os.getenv("DEBUG", "false").lower() == "true"
API_AUTH_KEY       = os.getenv("API_AUTH_KEY", "")
REQUEST_TIMEOUT    = int(os.getenv("REQUEST_TIMEOUT", 15))

logger = logging.getLogger("gst-api")
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

logger.info("Starting GST API | mock=%s", MOCK_MODE)

GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")

VALID_STATE_CODES = {
    "01","02","03","04","05","06","07","08","09","10",
    "11","12","13","14","15","16","17","18","19","20",
    "21","22","23","24","25","26","27","28","29","30",
    "31","32","33","34","35","36","37","38","96","97","99",
}

def validate_gstin(gstin):
    if len(gstin) != 15:
        return False, "GSTIN must be exactly 15 characters"
    if not GSTIN_RE.match(gstin):
        return False, "Invalid GSTIN format"
    if gstin[:2] not in VALID_STATE_CODES:
        return False, f"Invalid state code '{gstin[:2]}'"
    return True, ""

def ok(pd, http=200):
    return jsonify({"rs": "S", "rc": "GSTN0000", "rd": "Success", "pd": pd}), http

def err(rc, rd, http=400):
    return jsonify({"rs": "E", "rc": rc, "rd": rd, "pd": None}), http

MOCK_DECODED = {
    "stjCd": "JH017", "lgnm": "DEMO TAXPAYER PRIVATE LIMITED",
    "stj": "Jamshedpur", "dty": "Regular", "adadr": [], "cxdt": "",
    "gstin": "20AABCP1234C1ZX",
    "nba": ["Retail Business"], "lstupdt": "01/01/2024", "rgdt": "01/01/2020",
    "ctb": "Private Limited Company",
    "pradr": {"addr": {"bnm": "DEMO BUILDING", "st": "MAIN ROAD",
        "loc": "BISTUPUR JAMSHEDPUR", "bno": "1", "dst": "East Singhbhum",
        "lt": "86.2548449", "locality": "Bistupur", "pncd": "831001",
        "landMark": "", "stcd": "Jharkhand", "geocodelvl": "NA",
        "flno": "", "lg": "22.8046927"}, "ntr": "Retail Business"},
    "tradeNam": "DEMO ENTERPRISES", "ctjCd": "XX0405",
    "sts": "Active", "ctj": "BISTUPUR RANGE", "einvoiceStatus": "No",
}

MOCK_PD = {
    "status_cd": "1", "data": None, "rek": "", "hmac": "",
    "error": None, "decodedData": MOCK_DECODED,
}

@app.before_request
def _timer():
    g.t0 = time.perf_counter()

@app.after_request
def _log(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, x-api-key"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    ms = round((time.perf_counter() - g.get("t0", time.perf_counter())) * 1000, 1)
    logger.info("%s %s -> %d [%sms]", request.method, request.path, response.status_code, ms)
    return response

@app.get("/")
def index():
    return jsonify({
        "service": "GST Lookup API", "version": "1.0.0", "mock_mode": MOCK_MODE,
        "endpoints": {
            "root": "GET /", "health": "GET /health",
            "gst_search": "GET /api/gst/search?gstin=<GSTIN>",
            "gst_post":   "POST /api/gst/search  {\"gstin\": \"<GSTIN>\"}",
            "gst_by_name": "GET /api/gst/search-by-name?name=<business_name>",
            "gst_returns": "GET /api/gst/returns?gstin=<GSTIN>&fy=<FY>",
        },
    })

@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "gst-lookup", "mock_mode": MOCK_MODE})

@app.route("/api/gst/search", methods=["GET", "POST"])
def gst_search():
    if request.method == "GET":
        raw = request.args.get("gstin", "").strip()
    else:
        body = request.get_json(silent=True) or {}
        raw = body.get("gstin", "").strip()

    gstin = raw.upper()
    if not gstin:
        return err("GSTN0001", "GSTIN is required")
    valid, msg = validate_gstin(gstin)
    if not valid:
        return err("GSTN0002", msg)

    if MOCK_MODE:
        logger.info("[MOCK] %s", gstin)
        pd = {**MOCK_PD, "decodedData": {**MOCK_DECODED, "gstin": gstin}}
        return ok(pd)

    try:
        url = f"{CLEARTAX_BASE}/f/compliance-report/{gstin}/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": f"{CLEARTAX_BASE}/gst-number-search/",
        }
        logger.info("Fetching GSTIN: %s via Cleartax", gstin)
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        logger.info("Cleartax result for %s: sts=%s", gstin, data.get("taxpayerInfo", {}).get("sts"))
        return jsonify({"rs": "S", "rc": "GSTN0000", "rd": "Success", "pd": data}), 200
    except requests.Timeout:
        return err("GSTN0503", "Cleartax API timed out", 503)
    except requests.ConnectionError:
        return err("GSTN0502", "Cannot connect to Cleartax API", 502)
    except requests.HTTPError:
        sc = resp.status_code
        return err(f"GSTN0{sc}", f"Cleartax returned HTTP {sc}", 502)
    except ValueError:
        return err("GSTN0503", "Cleartax returned invalid JSON", 502)
    except Exception as e:
        logger.exception("Unhandled error: %s", e)
        return err("GSTN0500", "Internal server error", 500)


@app.route("/api/gst/returns", methods=["GET", "POST"])
def gst_returns():
    if request.method == "GET":
        gstin = request.args.get("gstin", "").strip().upper()
        fy = request.args.get("fy", "").strip()
    else:
        body = request.get_json(silent=True) or {}
        gstin = body.get("gstin", "").strip().upper()
        fy = body.get("fy", "").strip()

    if not gstin:
        return err("GSTN0001", "GSTIN is required")
    valid, msg = validate_gstin(gstin)
    if not valid:
        return err("GSTN0002", msg)
    if not fy:
        return err("GSTN0003", "Financial year (fy) is required, e.g. 2023-2024 or 2023")

    if len(fy) == 4:
        y = int(fy)
        fy = f"{y}-{y+1}"

    try:
        sess = requests.Session()
        sess.headers.update({
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Mobile Safari/537.36",
        })
        # Get session from searchtp page first
        init = sess.get("https://services.gst.gov.in/services/searchtp", timeout=REQUEST_TIMEOUT)
        init.raise_for_status()
        logger.info("GST returns session obtained")

        payload = {"gstin": gstin, "fy": fy}
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://services.gst.gov.in",
            "Referer": "https://services.gst.gov.in/services/searchtp",
        }
        resp = sess.post(
            "https://services.gst.gov.in/services/api/search/taxpayerReturnDetails",
            json=payload, headers=headers, timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return jsonify({"rs": "S", "rc": "GSTN0000", "rd": "Success", "pd": data}), 200
    except requests.Timeout:
        return err("GSTN0503", "GST portal timed out", 503)
    except requests.ConnectionError:
        return err("GSTN0502", "Cannot connect to GST portal", 502)
    except requests.HTTPError:
        sc = resp.status_code
        return err(f"GSTN0{sc}", f"GST portal returned HTTP {sc}", 502)
    except ValueError:
        return err("GSTN0503", f"GST portal returned invalid JSON: {resp.text[:200]}", 502)
    except Exception as e:
        logger.exception("Returns error: %s", e)
        return err("GSTN0500", "Internal server error", 500)


@app.route("/api/gst/search-by-name")
def gst_search_by_name():
    name = request.args.get("name", "").strip()
    if not name or len(name) < 3:
        return err("GSTN0004", "Name must be at least 3 characters")
    
    try:
        url = "https://blog-backend.mastersindia.co/api/v1/custom/search/name_and_pan/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.mastersindia.co",
            "Referer": "https://www.mastersindia.co/gst-number-search-by-name-and-pan/",
        }
        resp = requests.get(url, params={"keyword": name}, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return jsonify({"rs": "S", "rc": "GSTN0000", "rd": "Success", "pd": data}), 200
    except requests.Timeout:
        return err("GSTN0503", "Masters India API timed out", 503)
    except (requests.ConnectionError, requests.HTTPError):
        return err("GSTN0502", "Failed to fetch from Masters India", 502)
    except ValueError:
        return err("GSTN0503", "Invalid JSON from Masters India", 502)
    except Exception as e:
        logger.exception("Name search error: %s", e)
        return err("GSTN0500", "Internal server error", 500)


@app.errorhandler(404)
def not_found(_):
    return err("GSTN0404", "Endpoint not found", 404)

@app.errorhandler(405)
def method_not_allowed(_):
    return err("GSTN0405", "Method not allowed", 405)

@app.errorhandler(500)
def internal(_):
    return err("GSTN0500", "Internal server error", 500)
