import heapq
from app.schemas.evidence import EvidenceEvaluation


def prioritize(claims, limit: int):
    factual_claims = (c for c in claims if getattr(c.claim_type, "value", c.claim_type) == "factual")
    return heapq.nlargest(limit, factual_claims, key=lambda c: c.importance)


def compute_badge(bs_index: float) -> tuple[str, str]:
    """Returns (badge_level, badge_label) based on the 0-100 BS Index."""
    score = round(bs_index)
    if score <= 30:
        return "low", f"[✓ {score}]"
    if score <= 60:
        return "medium", f"[⚠️ {score}]"
    return "high", f"[🚨 {score}]"


def compute_bs_index(falsehood: float, spam: float, harm: float, propaganda: float = 0.0) -> float:
    """Return a severity-first 0–100 index from the risk dimensions.

    A verified severe problem in any one dimension should be able to produce a
    correspondingly severe index. Other dimensions provide limited corroboration
    without requiring all three to be extreme to reach the top of the scale.
    """
    f_clamped = max(0.0, min(100.0, falsehood))
    s_clamped = max(0.0, min(100.0, spam))
    h_clamped = max(0.0, min(100.0, harm))
    p_clamped = max(0.0, min(100.0, propaganda))

    dominant = max(f_clamped, s_clamped, h_clamped, p_clamped)
    weighted_signal = 0.45 * f_clamped + 0.35 * s_clamped + 0.20 * h_clamped + 0.20 * p_clamped
    corroboration = min(1.0, weighted_signal / 1.20 / 100.0) * 0.25
    return round(min(100.0, dominant + (100.0 - dominant) * corroboration), 1)


def aggregate(
    evaluations: list[tuple[float, EvidenceEvaluation]],
    spam_score: float = 0.0,
    harm_score: float = 0.0,
    propaganda_score: float = 0.0,
) -> dict:
    """Aggregates factuality evaluations, spam signals, and harm metrics into a unified BS Index (0-100)."""
    # A missing or failed evidence lookup is unknown, not evidence that the
    # claim is half false. Only grounded evaluations influence factuality.
    grounded_evaluations = [
        (importance, evaluation)
        for importance, evaluation in evaluations
        if evaluation.evidence_quality > 0 and evaluation.verification_status != "uncertain"
    ]
    if grounded_evaluations:
        total_weight = 0.0
        acc_total = 0.0
        qual_total = 0.0
        mislead_total = 0.0
        sens_total = 0.0
        for importance, e in grounded_evaluations:
            w = max(0.05, importance)
            total_weight += w
            acc_total += w * e.accuracy
            qual_total += w * e.evidence_quality
            mislead_total += w * e.misleadingness
            sens_total += w * e.sensationalism
        avg_accuracy = acc_total / total_weight
        avg_quality = qual_total / total_weight
        avg_misleading = mislead_total / total_weight
        avg_sensational = sens_total / total_weight
    else:
        avg_accuracy = 0.0
        avg_quality = 0.0
        avg_misleading = 0.0
        avg_sensational = 0.0

    # Falsehood score: accuracy is -1.0 (false) to 1.0 (true) -> maps to 100 (false) to 0 (true)
    falsehood_score = round(max(0.0, min(100.0, (1.0 - avg_accuracy) * 50.0)), 1) if grounded_evaluations else 0.0
    spam_score = round(max(0.0, min(100.0, spam_score)), 1)
    harm_score = round(max(0.0, min(100.0, harm_score)), 1)

    propaganda_score = round(max(0.0, min(100.0, propaganda_score)), 1)
    bs_index = compute_bs_index(falsehood_score, spam_score, harm_score, propaganda_score)
    badge_level, badge_label = compute_badge(bs_index)

    return {
        "bs_index": bs_index,
        "overall_score": bs_index,  # For backward-compatibility with existing consumers
        "badge_level": badge_level,
        "badge_label": badge_label,
        "dimensions": {
            "falsehood": falsehood_score,
            "spam": spam_score,
            "harm": harm_score,
            "propaganda": propaganda_score,
            "factuality_accuracy": round(avg_accuracy, 2),
            "evidence_quality": round(avg_quality * 10, 1),
            "sensationalism": round(avg_sensational * 10, 1),
            "misleadingness": round(avg_misleading * 10, 1),
        },
    }
