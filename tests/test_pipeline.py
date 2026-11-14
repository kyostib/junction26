import pytest
import sys
import types

from app.adapters.social.base import PostData
from app.pipeline.extract_claims import extract_claims
from app.pipeline.scoring import aggregate, prioritize
from app.providers.context_dev import ContextDevProvider, DemoWebResearchProvider, ProviderUnavailableError
from app.providers.llm import DemoLLMProvider
from app.schemas.claims import ClaimExtraction, ClaimType
from app.schemas.evidence import EvidenceEvaluation


@pytest.mark.asyncio
async def test_claim_extraction_and_priority():
    posts = [
        PostData(platform_post_id="a", text="The Great Wall of China is visible from the Moon with the naked eye."),
        PostData(platform_post_id="b", text="I love pizza and sunny weekends."),
    ]
    claims = await extract_claims(posts, DemoLLMProvider())
    assert len(claims) == 1
    assert claims[0].claim_type.value == "factual"
    assert prioritize(claims, 1) == claims


@pytest.mark.asyncio
async def test_demo_evidence_stance_and_score_are_deterministic():
    provider = DemoWebResearchProvider()
    evidence = await provider.research_claim("The Great Wall of China is visible from the Moon")
    assert evidence[0].stance.value == "contradicts"

    extracted = await extract_claims([PostData(platform_post_id="a", text="The Great Wall is visible from the Moon.")], DemoLLMProvider())
    evaluation = await DemoLLMProvider().evaluate_evidence(extracted[0], evidence)
    assert evaluation.accuracy == -1
    res1 = aggregate([(0.8, evaluation)], spam_score=20, harm_score=10)
    res2 = aggregate([(0.8, evaluation)], spam_score=20, harm_score=10)
    assert res1["bs_index"] == res2["bs_index"]
    assert res1["badge_level"] in ("medium", "high")


def test_opinion_not_prioritized_and_scoring_scale():
    opinion = ClaimExtraction(post_id="1", text="I love this", claim_type=ClaimType.opinion, importance=1)
    assert prioritize([opinion], 10) == []

    result = aggregate(
        [(1, EvidenceEvaluation(accuracy=-1, evidence_quality=0, misleadingness=1, sensationalism=1, verification_status="contradicted", reason="test"))],
        spam_score=100,
        harm_score=50,
    )
    # A fully severe dimension reaches the top of the normalized scale.
    assert result["bs_index"] == 100.0
    assert result["badge_level"] == "high"


def test_unknown_evidence_does_not_increase_bs_index():
    unknown = EvidenceEvaluation(
        accuracy=0,
        evidence_quality=0,
        misleadingness=0,
        sensationalism=0,
        verification_status="uncertain",
        reason="No evidence",
    )
    result = aggregate([(1, unknown)])
    assert result["bs_index"] == 0


def test_single_severe_dimension_is_not_diluted():
    from app.pipeline.scoring import compute_bs_index

    assert compute_bs_index(falsehood=0, spam=88, harm=0) >= 88


@pytest.mark.asyncio
async def test_missing_context_key_never_returns_demo_posts():
    provider = ContextDevProvider(api_key="test-key")
    provider.api_key = None
    with pytest.raises(ProviderUnavailableError):
        await provider.scrape_twitter_profile("real_user")


@pytest.mark.asyncio
async def test_context_dev_sdk_is_wrapped_and_normalized(monkeypatch):
    class FakeClient:
        def __init__(self, api_key):
            assert api_key == "test-secret"
            self.web = self

        def search(self, query, num_results, **kwargs):
            assert query == "claim" and num_results == 5
            return {"results": [{"url": "https://agency.gov/page", "title": "Agency", "description": "Relevant excerpt", "relevance": "high"}]}

    module = types.ModuleType("context.dev")
    module.ContextDev = FakeClient
    parent = types.ModuleType("context")
    parent.__path__ = []
    monkeypatch.setitem(sys.modules, "context", parent)
    monkeypatch.setitem(sys.modules, "context.dev", module)

    evidence = await ContextDevProvider("test-secret").research_claim("claim")
    assert evidence[0].source_domain == "agency.gov"
    assert evidence[0].source_quality == 0.95


