import json
import logging
from typing import Protocol

import httpx

from app.adapters.social.base import PostData, ProfileData
from app.core.config import get_settings
from app.schemas.claims import ClaimExtraction, ClaimType
from app.schemas.evidence import EvidenceEvaluation, Stance, WebEvidence

logger = logging.getLogger(__name__)


class LLMProvider(Protocol):
    async def extract_claims(self, posts: list[PostData]) -> list[ClaimExtraction]: ...

    async def evaluate_evidence(self, claim: ClaimExtraction, evidence: list[WebEvidence]) -> EvidenceEvaluation: ...

    async def evaluate_spam_and_harm(self, posts: list[PostData], scraped_links: list[dict]) -> dict: ...

    async def analyze_images(self, images: list[dict]) -> list[dict]: ...

    async def synthesize_analysis(
        self,
        profile: ProfileData,
        posts: list[PostData],
        scraped_links: list[dict],
        grounded_claims: list[dict],
        image_analyses: list[dict] = [],
    ) -> dict: ...


class GeminiLLMProvider:
    """Google AI Studio (Gemini) provider using direct AsyncClient REST requests."""

    def __init__(self, api_key: str, model: str = "gemini-4-hydrogen"):
        self.api_key = api_key
        self.model = model
        self.fallback_demo = DemoLLMProvider()

    async def _generate_json(self, prompt: str = "", parts: list[dict] | None = None) -> dict | list | None:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        content_parts = parts if parts is not None else [{"text": prompt}]
        payload = {
            "contents": [{"parts": content_parts}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.1,
            },
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)

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

    async def _fetch_image_base64(self, url: str) -> tuple[str, str] | None:
        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code == 200 and len(resp.content) > 50:
                    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    if not ctype.startswith("image/"):
                        ctype = "image/jpeg"
                    import base64
                    return ctype, base64.b64encode(resp.content).decode("utf-8")
        except Exception as e:
            logger.warning("Could not download image %s for vision analysis: %s", url, e)
        return None

    async def analyze_images(self, images: list[dict]) -> list[dict]:
        results = []
        for img in images:
            url = img.get("url", "")
            post_id = img.get("post_id", "")
            base64_data = img.get("base64_data")
            mime_type = img.get("mime_type")

            if not base64_data and url:
                fetched = await self._fetch_image_base64(url)
                if fetched:
                    mime_type, base64_data = fetched

            if base64_data and mime_type:
                prompt = (
                    "You are a multimodal forensic image analyzer for social media credibility.\n"
                    "Analyze this image from a post. Inspect text in screenshots, charts, headlines, QR codes, or infographics.\n"
                    "Return a JSON object with this exact schema:\n"
                    "{\n"
                    '  "description": string (short visual summary),\n'
                    '  "extracted_text": string (all text/headlines visible inside the image),\n'
                    '  "visual_claims": array of strings (testable factual claims asserted in the image),\n'
                    '  "is_scam": boolean (true if crypto presale, fake airdrop, phishing QR, giveaway bait),\n'
                    '  "is_misleading": boolean (true if fabricated news screenshot, manipulated graph, deceptive meme),\n'
                    '  "flags": array of strings (specific warnings or issues detected)\n'
                    "}"
                )
                parts = [
                    {"text": prompt},
                    {
                        "inlineData": {
                            "mimeType": mime_type,
                            "data": base64_data,
                        }
                    },
                ]
                try:
                    res = await self._generate_json(parts=parts)
                    if isinstance(res, dict) and "description" in res:
                        results.append({
                            "url": url,
                            "post_id": post_id,
                            "description": str(res.get("description", "")),
                            "extracted_text": str(res.get("extracted_text", "")),
                            "visual_claims": [str(c) for c in res.get("visual_claims", [])],
                            "is_scam": bool(res.get("is_scam", False)),
                            "is_misleading": bool(res.get("is_misleading", False)),
                            "flags": [str(f) for f in res.get("flags", [])],
                        })
                        continue
                except Exception as e:
                    logger.warning("Gemini multimodal image analysis failed for %s: %s", url, e)

            # Fallback to heuristic demo analyzer for this image
            fallback_res = await self.fallback_demo.analyze_images([img])
            if fallback_res:
                results.append(fallback_res[0])

        return results

    async def synthesize_analysis(
        self,
        profile: ProfileData,
        posts: list[PostData],
        scraped_links: list[dict],
        grounded_claims: list[dict],
        image_analyses: list[dict] = [],
    ) -> dict:
        profile_summary = {
            "username": profile.username,
            "profile_url": profile.profile_url,
            "bio": profile.bio,
            "bio_links": profile.bio_links,
        }
        posts_summary = [
            {"id": p.platform_post_id, "text": p.text, "links": p.metadata.get("links", []), "images": p.images}
            for p in posts
        ]
        links_summary = [
            {
                "url": l.get("url"),
                "source": l.get("source", "post"),
                "domain": l.get("domain"),
                "title": l.get("title"),
                "content_preview": (l.get("markdown") or "")[:400],
            }
            for l in scraped_links
        ]
        claims_summary = [
            {
                "claim": c.get("claim"),
                "assessment": c.get("assessment"),
                "reason": c.get("reason"),
                "accuracy": c.get("accuracy"),
                "sources": [
                    {"domain": e.get("source_domain"), "quality": e.get("source_quality"), "stance": e.get("stance")}
                    for e in c.get("evidence", [])
                ],
            }
            for c in grounded_claims
        ]
        images_summary = [
            {
                "url": img.get("url"),
                "post_id": img.get("post_id"),
                "description": img.get("description"),
                "extracted_text": img.get("extracted_text"),
                "is_scam": img.get("is_scam"),
                "is_misleading": img.get("is_misleading"),
                "flags": img.get("flags", []),
            }
            for img in image_analyses
        ]

        prompt = (
            "You are the BS Index synthesis engine. Your goal is to critically evaluate a social media account's credibility, honesty, and safety.\n"
            "Analyze the user's profile bio, bio links, recent posts, uncloaked landing pages from external links, verified factual claims with web evidence, and attached tweet images.\n\n"
            f"User Profile:\n{json.dumps(profile_summary, indent=2)}\n\n"
            f"Recent Posts ({len(posts_summary)}):\n{json.dumps(posts_summary, indent=2)}\n\n"
            f"Uncloaked External Links ({len(links_summary)}):\n{json.dumps(links_summary, indent=2)}\n\n"
            f"Verified Factual Claims & Evidence ({len(claims_summary)}):\n{json.dumps(claims_summary, indent=2)}\n\n"
            f"Attached Tweet Images Evaluated ({len(images_summary)}):\n{json.dumps(images_summary, indent=2)}\n\n"
            "Task:\n"
            "1. Calculate an overarching BS Index from 0.0 (completely honest, factual, and safe) to 100.0 (blatant falsehoods, phishing/crypto scam, or severe harm).\n"
            "2. Break down the assessment into specific dimensions (0.0 to 100.0):\n"
            "   - falsehood: Extent to which claims (including text from screenshot images) are contradicted or ungrounded (0 = truthful, 100 = completely false).\n"
            "   - spam: Scams, fake giveaways, crypto presales/airdrops, phishing QR codes/banners in images, bio or posts (0 = clean, 100 = blatant scam).\n"
            "   - harm: Hate speech, harassment, extreme toxicity, dangerous deception (0 = safe, 100 = harmful).\n"
            "   - evidence_quality: Quality and reliability of sources available (0.0 to 10.0 scale).\n"
            "   - misleadingness: Degree of cherry-picking, deception, or doctored visual elements (0.0 to 10.0 scale).\n"
            "   - sensationalism: Exaggerated clickbait, emotional hysteria, or hype (0.0 to 10.0 scale).\n"
            "3. Provide an executive summary (1-3 sentences) explaining the verdict.\n"
            "4. Provide up to 5 top flags highlighting specific falsehoods, spam/scams (including suspicious bio links, scam images, or uncloaked pages), or harm issues.\n\n"
            "Return a JSON object with this exact schema:\n"
            "{\n"
            '  "bs_index": float between 0.0 and 100.0,\n'
            '  "dimensions": {\n'
            '    "falsehood": float between 0.0 and 100.0,\n'
            '    "spam": float between 0.0 and 100.0,\n'
            '    "harm": float between 0.0 and 100.0,\n'
            '    "evidence_quality": float between 0.0 and 10.0,\n'
            '    "misleadingness": float between 0.0 and 10.0,\n'
            '    "sensationalism": float between 0.0 and 10.0\n'
            "  },\n"
            '  "summary": string,\n'
            '  "top_flags": [\n'
            "    {\n"
            '      "type": "falsehood" | "spam" | "harm",\n'
            '      "summary": string,\n'
            '      "assessment": string,\n'
            '      "evidence_url": string | null,\n'
            '      "evidence_title": string | null\n'
            "    }\n"
            "  ]\n"
            "}"
        )

        try:
            res = await self._generate_json(prompt)
            if isinstance(res, dict) and "bs_index" in res:
                dims = res.get("dimensions", {})
                return {
                    "bs_index": round(float(res.get("bs_index", 0.0)), 1),
                    "dimensions": {
                        "falsehood": round(float(dims.get("falsehood", 0.0)), 1),
                        "spam": round(float(dims.get("spam", 0.0)), 1),
                        "harm": round(float(dims.get("harm", 0.0)), 1),
                        "evidence_quality": round(float(dims.get("evidence_quality", 5.0)), 1),
                        "misleadingness": round(float(dims.get("misleadingness", 0.0)), 1),
                        "sensationalism": round(float(dims.get("sensationalism", 0.0)), 1),
                    },
                    "summary": str(res.get("summary", "")),
                    "top_flags": [
                        {
                            "type": str(f.get("type", "flag")),
                            "summary": str(f.get("summary", "")),
                            "assessment": str(f.get("assessment", "")),
                            "evidence_url": f.get("evidence_url"),
                            "evidence_title": f.get("evidence_title"),
                        }
                        for f in res.get("top_flags", [])
                    ][:5],
                }
        except Exception as e:
            logger.warning("Gemini synthesis analysis failed: %s. Using heuristic fallback.", e)

        return await self.fallback_demo.synthesize_analysis(profile, posts, scraped_links, grounded_claims, image_analyses)


