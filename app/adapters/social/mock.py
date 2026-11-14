from app.adapters.social.base import PostData, ProfileData, SocialProfileAdapter


class MockSocialAdapter(SocialProfileAdapter):
    async def fetch_profile(self, profile_url: str) -> ProfileData:
        username = profile_url.rstrip("/").rsplit("/", 1)[-1] or "sample-account"
        return ProfileData(
            platform="mock",
            username=username,
            profile_url=profile_url,
            bio="Tech researcher & market observer. Insights on climate and European tech policy.",
            bio_links=["https://example.org/report"],
        )

    async def fetch_posts(self, profile: ProfileData, limit: int = 10) -> list[PostData]:
        fixtures = [
            "The EU adopted a law targeting new cars with CO2 emissions from 2035; it did not prohibit every existing petrol car.",
            "The Great Wall of China is visible from the Moon with the naked eye.",
            "Global average temperatures have risen substantially since the late 19th century, according to NASA.",
            "I love pizza and sunny weekends.",
            "This policy is amazing 😂",
            "Everyone knows this ridiculous plan will destroy the country overnight!",
            "I was wrong about the Moon claim after checking the sources. Here's a correction: it is not visible from the Moon unaided.",
            "Read the report at https://example.org/report — it deserves a closer look.",
        ]
        images_map = {
            2: ["https://demo.invalid/images/great_wall_moon.jpg"],
            3: ["https://demo.invalid/images/nasa_climate_chart.png"],
        }
        return [
            PostData(
                platform_post_id=f"mock-{i}",
                text=text,
                post_url=f"{profile.profile_url}/posts/{i}",
                images=images_map.get(i, []),
                metadata={
                    "links": ["https://example.org/report"] if "https://example.org/report" in text else [],
                    "images": images_map.get(i, []),
                },
            )
            for i, text in enumerate(fixtures[:limit], 1)
        ]
