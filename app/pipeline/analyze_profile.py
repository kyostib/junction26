import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.social.mock import MockSocialAdapter
from app.adapters.social.twitter import ContextDevTwitterAdapter
from app.core.config import get_settings
from app.core.errors import SocialDataUnavailableError
from app.db.models import Analysis, Claim, Evidence, Post, Profile
from app.pipeline.evaluate_evidence import evaluate_evidence
from app.pipeline.extract_claims import extract_claims
from app.pipeline.scoring import aggregate, prioritize
from app.providers.context_dev import ContextDevProvider
from app.providers.llm import get_llm_provider

logger = logging.getLogger(__name__)


def _with_evaluated_stances(evidence, evaluation):
    assessment_by_url = {assessment.url: assessment for assessment in evaluation.source_assessments}
    return [
        entry.model_copy(update={"stance": assessment_by_url[entry.url].stance})
        if entry.url in assessment_by_url
        else entry
        for entry in evidence
    ]


def _best_evidence_for_assessment(evidence, assessment: str):
    expected_stances = {
        "contradicted": {"contradicts"},
        "partially_supported": {"partially_supports", "contradicts"},
    }.get(assessment, set())
    candidates = [entry for entry in evidence if entry.stance.value in expected_stances]
    return max(candidates, key=lambda entry: entry.relevance * entry.source_quality, default=None)


def _evidence_for_spam_flag(scraped_links: list[dict], flag: str) -> dict | None:
    lowered_flag = flag.lower()
    return next(
        (link for link in scraped_links if link.get("domain") and str(link["domain"]).lower() in lowered_flag),
        None,
    )


async def _complete_with_insufficient_evidence(
    analysis: Analysis,
    profile: Profile,
    session: AsyncSession,
    reason: str,
) -> dict:
    scores = aggregate([])
    completed_now = datetime.now(timezone.utc)
    result = {
        **scores,
        "posts_analyzed": 0,
        "factual_claims": 0,
        "claims_analyzed": 0,
        "claims_verified": 0,
        "claims": [],
        "top_flags": [],
        "scraped_links_count": 0,
        "source_mode": "unavailable",
        "score_note": reason,
    }
    analysis.status = "completed"
    analysis.overall_score = None
    analysis.badge_level = scores["badge_level"]
    analysis.badge_label = scores["badge_label"]
    analysis.factuality_score = None
    analysis.evidence_score = 0.0
    analysis.spam_score = 0.0
    analysis.harm_score = 0.0
    analysis.sensationalism_score = 0.0
    analysis.misleadingness_score = 0.0
    analysis.claims_analyzed = 0
    analysis.result = result
    analysis.completed_at = completed_now
    profile.last_analyzed_at = completed_now
    await session.commit()
    return result


