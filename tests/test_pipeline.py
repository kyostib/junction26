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
    assert compute_bs_index(falsehood=0, spam=0, harm=0, propaganda=75) >= 75


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


def test_is_external_link_filters_share_and_internal_urls():
    from app.providers.context_dev import is_external_link

    assert not is_external_link("https://x.com/aliceresearch", "aliceresearch")
    assert not is_external_link("https://twitter.com/aliceresearch", "aliceresearch")
    assert not is_external_link("https://x.com/share?url=https%3A%2F%2Fx.com")
    assert not is_external_link("https://twitter.com/intent/tweet?text=hello")
    assert not is_external_link("https://pbs.twimg.com/media/graphic123.jpg")
    assert not is_external_link("https://video.twimg.com/ext_tw_video/123/video.mp4")
    assert not is_external_link("https://example.org/photo.png?size=large")
    assert not is_external_link("https://example.org/animation.gif")
    assert not is_external_link("https://example.org/clip.mp4")
    assert not is_external_link("https://example.org/stream.webm")
    assert is_external_link("https://climate-policy.invalid/report", "aliceresearch")
    assert is_external_link("https://techpolicy.invalid/brief", "aliceresearch")
    assert is_external_link("https://t.co/abc123xyz", "aliceresearch")


@pytest.mark.asyncio
async def test_bio_and_media_strictly_excluded_from_consideration():
    from app.adapters.social.base import PostData, ProfileData
    from app.providers.llm import DemoLLMProvider

    llm = DemoLLMProvider()
    # Profile with scam / toxic bio, but clean posts and no links
    profile = ProfileData(
        platform="twitter",
        username="cleantweets",
        profile_url="https://x.com/cleantweets",
        bio="Presale 100x gem! Airdrop free allocation! DM for promo! You idiot hate speech",
        bio_links=["https://scam-airdrop.invalid/presale"],
    )
    posts = [
        PostData(platform_post_id="1", text="Enjoying a great coffee this morning.", images=["https://example.org/pic.png"]),
        PostData(platform_post_id="2", text="Reviewing research papers on renewable energy."),
    ]
    scraped_links = []
    grounded_claims = []

    synthesis = await llm.synthesize_analysis(profile, posts, scraped_links, grounded_claims)
    # Bio must be excluded: spam and harm scores must be 0 and no bio flags generated
    assert synthesis["dimensions"]["spam"] == 0.0
    assert synthesis["dimensions"]["harm"] == 0.0
    assert len(synthesis["top_flags"]) == 0


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
    assert result["bio"] == "Tech researcher & market observer. Insights on climate and European tech policy."
    assert result["bio_links"] == ["https://example.org/report"]
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


def test_inconclusive_evidence_with_nonzero_quality_does_not_inflate_falsehood():
    # When web search finds pages (quality > 0) but evidence is inconclusive (uncertain),
    # it must NOT be treated as 50% false.
    inconclusive = EvidenceEvaluation(
        accuracy=0.0,
        evidence_quality=0.85,
        misleadingness=0.0,
        sensationalism=0.0,
        verification_status="uncertain",
        reason="Evidence is inconclusive across sources.",
    )
    result = aggregate([(1.0, inconclusive)])
    assert result["bs_index"] == 0.0
    assert result["dimensions"]["falsehood"] == 0.0


@pytest.mark.asyncio
async def test_benign_colloquialisms_do_not_trigger_harm_score():
    llm = DemoLLMProvider()
    posts = [
        PostData(platform_post_id="p1", text="I hate Mondays and bad coffee."),
        PostData(platform_post_id="p2", text="I would die for this pizza, absolutely delicious!"),
        PostData(platform_post_id="p3", text="That workout yesterday will kill your legs."),
    ]
    res = await llm.evaluate_spam_and_harm(posts, [])
    assert res["harm_score"] == 0.0
    assert len(res["harm_flags"]) == 0


