import asyncio
import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

from app.core.config import get_settings
from app.core.dev_logger import log_dev_api_call
from app.schemas.evidence import Stance, WebEvidence

logger = logging.getLogger(__name__)

# Evidence source policy: domains excluded from claim verification
EXCLUDED_EVIDENCE_DOMAINS = {"www.x.com"}


class ProviderUnavailableError(RuntimeError):
    """Raised when live social data cannot be obtained safely."""


# ---------------------------------------------------------------------------
# Domain & Media Recognition Policy
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Regular Expressions for Parsing
# ---------------------------------------------------------------------------

URL_RE = re.compile(r"https?://[^\s)\]\"'>]+")
MD_IMAGE_RE = re.compile(r"!\[.*?\]\((https?://[^\s\)]+)\)")
HTML_IMG_RE = re.compile(r"""<img[^>]+src=["'](https?://[^"']+)["']""", re.IGNORECASE)
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s\)'\"]+)(?:\s+['\"][^'\"]*['\"])?\)")
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
    r"^(?:\[?joined\b.*|\[?[\d,\.kmb]+\s+following.*|\[?[\d,\.kmb]+\s+followers.*|"
    r"(?:posts|replies|highlights|articles|media|likes)(?:\s+(?:posts|replies|highlights|articles|media|likes))*)$",
    re.IGNORECASE,
)
SCRAPED_TWEET_START_RE = re.compile(
    r"(?:^|\s+)(?:-\s+)?\[@(?P<handle>[a-zA-Z0-9_]{1,30})\]\((?:https?://(?:x\.com|twitter\.com)/[^\)]+)?\)\s*"
    r"(?:\[(?P<name>[^\]]+)\]\((?:https?://(?:x\.com|twitter\.com)/[^\)]+)?\)\s*)?"
    r"(?:\[@(?P<handle2>[a-zA-Z0-9_]{1,30})\]\((?:https?://(?:x\.com|twitter\.com)/[^\)]+)?\)\s*)?"
    r"\[(?P<time>[^\]]+)\]\((?:https?://(?:x\.com|twitter\.com))?(?:/[a-zA-Z0-9_]+)?/status/(?P<status_id>\d+)\)",
    re.IGNORECASE,
)
FOOTER_METRICS_RE = re.compile(
    r"\s*(?:\[[\d,\.kmb]*\]\(https?://(?:x\.com|twitter\.com)/compose/post\?in_reply_to=\d+\))"
    r"(?:\s+[\d,\.kmb]+(?:\s*(?:views|replies|reposts|retweets|likes|quotes|bookmarks))?)*\s*$",
    re.IGNORECASE,
)
NAV_START_RE = re.compile(
    r"(?:\[?Joined\s+[^\]\n\r]+\]?(?:\([^\)]+\))?|\[?[\d,\.kmb]+\s+Following|\[?[\d,\.kmb]+\s+Followers|Mention\s+\[Mention\]|\[Follow\]|PostsPosts|Posts\s+Replies)",
    re.IGNORECASE,
)
REPLY_START_RE = re.compile(
    r"^Replying to\s+(?:\[?@([a-zA-Z0-9_]+)\]?(?:\([^\)]+\))?(?:\s*(?:and|,)\s*\[?@[a-zA-Z0-9_]+\]?(?:\([^\)]+\))?)*):?\s*",
    re.IGNORECASE,
)
LATEST_TWEETS_SPLIT_RE = re.compile(r"the latest (?:tweets|posts) from [^:]+:\s*", re.IGNORECASE)
CLEAN_QUERY_MD_RE = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
CLEAN_QUERY_URL_RE = re.compile(r"https?://\S+")
OPENS_PROFILE_PHOTO_RE = re.compile(
    r"\[?!*\[\s*Opens\s+profile\s+photo\s*\](?:\([^\)]*\))?\]?(?:\([^\)]*\))?",
    re.IGNORECASE,
)
JOINED_DATE_RE = re.compile(
    r"\[?\s*Joined\s+[^\W\d_]+(?:\s+\d{1,2},?)?\s+(?:\d{2,4}|YEAR)\s*\]?(?:\([^\)]*\))?",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Link & Media Utilities
# ---------------------------------------------------------------------------

def extract_urls(text: str) -> list[str]:
    """Extracts clean HTTP/HTTPS URLs from text, excluding direct media files and media CDNs."""
    cleaned: list[str] = []
    for raw in URL_RE.findall(text):
        url = raw.rstrip(".,;:!\"')>")
        if not url:
            continue
        url_lower = url.split("?")[0].split("#")[0].lower()
        if any(url_lower.endswith(ext) for ext in MEDIA_EXTENSIONS):
            continue
        if any(host in url_lower for host in MEDIA_HOSTS):
            continue
        if url not in cleaned:
            cleaned.append(url)
    return cleaned


def extract_image_urls(text: str) -> list[str]:
    """Extracts image URLs from Markdown tags, HTML img tags, or Twitter media URLs."""
    images: list[str] = []

    def _add(cand: str):
        clean = cand.rstrip(".,;:!\"')>")
        if clean and clean not in images:
            images.append(clean)

    for match in MD_IMAGE_RE.findall(text):
        _add(match)

    for match in HTML_IMG_RE.findall(text):
        _add(match)

    for match in URL_RE.findall(text):
        clean = match.rstrip(".,;:!\"')>")
        lower = clean.lower()
        clean_path = lower.split("?")[0].split("#")[0]
        if "pbs.twimg.com/media/" in lower or clean_path.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".bmp", ".ico", ".tiff")):
            _add(clean)

    return images


