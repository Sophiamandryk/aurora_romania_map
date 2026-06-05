"""
Output validation layer — second AI pass that fact-checks generated summaries
against the source snippets before they reach the user.

Problem it solves: GPT-4o-mini sometimes attributes facts from one country to another,
or cites numbers from a source in a wrong context (e.g., UK real-estate stats → Ukraine).

How it works:
  1. The summary + original source snippets are sent to a "fact-checker" GPT call.
  2. The checker verifies every factual claim (numbers, %, company names, geographies).
  3. If all claims are supported → returns original summary unchanged.
  4. If a claim is wrong → rewrites using only facts present in the sources.
  5. If nothing is supportable → returns a "not enough data" message.
"""
from src.config import OPENAI_API_KEY, setup_logging

logger = setup_logging("modules.validator")

_CHECKER_SYSTEM = """\
You are a strict fact-checker for a retail/economic intelligence report.

You receive:
  - TOPIC: the subject of the summary
  - SUMMARY: an AI-generated paragraph to verify
  - SOURCES: the actual article snippets the summary was based on

GROUNDING RULE: A fact is valid ONLY if it appears word-for-word or in clear paraphrase in one of
the provided source snippets. Training knowledge, logical inference, and "common sense" completions
are NOT acceptable evidence. If you cannot point to a specific source snippet that contains a claim,
that claim must be removed.

Your job — verify EVERY factual claim in the summary:

1. GEOGRAPHY — Is each country/region correctly attributed?
   Example of ERROR: "investments in Ukraine may grow 15% to £48bn" when the source says this about the UK.
   Example of ERROR: "Romania's retail grew 11%" when source says this about Ukraine.

2. NUMBERS — Does each figure (%, €, £, amount) actually appear verbatim in the sources?

3. COMPANIES — Are named companies actually mentioned in the provided source snippets?

4. TEMPORAL — Are date references (2026, Q1, last week) consistent with the source dates?

5. INVENTED CONTENT — Remove any sentence that cannot be traced to a specific source snippet,
   even if it sounds plausible. When in doubt, leave it out.

Decision:
- If ALL claims are fully supported by the sources → return the original summary UNCHANGED.
- If ANY claim is wrong/misattributed/uninventable → rewrite the summary in Ukrainian using ONLY
  facts that are clearly present in the source snippets. Keep the same language and style.
- If the sources don't support any meaningful summary on this topic →
  return exactly: "Достатньо підтверджених даних із джерел не знайдено."

Return ONLY the final summary text. No preamble, no explanation, no metadata."""


def validate_summary(
    summary: str,
    results: list[dict],
    topic: str = "",
) -> str:
    """
    Fact-check `summary` against `results` (list of {title, url, snippet}).
    Returns the validated (possibly rewritten) summary.
    Falls back to the original summary if OpenAI is unavailable.
    """
    if not OPENAI_API_KEY or not summary or not results:
        return summary

    sources_block = "\n\n".join(
        f"[Source {i+1}]\nTitle: {r.get('title','')}\nURL: {r.get('url','')}\n"
        f"Snippet: {(r.get('snippet') or '')[:400]}"
        for i, r in enumerate(results[:8])
    )

    user_msg = (
        f"TOPIC: {topic}\n\n"
        f"SUMMARY TO VERIFY:\n{summary}\n\n"
        f"SOURCES:\n{sources_block}"
    )

    try:
        from openai import OpenAI
        resp = OpenAI(api_key=OPENAI_API_KEY).chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _CHECKER_SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=500,
        )
        validated = resp.choices[0].message.content.strip()
        if validated != summary:
            logger.info(f"Validator rewrote summary for '{topic[:60]}'")
        return validated
    except Exception as e:
        logger.warning(f"Validator skipped ('{topic[:40]}'): {e}")
        return summary
