"""
YouTube Clipper Bot — Flask Backend
Receives clip requests from Make.com, processes with yt-dlp + ffmpeg,
sends result file directly back to the user via Telegram Bot API.
"""

from flask import Flask, request, jsonify
import yt_dlp
import subprocess
import os
import uuid
import threading
import requests
import re
import time
import logging

# ── Setup ──────────────────────────────────────────────────────────────────────
app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# In-memory job store (use Redis for multi-worker production)
JOBS: dict[str, dict] = {}


# ── Helpers ────────────────────────────────────────────────────────────────────

def time_to_seconds(t: str) -> float:
    """Convert HH:MM:SS, MM:SS, or raw seconds string to float seconds."""
    t = str(t).strip()
    if re.match(r"^\d+(\.\d+)?$", t):
        return float(t)
    parts = t.split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    raise ValueError(f"Invalid time format: '{t}'. Use HH:MM:SS, MM:SS, or seconds.")


def progress_bar(step: int, total: int = 4, width: int = 18) -> str:
    filled = int(width * step / total)
    bar = "█" * filled + "░" * (width - filled)
    pct = int(100 * step / total)
    return f"`[{bar}] {pct}%`"


def tg_send(bot_token: str, chat_id: str, text: str) -> dict:
    """Send a Telegram message and return the full API response."""
    r = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=15,
    )
    return r.json()


def tg_edit(bot_token: str, chat_id: str, message_id: int, text: str):
    """Edit an existing Telegram message (used for live progress updates)."""
    requests.post(
        f"https://api.telegram.org/bot{bot_token}/editMessageText",
        json={
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "Markdown",
        },
        timeout=15,
    )


def tg_send_document(bot_token: str, chat_id: str, file_path: str, caption: str = ""):
    """Upload a file to a Telegram chat."""
    with open(file_path, "rb") as fh:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendDocument",
            data={"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown"},
            files={"document": fh},
            timeout=120,
        )


def safe_filename(name: str) -> str:
    return re.sub(r"[^\w\-_.]", "_", name).strip("_") or "clip"


# ── Core clip worker ───────────────────────────────────────────────────────────

