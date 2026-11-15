TECHNIQUES = (
    ("fear_appeal", "Fear appeal", "Promoting a position by emphasizing a threat to create anxiety, especially when exaggerated or unsupported. Do not confuse with a proportionate, sourced risk assessment."),
    ("scapegoating", "Scapegoating", "Assigning broad blame to a person or group for a complex problem."),
    ("false_dilemma", "False dilemma", "Presenting a choice as having only two options when other plausible options exist."),
    ("loaded_language", "Loaded language", "Choosing emotionally charged words or metaphors to influence rather than inform precisely. Do not confuse with accurate, proportionate descriptions of genuinely emotional events."),
    ("cherry_picking", "Cherry-picking", "Presenting selected evidence while omitting relevant counterevidence or context."),
    ("repetition", "Repetition", "Repeating the same slogan or claim across multiple sampled posts."),
    ("us_vs_them", "Us-versus-them", "Dividing people into opposing in-groups and out-groups to encourage loyalty or hostility. Do not confuse with describing disagreement without stereotyping or delegitimizing people."),
    ("name_calling", "Name calling / labeling", "Attaching an insulting label to provoke rejection without argument. Do not confuse with a specific, supportable description such as 'convicted fraudster' when a conviction exists."),
    ("whataboutism", "Whataboutism", "Deflecting criticism by pointing to another party's alleged wrongdoing instead of addressing the criticism. Do not confuse with a relevant comparison that also answers the original point."),
    ("flag_waving", "Flag waving", "Using national, cultural, or group loyalty to pressure agreement or portray dissent as disloyal. Do not confuse with good-faith expressions of identity or civic pride."),
    ("bandwagon", "Bandwagon", "Claiming popularity or majority belief proves a position is right. Do not confuse with polling used only to describe popularity."),
)


CLAIM_CATEGORIES = {
    "supported": {
        "key": "supported",
        "label": "Supported",
        "description": "Backed by relevant, reliable sources.",
    },
    "contradicted": {
        "key": "contradicted",
        "label": "Contradicted",
        "description": "Conflicts with reliable evidence.",
    },
    "partially_supported": {
        "key": "partially_supported",
        "label": "Partially Supported",
        "description": "Partially backed by evidence, but key caveats or context are omitted.",
    },
    "unverified": {
        "key": "unverified",
        "label": "Unverified",
        "description": "Could not be verified with the available evidence.",
    },
}
# Backward compatibility alias
CLAIM_CATEGORIES["missing_context"] = CLAIM_CATEGORIES["partially_supported"]


def normalize_claim_stance(assessment: str | None, evidence: list | None = None) -> str:
    """Normalize raw evaluation status into canonical stance key: supported, contradicted, partially_supported, or unverified."""
    a = str(assessment or "unverified").lower().strip()
    if "support" in a and "partial" not in a:
        return "supported"
    if any(k in a for k in ("contradict", "refut", "false")):
        return "contradicted"
    if any(k in a for k in ("partial", "missing")):
        return "partially_supported"
    return "unverified"


def normalize_propaganda(raw: object, posts: list) -> list[dict]:
    """Validate model technique scores and retain evidence tied to supplied posts."""
    items = {item.get("key"): item for item in raw if isinstance(item, dict)} if isinstance(raw, list) else {}
    post_lookup = {
        str(post.platform_post_id): (post.text.casefold(), post.text[:280])
        for post in posts
    }
    result = []
    for key, name, description in TECHNIQUES:
        item = items.get(key, {})
        try:
            score = max(0.0, min(1.0, float(item.get("score", 0.0))))
        except (TypeError, ValueError):
            score = 0.0
        examples = []
        seen = set()
        raw_examples = item.get("examples")
        if not isinstance(raw_examples, list):
            if item.get("post_id") and item.get("matched_text"):
                raw_examples = [{"post_id": item.get("post_id"), "matched_text": item.get("matched_text"), "reason": item.get("reason", "")}]
            else:
                raw_examples = []
        for example in raw_examples:
            if not isinstance(example, dict):
                continue
            post_id = str(example.get("post_id", ""))
            if post_id in seen:
                continue
            post_info = post_lookup.get(post_id)
            if post_info is None:
                continue
            quote = str(example.get("matched_text", "")).strip()
            if not quote:
                continue
            text_cf, text_preview = post_info
            if quote.casefold() not in text_cf:
                continue
            seen.add(post_id)
            examples.append({"post_id": post_id, "text": text_preview, "matched_text": quote[:280], "reason": str(example.get("reason", ""))[:300]})
        # A non-zero score must be supported by at least one validated example.
        if not examples:
            score = 0.0
        result.append({"key": key, "name": name, "description": description, "score": score, "count": len(seen), "examples": examples[:3]})
    return result


def summarize_claim_evidence(claims: list[dict], total_claims: int | None = None) -> dict:
    canonical_keys = ("supported", "contradicted", "partially_supported", "unverified")
    buckets = {key: {**CLAIM_CATEGORIES[key], "count": 0, "claims": []} for key in canonical_keys}
    for claim in claims:
        key = normalize_claim_stance(claim.get("assessment"), claim.get("evidence"))
        # Standardize the assessment string on the claim dictionary itself so pills match breakdown chips
        claim["assessment"] = key
        buckets[key]["count"] += 1
        if len(buckets[key]["claims"]) < 3:
            buckets[key]["claims"].append({
                "claim": claim.get("claim", ""),
                "reason": claim.get("reason", ""),
                "evidence": (claim.get("evidence") or [])[:3],
            })
    return {"total": len(claims), "categories": list(buckets.values())}
