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
MEDIA_HOSTS = frozenset({"pbs.twimg.com", "video.twimg.com", "twimg.com", "ton.twitter.com"})
TWITTER_DOMAINS = frozenset({"x.com", "twitter.com", "mobile.twitter.com"})
HIGH_QUALITY_DOMAINS = frozenset({"reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "nature.com", "science.org", "snopes.com", "factcheck.org"})
RELIABLE_DOMAINS = frozenset({"wikipedia.org", "nytimes.com", "washingtonpost.com", "wsj.com", "theguardian.com"})
HIGH_QUALITY_SUFFIXES = (".gov", ".gov.uk", ".europa.eu", ".int")
ACADEMIC_SUFFIXES = (".edu", ".ac.uk")

URL_RE = re.compile(r"https?://[^\s)\]\"'>]+")
MD_IMAGE_RE = re.compile(r"!\[.*?\]\((https?://[^\s\)]+)\)")
HTML_IMG_RE = re.compile(r"""<img[^>]+src=["'](https?://[^"']+)["']""", re.IGNORECASE)
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s\)]+)\)")
TWEET_AUTHOR_PREFIX_RE = re.compile(r"^[^@\n]+@[a-zA-Z0-9_]+\s*·\s*\w+\s*")
AUTHOR_LINE_RE = re.compile(
    r"^(?:(?:\[?[^@\n]+?\]?(?:\([^\)]+\))?)\s+)?@(?P<handle>[a-zA-Z0-9_]{1,30})(?:\s*·\s*(?P<time>.*?))?$",
    re.IGNORECASE,
)
REPLY_LINE_RE = re.compile(r"^Replying to\s+(@[a-zA-Z0-9_]+(?:\s*(?:and|,)\s*@[a-zA-Z0-9_]+)*)", re.IGNORECASE)
METRICS_JUNK_RE = re.compile(
    r"^(?:show more|translate post|show this thread|pinned tweet|pinned|"
    r"[\d,\.kmb]+\s+(?:views|replies|reposts|retweets|likes|quotes|bookmarks)|"
    r"(?:💬|🔁|❤️|📊|🔖)\s*[\d,\.kmb]*)$",
    re.IGNORECASE,
)
HEADER_NAV_RE = re.compile(
    r"^(?:joined\s+\w+.*|[\d,\.kmb]+\s+following.*|[\d,\.kmb]+\s+followers.*|"
    r"(?:posts|replies|highlights|articles|media|likes)(?:\s+(?:posts|replies|highlights|articles|media|likes))*)$",
    re.IGNORECASE,
)
LATEST_TWEETS_SPLIT_RE = re.compile(r"the latest (?:tweets|posts) from [^:]+:\s*", re.IGNORECASE)
CLEAN_QUERY_MD_RE = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
CLEAN_QUERY_URL_RE = re.compile(r"https?://\S+")


