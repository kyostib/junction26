from typing import Protocol

from app.adapters.social.base import PostData
from app.schemas.claims import ClaimExtraction, ClaimType
from app.schemas.evidence import EvidenceEvaluation, Stance, WebEvidence


class LLMProvider(Protocol):
    async def extract_claims(self, posts: list[PostData]) -> list[ClaimExtraction]: ...

    async def evaluate_evidence(self, claim: ClaimExtraction, evidence: list[WebEvidence]) -> EvidenceEvaluation: ...


class DemoLLMProvider:
    """Deterministic offline implementation; replace with a model-backed provider for production."""

    async def extract_claims(self, posts: list[PostData]) -> list[ClaimExtraction]:
        found = []
        for post in posts:
            text = post.text.strip()
            lower = text.lower()
            if not text or any(x in lower for x in ("i love", "amazing 😂")):
                continue
            if any(x in lower for x in ("everyone knows", "destroy the country")):
                found.append(ClaimExtraction(post_id=post.platform_post_id, text=text, claim_type=ClaimType.rhetorical, importance=0.35))
            elif any(x in lower for x in ("i was wrong", "correction:")):
                found.append(ClaimExtraction(post_id=post.platform_post_id, text=text, claim_type=ClaimType.factual, importance=0.55))
            elif any(x in lower for x in ("will ", "going to ")):
                found.append(ClaimExtraction(post_id=post.platform_post_id, text=text, claim_type=ClaimType.prediction, importance=0.5))
            elif any(x in lower for x in ("eu adopted", "great wall", "temperatures have risen")):
                found.append(ClaimExtraction(post_id=post.platform_post_id, text=text, claim_type=ClaimType.factual, importance=0.85))
        return found

    async def evaluate_evidence(self, claim: ClaimExtraction, evidence: list[WebEvidence]) -> EvidenceEvaluation:
        if not evidence:
            return EvidenceEvaluation(accuracy=0, evidence_quality=0, misleadingness=0, sensationalism=0,
                                      verification_status="uncertain", reason="No usable web evidence was available.")
        weighted = [(e, e.relevance * e.source_quality) for e in evidence]
        denom = sum(w for _, w in weighted)
        stance_values = {Stance.supports: 1.0, Stance.partially_supports: 0.25, Stance.context_only: 0.0,
                         Stance.irrelevant: 0.0, Stance.contradicts: -1.0}
        accuracy = sum(stance_values[e.stance] * w for e, w in weighted) / denom if denom else 0.0
        if accuracy >= 0.65:
            status = "supported"
        elif accuracy <= -0.65:
            status = "contradicted"
        elif accuracy > 0.05:
            status = "partially_supported"
        else:
            status = "uncertain"
        misleading = 0.7 if any(e.stance == Stance.partially_supports for e in evidence) else 0.0
        return EvidenceEvaluation(accuracy=accuracy, evidence_quality=sum(e.source_quality for e in evidence) / len(evidence),
                                  misleadingness=misleading, sensationalism=0.0, verification_status=status,
                                  reason=f"Demo evaluator used {len(evidence)} explicitly labeled fixture source(s).")
