import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.analysis import run_analysis
from app.db.database import get_db
from app.db.models import Analysis, Profile
from app.schemas.analysis import (
    FlagItem,
    OverlayBatchRequest,
    OverlayBatchResponse,
    UserOverlayResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/overlay", tags=["overlay"])


def sanitize_handle(handle: str) -> str:
    h = handle.strip()
    if "://" in h:
        path = urlparse(h).path.strip("/")
        h = path.split("/")[0] if path else ""
    else:
        h = h.lstrip("@").strip()
    h = h.lower()
    return h if re.fullmatch(r"[a-z0-9_]{1,15}", h) else ""


@router.post("/twitter/batch", response_model=OverlayBatchResponse)
async def batch_twitter_overlay(
    payload: OverlayBatchRequest,
    tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    force_refresh: bool = False,
):
    """Batch lookup for Twitter handles, optimized for sub-50ms Chrome extension rendering."""
    clean_handles = list({clean for h in payload.usernames if h.strip() if (clean := sanitize_handle(h))})
    logger.debug("Batch overlay requested for handles: %s", clean_handles)
    if not clean_handles:
        raise HTTPException(status_code=422, detail="At least one valid X handle is required")

    # Query profiles and their latest analyses
    stmt = (
        select(Profile, Analysis)
        .outerjoin(Analysis, Profile.id == Analysis.profile_id)
        .where(Profile.platform == "twitter", Profile.username.in_(clean_handles))
        .order_by(Analysis.created_at.desc())
    )
    rows = (await db.execute(stmt)).all()

    profile_map: dict[str, tuple[Profile, Analysis | None]] = {}
    for prof, analysis in rows:
        if prof.username not in profile_map:
            profile_map[prof.username] = (prof, analysis)

    now = datetime.now(timezone.utc)
    stale_threshold = now - timedelta(hours=24)
    results: dict[str, UserOverlayResponse] = {}

    for handle in clean_handles:
        if handle in profile_map:
            prof, analysis = profile_map[handle]
            # Check if there is a completed, fresh analysis
            completed_at = analysis.completed_at if analysis else None
            if completed_at and completed_at.tzinfo is None:
                completed_at = completed_at.replace(tzinfo=timezone.utc)
            
            res_data = (analysis.result or {}) if analysis else {}
            is_legacy_result = bool(res_data and "posts_analyzed" not in res_data)

            if analysis and analysis.status == "completed" and completed_at and completed_at > stale_threshold and not force_refresh and not is_legacy_result:
                flags = [
                    FlagItem(
                        type=f.get("type", "flag"),
                        summary=f.get("summary", ""),
                        assessment=f.get("assessment", ""),
                        evidence_url=f.get("evidence_url"),
                        evidence_title=f.get("evidence_title"),
                    )
                    for f in res_data.get("top_flags", [])
                ]

                summary_text = res_data.get("summary")
                if not summary_text:
                    score_val = round(analysis.overall_score or 0)
                    level_val = analysis.badge_level or "low"
                    if level_val == "low":
                        summary_text = f"Context.dev analysis evaluated @{handle} and found low risk ({score_val}/100 BS Index). Content shows high credibility with minimal deceptive or promotional signals."
                    elif level_val == "medium":
                        summary_text = f"Context.dev analysis evaluated @{handle} and identified moderate credibility concerns ({score_val}/100 BS Index). Exercise caution regarding promotional claims or unverified links."
                    else:
                        summary_text = f"Context.dev analysis evaluated @{handle} and detected high risk signals ({score_val}/100 BS Index). Multiple flagged assertions, spam patterns, or unverified claims were identified."

                claims_list = res_data.get("claims", [])
                if not claims_list and flags:
                    claims_list = [
                        {
                            "claim": f.summary,
                            "assessment": f.assessment or ("contradicted" if f.type == "falsehood" else "uncertain"),
                            "reason": f"Signal identified during multi-source web grounding ({f.type}).",
                            "evidence": [
                                {
                                    "url": f.evidence_url,
                                    "title": f.evidence_title or "Evidence Source",
                                    "source_domain": "Evidence Source",
                                    "excerpt": f.summary,
                                }
                            ] if f.evidence_url else [],
                        }
                        for f in flags
                    ]

                results[handle] = UserOverlayResponse(
                    username=handle,
                    status="ready",
                    bs_index=analysis.overall_score,
                    badge_level=analysis.badge_level or "low",
                    badge_label=analysis.badge_label or f"[BS {round(analysis.overall_score or 0)}]",
                    dimensions=res_data.get("dimensions", {}),
                    top_flags=flags,
                    analyzed_at=completed_at,
                    report_url=f"/api/v1/analyses/{analysis.id}",
                    summary=summary_text,
                    details={
                        "summary": summary_text,
                        "bio": res_data.get("bio", ""),
                        "bio_links": res_data.get("bio_links", []),
                        "posts_analyzed": res_data.get("posts_analyzed", 0),
                        "posts": res_data.get("posts", []),
                        "scraped_links": res_data.get("scraped_links", []),
                        "max_tweets_configured": res_data.get("max_tweets_configured", 10),
                        "claims_verified": res_data.get("claims_verified", len(claims_list)),
                        "claims": claims_list,
                        "propaganda_profile": res_data.get("propaganda_profile", []),
                        "propaganda_analysis_status": res_data.get("propaganda_analysis_status", "unavailable"),
                        "claim_evidence_breakdown": res_data.get("claim_evidence_breakdown", {}),
                        "scraped_links_count": res_data.get("scraped_links_count", 0),
                        "source_mode": res_data.get("source_mode", "Context.dev Search & Scrape Gateway"),
                        "score_note": res_data.get("score_note", ""),
                    },
                )
                continue

            if analysis and analysis.status == "running":
                results[handle] = UserOverlayResponse(
                    username=handle,
                    status="analyzing",
                    badge_label="[⏳ Analyzing...]",
                )
                continue

            if analysis and analysis.status == "queued":
                results[handle] = UserOverlayResponse(
                    username=handle,
                    status="queued",
                    badge_label="[⏳ Checking...]",
                )
                continue

            # Avoid turning an upstream outage into a request-driven retry
            # storm. A failed job is retried after a bounded cooldown.
            if analysis and analysis.status == "failed":
                created_at = analysis.created_at
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                if created_at > now - timedelta(hours=1):
                    results[handle] = UserOverlayResponse(
                        username=handle,
                        status="failed",
                        badge_label="[Unavailable]",
                    )
                    continue

            # If stale or failed, enqueue a new analysis if none is already active
            active_stmt = select(Analysis).where(
                Analysis.profile_id == prof.id,
                Analysis.status.in_(["queued", "running"]),
            )
            existing_active = (await db.execute(active_stmt)).scalars().first()
            if not existing_active:
                new_analysis = Analysis(profile_id=prof.id, status="queued")
                db.add(new_analysis)
                try:
                    await db.flush()
                    tasks.add_task(run_analysis, new_analysis.id)
                except Exception:
                    await db.rollback()
            results[handle] = UserOverlayResponse(
                username=handle,
                status="queued",
                badge_label="[⏳ Checking...]",
            )
        else:
            # First time seeing this user: create Profile & Analysis and queue worker
            new_profile = Profile(
                platform="twitter",
                username=handle,
                profile_url=f"https://x.com/{handle}",
            )
            db.add(new_profile)
            try:
                await db.flush()
            except Exception:
                await db.rollback()
                stmt_refetch = select(Profile).where(Profile.platform == "twitter", Profile.username == handle)
                new_profile = (await db.execute(stmt_refetch)).scalar_one_or_none()
                if not new_profile:
                    results[handle] = UserOverlayResponse(username=handle, status="unknown")
                    continue

            active_stmt = select(Analysis).where(
                Analysis.profile_id == new_profile.id,
                Analysis.status.in_(["queued", "running"]),
            )
            existing_active = (await db.execute(active_stmt)).scalars().first()
            if not existing_active:
                new_analysis = Analysis(profile_id=new_profile.id, status="queued")
                db.add(new_analysis)
                try:
                    await db.flush()
                    tasks.add_task(run_analysis, new_analysis.id)
                except Exception:
                    await db.rollback()

            results[handle] = UserOverlayResponse(
                username=handle,
                status="queued",
                badge_label="[⏳ Checking...]",
            )

    await db.commit()
    return OverlayBatchResponse(results=results)


@router.get("/twitter/{username}", response_model=UserOverlayResponse)
async def get_single_twitter_overlay(
    username: str,
    tasks: BackgroundTasks,
    refresh: bool = False,
    db: AsyncSession = Depends(get_db),
):
    handle = sanitize_handle(username)
    batch_res = await batch_twitter_overlay(
        payload=OverlayBatchRequest(usernames=[handle]),
        tasks=tasks,
        db=db,
        force_refresh=refresh,
    )
    return batch_res.results.get(
        handle,
        UserOverlayResponse(username=handle, status="unknown"),
    )
