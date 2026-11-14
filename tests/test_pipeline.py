import pytest
import sys
import types

from app.adapters.social.base import PostData
from app.core.errors import SocialDataUnavailableError
from app.pipeline.extract_claims import extract_claims
from app.pipeline.scoring import aggregate, prioritize
from app.providers.context_dev import ContextDevProvider, DemoWebResearchProvider
from app.providers.llm import DemoLLMProvider
from app.schemas.claims import ClaimExtraction, ClaimType
from app.schemas.evidence import EvidenceEvaluation, Stance, WebEvidence


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
    assert evaluation.source_assessments[0].url == evidence[0].url
    assert evaluation.source_assessments[0].stance == Stance.contradicts
    res1 = aggregate([(0.8, evaluation)], spam_score=20, harm_score=10)
    res2 = aggregate([(0.8, evaluation)], spam_score=20, harm_score=10)
    assert res1["bs_index"] == res2["bs_index"]
    assert res1["score_status"] == "insufficient_evidence"


def test_opinion_not_prioritized_and_scoring_scale():
    opinion = ClaimExtraction(post_id="1", text="I love this", claim_type=ClaimType.opinion, importance=1)
    assert prioritize([opinion], 10) == []

    result = aggregate(
        [(1, EvidenceEvaluation(accuracy=-1, evidence_quality=1, evidence_confidence=1, misleadingness=1, sensationalism=1, verification_status="contradicted", reason="test"))],
        spam_score=100,
        harm_score=50,
    )
    # accuracy -1 -> falsehood 100. bs_index = 0.45*100 + 0.35*100 + 0.2*50 = 45 + 35 + 10 = 90
    assert result["bs_index"] == 90.0
    assert result["badge_level"] == "high"


@pytest.mark.asyncio
async def test_one_low_quality_source_cannot_verify_a_claim():
    claim = ClaimExtraction(post_id="a", text="A claim", claim_type=ClaimType.factual, importance=1)
    evidence = [WebEvidence(
        url="https://untrusted.example/claim",
        source_domain="untrusted.example",
        excerpt="confirmed",
        relevance=0.5,
        source_quality=0.55,
        stance=Stance.supports,
    )]
    evaluation = await DemoLLMProvider().evaluate_evidence(claim, evidence)
    assert evaluation.verification_status == "uncertain"
    assert aggregate([(1, evaluation)])["bs_index"] is None


def test_no_evidence_has_no_bs_index():
    result = aggregate([])
    assert result["bs_index"] is None
    assert result["badge_level"] == "unknown"
    assert result["score_status"] == "insufficient_evidence"


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


def test_markdown_ingestion_rejects_unattributable_text():
    provider = ContextDevProvider("test-secret")
    markdown = """
Profile navigation and unrelated text that should not become a post.

The actual public claim is here: [post](https://x.com/example/status/123456789)
"""
    tweets = provider._parse_tweets_from_markdown("example", markdown)
    assert len(tweets) == 1
    assert tweets[0]["id"] == "123456789"
    assert tweets[0]["url"] == "https://x.com/example/status/123456789"


@pytest.mark.asyncio
async def test_twitter_ingestion_without_context_key_fails_closed():
    provider = ContextDevProvider("test-secret")
    provider.api_key = None
    with pytest.raises(SocialDataUnavailableError):
        await provider.scrape_twitter_profile("example")


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
        assert data["sample_user"]["status"] in ("ready", "queued", "analyzing", "insufficient_evidence")
