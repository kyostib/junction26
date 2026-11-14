import asyncio
import json

from app.db.database import Base, SessionLocal, engine
from app.db.models import Analysis, Profile
from app.pipeline.analyze_profile import analyze_profile


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        profile = Profile(platform="mock", username="sample-account", profile_url="https://example.com/sample-account")
        session.add(profile)
        await session.flush()
        analysis = Analysis(profile_id=profile.id)
        session.add(analysis)
        await session.commit()
        result = await analyze_profile(analysis.id, session)
        print(json.dumps(result, indent=2))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
