from urllib.parse import urlparse

from app.adapters.social.base import PostData, ProfileData, SocialProfileAdapter
from app.providers.context_dev import ContextDevProvider


class ContextDevTwitterAdapter(SocialProfileAdapter):
    """Twitter/X profile adapter that uses Context.dev to scrape live profiles and tweets."""

    def __init__(self, provider: ContextDevProvider | None = None):
        self.provider = provider or ContextDevProvider()

    async def fetch_profile(self, profile_url: str) -> ProfileData:
        url = profile_url.strip()
        if "://" in url:
            path = urlparse(url).path.strip("/")
            username = path.split("/")[0] if path else "sample-user"
        else:
            username = url.lstrip("@").strip()

        return ProfileData(
            platform="twitter",
            username=username,
            profile_url=f"https://x.com/{username}",
        )

    async def fetch_posts(self, profile: ProfileData) -> list[PostData]:
        scraped = await self.provider.scrape_twitter_profile(profile.username)
        raw_tweets = scraped.get("tweets", [])

        posts = []
        for i, item in enumerate(raw_tweets, 1):
            post_id = item.get("id") or f"{profile.username}-{i}"
            text = item.get("text", "")
            links = item.get("links", [])
            posts.append(
                PostData(
                    platform_post_id=post_id,
                    text=text,
                    post_url=f"https://x.com/{profile.username}/status/{post_id}",
                    metadata={"links": links},
                )
            )
        return posts
