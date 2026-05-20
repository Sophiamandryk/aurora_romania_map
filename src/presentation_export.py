"""
Daily .pptx presentation generator.
Reads today's brief + Instagram digest from DB, structures them into slides
via GPT-4o-mini, generates a .pptx via pptxgenjs (Node.js), and notifies Telegram.
"""
import json
import re
import subprocess
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from src.config import OPENAI_API_KEY, DB_PATH, REPORTS_DIR, BASE_DIR, setup_logging

logger = setup_logging("reports.presentation")

# ── Color scheme ──────────────────────────────────────────────────────────────
_NAVY     = "1E2761"
_WHITE    = "FFFFFF"
_CORAL    = "F96167"
_GRAY     = "B0B8D4"
_DARK_NAV = "141C4E"


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_today_data(today_str: str) -> dict:
    """Pull today's intelligence from the database."""
    import sqlite3
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        _ACTIVE = ("status='active' AND "
                   "snapshot_date=(SELECT MAX(snapshot_date) FROM stores)")

        # Network stats
        row = conn.execute(
            f"SELECT COUNT(*) as n, COUNT(DISTINCT city) as c FROM stores WHERE {_ACTIVE}"
        ).fetchone()
        stores = row["n"] if row else 0
        cities = row["c"] if row else 0

        by_region = {}
        for r in conn.execute(
            f"SELECT region, COUNT(*) as n FROM stores WHERE {_ACTIVE} "
            "GROUP BY region ORDER BY n DESC"
        ):
            by_region[r["region"] or "Unknown"] = r["n"]

        pepco_cities = (conn.execute(
            "SELECT COUNT(DISTINCT city) as n FROM competitor_stores WHERE brand='Pepco'"
        ).fetchone() or {})
        pepco_n = pepco_cities["n"] if pepco_cities else 0

        gap_cities = [r["city"] for r in conn.execute("""
            SELECT cs.city FROM competitor_stores cs
            WHERE cs.city NOT IN (
                SELECT DISTINCT city FROM stores
                WHERE status='active'
                AND snapshot_date=(SELECT MAX(snapshot_date) FROM stores)
            )
            GROUP BY cs.city HAVING COUNT(DISTINCT cs.brand) >= 2
            ORDER BY COUNT(DISTINCT cs.brand) DESC LIMIT 6
        """)]

        # Today's web search results (Tavily brief)
        cutoff = (datetime.now() - timedelta(hours=25)).isoformat()
        web_results = [dict(r) for r in conn.execute(
            "SELECT title, url, snippet, query_topic FROM web_search_results "
            "WHERE searched_at >= ? ORDER BY searched_at DESC LIMIT 30",
            (cutoff,),
        )]

        # Today's Instagram batch analysis
        batch = conn.execute(
            "SELECT * FROM batch_analyses WHERE run_date = ? ORDER BY id DESC LIMIT 1",
            (today_str,),
        ).fetchone()
        batch_data = dict(batch) if batch else {}
        if batch_data.get("competitor_activity_json"):
            batch_data["competitor_activity"] = json.loads(
                batch_data["competitor_activity_json"]
            )

        # Top relevant social posts today
        ig_cutoff = (datetime.now() - timedelta(hours=25)).isoformat()
        ig_posts = [dict(r) for r in conn.execute(
            "SELECT competitor, caption, post_url, relevance_score, "
            "aurora_relevance_reason FROM social_posts "
            "WHERE is_relevant=1 AND scraped_at >= ? "
            "ORDER BY relevance_score DESC LIMIT 6",
            (ig_cutoff,),
        )]

        conn.close()
        return {
            "stores": stores,
            "cities": cities,
            "pepco_cities": pepco_n,
            "pepco_gap": pepco_n - cities,
            "by_region": by_region,
            "gap_cities": gap_cities,
            "web_results": web_results,
            "batch": batch_data,
            "ig_posts": ig_posts,
        }
    except Exception as e:
        logger.warning(f"Data load failed: {e}")
        return {
            "stores": 0, "cities": 0, "pepco_cities": 0, "pepco_gap": 0,
            "by_region": {}, "gap_cities": [], "web_results": [],
            "batch": {}, "ig_posts": [],
        }


