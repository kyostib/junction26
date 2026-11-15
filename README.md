# BS Meter 🎯

**BS Meter** calculates an explainable **"BS Index"** (0–100) for Twitter/X users based on their public tweets. It detects **falsehoods & unverified claims**, **spam & crypto/affiliate shills**, and **harmful content**.

The system is powered by **Context.dev** as its unified external gateway, hosted on FastAPI with a PostgreSQL backend, and comes with a **Chrome Extension** that injects real-time score overlay badges directly next to user handles on `x.com`.

---

## Architecture Overview

```
  ┌─────────────────────────────────────────────────────────────┐
  │                 Chrome Extension (x.com)                    │
  │  - MutationObserver watches handles: [data-testid="User-Name"]│
  │  - Injects Badge Pills: [✓ 12] [⚠️ 48] [🚨 78]              │
  │  - Hover Popover: BS breakdown & Context.dev citations      │
  └──────────────────────────────▲──────────────────────────────┘
                                 │ POST /api/v1/overlay/twitter/batch
                                 ▼
  ┌─────────────────────────────────────────────────────────────┐
  │                FastAPI Backend (Dockerized)                 │
  │  - Cache-first architecture (sub-50ms responses for feed)   │
  │  - Auto-enqueues background analysis for stale/new users    │
  │  - CORS enabled for web & extension requests                │
  └──────────────▲───────────────────────────────▲──────────────┘
                 │ asyncpg                       │
                 ▼                               ▼
  ┌──────────────────────────────┐ ┌───────────────────────────┐
  │      PostgreSQL 18 DB        │ │    Context.dev Gateway    │
  │  - Profiles (unique handles) │ │ 1. Ingestion: scrape x.com│
  │  - Cached analyses & scores  │ │ 2. Uncloak: scrape links  │
  │  - Claims & evidence traces  │ │ 3. Ground: web.search     │
  └──────────────────────────────┘ └───────────────────────────┘
```

---

## Quickstart with Docker & PostgreSQL

The fastest way to spin up the PostgreSQL database and FastAPI backend:

1. **Set your environment variables**:

   ```bash
   cp .env.example .env
   # Add your CONTEXT_DEV_API_KEY in .env
   ```

2. **Start the containers**:

   ```bash
   docker compose up --build
   ```

3. **Check health**:
   ```bash
   curl http://localhost:8000/health
   # {"status": "ok"}
   ```

---

## Local Development (Without Docker)

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

To run with SQLite locally:

```bash
export DATABASE_URL="sqlite+aiosqlite:///./bs_meter.db"
uvicorn app.main:app --reload
```

---

## Run the Chrome Extension

The extension runs in Chrome on `x.com` and `twitter.com`. It sends profile handles to the BS Meter API; the API and database must be running separately.

### 1. Start the API

Choose one:

- **Use the hosted service:** set the extension API address to `https://api.bsmeter.tech` in step 3 below.
- **Run locally:** start the Docker services using [Quickstart with Docker & PostgreSQL](#quickstart-with-docker--postgresql), then use `http://localhost:8000` as the API address. Confirm `http://localhost:8000/health` returns `{"status":"ok"}`.

For real profile analysis, configure the API's Context.dev key in its `.env` file. The extension can display cached results without it, but new or stale profiles need the analysis providers configured to complete a scan.

### 2. Load the extension in Chrome

1. Clone or download this repository.
2. In Chrome, open `chrome://extensions`.
3. Turn on **Developer mode**.
4. Click **Load unpacked** and select this repository's [`extension/`](extension/) directory.
5. Pin **BS Meter** from Chrome's Extensions menu so its settings are easy to open.

### 3. Connect it to the API

1. Click the BS Meter toolbar icon.
2. Leave **Enable** checked.
3. In **API Address**, enter one of:
   - `https://api.bsmeter.tech` for the hosted service.
   - `http://localhost:8000` for a locally running API.
4. Click **Save & Apply**. When Chrome asks, allow the extension to access that API address. The status dot should turn green when `/health` responds.

Enter only the API origin; do not append `/api` or a trailing endpoint path.

### 4. Try it on X

Open or refresh [x.com](https://x.com). BS Meter adds a score badge beside recognized profile handles. The first lookup may show a checking indicator while the API queues analysis; completed results are cached for later lookups. Hover a badge to see its breakdown and available evidence links.

Typical badges:

- 🟢 `[✓ 14]` — lower risk signals
- 🟡 `[⚠️ 48]` — caution
- 🔴 `[🚨 78]` — higher risk signals
- ⚪ `[⏳ Checking...]` — analysis queued or in progress

If the extension shows as offline, open the API's `/health` URL in a browser and confirm the API address in the popup matches it. API address changes are applied to already-open X tabs; if a badge does not appear, refresh the X tab and check that **Enable** is on.

---

## API Endpoints

### 1. Batch Overlay Lookup (Used by Chrome Extension)

`POST /api/v1/overlay/twitter/batch`

```json
{
  "usernames": ["elonmusk", "dr_who"]
}
```

Returns instantaneous cached scores or marks unknown handles as `queued` while scheduling analysis in the background.

### 2. Single Handle Overlay Lookup

`GET /api/v1/overlay/twitter/{username}?refresh=false`

### 3. Run Analysis in Terminal

You can run a direct analysis from your command line:

```bash
python -m app.cli elonmusk
```

---

## How Context.dev Powers BS Meter

Context.dev serves as the **unified contact point**:

1. **Twitter Scraping (`client.web.scrape`)**: Bypasses bot detection and renders the client-side JavaScript on `x.com/{username}` to extract recent tweets and profile metadata without needing an X API key.
2. **Link Uncloaking & Inspection (`client.web.scrape`)**: Follows shortened `t.co` URLs and renders destination landing pages to inspect them for crypto presales, phishing, and affiliate spam.
3. **Claim Grounding (`client.web.search`)**: Searches authoritative web sources to verify empirical claims.
