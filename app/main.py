import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.api.analysis import router as analysis_router
from app.api.overlay import router as overlay_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.database import Base, engine
from app.db import models  # noqa: F401

configure_logging()
logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        f"Starting BS Meter API in mode={settings.environment} "
        f"with database={settings.database_url.split('@')[-1]}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(
    title="BS Meter API",
    description="Explainable evidence signals, spam detection, and factuality scores powered by Context.dev.",
    lifespan=lifespan,
)

# Enable CORS for Chrome Extension overlays (chrome-extension://*), x.com, and web clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analysis_router)
app.include_router(overlay_router)

WEB_DIR = Path(__file__).parent / "web"
app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")


@app.get("/", include_in_schema=False)
async def landing_page():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok"}
