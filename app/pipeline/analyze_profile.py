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
from app.providers.context_dev import ContextDevProvider, is_external_link
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
            item.images = []
            links = item.metadata.get("links", []) if isinstance(item.metadata, dict) else []
            valid_links = [l for l in links if is_external_link(l, profile.username)]
            if isinstance(item.metadata, dict):
                item.metadata["links"] = valid_links
                item.metadata.pop("images", None)

            for link in valid_links:
                if link not in all_post_links:
                    all_post_links[link] = f"tweet-{item.platform_post_id}"

            if item.platform_post_id in existing_posts:
                persisted_posts.append(existing_posts[item.platform_post_id])
                continue

            post = Post(
                profile_id=profile.id,
                platform_post_id=str(item.platform_post_id)[:200],
                text=item.text,
                post_url=str(item.post_url)[:1000] if item.post_url else None,
                published_at=item.published_at,
                metadata_json=item.metadata,
            )
            session.add(post)
            persisted_posts.append(post)

        await session.flush()
        post_by_platform_id = {p.platform_post_id: p for p in persisted_posts}

        # 1. Uncloak & inspect external links strictly from recent sampled posts.
        link_sources: dict[str, str] = dict(all_post_links)

        async def scrape_link_with_source(url: str, src: str) -> dict:
            try:
                scraped = await context_provider.scrape_link(url)
                scraped["source"] = src
                return scraped
            except Exception as e:
                logger.warning("Scraping link %s failed: %s", url, e)
                return {"url": url, "source": src, "domain": "", "title": "", "markdown": "", "error": str(e)}

        llm = get_llm_provider()

        # 1 & 2. Concurrently scrape external links from posts and extract claims
        links_to_scrape = list(link_sources.items())[:6]
        scraped_links, claims = await asyncio.gather(
            asyncio.gather(*(scrape_link_with_source(u, s) for u, s in links_to_scrape)),
            extract_claims(posts, llm),
        )

        # Attach expanded link details back to the corresponding posts
        expanded_by_url = {}
        for s in scraped_links:
            orig_url = s.get("url", "")
            res_url = s.get("resolved_url") or orig_url
            domain = s.get("domain") or ""
            if not is_external_link(res_url, profile.username) or domain.lower().removeprefix("www.") in {"x.com", "twitter.com", "mobile.twitter.com"}:
                continue
            expanded_by_url[orig_url] = {
                "url": orig_url,
                "resolved_url": res_url,
                "domain": domain,
                "title": s.get("title", ""),
                "description": s.get("description") or (s.get("markdown") or "")[:250],
            }

        for p_item in posts:
            p_links = p_item.metadata.get("links", []) if isinstance(p_item.metadata, dict) else []
            for u in p_links:
                if u in expanded_by_url and expanded_by_url[u] not in p_item.expanded_links:
                    p_item.expanded_links.append(expanded_by_url[u])

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
                claim_type=str(getattr(item.claim_type, "value", item.claim_type))[:30],
                importance=item.importance,
            )
            session.add(row)
            claim_rows[item.id] = row
        await session.flush()

        # 3. Ground truth research via Context.dev Web Search (pipelined with evaluation)
        selected = prioritize(claims, settings.max_claims_to_verify)

        async def research_claim_safe(claim_text: str):
            try:
                return await context_provider.research_claim(claim_text)
            except Exception as e:
                logger.warning("Researching claim '%s' failed: %s", claim_text, e)
                return []

        async def research_and_evaluate(item):
            ev = await research_claim_safe(item.text)
            eval_result = await evaluate_evidence(item, ev, llm)
            return ev, eval_result

        eval_pairs = (
            await asyncio.gather(*(research_and_evaluate(item) for item in selected))
            if selected
            else []
        )
        all_evidence = [p[0] for p in eval_pairs]
        all_evaluations = [p[1] for p in eval_pairs]

        claim_results = []
        for item, evidence, evaluation in zip(selected, all_evidence, all_evaluations):
            row = claim_rows.get(item.id)
            if row:
                row.verification_status = str(evaluation.verification_status)[:40]
                for entry in evidence:
                    session.add(
                        Evidence(
                            claim_id=row.id,
                            url=str(entry.url)[:2000],
                            title=str(entry.title or "")[:1000],
                            source_domain=str(entry.source_domain or "")[:300],
                            excerpt=entry.excerpt,
                            stance=str(getattr(entry.stance, "value", entry.stance))[:40],
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
        falsehood_score = round(max(0.0, min(100.0, float(dims.get("falsehood", 0.0)))), 1)
        spam_score = round(max(0.0, min(100.0, float(dims.get("spam", 0.0)))), 1)
        harm_score = round(max(0.0, min(100.0, float(dims.get("harm", 0.0)))), 1)
        propaganda_score = round(max(0.0, min(100.0, float(dims.get("propaganda", 0.0)))), 1)
        sensationalism_score = round(max(0.0, min(100.0, float(dims.get("sensationalism", 0.0)))), 1)
        evidence_quality_score = round(max(0.0, min(100.0, float(dims.get("evidence_quality", 50.0)))), 1)

        dims["falsehood"] = falsehood_score
        dims["spam"] = spam_score
        dims["harm"] = harm_score
        dims["propaganda"] = propaganda_score
        dims["sensationalism"] = sensationalism_score
        dims["evidence_quality"] = evidence_quality_score
        dims.pop("misleadingness", None)

        propaganda_profile = synthesis.get("propaganda_profile", [])
        propaganda_analysis_status = synthesis.get("propaganda_analysis_status", "analyzed")

        bs_index = compute_bs_index(
            falsehood_score,
            spam_score,
            harm_score,
            propaganda_score,
            sensationalism_score,
        )
        badge_level, badge_label = compute_badge(bs_index)

        completed_now = datetime.now(timezone.utc)
        filtered_scraped_links = [
            {
                "url": l.get("url", ""),
                "resolved_url": l.get("resolved_url") or l.get("url", ""),
                "source": l.get("source", "post"),
                "domain": l.get("domain", ""),
                "title": l.get("title", ""),
                "preview": (l.get("description") or l.get("markdown") or "")[:250],
                "error": l.get("error"),
            }
            for l in scraped_links
            if is_external_link(l.get("resolved_url") or l.get("url", ""), profile.username)
            and l.get("domain", "").lower().removeprefix("www.") not in {"x.com", "twitter.com", "mobile.twitter.com"}
        ]
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
            "factual_claims": sum(getattr(c.claim_type, "value", c.claim_type) == "factual" for c in claims),
            "claims_analyzed": len(claims),
            "claims_verified": len(selected),
            "claims": claim_results,
            "posts": [
                {
                    "id": str(item.platform_post_id),
                    "text": item.text,
                    "url": str(item.post_url) if item.post_url else f"https://x.com/{profile.username}/status/{item.platform_post_id}",
                    "links": item.metadata.get("links", []) if isinstance(item.metadata, dict) else [],
                    "expanded_links": item.expanded_links,
                    "in_reply_to": item.in_reply_to,
                }
                for item in posts
            ],
            "scraped_links": filtered_scraped_links,
            "propaganda_profile": propaganda_profile,
            "propaganda_analysis_status": propaganda_analysis_status,
            "claim_evidence_breakdown": summarize_claim_evidence(claim_results, len(claims)),
            "scraped_links_count": len(filtered_scraped_links),
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
        analysis.evidence_score = evidence_quality_score
        analysis.sensationalism_score = sensationalism_score
        analysis.misleadingness_score = None
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
    finally:
        if "llm" in locals() and hasattr(llm, "aclose"):
            await llm.aclose()
