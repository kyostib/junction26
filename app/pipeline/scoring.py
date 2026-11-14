from app.schemas.evidence import EvidenceEvaluation


def prioritize(claims, limit: int):
    return sorted((c for c in claims if c.claim_type.value == "factual"), key=lambda c: c.importance, reverse=True)[:limit]


def compute_badge(bs_index: float) -> tuple[str, str]:
    """Returns (badge_level, badge_label) based on the 0-100 BS Index."""
    score = round(bs_index)
    if score <= 30:
        return "low", f"[✓ {score}]"
    if score <= 60:
        return "medium", f"[⚠️ {score}]"
    return "high", f"[🚨 {score}]"


def aggregate(
    evaluations: list[tuple[float, EvidenceEvaluation]],
    spam_score: float = 0.0,
    harm_score: float = 0.0,
) -> dict:
    """Aggregates factuality evaluations, spam signals, and harm metrics into a unified BS Index (0-100)."""
    # A missing or failed evidence lookup is unknown, not evidence that the
    # claim is half false.  Only grounded evaluations influence factuality.
    grounded_evaluations = [
        (importance, evaluation)
        for importance, evaluation in evaluations
        if evaluation.evidence_quality > 0 or evaluation.verification_status != "uncertain"
    ]
    if grounded_evaluations:
        total_weight = sum(max(0.05, importance) for importance, _ in grounded_evaluations)
        avg_accuracy = sum(max(0.05, i) * e.accuracy for i, e in grounded_evaluations) / total_weight
        avg_quality = sum(max(0.05, i) * e.evidence_quality for i, e in grounded_evaluations) / total_weight
        avg_misleading = sum(max(0.05, i) * e.misleadingness for i, e in grounded_evaluations) / total_weight
        avg_sensational = sum(max(0.05, i) * e.sensationalism for i, e in grounded_evaluations) / total_weight
    else:
        avg_accuracy = 0.0
        avg_quality = 0.0
        avg_misleading = 0.0
        avg_sensational = 0.0

    # Falsehood score: accuracy is -1.0 (false) to 1.0 (true) -> maps to 100 (false) to 0 (true)
    falsehood_score = round(max(0.0, min(100.0, (1.0 - avg_accuracy) * 50.0)), 1) if grounded_evaluations else 0.0
    spam_score = round(max(0.0, min(100.0, spam_score)), 1)
    harm_score = round(max(0.0, min(100.0, harm_score)), 1)

    # Composite BS Index (0 = cleanest/most truthful, 100 = severe falsehood/spam/harm)
    # Weights: 45% Falsehood, 35% Spam, 20% Harm
    bs_index = round(0.45 * falsehood_score + 0.35 * spam_score + 0.20 * harm_score, 1)
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
            "factuality_accuracy": round(avg_accuracy, 2),
            "evidence_quality": round(avg_quality * 10, 1),
            "sensationalism": round(avg_sensational * 10, 1),
            "misleadingness": round(avg_misleading * 10, 1),
        },
    }
