import logging
from urllib.parse import urlparse

import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import SessionLocal, get_db
from app.db.models import Analysis, Profile
from app.pipeline.analyze_profile import analyze_profile
from app.schemas.analysis import AnalysisCreate, AnalysisQueued, AnalysisResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/analyses", tags=["analyses"])


async def run_analysis(analysis_id: str):
    async with SessionLocal() as session:
        try:
            await analyze_profile(analysis_id, session)
        except Exception:
            logger.exception("Analysis %s failed", analysis_id)


@router.post("", response_model=AnalysisQueued, status_code=202)
async def create_analysis(
    payload: AnalysisCreate,
    tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    raw_url = str(payload.profile_url).strip()
    platform = payload.platform.lower().strip()
    if platform != "twitter":
        raise HTTPException(status_code=422, detail="Only the twitter platform is supported")

    if "://" in raw_url:
        parsed_url = urlparse(raw_url)
        if parsed_url.scheme not in {"http", "https"} or parsed_url.hostname not in {"x.com", "twitter.com", "www.x.com", "www.twitter.com"}:
            raise HTTPException(status_code=422, detail="profile_url must be an x.com or twitter.com URL")
        path = parsed_url.path.strip("/")
        username = path.split("/", 1)[0]
        profile_url = raw_url
    else:
        username = raw_url.lstrip("@").strip()
        profile_url = f"https://x.com/{username}" if platform == "twitter" else f"https://example.com/{username}"

    username = username.lower()
    if not re.fullmatch(r"[a-z0-9_]{1,15}", username):
        raise HTTPException(status_code=422, detail="profile_url must contain a valid X handle")

    # Deduplicate profile: lookup existing profile
    stmt = select(Profile).where(Profile.platform == platform, Profile.username == username)
    profile = (await db.execute(stmt)).scalar_one_or_none()

    if not profile:
        profile = Profile(platform=platform, username=username, profile_url=profile_url)
        db.add(profile)
        await db.flush()

    analysis = Analysis(profile_id=profile.id, status="queued")
    db.add(analysis)
    await db.commit()

    tasks.add_task(run_analysis, analysis.id)
    return AnalysisQueued(analysis_id=analysis.id)


@router.get("/{analysis_id}", response_model=AnalysisResponse)
async def get_analysis(analysis_id: str, db: AsyncSession = Depends(get_db)):
    analysis = await db.get(Analysis, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return AnalysisResponse(
        analysis_id=analysis.id,
        status=analysis.status,
        result=analysis.result,
        error=analysis.error,
        created_at=analysis.created_at,
        completed_at=analysis.completed_at,
    )