@pytest.mark.asyncio
async def test_severe_abusive_language_still_triggers_harm_score():
    llm = DemoLLMProvider()
    posts = [
        PostData(platform_post_id="p1", text="You are all subhuman traitors and should die in a fire!"),
    ]
    res = await llm.evaluate_spam_and_harm(posts, [])
    assert res["harm_score"] >= 80.0
    assert len(res["harm_flags"]) > 0


@pytest.mark.asyncio
async def test_create_analysis_deduplication_does_not_crash_integrity_error():
    from fastapi import BackgroundTasks
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.api.analysis import create_analysis
    from app.db.database import Base
    from app.schemas.analysis import AnalysisCreate

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        tasks = BackgroundTasks()
        payload = AnalysisCreate(profile_url="https://x.com/jack")
        
        # Call 1: Creates profile and queued analysis
        res1 = await create_analysis(payload, tasks, session)
        assert res1.status == "queued"
        
        # Call 2: Concurrent or duplicate call should return existing active analysis
        res2 = await create_analysis(payload, tasks, session)
        assert res2.status == "queued"
        assert res2.analysis_id == res1.analysis_id

    await engine.dispose()


@pytest.mark.asyncio
async def test_propaganda_queried_and_validated_alongside_other_dimensions():
    from app.adapters.social.base import PostData, ProfileData
    from app.providers.llm import GeminiLLMProvider

    provider = GeminiLLMProvider(api_key="fake-key")

    # Mock _generate_json response with out-of-range dimensions and propaganda techniques
    async def mock_generate_json(prompt="", parts=None):
        return {
            "dimensions": {
                "falsehood": 120.0,  # Should be clamped to 100.0
                "spam": -15.0,       # Should be clamped to 0.0
                "harm": 45.0,
                "propaganda": 85.5,
                "evidence_quality": 8.0,
                "misleadingness": 6.5,
                "sensationalism": 7.0,
            },
            "propaganda_techniques": [
                {
                    "key": "loaded_language",
                    "score": 0.85,
                    "post_id": "p-1",
                    "matched_text": "destroy the country",
                    "reason": "Emotive framing",
                }
            ],
            "summary": "Profile with heavy emotional rhetoric.",
            "top_flags": [
                {
                    "type": "propaganda",
                    "summary": "Manipulative rhetoric: 'destroy the country'",
                    "assessment": "rhetoric_flag",
                    "evidence_url": None,
                    "evidence_title": None,
                }
            ],
        }

    provider._generate_json = mock_generate_json
    posts = [PostData(platform_post_id="p-1", text="This bill will destroy the country!")]
    profile = ProfileData(platform="twitter", username="pundit", profile_url="https://x.com/pundit")

    res = await provider.synthesize_analysis(profile, posts, [], [])

    # Verify dimensions are clamped and validated consistently
    dims = res["dimensions"]
    assert dims["falsehood"] == 100.0
    assert dims["spam"] == 0.0
    assert dims["harm"] == 45.0
    assert dims["propaganda"] == 85.5
    assert dims["sensationalism"] == 7.0

    # Verify propaganda profile and flags
    assert res["propaganda_analysis_status"] == "analyzed"
    assert len(res["propaganda_profile"]) > 0
    loaded = next(t for t in res["propaganda_profile"] if t["key"] == "loaded_language")
    assert loaded["score"] == 0.85
    assert len(loaded["examples"]) == 1

    assert len(res["top_flags"]) == 1
    assert res["top_flags"][0]["type"] == "propaganda"


