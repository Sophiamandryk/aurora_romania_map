"""
3.2 Romania Network Expansion — daily diff of Aurora + competitor store locations.
No AI. Numbers only: opened, closed, relocated, rebranded per brand.

Aurora: diffs SQLite store snapshots (load_previous_snapshot vs load_snapshot).
Competitors: saves own JSON snapshots under data/snapshots/network_ro_comps_YYYY-MM-DD.json
             since the competitor_stores table overwrites history on each scrape.
"""
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from src.config import DATA_DIR, setup_logging

logger = setup_logging("modules.network_expansion_ro")

_SNAPSHOT_DIR = DATA_DIR / "snapshots"
_COMPS_PREFIX = "network_ro_comps"

# Distance thresholds (metres)
_SAME_M  = 300    # within 300m → same location
_RELOC_M = 2500   # search radius for relocation candidates


# ── Geometry helpers ──────────────────────────────────────────────────────────

def _haversine(a: dict, b: dict) -> Optional[float]:
    la, lo = a.get("latitude"), a.get("longitude")
    lb, lob = b.get("latitude"), b.get("longitude")
    if not all([la, lo, lb, lob]):
        return None
    R = 6371000
    phi1, phi2 = math.radians(la), math.radians(lb)
    dphi = math.radians(lb - la)
    dl   = math.radians(lob - lo)
    x = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(x), math.sqrt(1 - x))


def _loc_key(store: dict) -> str:
    lat, lon = store.get("latitude"), store.get("longitude")
    if lat and lon and lat != 0 and lon != 0:
        return f"{float(lat):.4f},{float(lon):.4f}"
    city = (store.get("city") or "").lower().strip()
    addr = (store.get("address") or "").lower().strip()[:40]
    return f"{city}::{addr}"


def _fmt_loc(store: dict) -> str:
    city = (store.get("city") or "").strip()
    addr = (store.get("address") or "").strip()
    if city and addr:
        return f"{city}, {addr}"
    return city or addr or "невідомо"


# ── Core diff logic ───────────────────────────────────────────────────────────

def _diff_stores(
    prev: list[dict],
    curr: list[dict],
    id_field: Optional[str] = None,
) -> dict:
    """
    Diff two store lists.
    id_field: stable ID to include in detail records (e.g. 'store_id' for Aurora).
    Returns counts + detail records.
    """
    prev_by_key = {_loc_key(s): s for s in prev}
    curr_by_key = {_loc_key(s): s for s in curr}

    prev_keys = set(prev_by_key)
    curr_keys = set(curr_by_key)

    new_keys     = curr_keys - prev_keys
    gone_keys    = prev_keys - curr_keys
    common_keys  = prev_keys & curr_keys

    opened = closed = relocated = rebranded = 0
    details: list[dict] = []

    gone_stores   = [prev_by_key[k] for k in gone_keys]
    matched_gone: set[str] = set()

    # Match new stores against gone stores → relocations
    for key in list(new_keys):
        store = curr_by_key[key]
        best, best_dist = None, float("inf")
        for g in gone_stores:
            d = _haversine(store, g)
            if d is not None and d < best_dist:
                best_dist, best = d, g
        if best and best_dist <= _RELOC_M:
            gk = _loc_key(best)
            if gk not in matched_gone:
                matched_gone.add(gk)
                new_keys.discard(key)
                gone_keys.discard(gk)
                relocated += 1
                sid = store.get(id_field, key) if id_field else key
                details.append({
                    "type":     "relocated",
                    "store_id": sid,
                    "from":     _fmt_loc(best),
                    "to":       _fmt_loc(store),
                })

    for key in new_keys:
        store = curr_by_key[key]
        opened += 1
        sid = store.get(id_field, key) if id_field else key
        details.append({
            "type":     "opened",
            "store_id": sid,
            "location": _fmt_loc(store),
        })

    for key in gone_keys:
        store = prev_by_key[key]
        closed += 1
        sid = store.get(id_field, key) if id_field else key
        details.append({
            "type":     "closed",
            "store_id": sid,
            "location": _fmt_loc(store),
        })

    for key in common_keys:
        p, c = prev_by_key[key], curr_by_key[key]
        pn = (p.get("name") or "").lower().strip()
        cn = (c.get("name") or "").lower().strip()
        if pn and cn and pn != cn:
            rebranded += 1
            sid = c.get(id_field, key) if id_field else key
            details.append({
                "type":      "rebranded",
                "store_id":  sid,
                "location":  _fmt_loc(c),
                "from_name": p.get("name", ""),
                "to_name":   c.get("name", ""),
            })

    return {
        "opened":    opened,
        "closed":    closed,
        "relocated": relocated,
        "rebranded": rebranded,
        "announced": 0,
        "_details":  details,
        "_prev_n":   len(prev),
        "_curr_n":   len(curr),
    }


# ── Competitor snapshot helpers ───────────────────────────────────────────────

def _comps_snap_path(d: str) -> Path:
    return _SNAPSHOT_DIR / f"{_COMPS_PREFIX}_{d}.json"


def _load_previous_comps_snapshot(today: str) -> tuple[dict[str, list[dict]], Optional[str]]:
    """Return (brand→stores dict, date_str) for the most recent snapshot before today."""
    files = sorted(_SNAPSHOT_DIR.glob(f"{_COMPS_PREFIX}_*.json"), reverse=True)
    for f in files:
        d = f.stem[len(_COMPS_PREFIX) + 1:]
        if d < today:
            try:
                return json.loads(f.read_text(encoding="utf-8")), d
            except Exception:
                continue
    return {}, None


