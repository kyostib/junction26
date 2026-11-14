TECHNIQUES = (
    ("fear_appeal", "Fear appeal", "Using threats or fear to influence the audience."),
    ("scapegoating", "Scapegoating", "Assigning broad blame to a person or group for a complex problem."),
    ("false_dilemma", "False dilemma", "Presenting a choice as having only two options when other plausible options exist."),
    ("loaded_language", "Loaded language", "Using emotionally charged wording in place of substantive support."),
    ("cherry_picking", "Cherry-picking", "Presenting selected evidence while omitting relevant counterevidence or context."),
    ("repetition", "Repetition", "Repeating the same slogan or claim across multiple sampled posts."),
    ("us_vs_them", "Us-versus-them", "Framing groups as opposing sides to encourage division."),
)


def normalize_propaganda(raw: object, posts: list) -> list[dict]:
    """Validate model technique scores and retain evidence tied to supplied posts."""
    items = {item.get("key"): item for item in raw if isinstance(item, dict)} if isinstance(raw, list) else {}
    post_by_id = {str(post.platform_post_id): post for post in posts}
    result = []
    for key, name, description in TECHNIQUES:
        item = items.get(key, {})
        try:
            score = max(0.0, min(1.0, float(item.get("score", 0.0))))
        except (TypeError, ValueError):
            score = 0.0
        examples = []
        seen = set()
        for example in item.get("examples", []) if isinstance(item.get("examples", []), list) else []:
            if not isinstance(example, dict):
                continue
            post_id = str(example.get("post_id", ""))
            post = post_by_id.get(post_id)
            quote = str(example.get("matched_text", "")).strip()
            if post is None or not quote or quote.casefold() not in post.text.casefold() or post_id in seen:
                continue
            seen.add(post_id)
            examples.append({"post_id": post_id, "text": post.text[:280], "matched_text": quote[:280], "reason": str(example.get("reason", ""))[:300]})
        # A non-zero score must be supported by at least one validated example.
        if not examples:
            score = 0.0
        result.append({"key": key, "name": name, "description": description, "score": score, "count": len(seen), "examples": examples[:3]})
    return result


def summarize_claim_evidence(claims: list[dict], total_claims: int) -> dict:
    categories = {
        "supported": {"label": "Supported", "description": "Backed by relevant, reliable sources."},
        "contradicted": {"label": "Contradicted", "description": "Conflicts with reliable evidence."},
        "missing_context": {"label": "Missing context", "description": "Partially supported or lacks key context."},
        "unverified": {"label": "Unverified", "description": "Could not be verified with the available evidence."},
    }
    buckets = {key: {**value, "count": 0, "claims": []} for key, value in categories.items()}
    for claim in claims:
        assessment = str(claim.get("assessment", "uncertain")).lower()
        evidence = claim.get("evidence") or []
        if assessment == "supported" and evidence and sum(float(item.get("source_quality", 0)) for item in evidence) / len(evidence) >= 0.5:
            key = "supported"
        elif assessment == "contradicted" and evidence:
            key = "contradicted"
        elif assessment in {"partially_supported", "partial", "missing_context"} or (assessment == "uncertain" and evidence):
            key = "missing_context"
        else:
            key = "unverified"
        buckets[key]["count"] += 1
        if len(buckets[key]["claims"]) < 3:
            buckets[key]["claims"].append({"claim": claim.get("claim", ""), "reason": claim.get("reason", ""), "evidence": evidence[:3]})
    # Claims not selected for research are unverified as well.
    unverified_extra = max(0, total_claims - len(claims))
    buckets["unverified"]["count"] += unverified_extra
    return {"total": total_claims, "categories": list(buckets.values())}