def extract_urls(text: str) -> list[str]:
    raw = URL_RE.findall(text)
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
    for match in MD_IMAGE_RE.findall(text):
        clean = match.rstrip(".,;:!\"')>")
        if clean not in images:
            images.append(clean)

    # HTML <img> tags
    for match in HTML_IMG_RE.findall(text):
        clean = match.rstrip(".,;:!\"')>")
        if clean not in images:
            images.append(clean)

    # Twitter media URLs or direct image extensions
    for match in URL_RE.findall(text):
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
        if not domain or any(domain == d or domain.endswith("." + d) for d in TWITTER_DOMAINS):
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
    if domain.endswith(HIGH_QUALITY_SUFFIXES):
        return 0.95
    if domain.endswith(ACADEMIC_SUFFIXES):
        return 0.85
    if domain in HIGH_QUALITY_DOMAINS:
        return 0.85
    if domain in RELIABLE_DOMAINS:
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
                        parts = LATEST_TWEETS_SPLIT_RE.split(bio_candidate)
                        if len(parts) > 1 and len(parts[1]) > 5:
                            bio_candidate = parts[1].strip()
                    bio = bio_candidate
                    bio_links = [l for l in extract_urls(bio) if is_external_link(l, username)]

                if " on X: " in text:
                    text = text.split(" on X: ", 1)[-1].strip('"').strip("'")

                if len(text) > 15 and len(tweets) < max_tweets:
                    in_reply_to = None
                    rep_match = re.match(r"^Replying to\s+(@[a-zA-Z0-9_]+):?\s*", text, re.IGNORECASE)
                    if rep_match:
                        in_reply_to = {"author": rep_match.group(1), "text": ""}
                        text = text[rep_match.end():].strip()

                    valid_tweet_links = [l for l in extract_urls(text) if is_external_link(l, username)]
                    tweets.append({
                        "id": f"tweet-{len(tweets)+1}",
                        "text": text,
                        "links": valid_tweet_links,
                        "images": extract_image_urls(text),
                        "in_reply_to": in_reply_to,
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
            res = {
                "url": url,
                "resolved_url": url,
                "domain": urlparse(url).hostname or "",
                "title": "Demo link",
                "description": "Synthetic page description",
                "markdown": "Synthetic page content",
                "is_scam": False,
            }
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
            resolved_url = getattr(response, "url", None) or (response.get("url") if isinstance(response, dict) else url) or url
            meta = getattr(response, "metadata", None)
            title = (
                getattr(meta, "title", None)
                or getattr(response, "title", None)
                or (response.get("title") if isinstance(response, dict) else "")
            )
            desc = (
                getattr(meta, "description", None)
                or (response.get("description") if isinstance(response, dict) else "")
            )
            res_dict = {
                "url": url,
                "resolved_url": str(resolved_url),
                "domain": urlparse(str(resolved_url)).hostname or urlparse(url).hostname or "",
                "title": str(title or ""),
                "description": str(desc or ""),
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
            return {"url": url, "resolved_url": url, "domain": urlparse(url).hostname or "", "title": "", "description": "", "markdown": "", "error": str(e)}

    async def research_claim(self, claim: str) -> list[WebEvidence]:
        """Searches authoritative web sources using Context.dev search API."""
        if not self.api_key:
            return DemoWebResearchProvider().research_claim_sync(claim)

        # Clean query: strip markdown links and raw URLs
        clean_query = CLEAN_QUERY_MD_RE.sub(r"\1", claim)
        clean_query = CLEAN_QUERY_URL_RE.sub("", clean_query).strip()
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
        clean_user = username.lstrip("@").strip()
        lines = [line.strip() for line in markdown.split("\n") if line.strip()]
        bio_lines = []
        timeline_lines = []
        is_in_timeline = False

        skip_keywords = (
            "log in", "sign up", "onboarding", "redirect_after_login",
            "cookies", "privacy policy", "terms of service", "don’t miss what’s happening",
            "explore", "notifications", "messages", "bookmarks", "lists", "more",
        )

        for line in lines:
            lower_line = line.lower()
            if any(k in lower_line for k in skip_keywords):
                continue

            if HEADER_NAV_RE.match(line):
                is_in_timeline = True
                continue

            # Header / Bio extraction (before timeline begins)
            if not is_in_timeline:
                if line.startswith("#") or line.startswith("@") or lower_line in ("follow", "following"):
                    continue
                if len(line) > 5:
                    bio_lines.append(line)
                    if len(bio_lines) >= 4:
                        is_in_timeline = True
                continue

            # Timeline lines
            if METRICS_JUNK_RE.match(line) or lower_line in ("follow", "following"):
                continue
            if line.startswith("#") and not line.startswith("# "):
                continue
            timeline_lines.append(line)

        bio = " ".join(bio_lines).strip()
        bio_links = [l for l in extract_urls(bio) if is_external_link(l, clean_user)]
        for _, link in MD_LINK_RE.findall(bio):
            if link not in bio_links and is_external_link(link, clean_user):
                bio_links.append(link)

        # Parse timeline into structured tweet blocks
        raw_blocks = []
        current_block = None

        for line in timeline_lines:
            rep_match = REPLY_LINE_RE.match(line)
            if rep_match:
                if current_block is not None:
                    current_block["reply_target"] = rep_match.group(1)
                continue

            auth_match = AUTHOR_LINE_RE.match(line)
            if auth_match and not line.lower().startswith("replying to"):
                if current_block and (current_block["lines"] or current_block["author"]):
                    raw_blocks.append(current_block)
                current_block = {
                    "author": auth_match.group("handle").lower(),
                    "time": auth_match.group("time"),
                    "reply_target": None,
                    "lines": [],
                }
                continue

            if current_block is not None:
                current_block["lines"].append(line)
            else:
                current_block = {
                    "author": clean_user.lower(),
                    "time": None,
                    "reply_target": None,
                    "lines": [line],
                }

        if current_block and (current_block["lines"] or current_block["author"]):
            raw_blocks.append(current_block)

        tweets = []
        clean_user_lower = clean_user.lower()

        for i, block in enumerate(raw_blocks):
            block_author = block.get("author") or clean_user_lower
            if block_author != clean_user_lower:
                # Parent tweet in a reply thread or quote; kept for context, not a user post
                continue

            in_reply_to = None
            if block.get("reply_target"):
                reply_author = block["reply_target"]
                parent_text = ""
                if i > 0 and raw_blocks[i - 1].get("author") != clean_user_lower:
                    parent_text = " ".join(raw_blocks[i - 1].get("lines", [])).strip()
                in_reply_to = {"author": reply_author, "text": parent_text}
            elif i > 0 and raw_blocks[i - 1].get("author") != clean_user_lower:
                prev = raw_blocks[i - 1]
                parent_text = " ".join(prev.get("lines", [])).strip()
                if parent_text:
                    prev_author = prev.get("author")
                    in_reply_to = {"author": f"@{prev_author}", "text": parent_text}

            tweet_text = " ".join(block.get("lines", [])).strip()
            tweet_text = TWEET_AUTHOR_PREFIX_RE.sub("", tweet_text).strip()
            if len(tweet_text) > 10:
                links = [l for l in extract_urls(tweet_text) if is_external_link(l, clean_user)]
                for _, link in MD_LINK_RE.findall(tweet_text):
                    if link not in links and is_external_link(link, clean_user):
                        links.append(link)
                images = extract_image_urls(" ".join(block.get("lines", [])))
                tweets.append({
                    "id": f"tweet-{len(tweets)+1}",
                    "text": tweet_text,
                    "links": links,
                    "images": images,
                    "in_reply_to": in_reply_to,
                })
                if len(tweets) >= max_tweets:
                    break

        # Fallback for unconventional markdown formats without standard author lines
        if not tweets and timeline_lines:
            chunk = []
            for line in timeline_lines:
                if len(line) > 20:
                    chunk.append(line)
                    if len(chunk) >= 2 or len(line) > 100:
                        text = " ".join(chunk)
                        cleaned = TWEET_AUTHOR_PREFIX_RE.sub("", text).strip()
                        if len(cleaned) > 15:
                            tweets.append({
                                "id": f"tweet-{len(tweets)+1}",
                                "text": cleaned,
                                "links": [l for l in extract_urls(cleaned) if is_external_link(l, clean_user)],
                                "images": extract_image_urls(text),
                                "in_reply_to": None,
                            })
                        chunk = []
                        if len(tweets) >= max_tweets:
                            break

        return bio, bio_links, tweets


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
