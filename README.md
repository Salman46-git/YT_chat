# YT RAG Chat

Chat with any YouTube video while you watch it. A Chrome extension adds a chat panel to YouTube, and a local Python backend answers your questions using **RAG** (Retrieval-Augmented Generation) over the video's transcript.

- **LLM:** [Groq](https://console.groq.com) (default `openai/gpt-oss-120b`)
- **Embeddings:** local `fastembed` model (Groq has no embeddings API, so this is free and needs no key)
- **Vector store:** FAISS (in memory)
- **Framework:** LangChain + FastAPI
- **Frontend:** Chrome extension (Manifest V3)

## Features

- Floating chat button on every YouTube watch page
- Answers grounded only in the video transcript (says "I don't know" otherwise)
- **Clickable timestamps** under each answer that jump the video to the relevant moment
- **Follow-up questions** work (the backend rewrites them into standalone questions using chat history)
- Automatically re-indexes when you switch videos; indexes are cached per video

## How it works

```
YouTube page ──(content.js)──► background.js ──HTTP──► FastAPI backend
                                                         │
                          1. fetch transcript (with timestamps)
                          2. split into ~1000 char chunks (overlapping)
                          3. embed locally + store in FAISS
                          4. retrieve top-k chunks for the question
                          5. Groq LLM answers using only those chunks
                                                         │
YouTube page ◄── answer + timestamp sources ◄────────────┘
```

## Project structure

```
yt-rag-chat/
├── backend/
│   ├── main.py            # FastAPI app + RAG pipeline
│   ├── requirements.txt
│   └── .env.example
├── extension/
│   ├── manifest.json
│   ├── background.js      # talks to the backend
│   ├── content.js         # chat panel injected into YouTube
│   └── styles.css
└── README.md
```

## Setup

### 1. Get a Groq API key

Create a free key at <https://console.groq.com/keys>.

### 2. Run the backend

Requires Python 3.9+.

```bash
cd backend
python -m venv .venv
pip install -r requirements.txtsource .venv/bin/activate        # Windows: .venv\Scripts\activate


cp .env.example .env             # Windows: copy .env.example .env
# open .env and paste your GROQ_API_KEY

uvicorn main:app --port 8000
```

Check it works: open <http://localhost:8000/health>. On the very first start the server downloads the embedding model (~130 MB, one time only) in the background; watch the console for "Embedding model ready", and `/health` will show `"embeddings_ready": true`.

### 3. Load the extension

1. Open Chrome and go to `chrome://extensions`
2. Turn on **Developer mode** (top right)
3. Click **Load unpacked** and select the `extension/` folder
4. Open any YouTube video, then click the 💬 button at the bottom-right

Works in other Chromium browsers too (Edge, Brave).

## Usage

1. Open a YouTube video that has captions.
2. Click 💬. The transcript is indexed (a few seconds).
3. Ask questions, such as "Summarize this video" or "What does he say about funding?"
4. Click a `▶ 12:34` chip under an answer to jump to that part of the video.

The backend must be running whenever you use the extension.

## Configuration

Set these in `backend/.env`:

| Variable | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` | (required) | Your Groq key |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Use `openai/gpt-oss-20b` for faster/cheaper answers. Check [Groq deprecations](https://console.groq.com/docs/deprecations) since models get retired |
| `EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | Local embedding model |
| `TRANSCRIPT_LANGS` | `en,en-US,en-GB,hi` | Preferred caption languages, in order |
| `TOP_K` | `5` | Chunks retrieved per question |

To run the backend on a different port, change `API_BASE` in `extension/background.js` and the `host_permissions` in `extension/manifest.json`, then click reload on `chrome://extensions`.

## API

| Endpoint | Body | Returns |
|---|---|---|
| `GET /health` | | status, model, number of cached videos |
| `POST /index` | `{"video_id": "..."}` | `{chunks, cached}` |
| `POST /chat` | `{"video_id": "...", "question": "...", "history": [{"role": "user", "content": "..."}]}` | `{answer, sources[]}` |

Interactive docs are at <http://localhost:8000/docs>.

## Troubleshooting

| Problem | Fix |
|---|---|
| "Cannot reach the backend" | Start the backend (`uvicorn main:app --port 8000`) and check <http://localhost:8000/health> |
| "This video has no captions" | The video has no transcript; try another video |
| "Could not fetch transcript" | YouTube may be blocking your IP or rate-limiting. Wait and retry. Cloud/VPN IPs are blocked more often, so run the backend on your own machine |
| Button doesn't appear | Reload the YouTube tab after installing or reloading the extension |
| Groq rate-limit errors | The free tier has limits; switch to `openai/gpt-oss-20b` or wait a minute |
| "Groq model ... is no longer available" / `model_decommissioned` | Groq retired that model (e.g. `llama-3.3-70b-versatile` was shut down on 16 Aug 2026). Set `GROQ_MODEL` in `backend/.env` to a current model and restart the backend |
| "Embedding model could not be loaded" | The first run needs internet to download the model into `backend/models_cache/`. Check your connection / firewall, then restart the backend |
| Transcript language | If a video has no English captions, the backend uses the first available transcript and translates it to English when possible |

## Security notes

- **Never commit your `.env` or paste API keys into code or notebooks.** `.gitignore` already excludes `.env`.
- The backend allows requests from any origin for local convenience. Don't expose it to the internet without adding authentication.

## Limitations and ideas for next steps

- Index is in memory, so it's lost when the backend restarts. Save it with `FAISS.save_local()` to persist.
- Videos without captions are not supported. You could add Whisper transcription as a fallback.
- Retrieval is plain similarity search. Possible upgrades: multi-query retrieval, MMR, or a reranker.
- Possible features: streaming answers, settings page in the extension, Chrome Web Store packaging.

## Sharing the project

When zipping this folder, **leave out `backend/.venv/`** (it is hundreds of MB and machine-specific) and never include `backend/.env`. Anyone can recreate the venv with `pip install -r requirements.txt`.