def is_external_link(url: str, username: str = "") -> bool:
    """Returns True if the URL is an external web link, filtering out Twitter/X internal URLs, media, and intents."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
        domain = (parsed.hostname or "").lower().removeprefix("www.")
        if not domain:
            return False
        # Filter internal Twitter/X domains (t.co is deliberately preserved for uncloaking)
        if any(domain == d or domain.endswith("." + d) for d in TWITTER_DOMAINS):
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
    """Determines baseline credibility score [0.0 - 1.0] based on domain authority tiers."""
    dom = domain.lower().removeprefix("www.")
    if dom.endswith(HIGH_QUALITY_SUFFIXES):
        return 0.95
    if dom.endswith(ACADEMIC_SUFFIXES):
        return 0.85
    if any(dom == d or dom.endswith("." + d) for d in HIGH_QUALITY_DOMAINS):
        return 0.85
    if any(dom == d or dom.endswith("." + d) for d in RELIABLE_DOMAINS):
        return 0.75
    return 0.55


def _extract_external_links(text: str, username: str = "") -> list[str]:
    """Helper to extract all valid external web links from text and markdown links."""
    candidates = extract_urls(text)
    for _, link in MD_LINK_RE.findall(text):
        clean = link.rstrip(".,;:!\"')>")
        if clean not in candidates:
            candidates.append(clean)
    return [u for u in candidates if is_external_link(u, username)]


def extract_bio_between_photo_and_joined(markdown: str) -> str | None:
    """Extracts lines between [Opens profile photo] and [Joined Month YEAR].
    Returns None if delimiters are not found.
    Returns str (which may be empty) if delimiters are found.
    """
    if not markdown:
        return None
    start_match = OPENS_PROFILE_PHOTO_RE.search(markdown)
    if not start_match:
        return None
    end_match = JOINED_DATE_RE.search(markdown, start_match.end())
    if not end_match:
        return None

    between_text = markdown[start_match.end():end_match.start()]
    bio_lines = [line.strip() for line in between_text.splitlines() if line.strip()]
    return "\n".join(bio_lines).strip()


# ---------------------------------------------------------------------------
# SDK Response Normalization Helpers
# ---------------------------------------------------------------------------

def _to_dict(obj: Any) -> dict:
    """Converts a dict, Pydantic model, or generic object to a dictionary."""
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "__dict__"):
        return vars(obj)
    return {}


def _extract_results(response: Any) -> list:
    """Extracts results list from Context.dev search response."""
    if response is None:
        return []
    res = getattr(response, "results", None)
    if res is not None:
        return list(res)
    if isinstance(response, dict):
        return list(response.get("results") or [])
    if hasattr(response, "model_dump"):
        dump = response.model_dump()
        if isinstance(dump, dict):
            return list(dump.get("results") or [])
    if hasattr(response, "__dict__"):
        return list(vars(response).get("results") or [])
    return []


def _extract_title(response: Any) -> str:
    """Extracts title string from scrape response."""
    meta = getattr(response, "metadata", None)
    if meta and hasattr(meta, "title") and meta.title:
        return str(meta.title)
    if hasattr(response, "title") and response.title:
        return str(response.title)
    if isinstance(response, dict):
        meta_dict = response.get("metadata")
        if isinstance(meta_dict, dict) and meta_dict.get("title"):
            return str(meta_dict["title"])
        return str(response.get("title") or "")
    return ""


def _extract_markdown(response: Any) -> str:
    """Extracts markdown text supporting both latest and legacy Context.dev SDK responses."""
    if response is None:
        return ""
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
    if isinstance(response, str):
        return response
    return ""


# ---------------------------------------------------------------------------
# Unified ContextDev Provider
# ---------------------------------------------------------------------------

class ContextDevProvider:
    """Context.dev unified gateway using the latest SDK:
    1. Twitter profile & tweet scraping (client.web.scrape with main_content_only)
    2. Fallback tweet search via Context.dev search (bypasses X login walls)
    3. Embedded link uncloaking & inspection (client.web.scrape)
    4. Live web search for fact-checking (client.web.search)
    """

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or get_settings().context_dev_api_key

    extract_bio_between_photo_and_joined = staticmethod(extract_bio_between_photo_and_joined)
    _extract_bio_between_photo_and_joined = staticmethod(extract_bio_between_photo_and_joined)

    def _get_client(self):
        """Instantiates the ContextDev client dynamically to support mock overrides."""
        if not self.api_key:
            raise ProviderUnavailableError("CONTEXT_DEV_API_KEY is not configured")
        from context.dev import ContextDev
        return ContextDev(api_key=self.api_key)

    async def _run_sdk_call(self, operation: str, query: dict, runner_func, timeout: float = 45):
        """Runs a synchronous Context.dev call in a worker thread with timing and error logging."""
        start_time = time.perf_counter()
        try:
            res = await asyncio.wait_for(asyncio.to_thread(runner_func), timeout=timeout)
            duration_ms = (time.perf_counter() - start_time) * 1000
            return res, duration_ms
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_dev_api_call(
                provider="context.dev",
                operation=operation,
                query=query,
                response=None,
                status="ERROR",
                duration_ms=duration_ms,
                error=str(e),
            )
            raise

    # -----------------------------------------------------------------------
    # Twitter Scraping & Parsing Logic
    # -----------------------------------------------------------------------

    def _parse_scraped_tweets(self, clean_user: str, markdown: str, max_tweets: int = 10) -> tuple[str, list[str], list[dict]]:
        """Parses Twitter DOM converted to markdown containing status links and handles."""
        clean_user_lower = clean_user.lower()
        matches = list(SCRAPED_TWEET_START_RE.finditer(markdown))
        if not matches:
            return "", [], []

        # Extract bio and bio links from header preceding the first tweet
        header_text = markdown[:matches[0].start()].strip()
        extracted_bio = extract_bio_between_photo_and_joined(header_text)
        if extracted_bio is None:
            extracted_bio = extract_bio_between_photo_and_joined(markdown)

        if extracted_bio is not None:
            bio = extracted_bio
        else:
            nav_m = NAV_START_RE.search(header_text)
            bio = header_text[:nav_m.start()].strip() if nav_m else header_text
            if bio.startswith("#"):
                bio = bio.lstrip("#").strip()
            bio = re.sub(r"^(?:\[?[^@\n]+?\]?(?:\([^\)]+\))?\s+)?@?[a-zA-Z0-9_]+\s*·\s*\w*\s*", "", bio).strip()
        bio_links = _extract_external_links(bio, clean_user)

        tweets: list[dict] = []
        prev_other_post: dict[str, str] | None = None

        for i, m in enumerate(matches):
            author = m.group("handle").lower()
            status_id = m.group("status_id")
            start_pos = m.end()
            end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
            body = markdown[start_pos:end_pos].strip()
            body = FOOTER_METRICS_RE.sub("", body).strip()
            body = body.rstrip(" -").strip()

            if author != clean_user_lower:
                prev_other_post = {"author": f"@{author}", "text": body}
                continue

            in_reply_to = None
            rep_m = REPLY_START_RE.match(body)
            if rep_m:
                rep_author = f"@{rep_m.group(1)}"
                parent_text = ""
                if prev_other_post and prev_other_post["author"].lower() == rep_author.lower():
                    parent_text = prev_other_post["text"]
                in_reply_to = {"author": rep_author, "text": parent_text}
                body = body[rep_m.end():].strip()
            elif prev_other_post:
                in_reply_to = dict(prev_other_post)

            prev_other_post = None

            if len(body) < 10 and not extract_urls(body):
                continue

            links = _extract_external_links(body, clean_user)
            tweets.append({
                "id": status_id,
                "text": body,
                "links": links,
                "images": extract_image_urls(body),
                "in_reply_to": in_reply_to,
            })
            if len(tweets) >= max_tweets:
                break

        return bio, bio_links, tweets

    def _parse_line_based_timeline(self, clean_user: str, markdown: str, max_tweets: int = 10) -> tuple[str, list[str], list[dict]]:
        """Parses clean multiline fixtures or text-structured timelines into posts with thread context."""
        lines = [line.strip() for line in markdown.split("\n") if line.strip()]
        bio_lines: list[str] = []
        timeline_lines: list[str] = []
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

            if OPENS_PROFILE_PHOTO_RE.search(line):
                continue

            if HEADER_NAV_RE.match(line):
                is_in_timeline = True
                continue

            # Header / Bio extraction phase
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

        extracted_bio = extract_bio_between_photo_and_joined(markdown)
        if extracted_bio is not None:
            bio = extracted_bio
        else:
            bio = " ".join(bio_lines).strip()
        bio_links = _extract_external_links(bio, clean_user)

        # Parse timeline into structured tweet blocks
        raw_blocks: list[dict] = []
        current_block: dict | None = None

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

        tweets: list[dict] = []
        clean_user_lower = clean_user.lower()

        for i, block in enumerate(raw_blocks):
            block_author = block.get("author") or clean_user_lower
            if block_author != clean_user_lower:
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
                links = _extract_external_links(tweet_text, clean_user)
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

        # Fallback for unconventional markdown without explicit author lines
        if not tweets and timeline_lines:
            chunk: list[str] = []
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
                                "links": _extract_external_links(cleaned, clean_user),
                                "images": extract_image_urls(text),
                                "in_reply_to": None,
                            })
                        chunk = []
                        if len(tweets) >= max_tweets:
                            break

        return bio, bio_links, tweets

    def _parse_profile_and_tweets(self, username: str, markdown: str, max_tweets: int = 10) -> tuple[str, list[str], list[dict]]:
        """Unified parser: detects scraped status-link format vs line-based timeline format."""
        clean_user = username.lstrip("@").strip()
        extracted_bio = extract_bio_between_photo_and_joined(markdown)

        if SCRAPED_TWEET_START_RE.search(markdown):
            scraped_bio, scraped_links, scraped_tweets = self._parse_scraped_tweets(clean_user, markdown, max_tweets=max_tweets)
            if extracted_bio is not None:
                return extracted_bio, _extract_external_links(extracted_bio, clean_user), scraped_tweets
            if scraped_tweets or scraped_bio:
                return scraped_bio, scraped_links, scraped_tweets

        line_bio, line_links, line_tweets = self._parse_line_based_timeline(clean_user, markdown, max_tweets=max_tweets)
        if extracted_bio is not None:
            return extracted_bio, _extract_external_links(extracted_bio, clean_user), line_tweets
        return line_bio, line_links, line_tweets

    # -----------------------------------------------------------------------
    # Public Gateway Operations
    # -----------------------------------------------------------------------

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

        try:
            logger.info("Executing Context.dev web.scrape for Twitter profile @%s", clean_user)
            client = self._get_client()
            response, duration_ms = await self._run_sdk_call(
                operation="client.web.scrape (twitter profile)",
                query=query,
                runner_func=lambda: client.web.scrape(
                    url=url,
                    formats={"markdown": True},
                    shared_params={"main_content_only": True},
                ),
                timeout=60,
            )
            markdown = _extract_markdown(response)
            title = _extract_title(response)
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

            # If direct scrape returned fewer than max_tweets (e.g. login wall or unauthenticated page only renders ~4-5 initial tweets), supplement via public search
            if len(tweets) < max_tweets:
                logger.info("Direct scrape for @%s returned %d tweets (target: %d). Supplementing via Context.dev search.", clean_user, len(tweets), max_tweets)
                try:
                    search_bio, search_links, search_tweets = await self._search_tweets_via_context_dev(clean_user, max_tweets=max_tweets)
                    if not bio:
                        bio = search_bio
                        bio_links = search_links
                    existing_ids = {t.get("id") for t in tweets if t.get("id")}
                    existing_texts = {" ".join(t.get("text", "").lower().split()) for t in tweets}
                    for st in search_tweets:
                        st_id = st.get("id")
                        st_text = " ".join(st.get("text", "").lower().split())
                        if (st_id and st_id in existing_ids) or (st_text and st_text in existing_texts):
                            continue
                        tweets.append(st)
                        if st_id:
                            existing_ids.add(st_id)
                        if len(tweets) >= max_tweets:
                            break
                except Exception as search_err:
                    logger.warning("Context.dev search supplement for @%s failed: %s", clean_user, search_err)

            return {
                "username": clean_user,
                "profile_url": url,
                "bio": bio,
                "bio_links": bio_links,
                "raw_markdown": markdown,
                "tweets": tweets,
            }
        except Exception as e:
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

        search_query_str = f"site:x.com/{username} -inurl:search"
        num_res = max(15, min(100, max_tweets * 2 + 5))
        query = {
            "operation": "client.web.search",
            "target": "twitter_search_fallback",
            "username": username,
            "query": search_query_str,
            "num_results": num_res,
        }

        try:
            client = self._get_client()
            response, duration_ms = await self._run_sdk_call(
                operation="client.web.search (tweets fallback)",
                query=query,
                runner_func=lambda: client.web.search(
                    query=search_query_str,
                    num_results=num_res,
                ),
                timeout=30,
            )
            results = _extract_results(response)
            log_dev_api_call(
                provider="context.dev",
                operation="client.web.search (tweets fallback)",
                query=query,
                response={"results_count": len(results), "results": results},
                status="SUCCESS",
                duration_ms=duration_ms,
            )

            bio = ""
            bio_links: list[str] = []
            tweets: list[dict] = []

            for item in results:
                item_dict = _to_dict(item)
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
                    extracted = extract_bio_between_photo_and_joined(bio_candidate)
                    if extracted is not None:
                        bio = extracted
                    else:
                        if " on X: " in bio_candidate:
                            bio_candidate = bio_candidate.split(" on X: ", 1)[-1].strip('"').strip("'")
                        if any(x in bio_candidate.lower() for x in ("the latest tweets from", "the latest posts from")):
                            parts = LATEST_TWEETS_SPLIT_RE.split(bio_candidate)
                            if len(parts) > 1 and len(parts[1]) > 5:
                                bio_candidate = parts[1].strip()
                        bio = bio_candidate
                    bio_links = _extract_external_links(bio, username)

                # If snippet contains markdown tweets from scraping
                if SCRAPED_TWEET_START_RE.search(text):
                    s_bio, s_links, s_tweets = self._parse_scraped_tweets(username, text, max_tweets=max(0, max_tweets - len(tweets)))
                    if not bio and s_bio:
                        bio = s_bio
                        bio_links = s_links
                    for t in s_tweets:
                        if len(tweets) < max_tweets:
                            tweets.append(t)
                    continue

                if "/status/" not in res_url:
                    continue

                if " on X: " in text:
                    text = text.split(" on X: ", 1)[-1].strip('"').strip("'")

                if len(text) > 15 and len(tweets) < max_tweets:
                    in_reply_to = None
                    rep_match = re.match(r"^Replying to\s+(@[a-zA-Z0-9_]+):?\s*", text, re.IGNORECASE)
                    if rep_match:
                        in_reply_to = {"author": rep_match.group(1), "text": ""}
                        text = text[rep_match.end():].strip()

                    status_m = re.search(r"/status/(\d+)", res_url)
                    post_id = status_m.group(1) if status_m else f"tweet-{len(tweets)+1}"

                    valid_tweet_links = _extract_external_links(text, username)
                    tweets.append({
                        "id": post_id,
                        "text": text,
                        "links": valid_tweet_links,
                        "images": extract_image_urls(text),
                        "in_reply_to": in_reply_to,
                    })

            logger.info("Context.dev web search retrieved %d public tweets for @%s", len(tweets), username)
            return bio, bio_links, tweets
        except Exception as e:
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

        try:
            logger.info("Executing Context.dev web.scrape for link %s", url)
            client = self._get_client()
            response, duration_ms = await self._run_sdk_call(
                operation="client.web.scrape (link uncloak)",
                query=query,
                runner_func=lambda: client.web.scrape(
                    url=url,
                    formats={"markdown": True},
                    shared_params={"main_content_only": True},
                ),
                timeout=30,
            )
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
            logger.warning("Context.dev link scrape failed for %s: %s", url, e)
            return {
                "url": url,
                "resolved_url": url,
                "domain": urlparse(url).hostname or "",
                "title": "",
                "description": "",
                "markdown": "",
                "error": str(e),
            }

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

        try:
            logger.info("Executing Context.dev web.search for claim: '%s'", clean_query[:80])
            client = self._get_client()
            search_str = clean_query[:200]
            response, duration_ms = await self._run_sdk_call(
                operation="client.web.search (research claim)",
                query=query,
                runner_func=lambda: client.web.search(
                    query=search_str,
                    num_results=5,
                ),
                timeout=45,
            )
            results = _extract_results(response)
            log_dev_api_call(
                provider="context.dev",
                operation="client.web.search (research claim)",
                query=query,
                response={"results_count": len(results), "results": results},
                status="SUCCESS",
                duration_ms=duration_ms,
            )

            evidence: list[WebEvidence] = []
            for item in results:
                item_dict = _to_dict(item)
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
            logger.warning("Context.dev web search for '%s' returned: %s. Continuing with empty evidence.", clean_query, e)
            return []


# ---------------------------------------------------------------------------
# Deterministic Demo Research Provider
# ---------------------------------------------------------------------------

class DemoWebResearchProvider:
    """Mock research provider producing deterministic evidence for offline evaluation."""
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
