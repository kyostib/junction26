from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.analysis import router as analysis_router
from app.api.overlay import router as overlay_router
from app.core.logging import configure_logging
from app.db.database import Base, engine
from app.db import models  # noqa: F401

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(
    title="BS Meter API",
    description="Explainable evidence signals, spam detection, and factuality scores powered by Context.dev.",
    lifespan=lifespan,
)

# Enable CORS for Chrome Extension overlays & web clients with Private Network Access
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
