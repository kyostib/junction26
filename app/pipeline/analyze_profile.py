from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.social.mock import MockSocialAdapter
from app.core.config import get_settings
from app.db.models import Analysis, Claim, Evidence, Post, Profile
from app.pipeline.evaluate_evidence import evaluate_evidence
from app.pipeline.extract_claims import extract_claims
from app.pipeline.scoring import aggregate, prioritize
from app.providers.context_dev import ContextDevProvider, DemoWebResearchProvider
from app.providers.llm import DemoLLMProvider


async def analyze_profile(analysis_id: str, session: AsyncSession) -> dict:
    analysis = await session.get(Analysis, analysis_id)
    if analysis is None:
        raise ValueError("Analysis not found")
    profile = await session.get(Profile, analysis.profile_id)
    analysis.status = "running"
    await session.commit()
    try:
        social = MockSocialAdapter()
        profile_data = await social.fetch_profile(profile.profile_url)
        posts = await social.fetch_posts(profile_data)
        persisted_posts = []
        for item in posts:
            post = Post(profile_id=profile.id, platform_post_id=item.platform_post_id, text=item.text,
                        post_url=item.post_url, published_at=item.published_at, metadata_json=item.metadata)
            session.add(post)
            persisted_posts.append(post)
        await session.flush()

        llm = DemoLLMProvider()
        claims = await extract_claims(posts, llm)
        post_by_platform_id = {p.platform_post_id: p for p in persisted_posts}
        claim_rows = {}
        for item in claims:
            row = Claim(post_id=post_by_platform_id[item.post_id].id, text=item.text,
                        claim_type=item.claim_type.value, importance=item.importance)
            session.add(row)
            claim_rows[item.post_id] = row
        await session.flush()

        settings = get_settings()
        research = ContextDevProvider() if settings.context_dev_api_key else DemoWebResearchProvider()
        demo = not bool(settings.context_dev_api_key)
        selected = prioritize(claims, settings.max_claims_to_verify)
        evals = []
        claim_results = []
        for item in selected:
            evidence = await research.research_claim(item.text)
            evaluation = await evaluate_evidence(item, evidence, llm)
            row = claim_rows[item.post_id]
            row.verification_status = evaluation.verification_status
            for entry in evidence:
                session.add(Evidence(claim_id=row.id, url=entry.url, title=entry.title,
                                     source_domain=entry.source_domain, excerpt=entry.excerpt,
                                     stance=entry.stance.value, source_quality=entry.source_quality,
                                     relevance=entry.relevance))
            evals.append((item.importance, evaluation))
            claim_results.append({"claim": item.text, "assessment": evaluation.verification_status,
                                  "reason": evaluation.reason,
                                  "accuracy": evaluation.accuracy,
                                  "evidence": [e.model_dump(mode="json") for e in evidence]})

        scores = aggregate(evals)
        issues = [c for c in claim_results if c["assessment"] in ("contradicted", "partially_supported")]
        supported = [c for c in claim_results if c["assessment"] == "supported"]
        uncertain = [c for c in claim_results if c["assessment"] == "uncertain"]
        result = {**scores, "posts_analyzed": len(posts), "factual_claims": sum(c.claim_type.value == "factual" for c in claims),
                  "claims_analyzed": len(claims), "claims_verified": len(selected),
                  "supported_claims": supported, "contradicted_claims": [c for c in claim_results if c["assessment"] == "contradicted"],
                  "uncertain_claims": uncertain, "top_issues": issues[:5], "top_supported": supported[:5],
                  "claims": claim_results, "source_mode": "demo fixtures (synthetic; not live evidence)" if demo else "Context.dev live search",
                  "score_note": "A product heuristic based on the posts and sources shown; not an objective measure of a person."}
        analysis.status = "completed"
        analysis.overall_score = scores["overall_score"]
        analysis.factuality_score = scores["dimensions"]["factuality"]
        analysis.evidence_score = scores["dimensions"]["evidence"]
        analysis.sensationalism_score = scores["dimensions"]["sensationalism"]
        analysis.misleadingness_score = scores["dimensions"]["misleadingness"]
        analysis.correction_score = 0
        analysis.claims_analyzed = len(claims)
        analysis.result = result
        analysis.completed_at = datetime.now(timezone.utc)
        await session.commit()
        return result
    except Exception:
        analysis.status = "failed"
        analysis.error = "Analysis failed; inspect server logs for details."
        analysis.completed_at = datetime.now(timezone.utc)
        await session.commit()
        raise
