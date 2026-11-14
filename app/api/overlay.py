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
    logger.info("Batch overlay requested for handles: %s", clean_handles)
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
            if analysis and analysis.status == "completed" and completed_at and completed_at > stale_threshold and not force_refresh:
                res_data = analysis.result or {}
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
                    summary=res_data.get("summary", ""),
                    details={
                        "summary": res_data.get("summary", ""),
                        "bio": res_data.get("bio", ""),
                        "bio_links": res_data.get("bio_links", []),
                        "posts_analyzed": res_data.get("posts_analyzed", 0),
                        "max_tweets_configured": res_data.get("max_tweets_configured", 10),
                        "images_analyzed": res_data.get("images_analyzed", 0),
                        "image_findings": res_data.get("image_findings", []),
                        "claims_verified": res_data.get("claims_verified", 0),
                        "claims": res_data.get("claims", []),
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

            # If stale or failed, enqueue a new analysis
            new_analysis = Analysis(profile_id=prof.id, status="queued")
            db.add(new_analysis)
            await db.flush()
            tasks.add_task(run_analysis, new_analysis.id)
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
            await db.flush()

            new_analysis = Analysis(profile_id=new_profile.id, status="queued")
            db.add(new_analysis)
            await db.flush()

            tasks.add_task(run_analysis, new_analysis.id)
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