def _read_report_file(today_str: str) -> str:
    """Read today's daily report markdown if it exists."""
    path = REPORTS_DIR / f"daily_report_{today_str}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")[:4000]
    return ""


# ── AI slide structuring ──────────────────────────────────────────────────────

_SYSTEM = """\
Ти готуєш щоденну презентацію для команди Aurora Multimarket Romania.
На основі наданих звітів структуруй контент у слайди.
Відповідай лише валідним JSON масивом слайдів.
Кожен слайд: {"title": "...", "content": "...", "type": "...", \
"metrics": [...], "items": [...]}
Типи: title | metric | insight | competitor | action
Максимум 8 слайдів. Лише найважливіше. Мова: українська."""

_USER_TPL = """\
Дата: {date}

=== МЕРЕЖА AURORA ===
Магазинів: {stores} | Міст: {cities} | Розрив vs Pepco: +{gap} міст
Регіони: {regions}
Міста-прогалини (є конкуренти, немає Aurora): {gap_cities}

=== КОНКУРЕНТНА АКТИВНІСТЬ (веб-пошук сьогодні) ===
{competitor_results}

=== РИНКОВІ ТРЕНДИ ===
{market_results}

=== INSTAGRAM — РЕЛЕВАНТНІ ПОСТИ СЬОГОДНІ ===
{ig_block}

=== AI-РЕКОМЕНДАЦІЯ ДЛЯ AURORA ===
{recommendations}

Побудуй до 8 слайдів. Для type=metric заповни поле metrics списком \
{{"label":"...","value":"..."}}. Для type=action заповни items списком рядків.
Для інших типів заповни content (текст абзацами). Пропускай слайд якщо немає даних.
"""


def _strip_md(text: str) -> str:
    """Remove basic markdown formatting so it renders cleanly in pptx."""
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"_(.*?)_", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text.strip()


def _build_slides_with_ai(data: dict, today_str: str) -> list[dict]:
    try:
        from openai import OpenAI
    except ImportError:
        logger.error("openai not installed — using fallback slides")
        return _build_slides_fallback(data, today_str)

    comp = [r for r in data["web_results"] if r["query_topic"] == "competitor"]
    mkt  = [r for r in data["web_results"] if r["query_topic"] in
            ("retail_trends", "consumer", "products")]

    comp_block = "\n".join(
        f"- {r['title']} ({r['url']})\n  {r['snippet'][:200]}" for r in comp[:6]
    ) or "Даних не знайдено"
    mkt_block = "\n".join(
        f"- {r['title']}\n  {r['snippet'][:200]}" for r in mkt[:5]
    ) or "Даних не знайдено"

    ig_block = "\n".join(
        f"- {p['competitor']}: {(p['caption'] or '')[:120]} → {p['post_url']}"
        for p in data["ig_posts"][:4]
    ) or "Постів не знайдено"

    regions_str = ", ".join(f"{k}: {v}" for k, v in data["by_region"].items())
    recs = (data["batch"].get("aurora_recommendations") or "")[:400]

    user_msg = _USER_TPL.format(
        date=today_str,
        stores=data["stores"],
        cities=data["cities"],
        gap=max(data["pepco_gap"], 0),
        regions=regions_str or "немає даних",
        gap_cities=", ".join(data["gap_cities"][:5]) or "немає даних",
        competitor_results=comp_block,
        market_results=mkt_block,
        ig_block=ig_block,
        recommendations=recs or "немає даних",
    )

    client = OpenAI(api_key=OPENAI_API_KEY)
    try:
        logger.info("Presentation: calling GPT-4o-mini for slide structure")
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=2000,
        )
        raw = json.loads(resp.choices[0].message.content)
        # AI may return {"slides": [...]} or just [...]
        slides = raw if isinstance(raw, list) else raw.get("slides", [])
        logger.info(f"Presentation: AI returned {len(slides)} slides")
        return slides
    except Exception as e:
        logger.error(f"Presentation AI call failed: {e}")
        return _build_slides_fallback(data, today_str)