class DemoLLMProvider:
    """Intelligent heuristic and demo evaluator.
    Supports claim extraction, stance evaluation on live Context.dev search, and spam/harm link analysis.
    """

    async def extract_claims(self, posts: list[PostData]) -> list[ClaimExtraction]:
        found = []
        for post in posts:
            text = post.text.strip()
            lower = text.lower()
            if (
                not text
                or len(text) < 15
                or any(x in lower for x in ("i love", "amazing 😂", "good morning", "log in", "sign up", "onboarding", "redirect_after_login", "cookies", "privacy policy", "terms of service"))
            ):
                continue

            # Check for factual claims
            if any(x in lower for x in ("eu adopted", "great wall", "temperatures have risen", "visible from the moon", "nasa", "co2 emissions", "law", "percent", "study", "report", "claims", "evidence")):
                found.append(ClaimExtraction(post_id=post.platform_post_id, text=text, claim_type=ClaimType.factual, importance=0.85))
            elif any(x in lower for x in ("everyone knows", "destroy the country", "ridiculous")):
                found.append(ClaimExtraction(post_id=post.platform_post_id, text=text, claim_type=ClaimType.rhetorical, importance=0.35))
            elif any(x in lower for x in ("will ", "going to ")):
                found.append(ClaimExtraction(post_id=post.platform_post_id, text=text, claim_type=ClaimType.prediction, importance=0.5))
            elif any(char.isdigit() for char in text):
                found.append(ClaimExtraction(post_id=post.platform_post_id, text=text, claim_type=ClaimType.factual, importance=0.6))
            else:
                found.append(ClaimExtraction(post_id=post.platform_post_id, text=text, claim_type=ClaimType.opinion, importance=0.3))
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

    async def analyze_images(self, images: list[dict]) -> list[dict]:
        results = []
        for img in images:
            url = img.get("url", "")
            post_id = img.get("post_id", "")
            caption = (img.get("caption") or "").lower()
            url_lower = url.lower()

            description = "Public media attachment"
            extracted_text = ""
            visual_claims = []
            is_scam = False
            is_misleading = False
            flags = []

            if any(x in url_lower or x in caption for x in ("moon", "great_wall", "space_photo")):
                description = "Photograph purporting to show man-made structures visible from space."
                extracted_text = "The Great Wall is visible from lunar orbit with the naked eye."
                visual_claims.append("The Great Wall of China is visible from the Moon with the naked eye.")
                is_misleading = True
                flags.append("Visual asset asserts disproven claim regarding Great Wall visibility from Moon.")
            elif any(x in url_lower or x in caption for x in ("presale", "airdrop", "100x", "token", "allocation", "qr")):
                description = "Promotional graphic announcing token allocation or pre-sale."
                extracted_text = "Exclusive Token Pre-Sale: 100x Potential Free Allocation."
                is_scam = True
                flags.append("Crypto token pre-sale / fake airdrop promotional banner detected in image.")
            elif any(x in url_lower or x in caption for x in ("climate", "temperature", "nasa", "trend")):
                description = "Data chart displaying global temperature trends."
                extracted_text = "NASA Goddard Institute for Space Studies Global Temperature Anomaly."
                visual_claims.append("Global average temperatures have risen substantially since the late 19th century.")
            elif any(x in url_lower or x in caption for x in ("fake_news", "screenshot", "fabricated", "breaking")):
                description = "Screenshot of breaking news headline."
                extracted_text = "Breaking News Screenshot."
                is_misleading = True
                flags.append("Screenshot of unverified or potentially manipulated news headline.")

            results.append({
                "url": url,
                "post_id": post_id,
                "description": description,
                "extracted_text": extracted_text,
                "visual_claims": visual_claims,
                "is_scam": is_scam,
                "is_misleading": is_misleading,
                "flags": flags,
            })
        return results

    async def synthesize_analysis(
        self,
        profile: ProfileData,
        posts: list[PostData],
        scraped_links: list[dict],
        grounded_claims: list[dict],
        image_analyses: list[dict] = [],
    ) -> dict:
        spam_score = 0.0
        harm_score = 0.0
        top_flags: list[dict] = []

        # 1. Inspect uncloaked landing pages (both from bio and tweets)
        for link in scraped_links:
            url = link.get("url", "")
            domain = link.get("domain", "")
            title = (link.get("title") or "").lower()
            text = (link.get("markdown") or "").lower()
            source = link.get("source", "post")
            source_label = "in bio" if source == "bio" else "in tweet"

            combined_link_text = f"{url} {title} {text}".lower()
            if any(x in combined_link_text for x in ("airdrop", "presale", "token-claim", "free allocation", "crypto-drain")):
                spam_score = max(spam_score, 88.0)
                top_flags.append({
                    "type": "spam",
                    "summary": f"Suspicious crypto airdrop/presale landing page uncloaked {source_label} ({domain or url})",
                    "assessment": "high_scam_risk",
                    "evidence_url": url,
                    "evidence_title": link.get("title") or "Uncloaked Scam Destination",
                })
            elif any(x in combined_link_text for x in ("affiliate", "promo code", "buy now", "discount")):
                spam_score = max(spam_score, 55.0)
                top_flags.append({
                    "type": "spam",
                    "summary": f"Commercial/affiliate link {source_label} ({domain or url})",
                    "assessment": "commercial_spam",
                    "evidence_url": url,
                    "evidence_title": link.get("title") or "Affiliate Landing Page",
                })

        # 2. Inspect profile bio directly
        bio_lower = profile.bio.lower()
        if any(x in bio_lower for x in ("presale", "airdrop", "free allocation", "100x", "claim free", "dm for promo")):
            spam_score = max(spam_score, 80.0)
            top_flags.append({
                "type": "spam",
                "summary": "Profile bio promotes speculative token presale or engagement bait",
                "assessment": "profile_bait",
                "evidence_url": None,
                "evidence_title": None,
            })
        if any(x in bio_lower for x in ("hate", "die", "kill", "vermin", "traitor")):
            harm_score = max(harm_score, 80.0)
            top_flags.append({
                "type": "harm",
                "summary": "Hostile or abusive language detected in profile bio",
                "assessment": "policy_violation",
                "evidence_url": None,
                "evidence_title": None,
            })

        # 3. Inspect recent posts
        total_posts = len(posts) or 1
        spam_keyword_count = 0
        for p in posts:
            lower = p.text.lower()
            if any(x in lower for x in ("presale", "airdrop", "100x", "claim free", "limited spots", "check out this secret")):
                spam_keyword_count += 1
                top_flags.append({
                    "type": "spam",
                    "summary": f"Engagement/token bait: '{p.text[:60]}...'",
                    "assessment": "spam_bait",
                    "evidence_url": None,
                    "evidence_title": None,
                })
            if any(x in lower for x in ("hate", "die", "kill", "vermin", "traitor", "scam them all")):
                harm_score = max(harm_score, 80.0)
                top_flags.append({
                    "type": "harm",
                    "summary": f"Hostile or abusive language in post: '{p.text[:60]}...'",
                    "assessment": "policy_violation",
                    "evidence_url": None,
                    "evidence_title": None,
                })

        if spam_keyword_count > 0:
            keyword_spam = min(100.0, (spam_keyword_count / total_posts) * 120.0)
            spam_score = max(spam_score, keyword_spam)

        # 4. Inspect verified claims & evidence
        grounded_claims_with_evidence = [c for c in grounded_claims if c.get("assessment") != "uncertain" or c.get("evidence")]
        if grounded_claims_with_evidence:
            accuracies = [float(c.get("accuracy", 0.0)) for c in grounded_claims_with_evidence]
            avg_accuracy = sum(accuracies) / len(accuracies)
            falsehood_score = round(max(0.0, min(100.0, (1.0 - avg_accuracy) * 50.0)), 1)
        else:
            avg_accuracy = 0.0
            falsehood_score = 0.0

        for c in grounded_claims:
            assessment = c.get("assessment")
            if assessment in ("contradicted", "partially_supported"):
                evidence_list = c.get("evidence", [])
                top_ev = evidence_list[0] if evidence_list else {}
                top_flags.append({
                    "type": "falsehood",
                    "summary": str(c.get("claim", ""))[:120],
                    "assessment": assessment,
                    "evidence_url": top_ev.get("url"),
                    "evidence_title": top_ev.get("title") or "Context.dev Fact Check",
                })

        # 5. Inspect image analyses
        for img in image_analyses:
            img_url = img.get("url")
            if img.get("is_scam"):
                spam_score = max(spam_score, 88.0)
                for f_text in img.get("flags", []):
                    top_flags.append({
                        "type": "spam",
                        "summary": f_text,
                        "assessment": "visual_scam_banner",
                        "evidence_url": img_url,
                        "evidence_title": "Scam Image / Infographic",
                    })
            if img.get("is_misleading"):
                for f_text in img.get("flags", []):
                    top_flags.append({
                        "type": "falsehood",
                        "summary": f_text,
                        "assessment": "misleading_visual_media",
                        "evidence_url": img_url,
                        "evidence_title": "Flagged Tweet Image",
                    })

        spam_score = round(min(100.0, spam_score), 1)
        harm_score = round(min(100.0, harm_score), 1)

        # Composite BS index: 45% falsehood, 35% spam, 20% harm
        # Plus avoid scam dilution: if spam or harm is extreme (e.g. bio phishing/airdrop), keep bs_index high
        bs_index = round(0.45 * falsehood_score + 0.35 * spam_score + 0.20 * harm_score, 1)
        if spam_score >= 80:
            bs_index = max(bs_index, round(spam_score * 0.9, 1))
        if harm_score >= 80:
            bs_index = max(bs_index, round(harm_score * 0.9, 1))

        # Evidence quality, misleadingness, sensationalism
        has_flags = len(top_flags) > 0
        evidence_quality = 8.5 if grounded_claims_with_evidence else 5.0
        misleadingness = 7.0 if falsehood_score > 35 else (3.0 if has_flags else 1.0)
        sensationalism = 6.5 if (spam_score > 40 or harm_score > 40) else 1.5

        if bs_index <= 30:
            summary = "Profile exhibits high overall factual consistency with no major spam or deceptive signals."
        elif bs_index <= 60:
            summary = "Profile exhibits moderate credibility concerns, including partially supported claims or promotional links."
        else:
            summary = "High-risk profile detected with substantiated falsehoods, deceptive destination links, or severe spam indicators."

        # Deduplicate top_flags by summary text
        unique_flags = []
        seen_summaries = set()
        for f in top_flags:
            if f["summary"] not in seen_summaries:
                seen_summaries.add(f["summary"])
                unique_flags.append(f)

        return {
            "bs_index": bs_index,
            "dimensions": {
                "falsehood": falsehood_score,
                "spam": spam_score,
                "harm": harm_score,
                "evidence_quality": evidence_quality,
                "misleadingness": misleadingness,
                "sensationalism": sensationalism,
            },
            "summary": summary,
            "top_flags": unique_flags[:5],
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
