from app.adapters.social.base import PostData
from app.providers.llm import LLMProvider


async def extract_claims(posts: list[PostData], llm: LLMProvider):
    seen = set()
    clean = []
    for post in posts:
        key = " ".join(post.text.lower().split())
        if key and key not in seen:
            seen.add(key)
            clean.append(post)
    return await llm.extract_claims(clean)