def process_clip(
    job_id: str,
    url: str,
    start: str,
    end: str,
    filename: str,
    fmt: str,          # "mp4" or "mp3"
    bot_token: str,
    chat_id: str,
    status_msg_id: int,
):
    job = JOBS[job_id]

    try:
        start_sec = time_to_seconds(start)
        end_sec   = time_to_seconds(end)
        duration  = end_sec - start_sec

        if duration <= 0:
            raise ValueError("End time must be after start time.")
        if duration > 3600:
            raise ValueError("Clip duration exceeds 1 hour limit.")

        # ── Step 1 / 4 — Download ──────────────────────────────────────────────
        tg_edit(bot_token, chat_id, status_msg_id,
            f"📥 *Step 1/4 — Downloading video…*\n{progress_bar(1)}")

        temp_base = os.path.join(DOWNLOAD_DIR, f"{job_id}_src")

        ydl_opts = {
            "format": (
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
                if fmt == "mp4"
                else "bestaudio/best"
            ),
            "outtmpl":      f"{temp_base}.%(ext)s",
            "quiet":        True,
            "no_warnings":  True,
            "merge_output_format": "mp4",
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_title = info.get("title", "video")

        # Find the downloaded file (yt-dlp may choose various extensions)
        src_file = None
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(f"{job_id}_src"):
                src_file = os.path.join(DOWNLOAD_DIR, f)
                break

        if not src_file or not os.path.exists(src_file):
            raise FileNotFoundError("Downloaded source file not found.")

        # ── Step 2 / 4 — Cut ──────────────────────────────────────────────────
        tg_edit(bot_token, chat_id, status_msg_id,
            f"✂️ *Step 2/4 — Cutting clip…*\n{progress_bar(2)}")

        out_name = safe_filename(filename) if filename else f"clip_{job_id[:8]}"
        out_ext  = "mp3" if fmt == "mp3" else "mp4"
        out_file = os.path.join(DOWNLOAD_DIR, f"{out_name}.{out_ext}")

        if fmt == "mp3":
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(start_sec),
                "-i", src_file,
                "-t", str(duration),
                "-vn",
                "-acodec", "libmp3lame",
                "-q:a", "2",
                out_file,
            ]
        else:
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(start_sec),
                "-i", src_file,
                "-t", str(duration),
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                "-c:a", "aac",
                "-b:a", "128k",
                "-movflags", "+faststart",
                out_file,
            ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg error:\n{result.stderr[-500:]}")

        # Clean up source
        os.remove(src_file)

        # ── Step 3 / 4 — Upload ───────────────────────────────────────────────
        tg_edit(bot_token, chat_id, status_msg_id,
            f"📤 *Step 3/4 — Uploading to Telegram…*\n{progress_bar(3)}")

        caption = (
            f"✅ *{out_name}.{out_ext}*\n"
            f"📹 _{video_title}_\n"
            f"⏱ `{start}` → `{end}` ({int(duration)}s)"
        )
        tg_send_document(bot_token, chat_id, out_file, caption)

        # ── Step 4 / 4 — Done ─────────────────────────────────────────────────
        tg_edit(bot_token, chat_id, status_msg_id,
            f"✅ *Done!* Your clip has been sent.\n{progress_bar(4)}")

        job["status"] = "done"
        log.info(f"Job {job_id} completed: {out_name}.{out_ext}")

        # Auto-cleanup after 60 s
        time.sleep(60)
        if os.path.exists(out_file):
            os.remove(out_file)

    except Exception as exc:
        log.error(f"Job {job_id} failed: {exc}")
        job["status"] = "error"
        job["error"]  = str(exc)
        tg_edit(bot_token, chat_id, status_msg_id,
            f"❌ *Error processing your clip:*\n`{str(exc)}`\n\nPlease check your URL and timestamps.")


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service": "YouTube Clipper Bot",
        "status": "running",
        "endpoints": {
            "POST /clip":          "Submit a clip job",
            "GET  /status/<id>":   "Check job status",
            "GET  /health":        "Health check"
        },
        "usage": "Send /clip <url> <start> <end> [filename] [mp3] to your Telegram bot"
    })


@app.route("/clip", methods=["POST"])
def clip():
    """
    POST /clip
    Body (JSON):
      {
        "url":       "https://youtu.be/...",
        "start":     "1:30",          // HH:MM:SS, MM:SS, or seconds
        "end":       "2:45",
        "filename":  "my_clip",       // optional, no extension
        "format":    "mp4",           // "mp4" | "mp3" (default: mp4)
        "bot_token": "123456:ABC...",
        "chat_id":   "987654321"
      }
    Returns: { "job_id": "...", "status": "processing" }
    """
    data = request.get_json(silent=True) or {}

    missing = [f for f in ("url", "start", "end", "bot_token", "chat_id") if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    fmt = data.get("format", "mp4").lower()
    if fmt not in ("mp4", "mp3"):
        return jsonify({"error": "format must be 'mp4' or 'mp3'"}), 400

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "processing"}

    # Send initial status message to user — we'll edit it in-place for progress
    resp     = tg_send(data["bot_token"], data["chat_id"],
                       f"🎬 *Processing your clip…*\n{progress_bar(0)}")
    msg_id   = resp.get("result", {}).get("message_id")

    if not msg_id:
        return jsonify({"error": "Failed to contact Telegram. Check bot_token and chat_id."}), 502

    thread = threading.Thread(
        target=process_clip,
        args=(
            job_id,
            data["url"],
            data["start"],
            data["end"],
            data.get("filename", ""),
            fmt,
            data["bot_token"],
            data["chat_id"],
            msg_id,
        ),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id, "status": "processing"})


@app.route("/status/<job_id>", methods=["GET"])
def status(job_id: str):
    """GET /status/<job_id> — poll job status."""
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "jobs": len(JOBS)})


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    log.info(f"Starting YT Clipper API on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
