from enum import StrEnum
from pydantic import BaseModel, Field


class Stance(StrEnum):
    supports = "supports"
    contradicts = "contradicts"
    partially_supports = "partially_supports"
    context_only = "context_only"
    irrelevant = "irrelevant"


class WebEvidence(BaseModel):
    url: str
    title: str = ""
    source_domain: str = ""
    excerpt: str = ""
    relevance: float = Field(default=0.5, ge=0, le=1)
    source_quality: float = Field(default=0.5, ge=0, le=1)
    stance: Stance = Stance.context_only


class EvidenceEvaluation(BaseModel):
    accuracy: float = Field(ge=-1, le=1)
    evidence_quality: float = Field(ge=0, le=1)
    misleadingness: float = Field(ge=0, le=1)
    sensationalism: float = Field(ge=0, le=1)
    verification_status: str
    reason: str
