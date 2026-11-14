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
    "supported": {"label": "Supported", "description": "Backed by relevant, reliable sources."},
    "contradicted": {"label": "Contradicted", "description": "Conflicts with reliable evidence."},
    "missing_context": {"label": "Missing context", "description": "Partially supported or lacks key context."},
    "unverified": {"label": "Unverified", "description": "Could not be verified with the available evidence."},
}


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
        for example in item.get("examples", []) if isinstance(item.get("examples", []), list) else []:
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


def summarize_claim_evidence(claims: list[dict], total_claims: int) -> dict:
    buckets = {key: {**value, "count": 0, "claims": []} for key, value in CLAIM_CATEGORIES.items()}
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
