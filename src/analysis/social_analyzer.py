"""
AI-powered social post analysis — commercial intelligence layer.
Analyzes Instagram posts from Aurora and competitors.
Produces structured Promos / Products / Openings intelligence for report
section 1.3, plus a Ukrainian narrative for Telegram.
"""
import json
from datetime import datetime

from src.config import OPENAI_API_KEY, setup_logging

logger = setup_logging("analysis.social_analyzer")

_MODEL = "gpt-4o-mini"
_MAX_CAPTION_LEN = 300


def _slim(posts: list[dict]) -> list[dict]:
    return [
        {
            "post_url": p.get("post_url", ""),
            # scraper stores brand in "competitor"; "brand" is only set by AI result dicts
            "brand": p.get("competitor") or p.get("brand") or "Aurora",
            "caption": (p.get("caption") or "")[:_MAX_CAPTION_LEN],
            "likes":    p.get("likes", 0),
            "comments": p.get("comments", 0),
        }
        for p in posts
    ]


_BATCH_SYSTEM = """\
You are a retail commercial intelligence analyst monitoring the Romanian discount retail market.
Aurora Multimarket is a Ukrainian variety discount retailer expanding in Romania.
Tracked brands: Aurora, Pepco, Penny, Profi, KiK, TEDi, Action, MrDIY.

Your task: extract COMMERCIAL INTELLIGENCE from Instagram posts.
Commercial intelligence means: active promotions, product launches, confirmed store openings.

LANGUAGE RULE — CRITICAL:
ALL text you write in ANY field must be in Ukrainian.
Posts are in Romanian — translate all Romanian content to Ukrainian before writing any field value.
Brand names (Pepco, KiK, etc.) and URLs stay as-is. Everything else: Ukrainian.

STRICT rules:
- A store opening is ONLY "confirmed" if the post text explicitly announces it with a location.
- Do NOT speculate about future expansion from vague location mentions or hashtags.
- Focus on: pricing signals, category strategy, campaign intensity, seasonal focus.
- Catalogue/promotional activity is commercial intelligence — categorise it as such, not as expansion.

GROUNDING RULE: Only describe what is explicitly visible in the provided post captions and metadata.
Do not infer, extrapolate, or use training knowledge about these brands to fill gaps.
Every claim in daily_narrative must be traceable to a specific post in the input.
When in doubt, leave it out.

Return ONLY valid JSON — no markdown fences, no text outside the JSON object.
"""

_BATCH_USER = """\
Analyze {n} Instagram posts from Aurora Multimarket and its Romanian competitors scraped today.

Posts:
{posts_json}

Return a JSON object with this EXACT structure:
{{
  "posts": [
    {{
      "post_url": "<url from input>",
      "brand": "<brand name, e.g. Aurora, Pepco, KiK>",
      "category": "promo|product|opening|noise",
      "is_relevant": true,
      "relevance_score": 0,
      "promo_detail": "<in Ukrainian: discount %, campaign name, seasonal angle — or empty string>",
      "product_detail": "<in Ukrainian: product name, category, collaboration — or empty string>",
      "opening_detail": "<in Ukrainian: city or mall ONLY if post explicitly confirms opening — or empty string>"
    }}
  ],
  "commercial_digest": {{
    "promos":   ["<brand> — <specific detail> (<post_url>)"],
    "products": ["<brand> — <product/category detail> (<post_url>)"],
    "openings": ["<brand> — <city/mall> відкриття (<post_url>)"]
  }},
  "brand_summary": {{
    "<brand>": "<in Ukrainian: one sentence about commercial activity today, or empty if no relevant posts>"
  }},
  "patterns": ["<cross-brand pattern if 2+ brands do the same thing simultaneously>"],
  "daily_narrative": "<Ukrainian analytical text — see rules below>"
}}

Category rules:
- "promo":   discount, sale, seasonal campaign, end-of-season, loyalty promo, price drop
- "product": new product, featured category, collaboration, new collection launch
- "opening": post EXPLICITLY announces store opening with location — confirmed only, score >= 80
- "noise":   lifestyle, generic holiday greetings, unrelated, no commercial signal

Relevance scoring (0–100):
- 80–100: confirmed store opening with city/mall name in caption
- 60–79:  major seasonal campaign launch, new product category
- 40–59:  pricing signal, standard promotional campaign with category focus
- 1–39:   minor promo, standard seasonal post, weak signal
- 0:      noise, irrelevant

is_relevant = true for relevance_score >= 40. Include ALL posts in "posts" array.
Add to "openings" only if score >= 80 AND opening_detail is non-empty.
Add to "promos" only if score >= 40 AND promo_detail is non-empty.
Add to "products" only if score >= 40 AND product_detail is non-empty.

Rules for daily_narrative:
- WRITE ENTIRELY IN UKRAINIAN. This is mandatory. Do NOT write in Romanian or any other language.
- Translate all Romanian post captions to Ukrainian before including them in the narrative.
- Minimum 130 words, analytical prose. No bullet lists, no section headers.
- Cover: (1) what each active brand showed commercially today with specifics,
  (2) cross-brand category or pricing patterns, (3) one concrete insight for Aurora.
- Follow each specific brand claim with the post URL in parentheses.
- Focus ONLY on what is commercially visible today — no expansion speculation.
- Include engagement numbers (likes/comments) where notable.
- Write like a retail analyst, not a summariser.
- Do NOT begin with a date, timestamp, or header line — start directly with the analysis.
- If all posts are noise, set daily_narrative to empty string "".
"""


def analyze_social_batch(posts: list[dict]) -> dict:
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set — skipping AI social analysis")
        return {}
    if not posts:
        logger.info("No posts to analyze in batch")
        return {}
    try:
        from openai import OpenAI
    except ImportError:
        logger.error("openai package not installed — pip install openai")
        return {}

    client = OpenAI(api_key=OPENAI_API_KEY)
    slim_posts = _slim(posts)
    posts_json = json.dumps(slim_posts, ensure_ascii=False, indent=2)
    user_prompt = _BATCH_USER.format(n=len(slim_posts), posts_json=posts_json)

    try:
        logger.info(f"OpenAI batch analysis: {len(posts)} posts → {_MODEL}")
        response = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _BATCH_SYSTEM},
                {"role": "user",   "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=4096,
        )
        raw = response.choices[0].message.content
        analysis = json.loads(raw)

        relevant_count = sum(1 for p in analysis.get("posts", []) if p.get("is_relevant"))
        narrative_words = len((analysis.get("daily_narrative") or "").split())
        logger.info(
            f"Batch analysis: {relevant_count}/{len(posts)} relevant, "
            f"narrative {narrative_words} words"
        )
        analysis["analyzed_at"] = datetime.utcnow().isoformat()
        analysis["post_count"] = len(posts)
        return analysis
    except json.JSONDecodeError as e:
        logger.error(f"OpenAI returned invalid JSON: {e}")
        return {}
    except Exception as e:
        logger.error(f"OpenAI social batch analysis failed: {e}")
        return {}
