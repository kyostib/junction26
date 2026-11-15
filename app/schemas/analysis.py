from datetime import datetime
from pydantic import BaseModel, Field


class AnalysisCreate(BaseModel):
    platform: str = "twitter"
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
    type: str  # "falsehood", "spam", "harm", "propaganda", "sensationalism"
    summary: str
    assessment: str
    evidence_url: str | None = None
    evidence_title: str | None = None


class UserOverlayResponse(BaseModel):
    username: str
    status: str  # "ready", "analyzing", "queued", "unknown"
    bs_index: float | None = None  # 0 to 100 scale (0 = trustworthy, 100 = extreme BS)
    badge_level: str | None = None  # "low", "medium", "high"
    badge_label: str | None = None  # e.g. "[✓ 12]", "[⚠️ 48]", "[🚨 78]"
    dimensions: dict | None = None  # {"falsehood": 20, "spam": 80, "harm": 10}
    top_flags: list[FlagItem] = []
    analyzed_at: datetime | None = None
    report_url: str | None = None
    summary: str | None = None
    details: dict | None = None


class OverlayBatchRequest(BaseModel):
    usernames: list[str] = Field(min_length=1, max_length=20, description="List of Twitter handles to look up")


class OverlayBatchResponse(BaseModel):
    results: dict[str, UserOverlayResponse]
