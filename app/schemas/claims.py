import uuid
from enum import StrEnum
from pydantic import BaseModel, Field


class ClaimType(StrEnum):
    factual = "factual"
    opinion = "opinion"
    prediction = "prediction"
    rhetorical = "rhetorical"
    subjective = "subjective"
    unverifiable = "unverifiable"


class ClaimExtraction(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    post_id: str
    text: str
    claim_type: ClaimType
    importance: float = Field(default=0.5, ge=0, le=1)
