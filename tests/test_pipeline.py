import pytest
import sys
import types

from app.adapters.social.base import PostData
from app.pipeline.extract_claims import extract_claims
from app.pipeline.scoring import aggregate, prioritize
from app.providers.context_dev import DemoWebResearchProvider
from app.providers.llm import DemoLLMProvider
from app.providers.context_dev import ContextDevProvider


@pytest.mark.asyncio
async def test_claim_extraction_and_priority():
    posts = [PostData(platform_post_id="a", text="The Great Wall of China is visible from the Moon with the naked eye."),
             PostData(platform_post_id="b", text="I love pizza and sunny weekends.")]
    claims = await extract_claims(posts, DemoLLMProvider())
    assert len(claims) == 1
    assert claims[0].claim_type.value == "factual"
    assert prioritize(claims, 1) == claims


@pytest.mark.asyncio
async def test_demo_evidence_stance_and_score_are_deterministic():
    provider = DemoWebResearchProvider()
    evidence = await provider.research_claim("The Great Wall of China is visible from the Moon")
    assert evidence[0].stance.value == "contradicts"
    evaluation = await DemoLLMProvider().evaluate_evidence(
        (await extract_claims([PostData(platform_post_id="a", text="The Great Wall is visible from the Moon.")], DemoLLMProvider()))[0], evidence)
    assert evaluation.accuracy == -1
    assert aggregate([(0.8, evaluation)]) == aggregate([(0.8, evaluation)])


def test_opinion_not_prioritized_and_scores_clamped():
    from app.schemas.claims import ClaimExtraction, ClaimType
    from app.schemas.evidence import EvidenceEvaluation
    opinion = ClaimExtraction(post_id="1", text="I love this", claim_type=ClaimType.opinion, importance=1)
    assert prioritize([opinion], 10) == []
    result = aggregate([(1, EvidenceEvaluation(accuracy=-1, evidence_quality=0, misleadingness=1,
                                                sensationalism=1, verification_status="contradicted", reason="test"))])
    assert result["overall_score"] == -10


@pytest.mark.asyncio
async def test_context_dev_sdk_is_wrapped_and_normalized(monkeypatch):
    class FakeClient:
        def __init__(self, api_key):
            assert api_key == "test-secret"
            self.web = self

        def search(self, query, num_results):
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
    assert evidence[0].source_quality == .95


def test_analysis_api_end_to_end():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        response = client.post("/api/v1/analyses", json={"platform": "mock", "profile_url": "https://example.com/sample"})
        assert response.status_code == 202
        analysis_id = response.json()["analysis_id"]
        result = client.get(f"/api/v1/analyses/{analysis_id}")
        assert result.status_code == 200
        assert result.json()["status"] == "completed"
        assert result.json()["result"]["posts_analyzed"] > 0
        assert result.json()["result"]["source_mode"].startswith("demo fixtures")
