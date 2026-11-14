import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.social.mock import MockSocialAdapter
from app.adapters.social.twitter import ContextDevTwitterAdapter
from app.core.config import get_settings
from app.db.models import Analysis, Claim, Evidence, Post, Profile
from app.pipeline.evaluate_evidence import evaluate_evidence
from app.pipeline.extract_claims import extract_claims
from app.pipeline.profile_signals import summarize_claim_evidence
from app.pipeline.scoring import compute_badge, compute_bs_index, prioritize
from app.providers.context_dev import ContextDevProvider
from app.providers.llm import get_llm_provider
from app.schemas.claims import ClaimExtraction

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
        max_tweets = settings.max_tweets_to_analyze
        context_provider = ContextDevProvider()

        # Select adapter: Twitter via Context.dev or Mock
        if profile.platform == "twitter":
            social = ContextDevTwitterAdapter(provider=context_provider, max_tweets=max_tweets)
        else:
            social = MockSocialAdapter()

        profile_data = await social.fetch_profile(profile.profile_url)
        posts = await social.fetch_posts(profile_data, limit=max_tweets)

        # Persist posts while avoiding duplicate inserts on re-analysis
        existing_posts_stmt = select(Post).where(Post.profile_id == profile.id)
        existing_posts = {p.platform_post_id: p for p in (await session.execute(existing_posts_stmt)).scalars()}

        persisted_posts = []
        all_post_links: dict[str, str] = {}

        for item in posts:
            for link in item.metadata.get("links", []):
                if link not in all_post_links:
                    all_post_links[link] = f"tweet-{item.platform_post_id}"

            if item.platform_post_id in existing_posts:
                persisted_posts.append(existing_posts[item.platform_post_id])
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

        await session.flush()
        post_by_platform_id = {p.platform_post_id: p for p in persisted_posts}

        # 1. Uncloak & inspect links across user's bio and recent tweets
        link_sources: dict[str, str] = {}
        for link in profile_data.bio_links:
            link_sources[link] = "bio"
        for link, src in all_post_links.items():
            if link not in link_sources:
                link_sources[link] = src

        async def scrape_link_with_source(url: str, src: str) -> dict:
            try:
                scraped = await context_provider.scrape_link(url)
                scraped["source"] = src
                return scraped
            except Exception as e:
                logger.warning("Scraping link %s failed: %s", url, e)
                return {"url": url, "source": src, "domain": "", "title": "", "markdown": "", "error": str(e)}

        links_to_scrape = list(link_sources.items())[:6]
        scraped_links = await asyncio.gather(*(scrape_link_with_source(u, s) for u, s in links_to_scrape))

        llm = get_llm_provider()

        # 2. Extract verifiable factual claims from text
        claims = await extract_claims(posts, llm)
        propaganda_profile = await llm.evaluate_propaganda(posts)

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
        claim_results = []

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

            claim_results.append({
                "claim": item.text,
                "assessment": evaluation.verification_status,
                "reason": evaluation.reason,
                "accuracy": evaluation.accuracy,
                "evidence": [e.model_dump(mode="json") for e in evidence],
            })

        # 4. Final Unified LLM Synthesis
        synthesis = await llm.synthesize_analysis(
            profile=profile_data,
            posts=posts,
            scraped_links=scraped_links,
            grounded_claims=claim_results,
        )

        dims = synthesis.get("dimensions", {})
        bs_index = compute_bs_index(
            float(dims.get("falsehood", 0.0)),
            float(dims.get("spam", 0.0)),
            float(dims.get("harm", 0.0)),
        )
        badge_level, badge_label = compute_badge(bs_index)

        completed_now = datetime.now(timezone.utc)
        result = {
            "bs_index": bs_index,
            "overall_score": bs_index,
            "badge_level": badge_level,
            "badge_label": badge_label,
            "dimensions": dims,
            "summary": synthesis.get("summary", ""),
            "top_flags": synthesis.get("top_flags", [])[:5],
            "bio": profile_data.bio,
            "bio_links": profile_data.bio_links,
            "posts_analyzed": len(posts),
            "max_tweets_configured": max_tweets,
            "factual_claims": sum(c.claim_type.value == "factual" for c in claims),
            "claims_analyzed": len(claims),
            "claims_verified": len(selected),
            "claims": claim_results,
            "propaganda_profile": propaganda_profile.get("techniques", []),
            "propaganda_analysis_status": propaganda_profile.get("status", "unavailable"),
            "claim_evidence_breakdown": summarize_claim_evidence(claim_results, len(claims)),
            "scraped_links_count": len(scraped_links),
            "source_mode": "Context.dev Search & Scrape Gateway",
            "score_note": "A product heuristic based on public posts, Context.dev web grounding, and link uncloaking.",
        }

        # Update Analysis record
        analysis.status = "completed"
        analysis.overall_score = bs_index
        analysis.badge_level = badge_level
        analysis.badge_label = badge_label
        analysis.factuality_score = dims.get("falsehood", 0.0)
        analysis.spam_score = dims.get("spam", 0.0)
        analysis.harm_score = dims.get("harm", 0.0)
        analysis.evidence_score = dims.get("evidence_quality", 0.0)
        analysis.sensationalism_score = dims.get("sensationalism", 0.0)
        analysis.misleadingness_score = dims.get("misleadingness", 0.0)
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
