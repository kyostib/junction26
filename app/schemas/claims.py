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
    post_id: str
    text: str
    claim_type: ClaimType
    importance: float = Field(ge=0, le=1)
