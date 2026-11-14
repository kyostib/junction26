from datetime import datetime
from typing import Literal
from pydantic import BaseModel, HttpUrl


class AnalysisCreate(BaseModel):
    platform: Literal["mock"] = "mock"
    profile_url: HttpUrl


class AnalysisQueued(BaseModel):
    analysis_id: str
    status: str = "queued"


class AnalysisResponse(BaseModel):
    analysis_id: str
    status: str
    result: dict | None = None
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
