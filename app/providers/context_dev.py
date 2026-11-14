import asyncio
import logging
import re
from urllib.parse import urlparse

from app.core.config import get_settings
from app.schemas.evidence import Stance, WebEvidence

logger = logging.getLogger(__name__)


class ProviderUnavailableError(RuntimeError):
    """Raised when live social data cannot be obtained safely."""


def extract_urls(text: str) -> list[str]:
    return re.findall(r"https?://[^\s)\]\"'>]+", text)


def source_quality(domain: str) -> float:
    domain = domain.lower().removeprefix("www.")
    if domain.endswith((".gov", ".gov.uk", ".europa.eu", ".int")):
        return 0.95
    if domain.endswith((".edu", ".ac.uk")):
        return 0.85
    if domain in {"reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "nature.com", "science.org", "snopes.com", "factcheck.org"}:
        return 0.85
    if domain in {"wikipedia.org", "nytimes.com", "washingtonpost.com", "wsj.com", "theguardian.com"}:
        return 0.75
    return 0.55


def _extract_markdown(response) -> str:
    """Extracts markdown text supporting both latest and legacy Context.dev SDK responses."""
    if hasattr(response, "markdown"):
        md = response.markdown
        if hasattr(md, "data"):
            return str(md.data or "")
        if isinstance(md, dict):
            return str(md.get("data") or md.get("markdown") or "")
        return str(md or "")
    if isinstance(response, dict):
        md = response.get("markdown")
        if isinstance(md, dict):
            return str(md.get("data") or md.get("markdown") or "")
        return str(md or "")
    return ""


class ContextDevProvider:
    """Context.dev unified gateway using the latest SDK:
    1. Twitter profile & tweet scraping (client.web.scrape with main_content_only)
    2. Fallback tweet search via Context.dev search (bypasses X login walls)
    3. Embedded link uncloaking & inspection (client.web.scrape)
    4. Live web search for fact-checking (client.web.search)
    """

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or get_settings().context_dev_api_key

    async def scrape_twitter_profile(self, username: str) -> dict:
        """Scrapes a Twitter/X user profile using Context.dev with headless JS rendering."""
        clean_user = username.lstrip("@").strip()
        url = f"https://x.com/{clean_user}"

        if not self.api_key:
            raise ProviderUnavailableError("CONTEXT_DEV_API_KEY is not configured")

        def scrape_sync():
            from context.dev import ContextDev
            client = ContextDev(api_key=self.api_key)
            return client.web.scrape(
                url=url,
                formats={"markdown": True},
                shared_params={"main_content_only": True},
            )

        try:
            logger.info("Executing Context.dev web.scrape for Twitter profile @%s", clean_user)
            response = await asyncio.wait_for(asyncio.to_thread(scrape_sync), timeout=60)
            markdown = _extract_markdown(response)
            tweets = self._parse_tweets_from_markdown(clean_user, markdown)

            # If direct scrape was blocked by an X login wall, search for public tweets via Context.dev search
            if not tweets:
                logger.info("Direct scrape for @%s returned no tweets (login wall). Falling back to Context.dev search.", clean_user)
                tweets = await self._search_tweets_via_context_dev(clean_user)

            return {
                "username": clean_user,
                "profile_url": url,
                "raw_markdown": markdown,
                "tweets": tweets,
            }
        except Exception as e:
            logger.warning("Context.dev scrape for @%s encountered error: %s. Trying search fallback.", clean_user, e)
            try:
                tweets = await self._search_tweets_via_context_dev(clean_user)
                if tweets:
                    return {
                        "username": clean_user,
                        "profile_url": url,
                        "raw_markdown": "",
                        "tweets": tweets,
                    }
            except Exception as search_err:
                logger.warning("Context.dev search fallback also failed: %s", search_err)
            raise ProviderUnavailableError("Context.dev could not retrieve public posts") from e

    async def _search_tweets_via_context_dev(self, username: str) -> list[dict]:
        """Searches for public tweets from a user using Context.dev web search (bypasses login walls)."""
        logger.info("Executing Context.dev web.search for tweets from @%s", username)

        def search_sync():
            from context.dev import ContextDev
            client = ContextDev(api_key=self.api_key)
            return client.web.search(
                query=f'site:x.com/{username} -inurl:status/ -inurl:search',
                num_results=10,
            )

        try:
            response = await asyncio.wait_for(asyncio.to_thread(search_sync), timeout=30)
            results = getattr(response, "results", None)
            if results is None and isinstance(response, dict):
                results = response.get("results", [])
            elif results is None and hasattr(response, "model_dump"):
                results = response.model_dump().get("results", [])

            tweets = []
            for item in (results or []):
                item_dict = item if isinstance(item, dict) else (item.model_dump() if hasattr(item, "model_dump") else vars(item))
                desc = item_dict.get("description") or ""
                title = item_dict.get("title") or ""
                text = desc if len(desc) > 20 else title

                lower = text.lower()
                if any(x in lower for x in ("log in", "sign up", "onboarding", "redirect_after_login")):
                    continue

                if " on X: " in text:
                    text = text.split(" on X: ", 1)[-1].strip('"').strip("'")

                if len(text) > 15:
                    tweets.append({
                        "id": f"tweet-{len(tweets)+1}",
                        "text": text,
                        "links": extract_urls(text),
                    })
                if len(tweets) >= 10:
                    break

            logger.info("Context.dev web search retrieved %d public tweets for @%s", len(tweets), username)
            return tweets
        except Exception as e:
            logger.warning("Context.dev search fallback for @%s failed: %s", username, e)
            return []

    async def scrape_link(self, url: str) -> dict:
        """Uncloaks and scrapes an external link (e.g. t.co) using Context.dev."""
        if not self.api_key:
            return {"url": url, "domain": urlparse(url).hostname or "", "title": "Demo link", "markdown": "Synthetic page content", "is_scam": False}

        def scrape_sync():
            from context.dev import ContextDev
            client = ContextDev(api_key=self.api_key)
            return client.web.scrape(
                url=url,
                formats={"markdown": True},
                shared_params={"main_content_only": True},
            )

        try:
            logger.info("Executing Context.dev web.scrape for link %s", url)
            response = await asyncio.wait_for(asyncio.to_thread(scrape_sync), timeout=30)
            markdown = _extract_markdown(response)
            title = getattr(response, "title", None) or (response.get("title") if isinstance(response, dict) else "")
            return {
                "url": url,
                "domain": urlparse(url).hostname or "",
                "title": str(title or ""),
                "markdown": markdown[:8000],
            }
        except Exception as e:
            logger.warning("Context.dev link scrape failed for %s: %s", url, e)
            return {"url": url, "domain": urlparse(url).hostname or "", "title": "", "markdown": "", "error": str(e)}

    async def research_claim(self, claim: str) -> list[WebEvidence]:
        """Searches authoritative web sources using Context.dev search API."""
        if not self.api_key:
            return DemoWebResearchProvider().research_claim_sync(claim)

        # Clean query: strip markdown links and raw URLs
        clean_query = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", claim)
        clean_query = re.sub(r"https?://\S+", "", clean_query).strip()
        if not clean_query or len(clean_query) < 5 or any(x in clean_query.lower() for x in ("log in", "sign up", "onboarding")):
            return []

        def search_sync():
            from context.dev import ContextDev
            client = ContextDev(api_key=self.api_key)
            return client.web.search(
                query=clean_query[:200],
                num_results=5,
            )

        try:
            logger.info("Executing Context.dev web.search for claim: '%s'", clean_query[:80])
            response = await asyncio.wait_for(asyncio.to_thread(search_sync), timeout=45)
            results = getattr(response, "results", None)
            if results is None and hasattr(response, "model_dump"):
                results = response.model_dump().get("results", [])
            elif results is None and isinstance(response, dict):
                results = response.get("results", [])
            elif results is None:
                results = vars(response).get("results", [])

            evidence = []
            for item in (results or []):
                item_dict = item if isinstance(item, dict) else (item.model_dump() if hasattr(item, "model_dump") else vars(item))
                url = item_dict.get("url")
                if not url:
                    continue

                excerpt = item_dict.get("description") or ""
                md_val = item_dict.get("markdown")
                if isinstance(md_val, dict):
                    excerpt = md_val.get("data") or md_val.get("markdown") or excerpt
                elif hasattr(md_val, "data"):
                    excerpt = md_val.data or excerpt
                elif isinstance(md_val, str) and md_val:
                    excerpt = md_val

                rel = item_dict.get("relevance", "medium")
                relevance = rel if isinstance(rel, (int, float)) else {"high": 0.9, "medium": 0.65, "low": 0.35}.get(str(rel).lower(), 0.5)
                domain = urlparse(url).hostname or ""

                evidence.append(
                    WebEvidence(
                        url=url,
                        title=item_dict.get("title") or "",
                        source_domain=domain,
                        excerpt=str(excerpt)[:5000],
                        relevance=relevance,
                        source_quality=source_quality(domain),
                        stance=Stance.context_only,
                    )
                )
            return evidence
        except Exception as e:
            logger.warning("Context.dev web search for '%s' returned: %s. Continuing with empty evidence.", clean_query, e)
            return []

    def _parse_tweets_from_markdown(self, username: str, markdown: str) -> list[dict]:
        """Parses markdown extracted by Context.dev into individual tweet structures."""
        lines = [line.strip() for line in markdown.split("\n") if line.strip()]
        tweets = []
        current_text = []

        for line in lines:
            lower_line = line.lower()
            if (
                line.startswith("#")
                or "follow" in lower_line
                or "joined" in lower_line
                or "log in" in lower_line
                or "sign up" in lower_line
                or "onboarding" in lower_line
                or "redirect_after_login" in lower_line
                or "cookies" in lower_line
                or "privacy policy" in lower_line
                or "terms of service" in lower_line
                or "don’t miss what’s happening" in lower_line
            ):
                continue
            if len(line) > 20:
                current_text.append(line)
                if len(current_text) >= 2 or len(line) > 100:
                    text = " ".join(current_text)
                    links = extract_urls(text)
                    tweets.append({
                        "id": f"tweet-{len(tweets)+1}",
                        "text": text,
                        "links": links,
                    })
                    current_text = []
            if len(tweets) >= 15:
                break

        return tweets

    def _demo_twitter_profile(self, username: str) -> dict:
        return {
            "username": username,
            "profile_url": f"https://x.com/{username}",
            "raw_markdown": f"# @{username} Profile on X",
            "tweets": [
                {
                    "id": f"{username}-1",
                    "text": "The EU adopted a law targeting new cars with CO2 emissions from 2035; it did not prohibit every existing petrol car.",
                    "links": [],
                },
                {
                    "id": f"{username}-2",
                    "text": "The Great Wall of China is visible from the Moon with the naked eye. Unbelievable ancient engineering!",
                    "links": [],
                },
                {
                    "id": f"{username}-3",
                    "text": "Check out this secret pre-sale token right now before it 100x! Claim free allocation here: https://airdrop-token-claims.invalid/presale",
                    "links": ["https://airdrop-token-claims.invalid/presale"],
                },
                {
                    "id": f"{username}-4",
                    "text": "Global average temperatures have risen substantially since the late 19th century, according to NASA data.",
                    "links": ["https://climate.nasa.gov"],
                },
                {
                    "id": f"{username}-5",
                    "text": "I love good coffee and quiet weekend mornings.",
                    "links": [],
                },
            ],
        }


class DemoWebResearchProvider:
    is_demo = True

    def research_claim_sync(self, claim: str) -> list[WebEvidence]:
        lower = claim.lower()
        if "great wall" in lower:
            return [
                WebEvidence(
                    url="https://demo.invalid/space/visibility",
                    title="Demo fixture: Great Wall Moon visibility",
                    source_domain="demo.invalid",
                    excerpt="The Great Wall of China is not visible from the Moon with the unaided eye.",
                    relevance=0.9,
                    source_quality=0.8,
                    stance=Stance.contradicts,
                )
            ]
        if "eu adopted" in lower:
            return [
                WebEvidence(
                    url="https://demo.invalid/eu/vehicle-rules",
                    title="Demo fixture: EU 2035 emission targets",
                    source_domain="demo.invalid",
                    excerpt="The EU regulations target new vehicle sales from 2035; existing combustion cars are not universally banned.",
                    relevance=0.9,
                    source_quality=0.85,
                    stance=Stance.partially_supports,
                )
            ]
        if "temperatures have risen" in lower or "climate" in lower:
            return [
                WebEvidence(
                    url="https://demo.invalid/climate/nasa-record",
                    title="Demo fixture: NASA global temperature records",
                    source_domain="demo.invalid",
                    excerpt="NASA Goddard Institute shows steady global temperature anomalies increasing since the late 19th century.",
                    relevance=0.95,
                    source_quality=0.95,
                    stance=Stance.supports,
                )
            ]
        return [
            WebEvidence(
                url="https://demo.invalid/general-context",
                title="Demo fixture: general reference",
                source_domain="demo.invalid",
                excerpt=f"General context regarding: {claim}",
                relevance=0.5,
                source_quality=0.6,
                stance=Stance.context_only,
            )
        ]

    async def research_claim(self, claim: str) -> list[WebEvidence]:
        return self.research_claim_sync(claim)
