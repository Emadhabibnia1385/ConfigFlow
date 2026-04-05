# -*- coding: utf-8 -*-
"""
ConfigFlow Iran Worker  – v1
Runs on the Iran server (same machine or nearby as 3x-ui panel).
Under National Internet (net-melli) conditions.

Responsibilities:
  1. Poll the Bot API for pending jobs
  2. Login to 3x-ui panel and create a client (inbound)
  3. Generate VLESS/VMess link
  4. Post the result back to the Bot API

Config via config.env:
  BOT_API_URL       = http://<foreign-server>:8080
  WORKER_API_KEY    = <shared-secret>
  PANEL_IP          = 127.0.0.1
  PANEL_PORT        = 2053
  PANEL_PATCH       = (optional, e.g. /xui)
  PANEL_USERNAME    = admin
  PANEL_PASSWORD    = yourpassword
  POLL_INTERVAL     = 10   (seconds, default 10)
  INBOUND_ID        = 1    (3x-ui inbound id to add clients to, default 1)
  PROTOCOL          = vless (vless|vmess|trojan, default vless)

Run:
  python worker.py
"""

import os
import re
import sys
import uuid
import json
import time
import http.cookiejar
import urllib.request
import urllib.parse
import urllib.error
import logging
from datetime import datetime, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv("config.env")
    load_dotenv()           # fallback to .env
except ImportError:
    pass  # dotenv optional – use environment variables directly

# ── Configuration ──────────────────────────────────────────────────────────────
BOT_API_URL    = os.getenv("BOT_API_URL", "").rstrip("/")
WORKER_API_KEY = os.getenv("WORKER_API_KEY", "")
PANEL_IP       = os.getenv("PANEL_IP",   "127.0.0.1")
PANEL_PORT     = int(os.getenv("PANEL_PORT", "2053"))
PANEL_PATCH    = os.getenv("PANEL_PATCH",    "").strip("/")
PANEL_USERNAME = os.getenv("PANEL_USERNAME", "")
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "")
POLL_INTERVAL  = int(os.getenv("POLL_INTERVAL", "10"))
INBOUND_ID     = int(os.getenv("INBOUND_ID", "1"))
PROTOCOL       = os.getenv("PROTOCOL", "vless").lower()

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("worker.log", encoding="utf-8"),
    ]
)
log = logging.getLogger("worker")

# ── Validation ─────────────────────────────────────────────────────────────────
def _validate_config():
    errors = []
    if not BOT_API_URL:
        errors.append("BOT_API_URL not set")
    if not WORKER_API_KEY:
        errors.append("WORKER_API_KEY not set")
    if not PANEL_USERNAME:
        errors.append("PANEL_USERNAME not set")
    if not PANEL_PASSWORD:
        errors.append("PANEL_PASSWORD not set")
    if errors:
        for e in errors:
            log.error("Config error: %s", e)
        sys.exit(1)

