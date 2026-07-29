from flask import Flask, request, jsonify
from flask_cors import CORS
import time
import hashlib
import logging
import os

from utils.extractor import MediaExtractor
from utils.cache import SimpleCache
from utils.rate_limiter import RateLimiter

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

extractor    = MediaExtractor()
cache        = SimpleCache(max_size=500, ttl_hours=6)
rate_limiter = RateLimiter(max_requests_per_minute=30, max_requests_per_hour=200)

stats = {
    "start_time":             time.time(),
    "total_requests":         0,
    "successful_extractions": 0,
    "failed_extractions":     0,
    "cache_hits":             0,
}

@app.route("/", methods=["GET"])
def home():
    uptime = int(time.time() - stats["start_time"])
    return jsonify({
        "status":  "running",
        "name":    "Media Saver Backend",
        "version": "2.0.0",
        "engine":  "yt-dlp",
        "uptime":  f"{uptime // 3600}h {(uptime % 3600) // 60}m",
        "stats":   stats,
    })

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":     "healthy",
        "timestamp":  int(time.time()),
        "cache_size": cache.size(),
        "version":    extractor.get_version()
    })

@app.route("/platforms", methods=["GET"])
def platforms():
    return jsonify({
        "status":    "success",
        "platforms": extractor.get_supported_platforms()
    })

@app.route("/extract", methods=["POST"])
def extract_media():
    stats["total_requests"] += 1

    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    if not rate_limiter.allow_request(client_ip):
        return jsonify({
            "status":      "error",
            "error":       "rate_limit_exceeded",
            "message":     "عدد الطلبات كثير جداً",
            "retry_after": rate_limiter.get_retry_after(client_ip)
        }), 429

    data = request.get_json()
    if not data or "url" not in data:
        return jsonify({
            "status":  "error",
            "error":   "missing_url",
            "message": "الرجاء إرسال الرابط في حقل url"
        }), 400

    url = data["url"].strip()
    start_time = data.get("start_time")
    end_time = data.get("end_time")
    
    if not url.startswith(("http://", "https://")):
        return jsonify({
            "status":  "error",
            "error":   "invalid_url",
            "message": "الرابط غير صالح"
        }), 400

    cache_key    = hashlib.md5(url.encode()).hexdigest()
    cached       = cache.get(cache_key)
    if cached:
        stats["cache_hits"] += 1
        cached["from_cache"] = True
        return jsonify({"status": "success", "data": cached})

    try:
        result = extractor.extract(url, start_time=start_time, end_time=end_time)
        # لا نحفظ في Cache إذا كان مقصوص
        if not start_time and not end_time:
            cache.put(cache_key, result)
        stats["successful_extractions"] += 1
        result["from_cache"] = False
        return jsonify({"status": "success", "data": result})

    except Exception as e:
        stats["failed_extractions"] += 1
        error_msg  = str(e)
        error_type = _classify_error(error_msg)
        logger.error(f"Extraction failed: {error_msg}")
        return jsonify({
            "status":  "error",
            "error":   error_type,
            "message": error_msg
        }), 400

def _classify_error(error: str) -> str:
    e = error.lower()
    if "private"   in e or "login"   in e: return "private_content"
    if "not found" in e or "404"     in e: return "not_found"
    if "geo"       in e or "country" in e: return "geo_restricted"
    if "copyright" in e or "removed" in e: return "copyright"
    if "age"       in e:                   return "age_restricted"
    if "live"      in e:                   return "live_stream"
    if "unsupported" in e:                 return "unsupported_platform"
    return "extraction_failed"

if __name__ == "__main__":
    port  = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)