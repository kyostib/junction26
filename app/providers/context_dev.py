import asyncio
import logging
import re
import time
from urllib.parse import urlparse

from app.core.config import get_settings
from app.core.dev_logger import log_dev_api_call
from app.schemas.evidence import Stance, WebEvidence

logger = logging.getLogger(__name__)

# Evidence source policy is managed here. Add or remove hostnames from this
# set to control which search results can be used to verify claims.
EXCLUDED_EVIDENCE_DOMAINS = {"www.x.com"}


class ProviderUnavailableError(RuntimeError):
    """Raised when live social data cannot be obtained safely."""


MEDIA_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".bmp", ".ico", ".tiff",
    ".mp4", ".mov", ".avi", ".webm", ".mkv", ".m4v", ".flv", ".wmv", ".ts", ".3gp", ".m3u8",
    ".mp3", ".wav", ".ogg", ".aac",
)
MEDIA_HOSTS = ("pbs.twimg.com", "video.twimg.com", "twimg.com", "ton.twitter.com")


def extract_urls(text: str) -> list[str]:
    raw = re.findall(r"https?://[^\s)\]\"'>]+", text)
    cleaned = []
    for u in raw:
        c = u.rstrip(".,;:!\"')>")
        if not c:
            continue
        c_lower = c.split("?")[0].split("#")[0].lower()
        if any(c_lower.endswith(ext) for ext in MEDIA_EXTENSIONS):
            continue
        if any(host in c_lower for host in MEDIA_HOSTS):
            continue
        if c not in cleaned:
            cleaned.append(c)
    return cleaned


def extract_image_urls(text: str) -> list[str]:
    images: list[str] = []
    # Markdown image syntax: ![alt](url)
    for match in re.findall(r"!\[.*?\]\((https?://[^\s\)]+)\)", text):
        clean = match.rstrip(".,;:!\"')>")
        if clean not in images:
            images.append(clean)

    # HTML <img> tags
    for match in re.findall(r"""<img[^>]+src=["'](https?://[^"']+)["']""", text, re.IGNORECASE):
        clean = match.rstrip(".,;:!\"')>")
        if clean not in images:
            images.append(clean)

    # Twitter media URLs or direct image extensions
    for match in re.findall(r"https?://[^\s)\]\"'>]+", text):
        clean = match.rstrip(".,;:!\"')>")
        lower = clean.lower()
        if "pbs.twimg.com/media/" in lower or lower.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
            if clean not in images:
                images.append(clean)

    return images


