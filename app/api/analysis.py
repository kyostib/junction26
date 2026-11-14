import logging
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
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
async def create_analysis(payload: AnalysisCreate, tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    url = str(payload.profile_url)
    username = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1] or "sample-account"
    profile = Profile(platform=payload.platform, username=username, profile_url=url)
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
    return AnalysisResponse(analysis_id=analysis.id, status=analysis.status, result=analysis.result,
                            error=analysis.error, created_at=analysis.created_at, completed_at=analysis.completed_at)
