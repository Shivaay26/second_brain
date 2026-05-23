# 🧠 Second Brain Bot

A Telegram-based AI assistant powered by Google's Gemini SDK, acting as your personal **"Second Brain."** It ingests messages, files, and YouTube audio, processes them through Gemini, and stores context in a local Qdrant vector database for long-term memory and retrieval.

---

# 🏗️ Architecture & Data Flow

1. **Input Layer (Telegram)**  
   The user interacts with the bot via Telegram using `python-telegram-bot`. Inputs can include:
   - Text messages
   - Screenshots
   - YouTube or Instagrams URLs

2. **Processing & Extraction**  
   If a YouTube/Insta link is detected, `yt-dlp` (backed by `ffmpeg`) extracts the best available audio stream directly to local storage.

3. **Memory & Context (Qdrant)**  
   User inputs and processed data are embedded and stored locally using `Qdrant` in file-mode.  
   This enables:
   - Semantic search
   - Long-term memory
   - Retrieval-Augmented Generation (RAG)

4. **AI Processing (Gemini)**  
   Retrieved context, system prompts, and local audio files are sent to Google's Gemini model via the official `google-genai` SDK.

5. **Output Layer**  
   Gemini's response is formatted and sent back to the user through Telegram. Temporary files (such as downloaded audio tracks) are automatically cleaned up afterward.

---

# 🛠️ Tech Stack

- **Language:** Python 3.10+
- **Bot Framework:** `python-telegram-bot`
- **AI Model:** Google Gemini (`google-genai`)
- **Vector Database:** Qdrant (`qdrant-client` local mode)
- **Media Processing:** `yt-dlp` + `ffmpeg`

---

# 🚀 Local Setup & Installation

## Prerequisites

You will need:

- **Python 3.10+**
- **FFmpeg**

### Install Dependencies

#### Ubuntu / Debian
```bash
sudo apt install python3 python3-venv ffmpeg
```

#### Mac
```bash
brew install ffmpeg
```

#### Windows
Download FFmpeg and add it to your system `PATH`.

---

## 1. Clone the Repository

```bash
git clone https://github.com/Shivaay26/second_brain.git
cd second_brain
```

---

## 2. Set Up the Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### On Windows
```bash
venv\Scripts\activate
```

---

## 3. Install Dependencies

```bash
pip install -r requirements.txt
```

> **Note:**  
> If you are on an ARM64 machine (such as Oracle Cloud OCI ARM instances), ensure `numpy` is listed **without a strict version pin** in `requirements.txt` to avoid architecture compatibility issues.

---

## 4. Environment Variables

Create a `.env` file in the project root:

```env
bot_token="Your telegram bot token"

gemini_api_key="Your gemini api key"

notion_token="Notion integration token"

tasks_db_id="Notion tasks db id"

daily_log_db_id="Notion daily log db id"

content_vault_db_id="Notion content vault db id"
```

---

## 5. Run the Bot

```bash
python main.py
```

---

# ☁️ Production Deployment (Linux / systemd)

To keep the bot running 24/7 on a cloud server (such as Oracle Cloud OCI) and survive reboots, deploy it as a `systemd` service.

---

## Create the Service File

```bash
sudo nano /etc/systemd/system/second-brain.service
```

---

## Paste the Following Configuration

```ini
[Unit]
Description=Second Brain Gemini Application
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/app
EnvironmentFile=/home/ubuntu/app/.env
ExecStart=/home/ubuntu/app/venv/bin/python main.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

---

## Enable and Start the Service

```bash
sudo systemctl daemon-reload
sudo systemctl enable second-brain.service
sudo systemctl start second-brain.service
```

---

# 🖥️ Server Management Commands

## View Live Logs

```bash
sudo journalctl -u second-brain.service -f
```

## Restart Bot (After `git pull`)

```bash
sudo systemctl restart second-brain.service
```

## Stop Bot

```bash
sudo systemctl stop second-brain.service
```

---

# ⚠️ Important Notes

## Database Locking

Qdrant runs in **local file mode**, which places a strict lock on the database directory.

This means you **cannot** run:

```bash
python main.py
```

manually while the `systemd` background service is active, or you will encounter:

```text
Resource temporarily unavailable
```

If you need to run debugging scripts manually, stop the background service first:

```bash
sudo systemctl stop second_brain.service
```

---

# 📌 Features

- Telegram-based AI assistant
- Long-term vector memory using Qdrant
- Retrieval-Augmented Generation (RAG)
- YouTube audio ingestion
- Gemini-powered contextual responses
- Automatic temporary file cleanup
- Local-first architecture
- Production-ready deployment via `systemd`

---

# 📄 License

This project is open-source and available under the MIT License.