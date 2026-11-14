# BS Meter

BS Meter analyzes public claims made by an account and returns a traceable evidence based content signal. The score is a product heuristic for the posts analyzed, not an objective measurement of a person's character.

## Setup

Requires Python 3.11+. Create an environment and install the project:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
```

Copy `.env.example` to `.env`. The defaults use a local SQLite database so the demo runs without PostgreSQL. For PostgreSQL set, for example, `DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/bsmeter`.

## Environment variables

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Async SQLAlchemy URL; SQLite is the local default, PostgreSQL is supported. |
| `CONTEXT_DEV_API_KEY` | Enables live Context.dev web search. If empty, synthetic demo fixtures are used and labeled in each result. |
| `LLM_API_KEY` | Reserved for a future model provider; current extraction and evaluation use the deterministic demo provider. |
| `LLM_PROVIDER` | Provider selection setting (currently `mock`). |
| `MAX_CLAIMS_TO_VERIFY` | Maximum factual claims researched per profile (default 5). |

No credential is stored in source. Live Context.dev errors fail the analysis and are logged; they never fall back to synthetic evidence.

## Database

Tables are created at API startup for the hackathon workflow. Alembic is configured for versioned migrations; create/apply migrations with:

```bash
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

## Run the API

```bash
uvicorn app.main:app --reload
```

Submit a mock profile:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/analyses \
  -H "Content-Type: application/json" \
  -d '{"platform":"mock","profile_url":"https://example.com/sample-account"}'
```

Poll `GET /api/v1/analyses/{analysis_id}` for `queued`, `running`, `completed`, or `failed`. A completed response includes dimensions, analyzed claims, assessments, reasons, and source URLs.

Run a full analysis in the terminal:

```bash
python -m app.cli
```

Run tests with `pytest`.

## Architecture

`app/adapters/social` isolates public-post collection; `MockSocialAdapter` supplies clearly synthetic sample posts. `app/providers` holds claim/evidence model interfaces and the Context.dev adapter. The pipeline cleans and extracts claims, prioritizes factual claims deterministically, researches within budget, evaluates evidence signals, and computes scores in Python. FastAPI queues work with an in-process background task, then SQLAlchemy persists profiles, posts, claims, evidence, and analysis results. PostgreSQL is supported; SQLite makes local setup immediate.

The current mock claim extraction and evaluation are deterministic demo implementations, not an LLM. Context.dev is live only when configured with a key. With no key, output marks evidence as synthetic `demo.invalid` fixtures. Social collection currently uses mock posts only.

## Next high value work

1. Add a production LLM provider with schema constrained extraction and evidence classification.
2. Replace mock social collection with a compliant, permissioned public data source.
3. Add migration revisions, persistence deduplication, and operational controls for job retries and usage limits.
