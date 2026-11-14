import logging
from urllib.parse import urlparse

from app.adapters.social.base import PostData, ProfileData, SocialProfileAdapter
from app.providers.context_dev import ContextDevProvider

logger = logging.getLogger(__name__)


class ContextDevTwitterAdapter(SocialProfileAdapter):
    """Twitter/X profile adapter that uses Context.dev to scrape live profiles and tweets."""

    def __init__(self, provider: ContextDevProvider | None = None, max_tweets: int = 10):
        self.provider = provider or ContextDevProvider()
        self.max_tweets = max_tweets
        self._cached_scrape: dict[str, dict] = {}

    async def fetch_profile(self, profile_url: str) -> ProfileData:
        url = profile_url.strip()
        if "://" in url:
            path = urlparse(url).path.strip("/")
            username = path.split("/")[0] if path else "sample-user"
        else:
            username = url.lstrip("@").strip()

        username = username.lower()
        bio = ""
        bio_links: list[str] = []

        try:
            scraped = await self.provider.scrape_twitter_profile(username, max_tweets=self.max_tweets)
            self._cached_scrape[username] = scraped
            bio = scraped.get("bio", "")
            bio_links = scraped.get("bio_links", [])
        except Exception as e:
            logger.warning("Pre-scraping profile header for @%s failed: %s", username, e)

        return ProfileData(
            platform="twitter",
            username=username,
            profile_url=f"https://x.com/{username}",
            bio=bio,
            bio_links=bio_links,
        )

    async def fetch_posts(self, profile: ProfileData, limit: int | None = None) -> list[PostData]:
        limit = limit if limit is not None else self.max_tweets
        username = profile.username.lower()

        if username in self._cached_scrape and self._cached_scrape[username]:
            scraped = self._cached_scrape[username]
        else:
            scraped = await self.provider.scrape_twitter_profile(username, max_tweets=limit)
            self._cached_scrape[username] = scraped

        raw_tweets = scraped.get("tweets", [])[:limit]
        posts = []
        for i, item in enumerate(raw_tweets, 1):
            post_id = item.get("id") or f"{username}-{i}"
            text = item.get("text", "")
            links = item.get("links", [])
            images = item.get("images", [])
            posts.append(
                PostData(
                    platform_post_id=post_id,
                    text=text,
                    post_url=f"https://x.com/{username}/status/{post_id}",
                    images=images,
                    metadata={"links": links, "images": images},
                )
            )
        return posts