@pytest.mark.asyncio
async def test_demo_provider_evaluates_propaganda_heuristics():
    from app.adapters.social.base import PostData, ProfileData
    from app.providers.llm import DemoLLMProvider

    llm = DemoLLMProvider()
    posts = [
        PostData(platform_post_id="1", text="Everyone knows this ridiculous plan will destroy the country overnight!"),
        PostData(platform_post_id="2", text="Real patriots will vote against this corrupt cabal."),
    ]
    profile = ProfileData(platform="twitter", username="rhetoric_user", profile_url="https://x.com/rhetoric_user")

    res = await llm.synthesize_analysis(profile, posts, [], [])

    # Verify propaganda is computed and included in dimensions
    assert "propaganda" in res["dimensions"]
    assert res["dimensions"]["propaganda"] > 0.0
    assert res["propaganda_analysis_status"] == "analyzed"

    # Verify active techniques are found
    assert any(t["score"] > 0 for t in res["propaganda_profile"])
    # Verify top_flags includes a propaganda flag
    assert any(f["type"] == "propaganda" for f in res["top_flags"])



def test_dev_api_logging_written_to_serial_output_in_dev_mode(capsys, monkeypatch):
    from app.core import config
    from app.core.dev_logger import log_dev_api_call

    fake_settings = config.Settings(environment="dev")
    monkeypatch.setattr(config, "get_settings", lambda: fake_settings)

    log_dev_api_call(
        provider="Context.dev",
        operation="web.search",
        query={"query": "test query", "api_key": "supersecret123"},
        response={"results": [{"title": "Test Title", "url": "https://example.com"}]},
        status="SUCCESS",
        duration_ms=45.2,
    )

    captured = capsys.readouterr()
    assert "PROVIDER: Context.dev" in captured.out
    assert "OPERATION: web.search" in captured.out
    assert "test query" in captured.out
    assert "[REDACTED]" in captured.out
    assert "supersecret123" not in captured.out
    assert "Test Title" in captured.out


def test_dev_api_logging_disabled_in_prod_mode(capsys, monkeypatch):
    from app.core import config
    from app.core.dev_logger import log_dev_api_call

    fake_settings = config.Settings(environment="prod")
    monkeypatch.setattr(config, "get_settings", lambda: fake_settings)

    log_dev_api_call(
        provider="llm",
        operation="gemini.generateContent",
        query={"prompt": "should not be logged"},
        response={"candidates": []},
    )

    captured = capsys.readouterr()
    assert captured.out == ""


def test_dev_api_logging_excludes_bsmeter_api_calls(capsys, monkeypatch):
    from app.core import config
    from app.core.dev_logger import log_dev_api_call

    fake_settings = config.Settings(environment="dev")
    monkeypatch.setattr(config, "get_settings", lambda: fake_settings)

    # These should be strictly excluded
    log_dev_api_call(
        provider="bsmeter_api",
        operation="POST /api/v1/overlay/twitter/batch",
        query={"usernames": ["testuser"]},
        response={"scores": []},
    )
    log_dev_api_call(
        provider="bs_meter",
        operation="GET /health",
        query={},
        response={"status": "ok"},
    )
    log_dev_api_call(
        provider="fastapi_internal",
        operation="create_analysis",
        query={"profile_url": "https://x.com/test"},
        response={},
    )

    # Since only excluded BS Meter API calls were made, nothing should be logged to serial output
    captured_excluded = capsys.readouterr()
    assert captured_excluded.out == ""

    # Now make allowed calls to context.dev and llm
    log_dev_api_call(
        provider="context.dev",
        operation="client.web.search",
        query={"query": "test context query"},
        response={"results": []},
    )
    log_dev_api_call(
        provider="llm",
        operation="Gemini generateContent",
        query={"prompt": "test llm prompt"},
        response={"text": "{}"},
    )

    captured = capsys.readouterr()
    assert "PROVIDER: context.dev" in captured.out
    assert "PROVIDER: llm" in captured.out
    assert "bsmeter" not in captured.out.lower()
    assert "fastapi" not in captured.out.lower()