# ── 3x-ui Client ──────────────────────────────────────────────────────────────
class XuiClient:
    """Minimal 3x-ui REST client with session management."""

    def __init__(self, ip, port, patch, username, password):
        self.base = f"http://{ip}:{port}"
        if patch:
            self.base += f"/{patch.strip('/')}"
        self.username = username
        self.password = password
        self._cookie_jar = http.cookiejar.CookieJar()
        self._opener     = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cookie_jar)
        )
        self._logged_in = False
        self._login_at  = None

    def _session_valid(self):
        if not self._logged_in or not self._login_at:
            return False
        # Sessions expire after ~50 minutes (3x-ui default is 1h)
        return (datetime.now() - self._login_at).total_seconds() < 3000

    def login(self):
        url     = f"{self.base}/login"
        payload = urllib.parse.urlencode({
            "username": self.username,
            "password": self.password,
        }).encode()
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/x-www-form-urlencoded",
                                              "User-Agent": "ConfigFlow-Worker/1.0"})
        try:
            with self._opener.open(req, timeout=15) as resp:
                body = json.loads(resp.read().decode("utf-8", errors="replace"))
            if body.get("success"):
                self._logged_in = True
                self._login_at  = datetime.now()
                log.info("3x-ui login OK")
                return True
            log.error("3x-ui login failed: %s", body.get("msg"))
            return False
        except Exception as e:
            log.error("3x-ui login exception: %s", e)
            return False

    def ensure_session(self):
        if not self._session_valid():
            return self.login()
        return True

    def _get(self, path):
        if not self.ensure_session():
            raise RuntimeError("Cannot login to 3x-ui")
        url = f"{self.base}{path}"
        req = urllib.request.Request(url, headers={"User-Agent": "ConfigFlow-Worker/1.0"})
        with self._opener.open(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))

    def _post(self, path, payload_dict):
        if not self.ensure_session():
            raise RuntimeError("Cannot login to 3x-ui")
        url     = f"{self.base}{path}"
        payload = json.dumps(payload_dict).encode("utf-8")
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "ConfigFlow-Worker/1.0"})
        with self._opener.open(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))

    def list_inbounds(self):
        return self._get("/xui/API/inbounds")

    def get_inbound(self, inbound_id):
        data = self._get(f"/xui/API/inbounds/get/{inbound_id}")
        if data.get("success"):
            return data.get("obj")
        raise RuntimeError(f"Cannot get inbound {inbound_id}: {data.get('msg')}")

    def add_client(self, inbound_id, client_json_str):
        """Add client to an inbound. client_json_str is the JSON string of client settings."""
        return self._post(
            f"/xui/API/inbounds/addClient",
            {"id": inbound_id, "settings": client_json_str}
        )


def _build_client_json(client_uuid, pkg_name, volume_gb, duration_days):
    """Build the 3x-ui client settings JSON string."""
    expire_ts = int((datetime.now() + timedelta(days=duration_days)).timestamp() * 1000)
    total_bytes = volume_gb * 1024 ** 3
    client = {
        "id":         client_uuid,
        "flow":       "",
        "email":      _safe_email(pkg_name, client_uuid),
        "limitIp":    0,
        "totalGB":    total_bytes,
        "expiryTime": expire_ts,
        "enable":     True,
        "tgId":       "",
        "subId":      "",
    }
    return json.dumps({"clients": [client]})


def _safe_email(pkg_name, client_uuid):
    """Generate a safe email tag for 3x-ui (letters/digits/hyphens only)."""
    safe = re.sub(r"[^a-zA-Z0-9\-]", "", pkg_name.replace(" ", "-"))[:20]
    short_uuid = client_uuid.replace("-", "")[:8]
    return f"{safe}-{short_uuid}".lower()


def _build_vless_link(client_uuid, ip, port, pkg_name, inbound):
    """Construct a VLESS link from inbound settings."""
    try:
        stream_settings = json.loads(inbound.get("streamSettings") or "{}")
    except (json.JSONDecodeError, TypeError):
        stream_settings = {}
    network = stream_settings.get("network", "tcp")
    security = stream_settings.get("security", "none")
    params = {
        "type": network,
        "security": security,
    }
    if network == "ws":
        ws_settings = stream_settings.get("wsSettings", {})
        params["path"] = ws_settings.get("path", "/")
        host = (ws_settings.get("headers") or {}).get("Host", ip)
        params["host"] = host
    if security == "tls":
        tls_settings = stream_settings.get("tlsSettings", {})
        sni = tls_settings.get("serverName", ip)
        params["sni"] = sni
        params["fp"] = "chrome"
    encoded_params = urllib.parse.urlencode(params)
    safe_name = urllib.parse.quote(pkg_name)
    return f"vless://{client_uuid}@{ip}:{port}?{encoded_params}#{safe_name}"


