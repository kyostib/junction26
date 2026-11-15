# BS Meter

**BS Meter** calculates an explainable **"BS Index"** (0–100) for Twitter/X profiles by analyzing their public activity for **falsehoods**, **crypto/affiliate spam**, **harmful links**, **propaganda**, and **sensationalism**.

Powered by **Context.dev** as its unified gateway for headless X scraping, link uncloaking, and web grounding.

---

## Architecture

```
Chrome Extension (MV3)  ──┐
[Profile pill & popover]  │ POST /api/v1/overlay/*
[4-tab inspect modal   ]  │
                          ├─► FastAPI Backend ──► PostgreSQL / SQLite
                          │   [sub-50ms cache]    [profiles, claims, evidence]
Website (/)               │   [async workers ]           ▲
[Username search demo  ] ─┘   [web static    ]           │
                                      │                  │
                                      └──► Context.dev ──┘
                                           ├─ scrape x.com (no API key)
                                           ├─ uncloak & inspect links
                                           └─ web.search claim grounding
```

---

## Features

- **Website (`/`)**: Direct web-based lookup for any `@username` or profile URL with real-time analysis polling.
- **Chrome Extension (MV3)**: 
  - Injects badge pills beside profile action buttons on `x.com` and `twitter.com`.
  - Hover popover with 5-dimension risk meters; click to pin.
  - Deep **Inspect Modal** with 4 tabs: **Overview**, **Propaganda & Bias**, **Stances & Claims**, and **Analyzed Posts**.
  - Dynamic dark/light theme matching X natively.
- **Severity-First Scoring**: A critical violation in any dimension dominates the score rather than being averaged away:
  - `dominant = max(falsehood, spam, harm, propaganda, sensationalism)`
  - `BS Index = min(100, dominant + (100 - dominant) * min(1.0, weighted_signal) * 0.2)`
  - Score tiers: `[0-30]` Low BS &middot; `[31-60]` Caution &middot; `[61-100]` High BS &middot

---

## Context.dev Integration

Context.dev acts as the unified external gateway:
1. **Profile Ingestion (`client.web.scrape`)**: Bypasses bot detection and renders JS on `x.com/{user}` to extract posts, replies, and bio without Twitter API keys.
2. **Link Uncloaking (`client.web.scrape`)**: Resolves `t.co` redirects and renders landing pages to flag crypto presales, phishing, and spam.
3. **Claim Grounding (`client.web.search`)**: Queries authoritative sources and fact-checkers to verify user stances.

---

## Quickstart

### Option A: Docker Compose (Recommended)
```bash
cp .env.example .env                  # Add your CONTEXT_DEV_API_KEY
docker compose up --build -d
```
- Website: [http://localhost:8000](http://localhost:8000)
- Health check: `curl http://localhost:8000/health` (`{"status":"ok"}`)

### Option B: Local Dev (SQLite)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export DATABASE_URL="sqlite+aiosqlite:///./bs_meter.db"
uvicorn app.main:app --reload
```

---

## Chrome Extension Setup (Recommended)

Download the extension directory form [Releases](https://github.com/kyostib/junction26/releases/tag/1.0) and follow the instructions there.

## Chrome Extension Setup (From Source)

1. Open `chrome://extensions` and enable **Developer mode**.
2. Click **Load unpacked** and select the [`extension/`](extension/) directory.
3. Open the extension popup, set **API Address** to `https://api.bsmeter.tech` (hosted) or `http://localhost:8000` (local), and click **Save & Apply** (status dot turns green).
4. Visit any profile on [x.com](https://x.com) to view live scores and evidence.

---

## CLI & Testing

```bash
# Run analysis in terminal
python -m app.cli elonmusk

# Run test suite (36 tests)
.venv/bin/pytest
```

---

## API Reference

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | `GET` | Website & Profile Analysis Demo |
| `/health` | `GET` | Health status check |
| `/api/v1/analyses` | `POST` | Queue profile scan (`{"platform": "twitter", "profile_url": "handle"}`) |
| `/api/v1/analyses/{id}` | `GET` | Poll scan status (`queued`, `running`, `completed`) & get full report |
| `/api/v1/overlay/twitter/batch` | `POST` | Batch overlay lookup for extension (`{"usernames": ["..."]}`) |
| `/api/v1/overlay/twitter/{username}` | `GET` | Single handle overlay lookup (`?refresh=false`) |

---

## Configuration (`.env`)

| Key | Default | Description |
|---|---|---|
| `CONTEXT_DEV_API_KEY` | `""` | Context.dev key for scraping, uncloaking, and search |
| `DATABASE_URL` | `postgresql+asyncpg:...` | DB connection string (PostgreSQL or SQLite) |
| `LLM_PROVIDER` | `mock` | Claim evaluation engine (`mock`, `openai`, `gemini`) |
| `LLM_API_KEY` | `""` | API key if using `openai` or `gemini` |
| `MAX_CLAIMS_TO_VERIFY` | `5` | Maximum claims verified per profile |
| `MAX_TWEETS_TO_ANALYZE`| `10` | Maximum recent tweets analyzed per profile |

---

## License

[MIT](LICENSE)