def _build_slides_fallback(data: dict, today_str: str) -> list[dict]:
    """Rule-based slides when OPENAI_API_KEY is missing or AI call fails."""
    slides = [
        {
            "type": "title",
            "title": f"Aurora Romania — Щоденний бриф {today_str}",
            "content": "Щоденний аналіз конкурентної розвідки",
            "metrics": None, "items": None,
        },
        {
            "type": "metric",
            "title": "Мережа Aurora",
            "content": "",
            "metrics": [
                {"label": "Магазинів",    "value": str(data["stores"])},
                {"label": "Міст",         "value": str(data["cities"])},
                {"label": "Розрив vs Pepco", "value": f"+{max(data['pepco_gap'],0)}"},
            ],
            "items": None,
        },
    ]

    comp = [r for r in data["web_results"] if r["query_topic"] == "competitor"]
    if comp:
        content = "\n".join(f"• {r['title']}" for r in comp[:4])
        slides.append({
            "type": "competitor",
            "title": "Конкурентна активність",
            "content": _strip_md(content),
            "metrics": None, "items": None,
        })

    if data["ig_posts"]:
        lines = [f"• {p['competitor']}: {(p['caption'] or '')[:100]}"
                 for p in data["ig_posts"][:3]]
        slides.append({
            "type": "insight",
            "title": "Instagram сигнали",
            "content": _strip_md("\n".join(lines)),
            "metrics": None, "items": None,
        })

    slides.append({
        "type": "action",
        "title": "Дії на завтра",
        "content": "",
        "metrics": None,
        "items": [
            "Перевірити офіційну карту Aurora",
            "Моніторити відкриття конкурентів",
            "Проаналізувати міста-прогалини",
        ],
    })
    return slides


# ── pptxgenjs script generation ───────────────────────────────────────────────

