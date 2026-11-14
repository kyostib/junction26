import logging
import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends
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
_overlay_mutation_lock = asyncio.Lock()


def sanitize_handle(handle: str) -> str:
    h = handle.strip()
    if "://" in h:
        path = urlparse(h).path.strip("/")
        return path.split("/")[0].lower() if path else "sample"
    return h.lstrip("@").strip().lower()


async def _batch_twitter_overlay(
    payload: OverlayBatchRequest,
    tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    refresh: bool = False,
):
    """Batch lookup for Twitter handles, optimized for sub-50ms Chrome extension rendering."""
    clean_handles = list({sanitize_handle(h) for h in payload.usernames if h.strip()})
    logger.info("Batch overlay requested for handles: %s", clean_handles)
    if not clean_handles:
        return OverlayBatchResponse(results={})

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
            if (
                analysis
                and analysis.status == "completed"
                and analysis.completed_at
                and analysis.completed_at > stale_threshold
                and not refresh
            ):
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
                is_insufficient = res_data.get("score_status") == "insufficient_evidence"
                results[handle] = UserOverlayResponse(
                    username=handle,
                    status="insufficient_evidence" if is_insufficient else "ready",
                    bs_index=analysis.overall_score,
                    badge_level=analysis.badge_level or "low",
                    badge_label=analysis.badge_label or f"[BS {round(analysis.overall_score or 0)}]",
                    dimensions=res_data.get("dimensions", {}),
                    top_flags=flags,
                    analyzed_at=analysis.completed_at,
                    report_url=f"/api/v1/analyses/{analysis.id}",
                )
                continue

            if analysis and analysis.status in {"queued", "running"}:
                results[handle] = UserOverlayResponse(
                    username=handle,
                    status="analyzing",
                    badge_label="[⏳ Queued...]" if analysis.status == "queued" else "[⏳ Analyzing...]",
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


@router.post("/twitter/batch", response_model=OverlayBatchResponse)
async def batch_twitter_overlay(
    payload: OverlayBatchRequest,
    tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    refresh: bool = False,
):
    # A single API process runs this MVP. Serializing lookup-and-enqueue avoids
    # concurrent extension polls creating duplicate queued analyses.
    async with _overlay_mutation_lock:
        return await _batch_twitter_overlay(payload, tasks, db, refresh)


@router.get("/twitter/{username}", response_model=UserOverlayResponse)
async def get_single_twitter_overlay(
    username: str,
    tasks: BackgroundTasks,
    refresh: bool = False,
    db: AsyncSession = Depends(get_db),
):
    handle = sanitize_handle(username)
    async with _overlay_mutation_lock:
        batch_res = await _batch_twitter_overlay(
            payload=OverlayBatchRequest(usernames=[handle]),
            tasks=tasks,
            db=db,
            refresh=refresh,
        )
    return batch_res.results.get(
        handle,
        UserOverlayResponse(username=handle, status="unknown"),
    )
