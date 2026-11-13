from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.analysis import router
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


app = FastAPI(title="BS Meter API", description="Evidence signals for public claims, not a judgment of a person.", lifespan=lifespan)
app.include_router(router)


@app.get("/health")
async def health():
    return {"status": "ok"}
