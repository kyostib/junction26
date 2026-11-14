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

## Chrome Extension Setup

The Chrome Extension adds inline badges and evidence popovers directly into Twitter/X (`x.com`):

1. Open Chrome and navigate to `chrome://extensions`.
2. Toggle on **"Developer mode"** in the top right.
3. Click **"Load unpacked"** and select the [`extension/`](extension/) directory from this repository.
4. Click the extension icon in your Chrome toolbar to configure the Backend API URL (defaults to `http://localhost:8000`).
5. Open [x.com](https://x.com) and browse your feed. You will see badges appear next to handles:
   - 🟢 `[✓ 14]` — Low BS / Authentic
   - 🟡 `[⚠️ 48]` — Caution / Misleading claims
   - 🔴 `[🚨 78]` — High BS / Contradicted claims or spam links
   - ⚪ `[⏳ Checking...]` — Currently analyzing via Context.dev

Hover over any badge to view the breakdown across **Falsehoods**, **Spam**, and **Harm**, along with direct links to the web evidence.

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
