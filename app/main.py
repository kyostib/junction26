import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

import logging
from app.api.analysis import router as analysis_router
from app.api.analysis import run_analysis
from app.api.overlay import router as overlay_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.database import Base, SessionLocal, engine
from app.db.models import Analysis
from sqlalchemy import select

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
    # BackgroundTasks are process-local. Resume work left queued or running by a
    # previous API process so a container restart cannot permanently strand it.
    async with SessionLocal() as session:
        result = await session.execute(
            select(Analysis.id).where(Analysis.status.in_(["queued", "running"]))
        )
        unfinished_analysis_ids = list(result.scalars())
    for analysis_id in unfinished_analysis_ids:
        asyncio.create_task(run_analysis(analysis_id))
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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_private_network=True,
    expose_headers=["Access-Control-Allow-Private-Network"],
)

app.include_router(analysis_router)
app.include_router(overlay_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