def test_parse_profile_and_tweets_thread_reply_separation():
    provider = ContextDevProvider(api_key="mock-key")
    sample_markdown = """
# Researcher Jane (@janeresearch)
AI & Renewable Energy Researcher. Direct link: https://jane-lab.invalid/brief.
Joined March 2019
500 Following  5,200 Followers
Posts  Replies  Highlights  Media

Jane Researcher @janeresearch · 1h
Published our Q3 overview on European grid storage: https://jane-lab.invalid/grid-2026.

Dr. Critic @critic · 4h
Solar expansion in Northern Europe is impossible due to seasonal darkness.

Jane Researcher @janeresearch · 2h
Replying to @critic
Actually, cross-border HVDC interconnections and wind complement winter demand nicely. https://hvdc-europe.invalid/stats

Jane Researcher @janeresearch · 1d
First paragraph of an extended post detailing battery supply chains.

Second paragraph explaining sodium-ion alternative chemistry and cost declines.
"""
    bio, bio_links, tweets = provider._parse_profile_and_tweets("janeresearch", sample_markdown, max_tweets=10)

    # 1. Bio and bio links
    assert "AI & Renewable Energy Researcher" in bio
    assert "https://jane-lab.invalid/brief" in bio_links
    assert not any("x.com" in l or "twitter.com" in l for l in bio_links)

    # 2. Tweets count and separation
    assert len(tweets) == 3

    # First tweet: standalone
    assert "Published our Q3 overview" in tweets[0]["text"]
    assert tweets[0]["in_reply_to"] is None
    assert "https://jane-lab.invalid/grid-2026" in tweets[0]["links"]

    # Second tweet: reply to @critic with thread context
    assert "Actually, cross-border HVDC" in tweets[1]["text"]
    assert "Replying to" not in tweets[1]["text"]
    assert tweets[1]["in_reply_to"] is not None
    assert tweets[1]["in_reply_to"]["author"] == "@critic"
    assert "Solar expansion in Northern Europe is impossible" in tweets[1]["in_reply_to"]["text"]
    assert "https://hvdc-europe.invalid/stats" in tweets[1]["links"]

    # Third tweet: multi-paragraph preserved in single post
    assert "First paragraph of an extended post" in tweets[2]["text"]
    assert "Second paragraph explaining sodium-ion" in tweets[2]["text"]
    assert tweets[2]["in_reply_to"] is None


def test_post_data_expanded_links_and_thread_context():
    post = PostData(
        platform_post_id="p-101",
        text="Solar battery storage has reached parity.",
        in_reply_to={"author": "@other", "text": "Storage is too expensive."},
        expanded_links=[
            {
                "url": "https://t.co/xyz",
                "resolved_url": "https://energy.invalid/storage",
                "domain": "energy.invalid",
                "title": "Grid Storage Parity",
                "description": "Analysis of battery storage costs.",
            }
        ],
    )
    dumped = post.model_dump()
    assert dumped["in_reply_to"]["author"] == "@other"
    assert dumped["expanded_links"][0]["domain"] == "energy.invalid"
    assert dumped["expanded_links"][0]["resolved_url"] == "https://energy.invalid/storage"


@pytest.mark.asyncio
async def test_analyze_profile_posts_have_expanded_links_and_bio():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.db.database import Base
    from app.db.models import Analysis, Profile
    from app.pipeline.analyze_profile import analyze_profile

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        profile = Profile(platform="mock", username="testuser", profile_url="https://x.com/testuser")
        session.add(profile)
        await session.flush()
        analysis = Analysis(profile_id=profile.id, status="queued")
        session.add(analysis)
        await session.commit()
        analysis_id = analysis.id

    async with session_factory() as session:
        result = await analyze_profile(analysis_id, session)

    # Check clean bio & bio links
    assert result["bio"] != ""
    assert len(result["bio_links"]) > 0

    # Check posts have expanded_links and in_reply_to fields
    assert "posts" in result
    assert len(result["posts"]) > 0
    post_with_link = next((p for p in result["posts"] if p.get("links")), None)
    assert post_with_link is not None
    assert "expanded_links" in post_with_link
    assert len(post_with_link["expanded_links"]) > 0
    assert post_with_link["expanded_links"][0]["domain"] == "example.org"
    assert "in_reply_to" in post_with_link

    # Check propaganda metrics in unified synthesis result
    assert "propaganda" in result["dimensions"]
    assert isinstance(result["dimensions"]["propaganda"], float)
    assert result["propaganda_analysis_status"] == "analyzed"
    assert isinstance(result["propaganda_profile"], list)

    await engine.dispose()