def test_overlay_batch_api_end_to_end():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        # Test health check
        health = client.get("/health")
        assert health.status_code == 200

        # Test batch overlay endpoint
        resp = client.post("/api/v1/overlay/twitter/batch", json={"usernames": ["sample_user", "@another_user"]})
        assert resp.status_code == 200
        data = resp.json()["results"]
        assert "sample_user" in data
        assert "another_user" in data
        # Background task runs during synchronous TestClient post
        assert data["sample_user"]["status"] in ("ready", "queued", "analyzing", "failed")


def test_overlay_schema_has_summary_and_details():
    from app.schemas.analysis import UserOverlayResponse

    resp = UserOverlayResponse(
        username="alice",
        status="ready",
        bs_index=15.0,
        badge_level="low",
        badge_label="[✓ 15]",
        summary="High credibility profile.",
        details={
            "posts_analyzed": 5,
            "claims": [{"claim": "Sample claim", "assessment": "supported"}],
        },
    )
    dumped = resp.model_dump()
    assert dumped["summary"] == "High credibility profile."
    assert dumped["details"]["posts_analyzed"] == 5
    assert dumped["details"]["claims"][0]["assessment"] == "supported"


def test_config_max_tweets_and_profile_data():
    from app.adapters.social.base import ProfileData
    from app.core.config import get_settings

    settings = get_settings()
    assert hasattr(settings, "max_tweets_to_analyze")
    assert settings.max_tweets_to_analyze >= 1

    profile = ProfileData(
        platform="twitter",
        username="testuser",
        profile_url="https://x.com/testuser",
        bio="Researcher. https://example.org",
        bio_links=["https://example.org"],
    )
    assert profile.bio == "Researcher. https://example.org"
    assert profile.bio_links == ["https://example.org"]


def test_markdown_bio_and_bio_links_extraction():
    provider = ContextDevProvider(api_key="mock-key")
    sample_markdown = """
# Alice Researcher (@aliceresearch)
AI policy & climate scientist. Reading [Our Report](https://climate-policy.invalid/report) and https://techpolicy.invalid/brief.
Joined October 2017
1,245 Following  18.9K Followers
Posts  Replies  Highlights  Media

Alice Researcher @aliceresearch · 1h
The EU adopted a law targeting new cars with CO2 emissions from 2035; it did not prohibit every existing petrol car.

Alice Researcher @aliceresearch · 5h
Global average temperatures have risen substantially since the late 19th century, according to NASA data https://climate.nasa.gov.
"""
    bio, bio_links, tweets = provider._parse_profile_and_tweets("aliceresearch", sample_markdown, max_tweets=2)
    assert "AI policy & climate scientist" in bio
    assert "https://climate-policy.invalid/report" in bio_links
    assert "https://techpolicy.invalid/brief" in bio_links
    assert len(tweets) == 2
    assert "The EU adopted a law" in tweets[0]["text"]


@pytest.mark.asyncio
async def test_mock_adapter_bio_and_limits():
    from app.adapters.social.mock import MockSocialAdapter

    adapter = MockSocialAdapter()
    profile = await adapter.fetch_profile("https://x.com/sample")
    assert profile.bio != ""
    assert len(profile.bio_links) > 0

    posts = await adapter.fetch_posts(profile, limit=3)
    assert len(posts) == 3


@pytest.mark.asyncio
async def test_llm_synthesis_bio_scam_detection():
    from app.adapters.social.base import PostData, ProfileData
    from app.providers.llm import DemoLLMProvider

    llm = DemoLLMProvider()
    profile = ProfileData(
        platform="twitter",
        username="scammer",
        profile_url="https://x.com/scammer",
        bio="Exclusive crypto airdrop! Claim free allocation before presale ends.",
        bio_links=["https://airdrop-token-claims.invalid/presale"],
    )
    posts = [
        PostData(platform_post_id="1", text="Don't miss the 100x gem!"),
        PostData(platform_post_id="2", text="Nice weather today."),
    ]
    scraped_links = [
        {
            "url": "https://airdrop-token-claims.invalid/presale",
            "source": "bio",
            "domain": "airdrop-token-claims.invalid",
            "title": "Crypto Presale Claim Portal",
            "markdown": "Free token allocation for early presale participants.",
        }
    ]
    grounded_claims = []

    synthesis = await llm.synthesize_analysis(profile, posts, scraped_links, grounded_claims)
    assert synthesis["bs_index"] >= 70.0
    assert synthesis["dimensions"]["spam"] >= 80.0
    flags = synthesis["top_flags"]
    assert any("bio" in f["summary"].lower() or "presale" in f["summary"].lower() for f in flags)