_JS_TEMPLATE = r"""
'use strict';
const path = require('path');
const PptxGenJS = require(path.join(__dirname, 'node_modules', 'pptxgenjs'));

const SLIDES = {slides_json};
const OUTPUT  = {output_json};

const C = {{
  navy:  '{navy}',
  white: '{white}',
  coral: '{coral}',
  gray:  '{gray}',
  dark:  '{dark}',
}};

async function main() {{
  const pres = new PptxGenJS();
  pres.layout = 'LAYOUT_WIDE';

  for (const slide of SLIDES) {{
    const s = pres.addSlide();
    navyBg(s, pres);
    switch ((slide.type || 'insight').toLowerCase()) {{
      case 'title':      buildTitle(s, slide, pres);      break;
      case 'metric':     buildMetric(s, slide, pres);     break;
      case 'action':     buildAction(s, slide, pres);     break;
      case 'competitor': buildInsight(s, slide, pres, C.coral); break;
      default:           buildInsight(s, slide, pres, C.gray);  break;
    }}
  }}

  await pres.writeFile({{ fileName: OUTPUT }});
  console.log('OK:' + OUTPUT);
}}

// ── backgrounds & helpers ─────────────────────────────────────────────────────

function navyBg(s, pres) {{
  s.addShape(pres.ShapeType.rect, {{
    x: 0, y: 0, w: '100%', h: '100%',
    fill: {{ color: C.navy }}, line: {{ color: C.navy }},
  }});
  // coral top bar
  s.addShape(pres.ShapeType.rect, {{
    x: 0, y: 0, w: '100%', h: 0.09,
    fill: {{ color: C.coral }}, line: {{ color: C.coral }},
  }});
}}

function safeText(v) {{ return String(v || '').replace(/[\t\r\n]+/g, ' ').trim(); }}

function addTitle(s, text, opts) {{
  s.addText(safeText(text), Object.assign({{
    fontFace: 'Arial Black', bold: true, color: C.white,
  }}, opts));
}}

function addBody(s, text, opts) {{
  s.addText(safeText(text), Object.assign({{
    fontFace: 'Calibri', color: C.white,
  }}, opts));
}}

// ── slide builders ────────────────────────────────────────────────────────────

function buildTitle(s, slide, pres) {{
  // Centered large title
  addTitle(s, slide.title, {{
    x: 0.5, y: 1.7, w: '85%', h: 1.6,
    fontSize: 36, align: 'center', valign: 'middle',
  }});
  if (slide.content) {{
    addBody(s, slide.content, {{
      x: 0.5, y: 3.5, w: '85%', h: 1.2,
      fontSize: 18, color: C.coral, align: 'center',
    }});
  }}
  // Bottom accent line
  s.addShape(pres.ShapeType.rect, {{
    x: 3.2, y: 6.4, w: 3.6, h: 0.06,
    fill: {{ color: C.coral }}, line: {{ color: C.coral }},
  }});
}}

function buildMetric(s, slide, pres) {{
  addTitle(s, slide.title, {{
    x: 0.4, y: 0.18, w: '92%', h: 0.75, fontSize: 24,
  }});

  const metrics = (slide.metrics || []).slice(0, 4);
  const n = Math.max(metrics.length, 1);
  const boxW = 9.5 / n;

  metrics.forEach((m, i) => {{
    const x = 0.25 + i * boxW;
    // callout box
    s.addShape(pres.ShapeType.rect, {{
      x, y: 1.1, w: boxW - 0.2, h: 3.8,
      fill: {{ color: C.dark }},
      line: {{ color: C.coral, size: 1.5 }},
    }});
    // big value
    addTitle(s, m.value || '', {{
      x: x + 0.05, y: 1.4, w: boxW - 0.3, h: 2.2,
      fontSize: 52, color: C.coral, align: 'center', valign: 'middle',
    }});
    // label
    addBody(s, m.label || '', {{
      x: x + 0.05, y: 3.65, w: boxW - 0.3, h: 0.9,
      fontSize: 13, color: C.gray, align: 'center',
    }});
  }});

  if (slide.content) {{
    addBody(s, slide.content, {{
      x: 0.4, y: 5.1, w: '92%', h: 0.7,
      fontSize: 12, color: C.gray, italic: true,
    }});
  }}
}}

function buildInsight(s, slide, pres, accentColor) {{
  // Left accent bar
  s.addShape(pres.ShapeType.rect, {{
    x: 0.25, y: 0.85, w: 0.07, h: 5.3,
    fill: {{ color: accentColor }}, line: {{ color: accentColor }},
  }});
  addTitle(s, slide.title, {{
    x: 0.55, y: 0.18, w: '92%', h: 0.75, fontSize: 26,
  }});
  const content = safeText(slide.content || '');
  addBody(s, content, {{
    x: 0.55, y: 1.05, w: '89%', h: 5.0,
    fontSize: 15, valign: 'top', paraSpaceAfter: 6,
  }});
}}

function buildAction(s, slide, pres) {{
  addTitle(s, slide.title, {{
    x: 0.4, y: 0.18, w: '92%', h: 0.75, fontSize: 26,
  }});

  let items = slide.items || [];
  if (!items.length && slide.content) {{
    items = slide.content.split('\n').filter(l => l.trim());
  }}
  items.slice(0, 5).forEach((item, i) => {{
    const y = 1.25 + i * 0.98;
    // coral circle
    s.addShape(pres.ShapeType.ellipse, {{
      x: 0.35, y: y, w: 0.55, h: 0.55,
      fill: {{ color: C.coral }}, line: {{ color: C.coral }},
    }});
    addTitle(s, String(i + 1), {{
      x: 0.35, y: y, w: 0.55, h: 0.55,
      fontSize: 18, align: 'center', valign: 'middle',
    }});
    addBody(s, safeText(item).replace(/^[\d\.\-\•\*]+\s*/, ''), {{
      x: 1.1, y: y + 0.02, w: '82%', h: 0.65,
      fontSize: 15, valign: 'middle',
    }});
  }});
}}

main().catch(e => {{ console.error('ERR:' + e.message); process.exit(1); }});
"""