@pytest.mark.asyncio
async def test_propaganda_queried_and_validated_alongside_other_dimensions():
    from app.adapters.social.base import PostData, ProfileData
    from app.providers.llm import GeminiLLMProvider

    provider = GeminiLLMProvider(api_key="fake-key")

    # Mock _generate_json response with out-of-range dimensions and propaganda techniques
    async def mock_generate_json(prompt="", parts=None):
        return {
            "dimensions": {
                "falsehood": 120.0,  # Should be clamped to 100.0
                "spam": -15.0,       # Should be clamped to 0.0
                "harm": 45.0,
                "propaganda": 85.5,
                "evidence_quality": 8.0,
                "misleadingness": 6.5,
                "sensationalism": 7.0,
            },
            "propaganda_techniques": [
                {
                    "key": "loaded_language",
                    "score": 0.85,
                    "post_id": "p-1",
                    "matched_text": "destroy the country",
                    "reason": "Emotive framing",
                }
            ],
            "summary": "Profile with heavy emotional rhetoric.",
            "top_flags": [
                {
                    "type": "propaganda",
                    "summary": "Manipulative rhetoric: 'destroy the country'",
                    "assessment": "rhetoric_flag",
                    "evidence_url": None,
                    "evidence_title": None,
                }
            ],
        }

    provider._generate_json = mock_generate_json
    posts = [PostData(platform_post_id="p-1", text="This bill will destroy the country!")]
    profile = ProfileData(platform="twitter", username="pundit", profile_url="https://x.com/pundit")

    res = await provider.synthesize_analysis(profile, posts, [], [])

    # Verify dimensions are clamped and validated consistently
    dims = res["dimensions"]
    assert dims["falsehood"] == 100.0
    assert dims["spam"] == 0.0
    assert dims["harm"] == 45.0
    assert dims["propaganda"] == 85.5
    assert dims["sensationalism"] == 7.0

    # Verify propaganda profile and flags
    assert res["propaganda_analysis_status"] == "analyzed"
    assert len(res["propaganda_profile"]) > 0
    loaded = next(t for t in res["propaganda_profile"] if t["key"] == "loaded_language")
    assert loaded["score"] == 0.85
    assert len(loaded["examples"]) == 1

    assert len(res["top_flags"]) == 1
    assert res["top_flags"][0]["type"] == "propaganda"


@pytest.mark.asyncio
async def test_demo_provider_evaluates_propaganda_heuristics():
    from app.adapters.social.base import PostData, ProfileData
    from app.providers.llm import DemoLLMProvider

    llm = DemoLLMProvider()
    posts = [
        PostData(platform_post_id="1", text="Everyone knows this ridiculous plan will destroy the country overnight!"),
        PostData(platform_post_id="2", text="Real patriots will vote against this corrupt cabal."),
    ]
    profile = ProfileData(platform="twitter", username="rhetoric_user", profile_url="https://x.com/rhetoric_user")

    res = await llm.synthesize_analysis(profile, posts, [], [])

    # Verify propaganda is computed and included in dimensions
    assert "propaganda" in res["dimensions"]
    assert res["dimensions"]["propaganda"] > 0.0
    assert res["propaganda_analysis_status"] == "analyzed"

    # Verify active techniques are found
    assert any(t["score"] > 0 for t in res["propaganda_profile"])
    # Verify top_flags includes a propaganda flag
    assert any(f["type"] == "propaganda" for f in res["top_flags"])






