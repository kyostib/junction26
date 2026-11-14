from abc import ABC, abstractmethod
from datetime import datetime
from pydantic import BaseModel


class ProfileData(BaseModel):
    platform: str
    username: str
    profile_url: str


class PostData(BaseModel):
    platform_post_id: str
    text: str
    post_url: str | None = None
    published_at: datetime | None = None
    metadata: dict = {}


class SocialProfileAdapter(ABC):
    @abstractmethod
    async def fetch_profile(self, profile_url: str) -> ProfileData: ...

    @abstractmethod
    async def fetch_posts(self, profile: ProfileData) -> list[PostData]: ...
