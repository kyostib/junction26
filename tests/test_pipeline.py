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
    # accuracy -1 -> falsehood 100. bs_index = 0.45*100 + 0.35*100 + 0.2*50 = 45 + 35 + 10 = 90
    assert result["bs_index"] == 90.0
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
