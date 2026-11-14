from app.schemas.evidence import WebEvidence


class WebResearchProvider:
    async def research_claim(self, claim: str) -> list[WebEvidence]:
        raise NotImplementedError
