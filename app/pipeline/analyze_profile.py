import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.social.mock import MockSocialAdapter
from app.adapters.social.twitter import ContextDevTwitterAdapter
from app.core.config import get_settings
from app.db.models import Analysis, Claim, Evidence, Post, Profile
from app.pipeline.evaluate_evidence import evaluate_evidence
from app.pipeline.extract_claims import extract_claims
from app.pipeline.scoring import aggregate, prioritize
from app.providers.context_dev import ContextDevProvider
from app.providers.llm import get_llm_provider

logger = logging.getLogger(__name__)


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

        # Select adapter: Twitter via Context.dev or Mock
        if profile.platform == "twitter":
            social = ContextDevTwitterAdapter(provider=context_provider)
        else:
            social = MockSocialAdapter()

        profile_data = await social.fetch_profile(profile.profile_url)
        posts = await social.fetch_posts(profile_data)

        # Persist only posts that are not already retained for this profile.
        # Reanalyses should not grow the database with identical scraped data.
        from sqlalchemy import select
        existing_post_ids = set(
            (await session.execute(select(Post.platform_post_id).where(Post.profile_id == profile.id))).scalars()
        )
        persisted_posts = []
        all_links = set()
        for item in posts:
            if item.platform_post_id in existing_post_ids:
                continue
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
        llm = get_llm_provider()
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

            row = claim_rows.get(item.id)
            if row:
                row.verification_status = evaluation.verification_status
                for entry in evidence:
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
                "evidence": [e.model_dump(mode="json") for e in evidence],
            }
            claim_results.append(claim_result_item)

            if evaluation.verification_status in ("contradicted", "partially_supported"):
                top_evidence = evidence[0] if evidence else None
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
            top_flags.append({
                "type": "spam",
                "summary": spam_flag,
                "assessment": "high_spam_risk",
                "evidence_url": scraped_links[0]["url"] if scraped_links else None,
                "evidence_title": "Uncloaked Destination Page",
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
            "source_mode": "Context.dev Search & Scrape Gateway",
            "score_note": "A product heuristic based on public posts, Context.dev web grounding, and link uncloaking.",
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