async def analyze_profile(analysis_id: str, session: AsyncSession) -> dict:
    analysis = await session.get(Analysis, analysis_id)
    if analysis is None:
        raise ValueError("Analysis not found")
    profile = await session.get(Profile, analysis.profile_id)
    if profile is None:
        raise ValueError("Profile not found")

    analysis.status = "running"
    await session.commit()

    try:
        settings = get_settings()
        context_provider = ContextDevProvider()
        llm = get_llm_provider()

        # The deterministic evaluator exists only for explicitly requested mock
        # fixtures. A real account must not receive a truthfulness score from it.
        if profile.platform != "mock" and settings.llm_provider.lower().strip() in {"mock", "demo"}:
            return await _complete_with_insufficient_evidence(
                analysis,
                profile,
                session,
                "A model-backed evidence evaluator is not configured for live profiles.",
            )

        # Select adapter: Twitter via Context.dev or Mock
        if profile.platform == "twitter":
            social = ContextDevTwitterAdapter(provider=context_provider)
        else:
            social = MockSocialAdapter()

        try:
            profile_data = await social.fetch_profile(profile.profile_url)
            posts = await social.fetch_posts(profile_data)
        except SocialDataUnavailableError as exc:
            return await _complete_with_insufficient_evidence(analysis, profile, session, str(exc))

        # Persist posts
        persisted_posts = []
        all_links = set()
        for item in posts:
            post = Post(
                profile_id=profile.id,
                platform_post_id=item.platform_post_id,
                text=item.text,
                post_url=item.post_url,
                published_at=item.published_at,
                metadata_json=item.metadata,
            )
            session.add(post)
            persisted_posts.append(post)

            # Collect embedded links for spam/scam uncloaking
            for link in item.metadata.get("links", []):
                all_links.add(link)

        await session.flush()
        post_by_platform_id = {p.platform_post_id: p for p in persisted_posts}

        # 1. Uncloak & inspect embedded links via Context.dev scraper
        scraped_links = []
        for link in list(all_links)[:5]:  # Budget limit: inspect up to 5 links
            scraped = await context_provider.scrape_link(link)
            scraped_links.append(scraped)

        # 2. Extract verifiable factual claims
        claims = await extract_claims(posts, llm)

        # Persist claims using unique claim IDs
        claim_rows = {}
        for item in claims:
            parent_post = post_by_platform_id.get(item.post_id)
            if not parent_post:
                continue
            row = Claim(
                id=item.id,
                post_id=parent_post.id,
                text=item.text,
                claim_type=item.claim_type.value,
                importance=item.importance,
            )
            session.add(row)
            claim_rows[item.id] = row
        await session.flush()

        # 3. Ground truth research via Context.dev Web Search
        selected = prioritize(claims, settings.max_claims_to_verify)
        evals = []
        claim_results = []
        top_flags = []

        for item in selected:
            try:
                evidence = await context_provider.research_claim(item.text)
            except Exception as e:
                logger.warning("Researching claim '%s' failed: %s", item.text, e)
                evidence = []
            evaluation = await evaluate_evidence(item, evidence, llm)
            assessed_evidence = _with_evaluated_stances(evidence, evaluation)

            row = claim_rows.get(item.id)
            if row:
                row.verification_status = evaluation.verification_status
                for entry in assessed_evidence:
                    session.add(
                        Evidence(
                            claim_id=row.id,
                            url=entry.url,
                            title=entry.title,
                            source_domain=entry.source_domain,
                            excerpt=entry.excerpt,
                            stance=entry.stance.value,
                            source_quality=entry.source_quality,
                            relevance=entry.relevance,
                        )
                    )

            evals.append((item.importance, evaluation))
            claim_result_item = {
                "claim": item.text,
                "assessment": evaluation.verification_status,
                "reason": evaluation.reason,
                "accuracy": evaluation.accuracy,
                "evidence_confidence": evaluation.evidence_confidence,
                "source_assessments": [assessment.model_dump(mode="json") for assessment in evaluation.source_assessments],
                "evidence": [e.model_dump(mode="json") for e in assessed_evidence],
            }
            claim_results.append(claim_result_item)

            if evaluation.verification_status in ("contradicted", "partially_supported"):
                top_evidence = _best_evidence_for_assessment(assessed_evidence, evaluation.verification_status)
                top_flags.append({
                    "type": "falsehood",
                    "summary": item.text[:120],
                    "assessment": evaluation.verification_status,
                    "evidence_url": top_evidence.url if top_evidence else None,
                    "evidence_title": top_evidence.title if top_evidence else "Fact Check Source",
                })

        # 4. Spam & Harm Analysis
        spam_harm_eval = await llm.evaluate_spam_and_harm(posts, scraped_links)
        for spam_flag in spam_harm_eval.get("spam_flags", []):
            linked_evidence = _evidence_for_spam_flag(scraped_links, spam_flag)
            top_flags.append({
                "type": "spam",
                "summary": spam_flag,
                "assessment": "high_spam_risk",
                "evidence_url": linked_evidence.get("url") if linked_evidence else None,
                "evidence_title": linked_evidence.get("title") if linked_evidence else None,
            })

        for harm_flag in spam_harm_eval.get("harm_flags", []):
            top_flags.append({
                "type": "harm",
                "summary": harm_flag,
                "assessment": "policy_violation",
                "evidence_url": None,
                "evidence_title": None,
            })

        # 5. Composite Scoring
        scores = aggregate(
            evals,
            spam_score=spam_harm_eval["spam_score"],
            harm_score=spam_harm_eval["harm_score"],
        )

        completed_now = datetime.now(timezone.utc)
        result = {
            **scores,
            "posts_analyzed": len(posts),
            "factual_claims": sum(c.claim_type.value == "factual" for c in claims),
            "claims_analyzed": len(claims),
            "claims_verified": len(selected),
            "claims": claim_results,
            "top_flags": top_flags[:5],
            "scraped_links_count": len(scraped_links),
            "source_mode": "mock fixtures" if profile.platform == "mock" else "Context.dev Search & Scrape Gateway",
            "score_note": "A product heuristic based on the attributable public posts and evidence shown. Insufficient evidence produces no BS Index.",
        }

        # Update Analysis record
        analysis.status = "completed"
        analysis.overall_score = scores["bs_index"]
        analysis.badge_level = scores["badge_level"]
        analysis.badge_label = scores["badge_label"]
        analysis.factuality_score = scores["dimensions"]["falsehood"]
        analysis.spam_score = scores["dimensions"]["spam"]
        analysis.harm_score = scores["dimensions"]["harm"]
        analysis.evidence_score = scores["dimensions"]["evidence_quality"]
        analysis.sensationalism_score = scores["dimensions"]["sensationalism"]
        analysis.misleadingness_score = scores["dimensions"]["misleadingness"]
        analysis.claims_analyzed = len(claims)
        analysis.result = result
        analysis.completed_at = completed_now

        # Update profile timestamp
        profile.last_analyzed_at = completed_now

        await session.commit()
        return result

    except Exception:
        logger.exception("Analysis %s failed", analysis_id)
        await session.rollback()
        try:
            analysis.status = "failed"
            analysis.error = "Analysis failed; inspect server logs for details."
            analysis.completed_at = datetime.now(timezone.utc)
            await session.commit()
        except Exception:
            logger.exception("Failed to mark analysis %s as failed", analysis_id)
        raise
