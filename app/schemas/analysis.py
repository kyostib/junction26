from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class AnalysisCreate(BaseModel):
    platform: Literal["twitter", "mock"] = "twitter"
    profile_url: str = Field(description="Twitter profile URL or handle (e.g. @username or https://x.com/username)")


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


# Schemas for Chrome Extension & Overlay API
class FlagItem(BaseModel):
    type: str  # "falsehood", "spam", "harm"
    summary: str
    assessment: str
    evidence_url: str | None = None
    evidence_title: str | None = None


class UserOverlayResponse(BaseModel):
    username: str
    status: str  # "ready", "analyzing", "queued", "insufficient_evidence", "unknown"
    bs_index: float | None = None  # 0 to 100, or null when evidence is insufficient
    badge_level: str | None = None  # "low", "medium", "high", "unknown"
    badge_label: str | None = None  # e.g. "[✓ 12]", "[⚠️ 48]", "[🚨 78]"
    dimensions: dict | None = None  # {"falsehood": 20, "spam": 80, "harm": 10}
    top_flags: list[FlagItem] = Field(default_factory=list)
    analyzed_at: datetime | None = None
    report_url: str | None = None


class OverlayBatchRequest(BaseModel):
    usernames: list[str] = Field(description="List of Twitter handles to look up")


class OverlayBatchResponse(BaseModel):
    results: dict[str, UserOverlayResponse]
