import os
import re
import json
import time
import logging

import requests
import urllib3
urllib3.disable_warnings()
from flask import Flask, g, jsonify, request

try:
    from flask_cors import CORS
    _cors_available = True
except ImportError:
    _cors_available = False

app = Flask(__name__)
if _cors_available:
    CORS(app, resources={r"/api/*": {"origins": "*"}})

MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() == "true"

GST_API_URL        = os.getenv("GST_API_URL", "https://apigw.umangapp.in/gstApi/ws1/search")
GST_API_KEY        = os.getenv("GST_API_KEY", "VKE9PnbY5k1ZYapR5PyYQ33I26sXTX569Ed7eqyg")
UMANG_TKN          = os.getenv("UMANG_TKN", "iad1cc7d81-1533-44b0-9967-35599386d3df/2")
UMANG_TRKR         = os.getenv("UMANG_TRKR", "213132")
UMANG_USRID        = os.getenv("UMANG_USRID", "09")
UMANG_DEPTID       = os.getenv("UMANG_DEPTID", "63")
UMANG_SRVID        = os.getenv("UMANG_SRVID", "559")
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
            "gst_returns_get":  "GET /api/gst/returns?gstin=<GSTIN>&fy=<FY>",
            "gst_returns_post": "POST /api/gst/returns  {\"gstin\": \"<GSTIN>\", \"fy\": \"<FY>\"}",
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

    if not GST_API_URL:
        return err("GSTN0099", "GST_API_URL is not configured", 500)


@app.route("/api/gst/returns", methods=["GET", "POST"])
def gst_returns():
    if request.method == "GET":
        gstin = request.args.get("gstin", "").strip().upper()
        fy    = request.args.get("fy", "").strip()
    else:
        body = request.get_json(silent=True) or {}
        gstin = body.get("gstin", "").strip().upper()
        fy    = body.get("fy", "").strip()

    if not gstin:
        return err("GSTN0001", "GSTIN is required")
    valid, msg = validate_gstin(gstin)
    if not valid:
        return err("GSTN0002", msg)
    if not fy:
        return err("GSTN0003", "Financial year (fy) is required, e.g. 2022-2023 or 2022")

    if len(fy) == 4:
        y = int(fy)
        fy = f"{y}-{y+1}"

    try:
        sess = requests.Session()
        sess.headers.update({
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Mobile Safari/537.36",
        })
        init = sess.get("https://services.gst.gov.in/services/searchtp", timeout=REQUEST_TIMEOUT)
        init.raise_for_status()
        logger.info("GST session obtained: %s", init.cookies)

        payload = {"gstin": gstin, "fy": fy}
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://services.gst.gov.in",
            "Referer": "https://services.gst.gov.in/services/searchtp",
            "sec-ch-ua": '"Brave";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": '"Android"',
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }
        resp = sess.post(
            "https://services.gst.gov.in/services/api/search/taxpayerReturnDetails",
            json=payload, headers=headers, timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return jsonify(data), resp.status_code
    except requests.Timeout:
        return err("GSTN0503", "GST portal timed out", 503)
    except requests.ConnectionError:
        return err("GSTN0502", "Cannot connect to GST portal", 502)
    except requests.HTTPError:
        return err(f"GSTN0{resp.status_code}", f"GST portal returned HTTP {resp.status_code}", 502)
    except ValueError:
        return err("GSTN0503", "GST portal returned invalid JSON", 502)
    except Exception as e:
        logger.exception("Unhandled error: %s", e)
        return err("GSTN0500", "Internal server error", 500)

    try:
        headers = {
            "Accept": "application/json", "Content-Type": "application/json",
            "x-api-key": GST_API_KEY,
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36",
            "subsid": "0", "deptid": UMANG_DEPTID, "tenantid": "", "formtrkr": "0",
            "srvid": UMANG_SRVID, "subsid2": "0",
            "origin": "https://web.umang.gov.in",
            "referer": "https://web.umang.gov.in/",
        }
        payload = {
            "tkn": UMANG_TKN, "trkr": UMANG_TRKR, "lang": "en",
            "lat": "21", "lon": "90", "lac": "90", "usag": "90",
            "apitrkr": str(int(time.time())), "usrid": UMANG_USRID,
            "mode": "web", "pltfrm": "android", "did": "123234",
            "deptid": UMANG_DEPTID, "formtrkr": "0", "srvid": UMANG_SRVID,
            "subsid": "0", "subsid2": "0", "trackingId": "",
            "source": "UMANG", "consumerId": "", "partnerCode": "",
            "consumerNumber": "", "gstin": gstin,
        }
        logger.info("Fetching GSTIN: %s via UMANG", gstin)
        upstream = requests.post(GST_API_URL, json=payload, headers=headers, timeout=REQUEST_TIMEOUT, verify=False)
        upstream.raise_for_status()
        data = upstream.json()
        logger.info("UMANG result for %s: %s", gstin, json.dumps(data, indent=2)[:300])
        decoded = data.get("pd", {}).get("decodedData") or data
        return jsonify({"rs": "S", "rc": "GSTN0000", "rd": "Success", "pd": decoded}), upstream.status_code
    except requests.Timeout:
        return err("GSTN0503", "Upstream API timed out", 503)
    except requests.ConnectionError:
        return err("GSTN0502", "Cannot connect to upstream GST API", 502)
    except requests.HTTPError:
        sc = upstream.status_code
        return err(f"GSTN0{sc}", f"Upstream returned HTTP {sc}", 502)
    except ValueError:
        try:
            return err("GSTN0503", f"Upstream returned invalid JSON: {upstream.text[:200]}", 502)
        except:
            return err("GSTN0503", "Upstream returned invalid JSON", 502)
    except Exception as e:
        logger.exception("Unhandled error: %s", e)
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
