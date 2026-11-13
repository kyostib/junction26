from app.schemas.evidence import EvidenceEvaluation


def prioritize(claims, limit: int):
    return sorted((c for c in claims if c.claim_type.value == "factual"), key=lambda c: c.importance, reverse=True)[:limit]


def claim_score(e: EvidenceEvaluation) -> float:
    return max(-1.0, min(1.0, .60 * e.accuracy + .20 * e.evidence_quality - .10 * e.misleadingness - .10 * e.sensationalism))


def aggregate(evaluations: list[tuple[float, EvidenceEvaluation]]) -> dict:
    if not evaluations:
        return {"overall_score": 0.0, "dimensions": {"factuality": 0, "evidence": 0, "sensationalism": 0, "misleadingness": 0, "correction": 0}}
    total = sum(max(.05, importance) for importance, _ in evaluations)
    avg = lambda f: sum(max(.05, i) * f(e) for i, e in evaluations) / total
    norm = lambda v: round(max(-10, min(10, 10 * v)), 1)
    return {"overall_score": norm(avg(claim_score)), "dimensions": {
        "factuality": norm(avg(lambda e: e.accuracy)),
        "evidence": round(avg(lambda e: e.evidence_quality) * 10, 1),
        "sensationalism": round(avg(lambda e: e.sensationalism) * 10, 1),
        "misleadingness": round(avg(lambda e: e.misleadingness) * 10, 1),
        "correction": 0.0}}