# ── Bot API helpers ────────────────────────────────────────────────────────────
def _api_request(method, path, body=None):
    url = f"{BOT_API_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(
        url, data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "X-API-Key": WORKER_API_KEY,
            "User-Agent": "ConfigFlow-Worker/1.0",
        }
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def fetch_pending_jobs():
    return _api_request("GET", "/jobs/pending").get("jobs", [])


def mark_job_processing(job_id):
    return _api_request("POST", f"/jobs/{job_id}/start")


def post_job_result(job_id, result_config, result_link):
    return _api_request("POST", f"/jobs/{job_id}/result", {
        "result_config": result_config,
        "result_link": result_link,
    })


def post_job_error(job_id, error_msg):
    return _api_request("POST", f"/jobs/{job_id}/error", {"error": error_msg})


# ── Job processor ──────────────────────────────────────────────────────────────
def process_job(job, xui):
    job_id   = job["id"]
    job_uuid = job["job_uuid"]
    pkg_name = job["pkg_name"]
    vol_gb   = job["volume_gb"]
    dur_days = job["duration_days"]
    panel_ip = job.get("ip", PANEL_IP)
    panel_port = job.get("port", PANEL_PORT)
    inbound_id = int(job.get("inbound_id") or INBOUND_ID)

    log.info("Processing job #%d  uuid=%s  pkg=%s %dGB/%dd  inbound#%d",
             job_id, job_uuid[:8], pkg_name, vol_gb, dur_days, inbound_id)

    try:
        mark_job_processing(job_id)
    except Exception as e:
        log.warning("Could not mark job #%d as processing: %s", job_id, e)

    try:
        client_uuid = str(uuid.uuid4())
        client_json = _build_client_json(client_uuid, pkg_name, vol_gb, dur_days)

        resp = xui.add_client(inbound_id, client_json)
        if not resp.get("success"):
            raise RuntimeError(f"3x-ui addClient failed: {resp.get('msg', resp)}")

        # Build delivery link
        try:
            inbound = xui.get_inbound(inbound_id)
        except Exception:
            inbound = {}

        link = _build_vless_link(client_uuid, panel_ip, panel_port, pkg_name, inbound)

        post_job_result(job_id, client_uuid, link)
        log.info("Job #%d done  link=%s…", job_id, link[:60])

    except Exception as e:
        err = str(e)[:400]
        log.error("Job #%d failed: %s", job_id, err)
        try:
            post_job_error(job_id, err)
        except Exception as e2:
            log.error("Could not post error for job #%d: %s", job_id, e2)


# ── Main polling loop ──────────────────────────────────────────────────────────
def main():
    _validate_config()

    log.info("ConfigFlow Iran Worker starting...")
    log.info(" Bot API: %s", BOT_API_URL)
    log.info(" Panel:   %s:%d  (inbound #%d)", PANEL_IP, PANEL_PORT, INBOUND_ID)
    log.info(" Protocol: %s | Poll: every %ds", PROTOCOL, POLL_INTERVAL)

    xui = XuiClient(PANEL_IP, PANEL_PORT, PANEL_PATCH, PANEL_USERNAME, PANEL_PASSWORD)

    # Initial login
    if not xui.login():
        log.error("Initial login to 3x-ui failed — will retry in the loop")

    consecutive_errors = 0
    while True:
        try:
            jobs = fetch_pending_jobs()
            if jobs:
                log.info("Fetched %d pending job(s)", len(jobs))
                for job in jobs:
                    process_job(job, xui)
            else:
                log.debug("No pending jobs")
            consecutive_errors = 0

        except urllib.error.URLError as e:
            consecutive_errors += 1
            log.warning("Network error reaching Bot API (#%d): %s", consecutive_errors, e)
            if consecutive_errors >= 5:
                log.error("5+ consecutive API failures — sleeping 60s before retry")
                time.sleep(60)
                consecutive_errors = 0

        except Exception as e:
            consecutive_errors += 1
            log.error("Unexpected error in poll loop: %s", e)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