def is_external_link(url: str, username: str = "") -> bool:
    """Returns True if the URL is an external web link, filtering out Twitter/X internal URLs, images, and videos."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
        domain = (parsed.hostname or "").lower().removeprefix("www.")
        if not domain or domain in {"x.com", "twitter.com", "mobile.twitter.com"}:
            return False
        if any(domain == h or domain.endswith("." + h) for h in MEDIA_HOSTS):
            return False
        path = (parsed.path or "").lower()
        if "share" in path or "intent" in path:
            return False
        clean_path = path.split("?")[0].split("#")[0]
        if any(clean_path.endswith(ext) for ext in MEDIA_EXTENSIONS):
            return False
        clean_user = username.lower().lstrip("@")
        if clean_user and path.strip("/").split("/")[0].lower() == clean_user:
            return False
        return True
    except Exception:
        return False


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

    async def scrape_twitter_profile(self, username: str, max_tweets: int = 10) -> dict:
        """Scrapes a Twitter/X user profile using Context.dev with headless JS rendering."""
        clean_user = username.lstrip("@").strip()
        url = f"https://x.com/{clean_user}"

        if not self.api_key:
            raise ProviderUnavailableError("CONTEXT_DEV_API_KEY is not configured")

        query = {
            "operation": "client.web.scrape",
            "target": "twitter_profile",
            "username": clean_user,
            "url": url,
            "formats": {"markdown": True},
            "shared_params": {"main_content_only": True},
        }

        def scrape_sync():
            from context.dev import ContextDev
            client = ContextDev(api_key=self.api_key)
            return client.web.scrape(
                url=url,
                formats={"markdown": True},
                shared_params={"main_content_only": True},
            )

        start_time = time.perf_counter()
        try:
            logger.info("Executing Context.dev web.scrape for Twitter profile @%s", clean_user)
            response = await asyncio.wait_for(asyncio.to_thread(scrape_sync), timeout=60)
            duration_ms = (time.perf_counter() - start_time) * 1000
            markdown = _extract_markdown(response)
            title = getattr(response, "title", None) or (response.get("title") if isinstance(response, dict) else "")
            log_dev_api_call(
                provider="context.dev",
                operation="client.web.scrape (twitter profile)",
                query=query,
                response={
                    "status": "success",
                    "title": str(title or ""),
                    "markdown_length": len(markdown),
                    "markdown_preview": markdown[:1000] if len(markdown) > 1000 else markdown,
                },
                status="SUCCESS",
                duration_ms=duration_ms,
            )
            bio, bio_links, tweets = self._parse_profile_and_tweets(clean_user, markdown, max_tweets=max_tweets)

            # If direct scrape was blocked by an X login wall, search for public tweets via Context.dev search
            if not tweets:
                logger.info("Direct scrape for @%s returned no tweets (login wall). Falling back to Context.dev search.", clean_user)
                search_bio, search_links, tweets = await self._search_tweets_via_context_dev(clean_user, max_tweets=max_tweets)
                if not bio:
                    bio = search_bio
                    bio_links = search_links

            return {
                "username": clean_user,
                "profile_url": url,
                "bio": bio,
                "bio_links": bio_links,
                "raw_markdown": markdown,
                "tweets": tweets,
            }
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_dev_api_call(
                provider="context.dev",
                operation="client.web.scrape (twitter profile)",
                query=query,
                response=None,
                status="ERROR",
                duration_ms=duration_ms,
                error=str(e),
            )
            logger.warning("Context.dev scrape for @%s encountered error: %s. Trying search fallback.", clean_user, e)
            try:
                bio, bio_links, tweets = await self._search_tweets_via_context_dev(clean_user, max_tweets=max_tweets)
                if tweets or bio:
                    return {
                        "username": clean_user,
                        "profile_url": url,
                        "bio": bio,
                        "bio_links": bio_links,
                        "raw_markdown": "",
                        "tweets": tweets,
                    }
            except Exception as search_err:
                logger.warning("Context.dev search fallback also failed: %s", search_err)
            raise ProviderUnavailableError("Context.dev could not retrieve public posts") from e

    async def _search_tweets_via_context_dev(self, username: str, max_tweets: int = 10) -> tuple[str, list[str], list[dict]]:
        """Searches for public tweets and profile snippet from a user using Context.dev web search."""
        logger.info("Executing Context.dev web.search for tweets from @%s", username)

        query = {
            "operation": "client.web.search",
            "target": "twitter_search_fallback",
            "username": username,
            "query": f'site:x.com/{username} -inurl:search',
            "num_results": max(10, max_tweets + 3),
        }

        def search_sync():
            from context.dev import ContextDev
            client = ContextDev(api_key=self.api_key)
            return client.web.search(
                query=f'site:x.com/{username} -inurl:search',
                num_results=max(10, max_tweets + 3),
            )

        start_time = time.perf_counter()
        try:
            response = await asyncio.wait_for(asyncio.to_thread(search_sync), timeout=30)
            duration_ms = (time.perf_counter() - start_time) * 1000
            results = getattr(response, "results", None)
            if results is None and isinstance(response, dict):
                results = response.get("results", [])
            elif results is None and hasattr(response, "model_dump"):
                results = response.model_dump().get("results", [])

            log_dev_api_call(
                provider="context.dev",
                operation="client.web.search (tweets fallback)",
                query=query,
                response={"results_count": len(results or []), "results": results},
                status="SUCCESS",
                duration_ms=duration_ms,
            )

            bio = ""
            bio_links = []
            tweets = []
            for item in (results or []):
                item_dict = item if isinstance(item, dict) else (item.model_dump() if hasattr(item, "model_dump") else vars(item))
                desc = item_dict.get("description") or ""
                title = item_dict.get("title") or ""
                res_url = item_dict.get("url") or ""
                text = desc if len(desc) > 20 else title

                lower = text.lower()
                if any(x in lower for x in ("log in", "sign up", "onboarding", "redirect_after_login")):
                    continue

                # Check if this result is the profile home page (has bio)
                if not bio and "/status/" not in res_url:
                    bio_candidate = text
                    if " on X: " in bio_candidate:
                        bio_candidate = bio_candidate.split(" on X: ", 1)[-1].strip('"').strip("'")
                    if any(x in bio_candidate.lower() for x in ("the latest tweets from", "the latest posts from")):
                        # e.g. "The latest Tweets from Name (@handle). Bio text here."
                        parts = re.split(r"the latest (?:tweets|posts) from [^:]+:\s*", bio_candidate, flags=re.IGNORECASE)
                        if len(parts) > 1 and len(parts[1]) > 5:
                            bio_candidate = parts[1].strip()
                    bio = bio_candidate
                    bio_links = [l for l in extract_urls(bio) if is_external_link(l, username)]

                if " on X: " in text:
                    text = text.split(" on X: ", 1)[-1].strip('"').strip("'")

                if len(text) > 15 and len(tweets) < max_tweets:
                    tweets.append({
                        "id": f"tweet-{len(tweets)+1}",
                        "text": text,
                        "links": extract_urls(text),
                        "images": extract_image_urls(text),
                    })

            logger.info("Context.dev web search retrieved %d public tweets for @%s", len(tweets), username)
            return bio, bio_links, tweets
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_dev_api_call(
                provider="context.dev",
                operation="client.web.search (tweets fallback)",
                query=query,
                response=None,
                status="ERROR",
                duration_ms=duration_ms,
                error=str(e),
            )
            logger.warning("Context.dev search fallback for @%s failed: %s", username, e)
            return "", [], []

    async def scrape_link(self, url: str) -> dict:
        """Uncloaks and scrapes an external link (e.g. t.co) using Context.dev."""
        query = {
            "operation": "client.web.scrape",
            "target": "link_uncloak",
            "url": url,
            "formats": {"markdown": True},
            "shared_params": {"main_content_only": True},
        }

        if not self.api_key:
            res = {"url": url, "domain": urlparse(url).hostname or "", "title": "Demo link", "markdown": "Synthetic page content", "is_scam": False}
            log_dev_api_call(provider="context.dev (demo)", operation="client.web.scrape (demo link)", query=query, response=res, status="DEMO")
            return res

        def scrape_sync():
            from context.dev import ContextDev
            client = ContextDev(api_key=self.api_key)
            return client.web.scrape(
                url=url,
                formats={"markdown": True},
                shared_params={"main_content_only": True},
            )

        start_time = time.perf_counter()
        try:
            logger.info("Executing Context.dev web.scrape for link %s", url)
            response = await asyncio.wait_for(asyncio.to_thread(scrape_sync), timeout=30)
            duration_ms = (time.perf_counter() - start_time) * 1000
            markdown = _extract_markdown(response)
            title = getattr(response, "title", None) or (response.get("title") if isinstance(response, dict) else "")
            res_dict = {
                "url": url,
                "domain": urlparse(url).hostname or "",
                "title": str(title or ""),
                "markdown": markdown[:8000],
            }
            log_dev_api_call(
                provider="context.dev",
                operation="client.web.scrape (link uncloak)",
                query=query,
                response=res_dict,
                status="SUCCESS",
                duration_ms=duration_ms,
            )
            return res_dict
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_dev_api_call(
                provider="context.dev",
                operation="client.web.scrape (link uncloak)",
                query=query,
                response=None,
                status="ERROR",
                duration_ms=duration_ms,
                error=str(e),
            )
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

        query = {
            "operation": "client.web.search",
            "target": "research_claim",
            "claim": claim,
            "query": clean_query[:200],
            "num_results": 5,
        }

        def search_sync():
            from context.dev import ContextDev
            client = ContextDev(api_key=self.api_key)
            return client.web.search(
                query=clean_query[:200],
                num_results=5,
            )

        start_time = time.perf_counter()
        try:
            logger.info("Executing Context.dev web.search for claim: '%s'", clean_query[:80])
            response = await asyncio.wait_for(asyncio.to_thread(search_sync), timeout=45)
            duration_ms = (time.perf_counter() - start_time) * 1000
            results = getattr(response, "results", None)
            if results is None and hasattr(response, "model_dump"):
                results = response.model_dump().get("results", [])
            elif results is None and isinstance(response, dict):
                results = response.get("results", [])
            elif results is None:
                results = vars(response).get("results", [])

            log_dev_api_call(
                provider="context.dev",
                operation="client.web.search (research claim)",
                query=query,
                response={"results_count": len(results or []), "results": results},
                status="SUCCESS",
                duration_ms=duration_ms,
            )

            evidence = []
            for item in (results or []):
                item_dict = item if isinstance(item, dict) else (item.model_dump() if hasattr(item, "model_dump") else vars(item))
                url = item_dict.get("url")
                if not url:
                    continue
                domain = (urlparse(url).hostname or "").lower()
                if domain in EXCLUDED_EVIDENCE_DOMAINS:
                    logger.debug("Skipping excluded evidence source: %s", domain)
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
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_dev_api_call(
                provider="context.dev",
                operation="client.web.search (research claim)",
                query=query,
                response=None,
                status="ERROR",
                duration_ms=duration_ms,
                error=str(e),
            )
            logger.warning("Context.dev web search for '%s' returned: %s. Continuing with empty evidence.", clean_query, e)
            return []

    def _parse_profile_and_tweets(self, username: str, markdown: str, max_tweets: int = 10) -> tuple[str, list[str], list[dict]]:
        """Parses profile header bio, bio links, and timeline tweets from markdown."""
        lines = [line.strip() for line in markdown.split("\n") if line.strip()]
        bio_lines = []
        tweets = []
        current_text = []
        is_in_timeline = False

        skip_keywords = (
            "log in", "sign up", "onboarding", "redirect_after_login",
            "cookies", "privacy policy", "terms of service", "don’t miss what’s happening",
            "explore", "notifications", "messages", "bookmarks", "lists", "profile", "more",
        )

        for line in lines:
            lower_line = line.lower()
            if any(k in lower_line for k in skip_keywords):
                continue

            # Transition boundary between header and timeline
            if any(x in lower_line for x in ("following", "followers", "joined ", "highlights", "posts", "replies", "media")):
                is_in_timeline = True
                continue

            # Header / Bio extraction (before timeline begins)
            if not is_in_timeline:
                if line.startswith("#"):
                    continue
                if len(line) > 5 and not line.startswith("@"):
                    bio_lines.append(line)
                    if len(bio_lines) >= 3:
                        is_in_timeline = True
                continue

            # Tweet extraction (in timeline)
            if "follow" in lower_line or line.startswith("#"):
                continue

            if len(line) > 20:
                current_text.append(line)
                if len(current_text) >= 2 or len(line) > 100:
                    text = " ".join(current_text)
                    cleaned_text = re.sub(r"^[^@\n]+@[a-zA-Z0-9_]+\s*·\s*\w+\s*", "", text).strip()
                    if len(cleaned_text) > 15:
                        links = extract_urls(cleaned_text)
                        images = extract_image_urls(text)
                        tweets.append({
                            "id": f"tweet-{len(tweets)+1}",
                            "text": cleaned_text,
                            "links": links,
                            "images": images,
                        })
                    current_text = []
            if len(tweets) >= max_tweets:
                break

        bio = " ".join(bio_lines).strip()
        bio_links = [l for l in extract_urls(bio) if is_external_link(l, username)]
        for _, link in re.findall(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)", bio):
            if link not in bio_links and is_external_link(link, username):
                bio_links.append(link)

        return bio, bio_links, tweets

    def _parse_tweets_from_markdown(self, username: str, markdown: str) -> list[dict]:
        """Parses markdown extracted by Context.dev into individual tweet structures."""
        _, _, tweets = self._parse_profile_and_tweets(username, markdown, max_tweets=15)
        return tweets

    def _demo_twitter_profile(self, username: str, max_tweets: int = 10) -> dict:
        sample_tweets = [
            {
                "id": f"{username}-1",
                "text": "The EU adopted a law targeting new cars with CO2 emissions from 2035; it did not prohibit every existing petrol car.",
                "links": [],
                "images": [],
            },
            {
                "id": f"{username}-2",
                "text": "The Great Wall of China is visible from the Moon with the naked eye. Unbelievable ancient engineering!",
                "links": [],
                "images": ["https://demo.invalid/images/great_wall_space_photo.jpg"],
            },
            {
                "id": f"{username}-3",
                "text": "Check out this secret pre-sale token right now before it 100x! Claim free allocation here: https://airdrop-token-claims.invalid/presale",
                "links": ["https://airdrop-token-claims.invalid/presale"],
                "images": ["https://demo.invalid/images/presale_100x_allocation_banner.png"],
            },
            {
                "id": f"{username}-4",
                "text": "Global average temperatures have risen substantially since the late 19th century, according to NASA data.",
                "links": ["https://climate.nasa.gov"],
                "images": ["https://demo.invalid/images/nasa_temperature_trend.png"],
            },
            {
                "id": f"{username}-5",
                "text": "I love good coffee and quiet weekend mornings.",
                "links": [],
                "images": [],
            },
        ]
        return {
            "username": username,
            "profile_url": f"https://x.com/{username}",
            "bio": "Crypto researcher & futurist. Check out our exclusive pre-sale allocation below!",
            "bio_links": ["https://airdrop-token-claims.invalid/presale"],
            "raw_markdown": f"# @{username} Profile on X\nCrypto researcher & futurist...",
            "tweets": sample_tweets[:max_tweets],
        }


class DemoWebResearchProvider:
    is_demo = True

    def research_claim_sync(self, claim: str) -> list[WebEvidence]:
        lower = claim.lower()
        if "great wall" in lower:
            results = [
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
        elif "eu adopted" in lower:
            results = [
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
        elif "temperatures have risen" in lower or "climate" in lower:
            results = [
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
        else:
            results = [
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
        log_dev_api_call(
            provider="context.dev (demo)",
            operation="research_claim_sync",
            query={"claim": claim},
            response=[e.model_dump() for e in results],
            status="SUCCESS",
        )
        return results

    async def research_claim(self, claim: str) -> list[WebEvidence]:
        return self.research_claim_sync(claim)
