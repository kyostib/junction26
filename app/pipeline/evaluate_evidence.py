from app.providers.llm import LLMProvider
from app.schemas.claims import ClaimExtraction
from app.schemas.evidence import WebEvidence


async def evaluate_evidence(claim: ClaimExtraction, evidence: list[WebEvidence], llm: LLMProvider):
    return await llm.evaluate_evidence(claim, evidence)