@pytest.mark.asyncio
async def test_end_to_end_analysis_pipeline_with_bio():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.db.database import Base
    from app.db.models import Analysis, Profile
    from app.pipeline.analyze_profile import analyze_profile

    # Isolated in-memory async SQLite engine
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        profile = Profile(platform="mock", username="testuser", profile_url="https://example.com/testuser")
        session.add(profile)
        await session.flush()

        analysis = Analysis(profile_id=profile.id, status="queued")
        session.add(analysis)
        await session.commit()
        analysis_id = analysis.id

    async with session_factory() as session:
        result = await analyze_profile(analysis_id, session)

    assert result["bs_index"] >= 0.0
    assert "bio" in result
    assert "bio_links" in result
    assert "max_tweets_configured" in result
    assert "dimensions" in result
    assert "top_flags" in result
    assert result["posts_analyzed"] <= result["max_tweets_configured"]

    await engine.dispose()


def test_image_url_extraction_helpers():
    from app.providers.context_dev import extract_image_urls, extract_urls

    sample_text = (
        "Look at this breaking story! ![Chart](https://pbs.twimg.com/media/graphic123.jpg) "
        "Also read https://example.org/article and check out https://pbs.twimg.com/media/banner.png."
    )
    extracted_imgs = extract_image_urls(sample_text)
    assert "https://pbs.twimg.com/media/graphic123.jpg" in extracted_imgs
    assert "https://pbs.twimg.com/media/banner.png" in extracted_imgs

    # Ensure extract_urls does not treat images as landing pages
    web_links = extract_urls(sample_text)
    assert "https://example.org/article" in web_links
    assert "https://pbs.twimg.com/media/graphic123.jpg" not in web_links
    assert "https://pbs.twimg.com/media/banner.png" not in web_links


@pytest.mark.asyncio
async def test_overlay_legacy_cache_invalidation_and_rich_details():
    from datetime import datetime, timezone
    from fastapi import BackgroundTasks
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.api.overlay import batch_twitter_overlay
    from app.db.database import Base
    from app.db.models import Analysis, Profile
    from app.schemas.analysis import OverlayBatchRequest

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        # Case 1: Profile with a modern completed analysis containing posts_analyzed
        p1 = Profile(platform="twitter", username="modernuser", profile_url="https://x.com/modernuser")
        session.add(p1)
        await session.flush()

        completed_at = datetime.now(timezone.utc)
        a1 = Analysis(
            profile_id=p1.id,
            status="completed",
            overall_score=42.0,
            badge_level="medium",
            completed_at=completed_at,
            result={
                "bs_index": 42.0,
                "summary": "Modern test summary",
                "posts_analyzed": 8,
                "bio": "Crypto researcher & dev",
                "bio_links": ["https://example.org/project"],
                "claims": [{"claim": "Test claim", "assessment": "supported"}],
                "top_flags": [],
            },
        )
        session.add(a1)

        # Case 2: Profile with legacy analysis lacking posts_analyzed
        p2 = Profile(platform="twitter", username="legacyuser", profile_url="https://x.com/legacyuser")
        session.add(p2)
        await session.flush()

        a2 = Analysis(
            profile_id=p2.id,
            status="completed",
            overall_score=15.0,
            completed_at=completed_at,
            result={"dimensions": {}, "summary": "Old summary", "top_flags": []},  # lacks posts_analyzed
        )
        session.add(a2)
        await session.commit()

    async with session_factory() as session:
        tasks = BackgroundTasks()
        payload = OverlayBatchRequest(usernames=["modernuser", "legacyuser"])
        resp = await batch_twitter_overlay(payload, tasks, session)

        # Modern user should be ready with full details
        modern = resp.results["modernuser"]
        assert modern.status == "ready"
        assert modern.bs_index == 42.0
        assert modern.summary == "Modern test summary"
        assert modern.details is not None
        assert modern.details["posts_analyzed"] == 8
        assert modern.details["bio"] == "Crypto researcher & dev"
        assert len(modern.details["claims"]) == 1

        # Legacy user should have been invalidated and re-queued
        legacy = resp.results["legacyuser"]
        assert legacy.status == "queued"

    await engine.dispose()

