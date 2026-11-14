import json
import logging
from typing import Protocol

import httpx

from app.adapters.social.base import PostData
from app.core.config import get_settings
from app.schemas.claims import ClaimExtraction, ClaimType
from app.schemas.evidence import EvidenceEvaluation, Stance, WebEvidence

logger = logging.getLogger(__name__)


class LLMProvider(Protocol):
    async def extract_claims(self, posts: list[PostData]) -> list[ClaimExtraction]: ...

    async def evaluate_evidence(self, claim: ClaimExtraction, evidence: list[WebEvidence]) -> EvidenceEvaluation: ...

    async def evaluate_spam_and_harm(self, posts: list[PostData], scraped_links: list[dict]) -> dict: ...


class GeminiLLMProvider:
    """Google AI Studio (Gemini) provider using direct AsyncClient REST requests."""

    def __init__(self, api_key: str, model: str = "gemini-4-hydrogen"):
        self.api_key = api_key
        self.model = model
        self.fallback_demo = DemoLLMProvider()

    async def _generate_json(self, prompt: str) -> dict | list | None:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.1,
            },
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 404 and "2.0" in self.model:
                # Fallback to gemini-1.5-flash if 2.0-flash is unavailable on this key
                fallback_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={self.api_key}"
                resp = await client.post(fallback_url, json=payload)

            if not resp.is_success:
                logger.warning("Gemini API call failed with status %s: %s", resp.status_code, resp.text)
                return None

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return None
            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                return None
            text = parts[0].get("text", "")
            try:
                return json.loads(text)
            except Exception as e:
                logger.warning("Failed to parse Gemini JSON response: %s", e)
                return None

    async def extract_claims(self, posts: list[PostData]) -> list[ClaimExtraction]:
        post_items = [{"id": p.platform_post_id, "text": p.text} for p in posts]
        prompt = (
            "You are a factual claim extraction engine for social media. Given the following tweets, extract all testable factual assertions. "
            "Ignore trivial personal opinions, jokes, and greetings. "
            "Return a JSON array of objects with the exact format:\n"
            '[{"post_id": string, "text": string, "claim_type": "factual"|"opinion"|"prediction"|"rhetorical", "importance": float between 0.0 and 1.0}]\n\n'
            f"Tweets:\n{json.dumps(post_items, indent=2)}"
        )

        try:
            res = await self._generate_json(prompt)
            if isinstance(res, list) and res:
                claims = []
                for item in res:
                    ctype = item.get("claim_type", "factual").lower()
                    claim_type_enum = ClaimType.factual if ctype not in ClaimType.__members__ else ClaimType(ctype)
                    claims.append(
                        ClaimExtraction(
                            post_id=str(item.get("post_id")),
                            text=str(item.get("text")),
                            claim_type=claim_type_enum,
                            importance=float(item.get("importance", 0.7)),
                        )
                    )
                return claims
        except Exception as e:
            logger.warning("Gemini claim extraction failed: %s. Using heuristic fallback.", e)

        return await self.fallback_demo.extract_claims(posts)

    async def evaluate_evidence(self, claim: ClaimExtraction, evidence: list[WebEvidence]) -> EvidenceEvaluation:
        if not evidence:
            return await self.fallback_demo.evaluate_evidence(claim, evidence)

        evidence_items = [
            {"source": e.source_domain, "url": e.url, "excerpt": e.excerpt, "quality": e.source_quality}
            for e in evidence
        ]

        prompt = (
            "You are an evidence evaluation engine. Compare the statement against the web evidence found by Context.dev.\n"
            f"Statement: \"{claim.text}\"\n\n"
            f"Web Evidence Sources:\n{json.dumps(evidence_items, indent=2)}\n\n"
            "Return a JSON object with this exact schema:\n"
            "{\n"
            '  "accuracy": float between -1.0 (completely false/contradicted) and 1.0 (completely true/supported),\n'
            '  "verification_status": "supported" | "contradicted" | "partially_supported" | "uncertain",\n'
            '  "reason": string (short 1-2 sentence explanation of whether evidence backs the claim),\n'
            '  "misleadingness": float between 0.0 and 1.0,\n'
            '  "sensationalism": float between 0.0 and 1.0\n'
            "}"
        )

        try:
            res = await self._generate_json(prompt)
            if isinstance(res, dict) and "accuracy" in res:
                avg_quality = sum(e.source_quality for e in evidence) / len(evidence)
                return EvidenceEvaluation(
                    accuracy=float(res.get("accuracy", 0.0)),
                    evidence_quality=avg_quality,
                    misleadingness=float(res.get("misleadingness", 0.0)),
                    sensationalism=float(res.get("sensationalism", 0.0)),
                    verification_status=str(res.get("verification_status", "uncertain")),
                    reason=str(res.get("reason", "Evaluated via Gemini and Context.dev sources.")),
                )
        except Exception as e:
            logger.warning("Gemini evidence evaluation failed: %s. Using heuristic fallback.", e)

        return await self.fallback_demo.evaluate_evidence(claim, evidence)

    async def evaluate_spam_and_harm(self, posts: list[PostData], scraped_links: list[dict]) -> dict:
        post_texts = [p.text for p in posts]
        link_previews = [{"url": l.get("url"), "title": l.get("title"), "domain": l.get("domain")} for l in scraped_links]

        prompt = (
            "You are a spam, scam, and harmful content detector for social media profiles.\n"
            "Analyze these tweets and the external landing pages uncloaked from links in the tweets.\n\n"
            f"Tweets:\n{json.dumps(post_texts, indent=2)}\n\n"
            f"Uncloaked Links:\n{json.dumps(link_previews, indent=2)}\n\n"
            "Look for:\n"
            "- Crypto token presales, fake airdrops, phishing links, affiliate spam, engagement farming.\n"
            "- Severe harassment, hate speech, abusive language.\n\n"
            "Return a JSON object with this exact schema:\n"
            "{\n"
            '  "spam_score": float between 0.0 (no spam) and 100.0 (blatant scam/spam),\n'
            '  "harm_score": float between 0.0 (safe) and 100.0 (toxic/harmful),\n'
            '  "spam_flags": array of strings (describe any flagged spam/scam reasons),\n'
            '  "harm_flags": array of strings (describe any flagged harmful/toxic reasons)\n'
            "}"
        )

        try:
            res = await self._generate_json(prompt)
            if isinstance(res, dict) and "spam_score" in res:
                return {
                    "spam_score": round(float(res.get("spam_score", 0.0)), 1),
                    "harm_score": round(float(res.get("harm_score", 0.0)), 1),
                    "spam_flags": [str(x) for x in res.get("spam_flags", [])][:3],
                    "harm_flags": [str(x) for x in res.get("harm_flags", [])][:3],
                }
        except Exception as e:
            logger.warning("Gemini spam/harm analysis failed: %s. Using heuristic fallback.", e)

        return await self.fallback_demo.evaluate_spam_and_harm(posts, scraped_links)


