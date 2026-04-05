# -*- coding: utf-8 -*-
"""
ConfigFlow Worker API Server
Runs on the foreign (non-Iran) server alongside bot.py.
The Iran Worker polls this API to receive jobs and post results.

Run standalone:  python api.py
Or started automatically by bot.py when worker_api_enabled=1.
"""

import os
import json
import sqlite3
import functools
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

try:
    from flask import Flask, request, jsonify
except ImportError:
    raise SystemExit("Flask is required: pip install flask")

DB_NAME  = os.getenv("DB_NAME", "configflow.db")
API_PORT = int(os.getenv("WORKER_API_PORT", "8080"))

app = Flask(__name__)


# ── DB helpers (read-only wrappers, separate connection per request) ───────────
def _conn():
    c = sqlite3.connect(DB_NAME, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c

def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _get_api_key():
    """Read API key live from DB (so changes take effect without restart)."""
    with _conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key='worker_api_key'").fetchone()
    return (row["value"] or "").strip() if row else ""


def _api_enabled():
    with _conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key='worker_api_enabled'").fetchone()
    return row and row["value"] == "1"


# ── Auth decorator ─────────────────────────────────────────────────────────────
def require_api_key(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not _api_enabled():
            return jsonify({"error": "API disabled"}), 503
        expected = _get_api_key()
        if not expected:
            return jsonify({"error": "API key not configured on server"}), 503
        provided = request.headers.get("X-API-Key", "")
        if provided != expected:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "ConfigFlow Worker API"})


@app.route("/jobs/pending", methods=["GET"])
@require_api_key
def get_pending_jobs():
    """Return up to 20 pending/failed jobs with panel credentials."""
    with _conn() as c:
        rows = c.execute(
            "SELECT j.id, j.job_uuid, j.user_id, j.panel_id, j.panel_package_id,"
            " j.status, j.retry_count, j.created_at,"
            " p.ip, p.port, p.patch, p.username, p.password,"
            " pp.name AS pkg_name, pp.volume_gb, pp.duration_days, pp.inbound_id"
            " FROM xui_jobs j"
            " JOIN panels p  ON p.id=j.panel_id"
            " JOIN panel_packages pp ON pp.id=j.panel_package_id"
            " WHERE j.status IN ('pending','failed') AND j.retry_count < 5"
            "   AND p.is_active=1"
            " ORDER BY j.created_at ASC LIMIT 20"
        ).fetchall()
    jobs = [dict(r) for r in rows]
    return jsonify({"jobs": jobs})


@app.route("/jobs/<int:job_id>/start", methods=["POST"])
@require_api_key
def start_job(job_id):
    """Mark job as 'processing'."""
    with _conn() as c:
        row = c.execute("SELECT * FROM xui_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return jsonify({"error": "Job not found"}), 404
        if row["status"] not in ("pending", "failed"):
            return jsonify({"error": "Job not in actionable state", "status": row["status"]}), 409
        c.execute(
            "UPDATE xui_jobs SET status='processing', updated_at=? WHERE id=?",
            (_now(), job_id)
        )
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/jobs/<int:job_id>/result", methods=["POST"])
@require_api_key
def post_result(job_id):
    """Worker posts the generated config + link after successful 3x-ui client creation."""
    data = request.get_json(silent=True) or {}
    result_config = (data.get("result_config") or "").strip()
    result_link   = (data.get("result_link") or "").strip()
    if not result_config and not result_link:
        return jsonify({"error": "result_config or result_link required"}), 400

    with _conn() as c:
        row = c.execute("SELECT * FROM xui_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return jsonify({"error": "Job not found"}), 404
        c.execute(
            "UPDATE xui_jobs SET status='done', result_config=?, result_link=?,"
            " error_msg=NULL, updated_at=? WHERE id=?",
            (result_config, result_link, _now(), job_id)
        )
        user_id = row["user_id"]

    # Attempt to notify the user via bot (fire-and-forget, best effort)
    _notify_user_job_done(job_id, user_id, result_config, result_link)
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/jobs/<int:job_id>/error", methods=["POST"])
@require_api_key
def post_error(job_id):
    """Worker reports a failure so the job can be retried."""
    data = request.get_json(silent=True) or {}
    error_msg = (data.get("error") or "Unknown error")[:500]

    with _conn() as c:
        row = c.execute("SELECT * FROM xui_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return jsonify({"error": "Job not found"}), 404
        new_retry = (row["retry_count"] or 0) + 1
        new_status = "failed" if new_retry < 5 else "error"
        c.execute(
            "UPDATE xui_jobs SET status=?, error_msg=?, retry_count=?, updated_at=?"
            " WHERE id=?",
            (new_status, error_msg, new_retry, _now(), job_id)
        )
    return jsonify({"ok": True, "job_id": job_id, "retry_count": new_retry})


@app.route("/jobs/<int:job_id>", methods=["GET"])
@require_api_key
def get_job(job_id):
    """Get job status (for polling from bot or worker)."""
    with _conn() as c:
        row = c.execute("SELECT * FROM xui_jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(dict(row))


# ── Internal: Deliver config to Telegram user ─────────────────────────────────
def _notify_user_job_done(job_id, user_id, result_config, result_link):
    """Best-effort: send the config to the user via Telegram bot."""
    try:
        import telebot
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            return
        b = telebot.TeleBot(token, parse_mode="HTML")

        with _conn() as c:
            job = c.execute(
                "SELECT j.*, pp.name AS pkg_name, pp.volume_gb, pp.duration_days, p.name AS panel_name"
                " FROM xui_jobs j"
                " JOIN panel_packages pp ON pp.id=j.panel_package_id"
                " JOIN panels p ON p.id=j.panel_id"
                " WHERE j.id=?", (job_id,)
            ).fetchone()

        if not job:
            return

        import html as html_mod, io
        esc_fn = lambda t: html_mod.escape(str(t or ""))

        text = (
            "✅ <b>کانفیگ شما آماده است</b>\n\n"
            f"📦 Package: <b>{esc_fn(job['pkg_name'])}</b>\n"
            f"🖥 Panel: <b>{esc_fn(job['panel_name'])}</b>\n"
            f"🔋 Volume: <b>{job['volume_gb']} GB</b>\n"
            f"⏰ Duration: <b>{job['duration_days']} days</b>\n\n"
            f"🔗 Config:\n<code>{esc_fn(result_config)}</code>"
        )
        if result_link:
            text += f"\n\n🌐 Link: {esc_fn(result_link)}"

        import qrcode
        qr_img = qrcode.make(result_config or result_link)
        bio = io.BytesIO()
        qr_img.save(bio, format="PNG")
        bio.seek(0)
        bio.name = "qrcode.png"
        b.send_photo(user_id, bio, caption=text, parse_mode="HTML")
    except Exception:
        pass


if __name__ == "__main__":
    print(f"✅ ConfigFlow Worker API starting on port {API_PORT}...")
    app.run(host="0.0.0.0", port=API_PORT, use_reloader=False)