def _generate_js_script(slides: list[dict], output_path: str) -> str:
    """Return a self-contained Node.js script that produces the .pptx."""
    # Sanitize slide content before embedding
    clean_slides = []
    for s in slides:
        cs = dict(s)
        if cs.get("content"):
            cs["content"] = _strip_md(cs["content"])
        if cs.get("items"):
            cs["items"] = [_strip_md(str(x)) for x in cs["items"]]
        clean_slides.append(cs)

    return _JS_TEMPLATE.format(
        slides_json=json.dumps(clean_slides, ensure_ascii=False),
        output_json=json.dumps(str(output_path)),
        navy=_NAVY, white=_WHITE, coral=_CORAL, gray=_GRAY, dark=_DARK_NAV,
    )


# ── Runner ────────────────────────────────────────────────────────────────────

def _run_node(script: str) -> bool:
    """Write JS to a temp file in BASE_DIR, run it, clean up. Returns success."""
    tmp = BASE_DIR / "_tmp_pptx_gen.js"
    try:
        tmp.write_text(script, encoding="utf-8")
        result = subprocess.run(
            ["node", str(tmp)],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=60,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode != 0:
            logger.error(f"pptxgenjs failed: {stderr or stdout}")
            return False
        if stdout.startswith("ERR:"):
            logger.error(f"pptxgenjs error: {stdout}")
            return False
        logger.info(f"pptxgenjs: {stdout}")
        return True
    except subprocess.TimeoutExpired:
        logger.error("pptxgenjs timed out after 60s")
        return False
    except Exception as e:
        logger.error(f"Node.js execution failed: {e}")
        return False
    finally:
        if tmp.exists():
            tmp.unlink()


# ── Telegram notification ─────────────────────────────────────────────────────

def _send_telegram(pptx_path: Path, today_str: str, data: dict) -> None:
    from src.alerts.telegram_alerts import TelegramBot

    recs = (data["batch"].get("aurora_recommendations") or "").strip()
    summary = recs[:180] if recs else f"{data['stores']} магазинів | {data['cities']} міст"

    msg = (
        f"📊 *Презентація за {today_str} готова*\n\n"
        f"{summary}\n\n"
        f"📁 `{pptx_path.name}` збережено локально"
    )
    try:
        TelegramBot()._send(msg, disable_preview=True)
        logger.info("Presentation: Telegram notification sent")
    except Exception as e:
        logger.warning(f"Presentation: Telegram notification failed: {e}")


# ── Public entry point ────────────────────────────────────────────────────────

def generate_presentation(
    today_str: str = None,
    dry_run: bool = False,
) -> Optional[Path]:
    """
    Generate a daily .pptx presentation from today's intelligence data.
    Returns the path to the saved file, or None on failure.
    dry_run=True: builds the file but does not send a Telegram notification.
    """
    today_str = today_str or date.today().isoformat()
    output_path = REPORTS_DIR / f"presentation_{today_str}.pptx"

    logger.info(f"Presentation: loading today's data ({today_str})")
    data = _load_today_data(today_str)

    # Build slides
    if OPENAI_API_KEY:
        slides = _build_slides_with_ai(data, today_str)
    else:
        logger.info("Presentation: OPENAI_API_KEY not set — using rule-based slides")
        slides = _build_slides_fallback(data, today_str)

    if not slides:
        logger.warning("Presentation: no slides generated — skipping")
        return None

    # Ensure title slide is first
    if slides[0].get("type") != "title":
        slides.insert(0, {
            "type": "title",
            "title": f"Aurora Romania — Щоденний бриф {today_str}",
            "content": "Щоденний аналіз конкурентної розвідки",
            "metrics": None, "items": None,
        })

    logger.info(f"Presentation: generating {len(slides)} slides → {output_path.name}")
    script = _generate_js_script(slides, str(output_path))

    if not _run_node(script):
        return None

    if not output_path.exists():
        logger.error(f"Presentation: pptx file not created at {output_path}")
        return None

    size_kb = output_path.stat().st_size // 1024
    logger.info(f"Presentation saved: {output_path} ({size_kb} KB)")

    if not dry_run:
        _send_telegram(output_path, today_str, data)

    return output_path
