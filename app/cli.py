import asyncio
import json
import sys

from sqlalchemy import select

from app.db.database import Base, SessionLocal, engine
from app.db.models import Analysis, Profile
from app.pipeline.analyze_profile import analyze_profile


async def main():
    handle = sys.argv[1].lstrip("@") if len(sys.argv) > 1 else "sample-account"
    platform = "twitter" if len(sys.argv) > 1 else "mock"

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:
        # Check existing
        stmt = select(Profile).where(Profile.platform == platform, Profile.username == handle)
        profile = (await session.execute(stmt)).scalar_one_or_none()
        if not profile:
            profile = Profile(platform=platform, username=handle, profile_url=f"https://x.com/{handle}")
            session.add(profile)
            await session.flush()

        analysis = Analysis(profile_id=profile.id)
        session.add(analysis)
        await session.commit()

        print(f"Running BS Index analysis for @{handle} via Context.dev...")
        result = await analyze_profile(analysis.id, session)
        print("\n--- Analysis Result ---")
        print(json.dumps(result, indent=2))

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
