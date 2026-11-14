import asyncio
import logging
from urllib.parse import urlparse

from app.core.config import get_settings
from app.schemas.evidence import Stance, WebEvidence

logger = logging.getLogger(__name__)


class ContextDevProvider:
    """Context.dev web-search adapter. SDK-specific code is kept in this module."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or get_settings().context_dev_api_key

    async def research_claim(self, claim: str) -> list[WebEvidence]:
        if not self.api_key:
            raise RuntimeError("CONTEXT_DEV_API_KEY is required for live web research")

        def search_sync():
            from context.dev import ContextDev
            client = ContextDev(api_key=self.api_key)
            return client.web.search(query=claim, num_results=5)

        try:
            response = await asyncio.wait_for(asyncio.to_thread(search_sync), timeout=45)
            raw = response.model_dump() if hasattr(response, "model_dump") else response
            if not isinstance(raw, dict):
                raw = vars(response)
            results = raw.get("results", [])
            evidence = []
            for item in results:
                if hasattr(item, "model_dump"):
                    item = item.model_dump()
                url = item.get("url")
                if not url:
                    continue
                excerpt = item.get("description") or ""
                markdown = item.get("markdown")
                if isinstance(markdown, dict):
                    excerpt = markdown.get("markdown") or excerpt
                elif hasattr(markdown, "markdown"):
                    excerpt = markdown.markdown or excerpt
                rel = item.get("relevance", "medium")
                relevance = rel if isinstance(rel, (int, float)) else {"high": .9, "medium": .65, "low": .35}.get(str(rel).lower(), .5)
                evidence.append(WebEvidence(url=url, title=item.get("title") or "", source_domain=urlparse(url).hostname or "",
                                            excerpt=str(excerpt)[:5000], relevance=relevance, source_quality=source_quality(urlparse(url).hostname or ""), stance=Stance.context_only))
            return evidence
        except Exception:
            logger.exception("Context.dev web research failed")
            raise


def source_quality(domain: str) -> float:
    domain = domain.lower().removeprefix("www.")
    if domain.endswith((".gov", ".gov.uk", ".europa.eu", ".int")):
        return .95
    if domain.endswith((".edu", ".ac.uk")):
        return .85
    if domain in {"reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "nature.com", "science.org"}:
        return .8
    return .55


class DemoWebResearchProvider:
    """Clearly synthetic evidence for local pipeline demos only; never used after live API failures."""
    is_demo = True

    async def research_claim(self, claim: str) -> list[WebEvidence]:
        lower = claim.lower()
        if "great wall" in lower:
            return [WebEvidence(url="https://demo.invalid/space/visibility", title="Demo fixture: wall visibility",
                                source_domain="demo.invalid", excerpt="Synthetic demo fixture: the Great Wall is not visible from the Moon unaided.",
                                relevance=.9, source_quality=.6, stance=Stance.contradicts)]
        if "eu adopted" in lower:
            return [WebEvidence(url="https://demo.invalid/eu/vehicle-rules", title="Demo fixture: vehicle rules",
                                source_domain="demo.invalid", excerpt="Synthetic demo fixture: 2035 rules address new cars; existing petrol cars are not universally prohibited.",
                                relevance=.9, source_quality=.6, stance=Stance.partially_supports)]
        if "temperatures have risen" in lower:
            return [WebEvidence(url="https://demo.invalid/climate/temperature", title="Demo fixture: global temperatures",
                                source_domain="demo.invalid", excerpt="Synthetic demo fixture: global average temperature has increased since the late 19th century.",
                                relevance=.9, source_quality=.6, stance=Stance.supports)]
        return []
