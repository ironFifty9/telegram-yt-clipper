# 🎬 YouTube Clipper Bot

A hybrid **Telegram Bot + Make.com** automation that lets users cut YouTube videos into MP4/MP3 clips — right from Telegram.

---

## Architecture

```
User (Telegram)
     │  /clip <url> <start> <end> [filename] [mp3]
     ▼
Telegram Bot
     │  webhook (new message event)
     ▼
Make.com Scenario
     │  parses message → HTTP POST
     ▼
Flask API (your server)
     │  yt-dlp downloads, ffmpeg cuts
     ▼
Telegram Bot API
     │  sends file directly to user
     ▼
User receives clip ✅
```

---

## 1 · Deploy the Backend

### Option A — Railway (Recommended, free tier available)

1. Push this folder to a GitHub repo
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Railway auto-detects `railway.toml` and installs `ffmpeg`
4. Note your public URL: `https://your-app.up.railway.app`

### Option B — Render

1. Push to GitHub
2. New Web Service → connect repo
3. **Build Command:** `pip install -r requirements.txt`
4. **Start Command:** `gunicorn app:app --workers 4 --timeout 300 --bind 0.0.0.0:$PORT`
5. Add environment variable: `NIXPACKS_APT_PKGS=ffmpeg`  
   *(Render: use the shell to run `apt-get install -y ffmpeg` via a build script)*

### Option C — Local (with ngrok for testing)

```bash
# Install system deps
sudo apt install ffmpeg          # Linux
brew install ffmpeg              # macOS

# Install Python deps
pip install -r requirements.txt

# Run server
python app.py

# Expose via ngrok (new terminal)
ngrok http 5000
# Copy the https://xxxx.ngrok.io URL
```

---

## 2 · Create the Telegram Bot

1. Open Telegram → search **@BotFather** → `/newbot`
2. Follow prompts, choose a name and username
3. Copy the **Bot Token** (looks like `123456789:ABCdef...`)
4. Optionally set bot commands via BotFather:
   ```
   /setcommands
   clip - Cut a YouTube video clip
   start - Show usage instructions
   help - Show help
   ```

---

## 3 · Set Up Make.com Scenario

### Import the scenario manually:

Create a new scenario with these **5 modules** in order:

---

#### Module 1 — Telegram Bot: Watch Updates
- **Connection:** Add your bot token
- **Update Types:** `message`

---

#### Module 2 — Text Parser: Parse with Regex
- **Text:** `{{1.message.text}}`
- **Pattern:**
  ```
  ^\/clip\s+(https?:\/\/\S+)\s+(\S+)\s+(\S+)(?:\s+(\S+?))?(?:\s+(mp3|mp4))?$
  ```
- This captures:
  - Group 1 → YouTube URL
  - Group 2 → Start time
  - Group 3 → End time
  - Group 4 → Filename (optional)
  - Group 5 → Format `mp3` or `mp4` (optional)

---

#### Module 3 — Router (Add a filter)
- Add a **Filter** before HTTP module:
  - Condition: `{{1.message.text}}` **Contains** `/clip`
  - This ignores non-clip messages

---

#### Module 4 — HTTP: Make a Request
- **URL:** `https://your-app.up.railway.app/clip`
- **Method:** `POST`
- **Body type:** `Raw`
- **Content type:** `application/json`
- **Body:**
  ```json
  {
    "url":       "{{2.group[].value[1]}}",
    "start":     "{{2.group[].value[2]}}",
    "end":       "{{2.group[].value[3]}}",
    "filename":  "{{2.group[].value[4]}}",
    "format":    "{{if(2.group[].value[5]; 2.group[].value[5]; \"mp4\")}}",
    "bot_token": "YOUR_BOT_TOKEN_HERE",
    "chat_id":   "{{1.message.chat.id}}"
  }
  ```
  > ⚠️ Replace `YOUR_BOT_TOKEN_HERE` with your actual bot token.

---

#### Module 5 — (Optional) Telegram: Send a Message
- Send a quick "⏳ Got it! Processing your clip…" reply while the backend works
- **Chat ID:** `{{1.message.chat.id}}`
- **Text:** `⏳ Got it! Processing your clip…`
- Place this **before** Module 4 so users get instant feedback

---

### Webhook vs. Polling
- By default Make.com **polls** Telegram (every 15 min on free plan)
- For instant responses, set a **Telegram webhook** pointing to Make.com:
  1. In Make.com, use module **Telegram Bot: Custom API Call**
  2. Call `setWebhook` with your Make.com webhook URL
  - Or use curl:
    ```bash
    curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=<MAKE_WEBHOOK_URL>"
    ```

---

## 4 · Using the Bot

Send commands in this format:

```
/clip <youtube_url> <start> <end> [filename] [mp3]
```

### Examples

```bash
# Basic MP4 clip
/clip https://youtu.be/dQw4w9WgXcQ 0:30 1:45

# With custom filename
/clip https://youtu.be/dQw4w9WgXcQ 0:30 1:45 my_clip

# MP3 audio export
/clip https://youtu.be/dQw4w9WgXcQ 0:30 1:45 my_audio mp3

# Using hour:min:sec format
/clip https://youtu.be/dQw4w9WgXcQ 1:02:30 1:05:00 highlight_reel
```

### Time formats accepted
| Format | Example | Meaning |
|--------|---------|---------|
| `MM:SS` | `1:30` | 1 min 30 sec |
| `HH:MM:SS` | `1:02:30` | 1 hr 2 min 30 sec |
| Seconds | `90` | 90 seconds |

---

## 5 · API Reference

### `POST /clip`
```json
{
  "url":       "https://youtu.be/...",
  "start":     "1:30",
  "end":       "2:45",
  "filename":  "my_clip",
  "format":    "mp4",
  "bot_token": "123456:ABC...",
  "chat_id":   "987654321"
}
```
**Response:** `{ "job_id": "uuid", "status": "processing" }`

### `GET /status/<job_id>`
**Response:** `{ "status": "processing" | "done" | "error" }`

### `GET /health`
**Response:** `{ "status": "ok", "jobs": 5 }`

---

## 6 · Limits & Notes

| Limit | Value |
|-------|-------|
| Max clip duration | 60 minutes |
| Max Telegram file size | 50 MB (bot API limit) |
| Supported URLs | Any yt-dlp compatible source |

> **Telegram file size limit:** Files over 50 MB cannot be sent via the Bot API. For longer clips, consider integrating with Google Drive or Dropbox upload as an alternative delivery method.

---

## 7 · Troubleshooting

| Problem | Fix |
|---------|-----|
| `ffmpeg not found` | Install ffmpeg: `apt install ffmpeg` or `brew install ffmpeg` |
| `yt-dlp error: Video unavailable` | Video may be geo-restricted or private |
| `Failed to contact Telegram` | Double-check your bot token and chat ID |
| Make.com regex not matching | Test your regex at [regex101.com](https://regex101.com) |
| File too large | Shorten the clip or use MP3 format |
# telegram-yt-clipper