def _load_current_competitors() -> dict[str, list[dict]]:
    """Load each brand's stores at its own max scraped_date (avoids the global-MAX bug)."""
    from src.storage.sqlite_store import _connect
    with _connect() as conn:
        rows = conn.execute("""
            SELECT cs.*
            FROM competitor_stores cs
            INNER JOIN (
                SELECT brand, MAX(scraped_date) AS max_date
                FROM competitor_stores GROUP BY brand
            ) latest ON cs.brand = latest.brand AND cs.scraped_date = latest.max_date
            ORDER BY cs.brand, cs.city
        """).fetchall()
    result: dict[str, list[dict]] = {}
    for r in rows:
        d = dict(r)
        result.setdefault(d["brand"], []).append(d)
    return result


# ── Output helper ─────────────────────────────────────────────────────────────

def _save_output(today: str, data: dict) -> None:
    path = DATA_DIR / f"aurora_output_{today}.json"
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing.update(data)
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Main entry point ──────────────────────────────────────────────────────────

def run(today: str = None) -> dict:
    """
    Run section 3.2. Diffs Aurora + competitors, saves snapshots, writes aurora_output JSON.
    """
    today = today or date.today().isoformat()
    checked_at = datetime.now(timezone.utc).isoformat()

    # ── Step 1: previous state ────────────────────────────────────────────────
    from src.storage.sqlite_store import load_snapshot, load_previous_snapshot

    aurora_prev  = load_previous_snapshot()
    prev_comps, prev_comps_date = _load_previous_comps_snapshot(today)

    first_run_aurora = len(aurora_prev) == 0
    first_run_comps  = prev_comps_date is None

    if first_run_aurora:
        logger.info("3.2 Aurora: no previous SQLite snapshot — first run")
    else:
        logger.info(f"3.2 Aurora previous: {len(aurora_prev)} stores")

    if first_run_comps:
        logger.info("3.2 Competitors: no previous JSON snapshot — first run")
    else:
        logger.info(f"3.2 Competitors previous snapshot: {prev_comps_date}")

    # ── Step 2: current state ─────────────────────────────────────────────────
    aurora_curr = load_snapshot()
    logger.info(f"3.2 Aurora current: {len(aurora_curr)} stores")

    comp_curr = _load_current_competitors()
    for brand, stores in comp_curr.items():
        logger.info(f"3.2 {brand} current: {len(stores)} stores")

    # ── Step 3: diff ──────────────────────────────────────────────────────────
    by_brand: dict = {}
    all_details: list[dict] = []
    has_changes = False

    if not first_run_aurora:
        res = _diff_stores(aurora_prev, aurora_curr, id_field="store_id")
        by_brand["Aurora"] = {k: res[k] for k in ("opened", "closed", "relocated", "rebranded", "announced")}
        for det in res["_details"]:
            all_details.append({"brand": "Aurora", **det})
        if any(res[k] for k in ("opened", "closed", "relocated", "rebranded")):
            has_changes = True
        logger.info(
            f"3.2 Aurora diff: {res['_prev_n']} → {res['_curr_n']} stores | "
            f"opened={res['opened']} closed={res['closed']} "
            f"relocated={res['relocated']} rebranded={res['rebranded']}"
        )

    if not first_run_comps:
        for brand, curr_stores in comp_curr.items():
            prev_stores = prev_comps.get(brand, [])
            if not prev_stores:
                # Brand was added after the last snapshot — treat as baseline, skip diff
                logger.info(f"3.2 {brand}: not in previous snapshot ({prev_comps_date}) — baseline only")
                continue
            res = _diff_stores(prev_stores, curr_stores)
            by_brand[brand] = {k: res[k] for k in ("opened", "closed", "relocated", "rebranded", "announced")}
            for det in res["_details"]:
                all_details.append({"brand": brand, **det})
            if any(res[k] for k in ("opened", "closed", "relocated", "rebranded")):
                has_changes = True
            logger.info(
                f"3.2 {brand} diff: {res['_prev_n']} → {res['_curr_n']} stores | "
                f"opened={res['opened']} closed={res['closed']} "
                f"relocated={res['relocated']} rebranded={res['rebranded']}"
            )

    # ── Step 4: save competitor snapshot ──────────────────────────────────────
    _comps_snap_path(today).write_text(
        json.dumps(
            {brand: stores for brand, stores in comp_curr.items()},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    logger.info(f"3.2 saved competitor snapshot for {today}")

    # ── Build output section ──────────────────────────────────────────────────
    first_run = first_run_aurora and first_run_comps
    if first_run:
        section: dict = {
            "checked_at":      checked_at,
            "period_hours":    24,
            "changes_detected": False,
            "message": "Перший запуск — базовий знімок збережено. Diff буде доступний завтра.",
        }
    elif not has_changes:
        section = {
            "checked_at":      checked_at,
            "period_hours":    24,
            "changes_detected": False,
            "message": "Змін за останні 24 години не виявлено.",
        }
    else:
        section = {
            "checked_at":      checked_at,
            "period_hours":    24,
            "changes_detected": True,
            "by_brand":        by_brand,
            "details":         all_details,
        }

    _save_output(today, {"3.2_network_expansion_ro": section})
    logger.info(f"3.2 done: changes_detected={section['changes_detected']}")
    return section
