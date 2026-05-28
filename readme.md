# Second Brain Bot

A Telegram-based personal knowledge assistant that captures messages, images, voice notes, audio, and useful links, then routes them into Notion and a local Qdrant vector store. It uses Google's Gemini API for transcription, image analysis, triage, summaries, reflection, and retrieval-augmented answers.

## What It Does

- Queues incoming Telegram messages and media in SQLite.
- Processes text, images, audio, YouTube links, and short-form media links.
- Uses Gemini to analyze content and decide whether it should become a task, log, or long-term memory.
- Saves tasks and logs to Notion.
- Saves semantic memories to local Qdrant.
- Supports `/ask` for memory search and live Notion context.
- Supports `/reflect` for long-range reflection across daily, weekly, and monthly summaries.
- Sends scheduled morning briefs and evening journal prompts.
- Generates daily, weekly, and monthly summaries.

## Project Structure

```text
.
├── main.py                  # Telegram bot entrypoint, command handlers, scheduler
├── ai_engine.py             # Queue compiler and triage pipeline
├── media_processor.py       # Image, audio, YouTube, and short-form media processing
├── brief.py                 # Morning brief and journal flow
├── summary_engine.py        # Daily, weekly, and monthly summary generation
├── config.py                # Models, paths, batch sizes, schedule times
├── services/
│   ├── storage.py           # SQLite queue and local data folders
│   ├── llm_service.py       # Gemini text, structured output, embeddings, file APIs
│   ├── notion_service.py    # Notion database/page helpers
│   └── qdrant_service.py    # Qdrant local vector storage
└── second_brain_data/       # Runtime data, created automatically
```

## Runtime Data

The app stores local runtime data in:

```text
second_brain_data/
├── queue.db
├── qdrant_db/
├── images/
├── videos/
├── audio/
└── documents/
```

Keep this directory persistent if you deploy the bot. It contains the queue, local vector database, and temporary media folders.

## Requirements

- Python 3.10+
- FFmpeg
- Telegram bot token
- Google Gemini API key
- Notion integration token
- Notion database IDs for tasks, logs, summaries, and journal entries

## Local Setup

Clone the repository:

```bash
git clone https://github.com/Shivaay26/second_brain.git
cd second_brain
```

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

On Windows:

```bash
.venv\Scripts\activate
```

Install Python dependencies:

```bash
pip install -r requirements.txt
```

Install FFmpeg:

```bash
sudo apt update
sudo apt install ffmpeg
```

On macOS:

```bash
brew install ffmpeg
```

## Environment Variables

Create a `.env` file in the project root:

```env
bot_token="YOUR_TELEGRAM_BOT_TOKEN"
my_chat_id="YOUR_TELEGRAM_CHAT_ID"

gemini_api_key="YOUR_GEMINI_API_KEY"
notion_token="YOUR_NOTION_INTEGRATION_TOKEN"

tasks_db_id="YOUR_NOTION_TASKS_DATABASE_ID"
daily_log_db_id="YOUR_NOTION_DAILY_LOG_DATABASE_ID"
daily_summary_db_id="YOUR_NOTION_DAILY_SUMMARY_DATABASE_ID"
weekly_summary_db_id="YOUR_NOTION_WEEKLY_SUMMARY_DATABASE_ID"
monthly_summary_db_id="YOUR_NOTION_MONTHLY_SUMMARY_DATABASE_ID"
journal_db_id="YOUR_NOTION_JOURNAL_DATABASE_ID"
```

`my_chat_id` should be your Telegram chat ID as a number. The bot uses it for scheduled messages such as morning briefs and journal prompts.

## Run Locally

```bash
python main.py
```

The bot starts Telegram polling and registers scheduled background jobs for:

- Queue compilation
- Journal prompt
- Morning brief
- Daily summary
- Weekly summary
- Monthly summary

## Telegram Commands

- `/ask <question>`: search memories and live Notion state.
- `/reflect <question or goal>`: analyze recent summaries and longer-term patterns.
- `/done <task name>`: mark a matching Notion task as done.
- `/delete <memory indexes>`: delete memories returned by `/ask`.
- `/clear`: clear the in-memory conversation context.

## Deployment Notes

Qdrant runs in local file mode, so only one bot process should access `second_brain_data/qdrant_db` at a time. Do not run a local debug process while the production bot is already running against the same data directory.

For server deployment, keep these persistent:

- `.env`
- `second_brain_data/`

Do not commit them to git.

## Docker Notes

This project can be containerized. A Docker setup should:

- Use a Python slim image.
- Install FFmpeg inside the image.
- Install `requirements.txt`.
- Copy the app code.
- Load secrets from `.env` at runtime.
- Mount `second_brain_data/` as a volume so Qdrant and SQLite data survive container rebuilds.

Example runtime shape:

```bash
docker run --env-file .env \
  -v "$(pwd)/second_brain_data:/app/second_brain_data" \
  second-brain-bot
```

## Important Caveats

- Local Qdrant uses file locks. Run only one container or process against the same `second_brain_data` directory.
- Media downloads depend on `yt-dlp` and FFmpeg.
- Notion rich text properties have size limits, so very large generated text may need chunking or truncation.
- The app currently uses long-running Telegram polling, not webhooks.

## License

MIT License.
