"""
AI-powered social post analysis using OpenAI gpt-4o-mini.
Sends all new Instagram posts as a single batch call per pipeline run.
Produces a Ukrainian narrative daily briefing for Telegram.
"""
import json
from datetime import datetime
from typing import Optional

from src.config import OPENAI_API_KEY, setup_logging

logger = setup_logging("analysis.social_analyzer")

_MODEL = "gpt-4o-mini"
_MAX_CAPTION_LEN = 300


def _slim(posts: list[dict]) -> list[dict]:
    return [
        {
            "post_url": p.get("post_url", ""),
            "competitor": p.get("competitor", ""),
            "is_own": p.get("is_own", False),
            "caption": (p.get("caption") or "")[:_MAX_CAPTION_LEN],
            "likes": p.get("likes", 0),
            "comments": p.get("comments", 0),
            "keywords_matched": p.get("keywords_matched", []),
        }
        for p in posts
    ]


_BATCH_SYSTEM = """\
You are a retail market intelligence analyst monitoring the Romanian discount retail market.
Aurora Multimarket is a Ukrainian variety discount retailer actively expanding in Romania.
Its direct competitors tracked here: Pepco, Penny, Profi, KiK, TEDi, Action, MrDIY.
Return ONLY valid JSON — no markdown fences, no explanation text outside the JSON object.
"""

_BATCH_USER = """\
Analyze the following {n} Instagram posts from Aurora Multimarket and its Romanian competitors \
scraped in the last 24 hours.

Posts:
{posts_json}

Return a JSON object with this exact structure:
{{
  "posts": [
    {{
      "post_url": "<url from input>",
      "is_relevant": true,
      "relevance_score": 85,
      "aurora_relevance_reason": "<why this matters for Aurora's Romania expansion strategy, or empty string>"
    }}
  ],
  "patterns": ["<cross-competitor pattern if 2+ brands do the same thing simultaneously>"],
  "top_signals": ["<signal 1>", "<signal 2>", "<signal 3>"],
  "competitor_activity": {{"<brand>": "<one-sentence activity summary>"}},
  "aurora_recommendations": "<1-2 concrete sentences for Aurora's expansion team>",
  "daily_narrative": "<Ukrainian narrative text — see rules below>"
}}

Relevance scoring:
- 80-100: New store opening, grand opening announcement, coming soon
- 60-79: Major promo, new product category, delivery or app launch
- 40-59: Pricing strategy, significant campaign with strategic implications
- 1-39: Minor promo, standard seasonal content with weak signal
- 0: Generic holiday post, influencer lifestyle, fully unrelated content

Set is_relevant=true for scores >= 40.
Include ALL posts in "posts" array (relevant and not).
Leave aurora_relevance_reason empty for irrelevant posts.

Rules for daily_narrative:
- MINIMUM 150 words, target 200–280 words. If your draft is under 150 words, expand it — \
add engagement data (likes/comments), explain the strategic implication for Aurora, \
or note what is absent. Do not stop at one sentence per competitor.
- Write in Ukrainian, analytical prose. No bullet points or numbered lists.
- Structure: (1) most active competitors with specific detail, (2) patterns or trends across \
multiple brands, (3) one concrete recommendation for Aurora.
- For every specific claim about a competitor action (e.g. "Pepco відкрила новий магазин", \
"Penny просувала акцію") — immediately follow it with the post URL in parentheses. \
Example: "Pepco анонсувала відкриття нового магазину в Аргеș \
(https://www.instagram.com/p/ABC123/). Penny активно просувала продукти до сезону гриля \
(https://www.instagram.com/p/DEF456/)."
- General trend observations that summarise multiple posts do NOT need individual URLs, \
but must name which competitors are involved.
- Include engagement context where notable (e.g. "пост зібрав 216 лайків").
- Write like a consultant, not a bot. Avoid filler phrases.
- Do NOT start with a timestamp, date, or any header line such as "[DD.MM.YYYY HH:MM] NAME:" — \
begin directly with the analysis text.
- If all posts are generic noise with zero strategic value, set daily_narrative to empty string.
"""


def analyze_social_batch(posts: list[dict]) -> dict:
    """
    Analyze a batch of social posts with OpenAI gpt-4o-mini.
    Returns structured analysis dict including daily_narrative for Telegram.
    Returns {} gracefully if API key missing or call fails.
    """
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
        logger.info(f"OpenAI batch analysis: {len(posts)} posts → gpt-4o-mini")
        response = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _BATCH_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=2500,
        )
        raw = response.choices[0].message.content
        analysis = json.loads(raw)

        relevant_count = sum(1 for p in analysis.get("posts", []) if p.get("is_relevant"))
        narrative_words = len((analysis.get("daily_narrative") or "").split())
        logger.info(
            f"Batch analysis complete: {relevant_count}/{len(posts)} relevant, "
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