class DemoLLMProvider:
    """Intelligent heuristic and demo evaluator.
    Supports claim extraction, stance evaluation on live Context.dev search, and spam/harm link analysis.
    """

    async def extract_claims(self, posts: list[PostData]) -> list[ClaimExtraction]:
        found = []
        for post in posts:
            text = post.text.strip()
            lower = text.lower()
            if not text or any(x in lower for x in ("i love", "amazing 😂", "good morning")):
                continue

            # Check for factual claims
            if any(x in lower for x in ("eu adopted", "great wall", "temperatures have risen", "visible from the moon", "nasa", "co2 emissions", "law", "percent", "study")):
                found.append(ClaimExtraction(post_id=post.platform_post_id, text=text, claim_type=ClaimType.factual, importance=0.85))
            elif any(x in lower for x in ("everyone knows", "destroy the country", "ridiculous")):
                found.append(ClaimExtraction(post_id=post.platform_post_id, text=text, claim_type=ClaimType.rhetorical, importance=0.35))
            elif any(x in lower for x in ("will ", "going to ")):
                found.append(ClaimExtraction(post_id=post.platform_post_id, text=text, claim_type=ClaimType.prediction, importance=0.5))
            else:
                found.append(ClaimExtraction(post_id=post.platform_post_id, text=text, claim_type=ClaimType.factual, importance=0.6))
        return found

    async def evaluate_evidence(self, claim: ClaimExtraction, evidence: list[WebEvidence]) -> EvidenceEvaluation:
        if not evidence:
            return EvidenceEvaluation(
                accuracy=0.0,
                evidence_quality=0.0,
                misleadingness=0.0,
                sensationalism=0.0,
                verification_status="uncertain",
                reason="No usable web evidence was available via Context.dev.",
            )

        claim_lower = claim.text.lower()
        evaluated_evidence = []

        for e in evidence:
            excerpt_lower = e.excerpt.lower()
            stance = e.stance

            # If stance was not pre-labeled (live search from Context.dev), classify it
            if stance == Stance.context_only:
                if any(x in excerpt_lower for x in ("not visible", "myth", "false", "disproven", "incorrect", "debunked", "untrue", "prohibit every")):
                    stance = Stance.contradicts
                elif any(x in excerpt_lower for x in ("increased", "record high", "confirmed", "true", "adopted", "regulations target new")):
                    if "not universally" in excerpt_lower or "partially" in excerpt_lower:
                        stance = Stance.partially_supports
                    else:
                        stance = Stance.supports

            evaluated_evidence.append((e, stance, e.relevance * e.source_quality))

        denom = sum(weight for _, _, weight in evaluated_evidence)
        stance_values = {
            Stance.supports: 1.0,
            Stance.partially_supports: 0.25,
            Stance.context_only: 0.0,
            Stance.irrelevant: 0.0,
            Stance.contradicts: -1.0,
        }

        accuracy = sum(stance_values[st] * w for _, st, w in evaluated_evidence) / denom if denom else 0.0

        if accuracy >= 0.5:
            status = "supported"
            reason = "Claim is substantiated by authoritative Context.dev web sources."
        elif accuracy <= -0.5:
            status = "contradicted"
            reason = "Authoritative web evidence directly contradicts this claim."
        elif accuracy > 0.05 or any(st == Stance.partially_supports for _, st, _ in evaluated_evidence):
            status = "partially_supported"
            reason = "Evidence partially supports the statement, but nuances or key caveats were omitted."
        else:
            status = "uncertain"
            reason = "Evidence is inconclusive or mixed across sources."

        misleading = 0.7 if status == "partially_supported" or status == "contradicted" else 0.0
        sensationalism = 0.6 if any(x in claim_lower for x in ("unbelievable", "secret", "never seen before", "destroy")) else 0.1

        avg_quality = sum(e.source_quality for e, _, _ in evaluated_evidence) / len(evaluated_evidence)
        return EvidenceEvaluation(
            accuracy=accuracy,
            evidence_quality=avg_quality,
            misleadingness=misleading,
            sensationalism=sensationalism,
            verification_status=status,
            reason=reason,
        )

    async def evaluate_spam_and_harm(self, posts: list[PostData], scraped_links: list[dict]) -> dict:
        spam_score = 0.0
        harm_score = 0.0
        spam_flags = []
        harm_flags = []

        for link in scraped_links:
            url = link.get("url", "")
            title = link.get("title", "").lower()
            text = link.get("markdown", "").lower()

            if any(x in url.lower() or x in title or x in text for x in ("airdrop", "presale", "token-claim", "free allocation", "crypto-drain")):
                spam_score = max(spam_score, 88.0)
                spam_flags.append(f"Suspicious crypto airdrop/presale page uncloaked at {link.get('domain', url)}")
            elif any(x in title or x in text for x in ("affiliate", "promo code", "buy now", "discount")):
                spam_score = max(spam_score, 55.0)
                spam_flags.append(f"Commercial/affiliate link at {link.get('domain', url)}")

        total_posts = len(posts) or 1
        spam_keyword_count = 0
        for p in posts:
            lower = p.text.lower()
            if any(x in lower for x in ("presale", "airdrop", "100x", "claim free", "limited spots", "check out this secret")):
                spam_keyword_count += 1
                spam_flags.append(f"Engagement/token bait: '{p.text[:60]}...'")
            if any(x in lower for x in ("hate", "die", "kill", "vermin", "traitor", "scam them all")):
                harm_score = max(harm_score, 80.0)
                harm_flags.append("Hostile or abusive language detected in post")

        if spam_keyword_count > 0:
            keyword_spam = min(100.0, (spam_keyword_count / total_posts) * 120.0)
            spam_score = max(spam_score, keyword_spam)

        return {
            "spam_score": round(min(100.0, spam_score), 1),
            "harm_score": round(min(100.0, harm_score), 1),
            "spam_flags": list(set(spam_flags))[:3],
            "harm_flags": list(set(harm_flags))[:3],
        }


def get_llm_provider() -> LLMProvider:
    """Factory that returns GeminiLLMProvider if configured, otherwise DemoLLMProvider."""
    settings = get_settings()
    provider_name = (settings.llm_provider or "").lower().strip()
    api_key = settings.llm_api_key

    if provider_name in ("gemini", "google", "google-ai-studio") and api_key:
        logger.info("Initializing GeminiLLMProvider with model gemini-2.0-flash")
        return GeminiLLMProvider(api_key=api_key)

    return DemoLLMProvider()
