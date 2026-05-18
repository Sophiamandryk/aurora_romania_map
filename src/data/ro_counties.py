"""
Romania city → county → development region mapping.

Keys use the same normalization as competitor_analysis._normalize_city:
  lowercase → strip diacritics → hyphens to spaces → collapse whitespace → apply aliases.

Development regions (NUTS-2):
  Nord-Est       : Bacău BC, Botoșani BT, Iași IS, Neamț NT, Suceava SV, Vaslui VS
  Sud-Est        : Brăila BR, Buzău BZ, Constanța CT, Galați GL, Tulcea TL, Vrancea VN
  Sud-Muntenia   : Argeș AG, Călărași CL, Dâmbovița DB, Giurgiu GR, Ialomița IL,
                   Prahova PH, Teleorman TR
  Sud-Vest Oltenia: Dolj DJ, Gorj GJ, Mehedinți MH, Olt OT, Vâlcea VL
  Vest           : Arad AR, Caraș-Severin CS, Hunedoara HD, Timiș TM
  Nord-Vest      : Bihor BH, Bistrița-Năsăud BN, Cluj CJ, Maramureș MM,
                   Satu Mare SM, Sălaj SJ
  Centru         : Alba AB, Brașov BV, Covasna CV, Harghita HR, Mureș MS, Sibiu SB
  București-Ilfov: București B, Ilfov IF
"""
import unicodedata

# German/alternative spellings used by KiK API
_ALIASES: dict[str, str] = {
    "bukarest": "bucuresti",
    "klausenburg": "cluj napoca",
    "hermannstadt": "sibiu",
    "kronstadt": "brasov",
    "temeschwar": "timisoara",
    "grosswardein": "oradea",
    "neumarkt": "targu mures",
}

# (county_name, county_code, region)
_LOOKUP: dict[str, tuple[str, str, str]] = {
    # ── Alba ─────────────────────────────────────────────────────────────────
    "alba iulia":        ("Alba", "AB", "Centru"),
    "aiud":              ("Alba", "AB", "Centru"),
    "blaj":              ("Alba", "AB", "Centru"),
    "sebes":             ("Alba", "AB", "Centru"),
    "cugir":             ("Alba", "AB", "Centru"),
    "ocna mures":        ("Alba", "AB", "Centru"),
    # ── Arad ─────────────────────────────────────────────────────────────────
    "arad":              ("Arad", "AR", "Vest"),
    "lipova":            ("Arad", "AR", "Vest"),
    "ineu":              ("Arad", "AR", "Vest"),
    "vladimirescu":      ("Arad", "AR", "Vest"),
    # ── Argeș ────────────────────────────────────────────────────────────────
    "pitesti":           ("Argeș", "AG", "Sud-Muntenia"),
    "campulung muscel":  ("Argeș", "AG", "Sud-Muntenia"),
    "curtea de arges":   ("Argeș", "AG", "Sud-Muntenia"),
    "mioveni":           ("Argeș", "AG", "Sud-Muntenia"),
    "costesti":          ("Argeș", "AG", "Sud-Muntenia"),
    "geamana":           ("Argeș", "AG", "Sud-Muntenia"),
    # ── Bacău ─────────────────────────────────────────────────────────────────
    "bacau":             ("Bacău", "BC", "Nord-Est"),
    "onesti":            ("Bacău", "BC", "Nord-Est"),
    "moinesti":          ("Bacău", "BC", "Nord-Est"),
    "comanesti":         ("Bacău", "BC", "Nord-Est"),
    "buhusi":            ("Bacău", "BC", "Nord-Est"),
    # ── Bihor ─────────────────────────────────────────────────────────────────
    "oradea":            ("Bihor", "BH", "Nord-Vest"),
    "salonta":           ("Bihor", "BH", "Nord-Vest"),
    "beius":             ("Bihor", "BH", "Nord-Vest"),
    "alesd":             ("Bihor", "BH", "Nord-Vest"),
    "marghita":          ("Bihor", "BH", "Nord-Vest"),
    "stei":              ("Bihor", "BH", "Nord-Vest"),
    # ── Bistrița-Năsăud ───────────────────────────────────────────────────────
    "bistrita":          ("Bistrița-Năsăud", "BN", "Nord-Vest"),
    "nasaud":            ("Bistrița-Năsăud", "BN", "Nord-Vest"),
    "sangeorz bai":      ("Bistrița-Năsăud", "BN", "Nord-Vest"),
    # ── Botoșani ──────────────────────────────────────────────────────────────
    "botosani":          ("Botoșani", "BT", "Nord-Est"),
    "dorohoi":           ("Botoșani", "BT", "Nord-Est"),
    # ── Brașov ────────────────────────────────────────────────────────────────
    "brasov":            ("Brașov", "BV", "Centru"),
    "fagagas":           ("Brașov", "BV", "Centru"),
    "sacele":            ("Brașov", "BV", "Centru"),
    "codlea":            ("Brașov", "BV", "Centru"),
    "zarnesti":          ("Brașov", "BV", "Centru"),
    "rasnov":            ("Brașov", "BV", "Centru"),
    "rupea":             ("Brașov", "BV", "Centru"),
    # ── Brăila ────────────────────────────────────────────────────────────────
    "braila":            ("Brăila", "BR", "Sud-Est"),
    # ── București ─────────────────────────────────────────────────────────────
    "bucuresti":         ("București", "B", "București-Ilfov"),
    "bucharest":         ("București", "B", "București-Ilfov"),
    # ── Buzău ─────────────────────────────────────────────────────────────────
    "buzau":             ("Buzău", "BZ", "Sud-Est"),
    "ramnicu sarat":     ("Buzău", "BZ", "Sud-Est"),
    "nehoiu":            ("Buzău", "BZ", "Sud-Est"),
    # ── Călărași ──────────────────────────────────────────────────────────────
    "calarasi":          ("Călărași", "CL", "Sud-Muntenia"),
    "oltenita":          ("Călărași", "CL", "Sud-Muntenia"),
    "lehliu":            ("Călărași", "CL", "Sud-Muntenia"),
    # ── Caraș-Severin ─────────────────────────────────────────────────────────
    "resita":            ("Caraș-Severin", "CS", "Vest"),
    "caransebes":        ("Caraș-Severin", "CS", "Vest"),
    "otelu rosu":        ("Caraș-Severin", "CS", "Vest"),
    "moldova veche":     ("Caraș-Severin", "CS", "Vest"),
    "oravita":           ("Caraș-Severin", "CS", "Vest"),
    # ── Cluj ──────────────────────────────────────────────────────────────────
    "cluj napoca":       ("Cluj", "CJ", "Nord-Vest"),
    "turda":             ("Cluj", "CJ", "Nord-Vest"),
    "dej":               ("Cluj", "CJ", "Nord-Vest"),
    "gherla":            ("Cluj", "CJ", "Nord-Vest"),
    "floresti":          ("Cluj", "CJ", "Nord-Vest"),
    "campia turzii":     ("Cluj", "CJ", "Nord-Vest"),
    # ── Constanța ─────────────────────────────────────────────────────────────
    "constanta":         ("Constanța", "CT", "Sud-Est"),
    "mangalia":          ("Constanța", "CT", "Sud-Est"),
    "medgidia":          ("Constanța", "CT", "Sud-Est"),
    "navodari":          ("Constanța", "CT", "Sud-Est"),
    "cernavoda":         ("Constanța", "CT", "Sud-Est"),
    # ── Covasna ───────────────────────────────────────────────────────────────
    "sfantu gheorghe":   ("Covasna", "CV", "Centru"),
    "targu secuiesc":    ("Covasna", "CV", "Centru"),
    # ── Dâmbovița ─────────────────────────────────────────────────────────────
    "targoviste":        ("Dâmbovița", "DB", "Sud-Muntenia"),
    "moreni":            ("Dâmbovița", "DB", "Sud-Muntenia"),
    "gaesti":            ("Dâmbovița", "DB", "Sud-Muntenia"),
    "titu":              ("Dâmbovița", "DB", "Sud-Muntenia"),
    "crevedia":          ("Dâmbovița", "DB", "Sud-Muntenia"),
    # ── Dolj ──────────────────────────────────────────────────────────────────
    "craiova":           ("Dolj", "DJ", "Sud-Vest Oltenia"),
    "bailesti":          ("Dolj", "DJ", "Sud-Vest Oltenia"),
    "calafat":           ("Dolj", "DJ", "Sud-Vest Oltenia"),
    # ── Galați ────────────────────────────────────────────────────────────────
    "galati":            ("Galați", "GL", "Sud-Est"),
    "tecuci":            ("Galați", "GL", "Sud-Est"),
    # ── Giurgiu ───────────────────────────────────────────────────────────────
    "giurgiu":           ("Giurgiu", "GR", "Sud-Muntenia"),
    # ── Gorj ──────────────────────────────────────────────────────────────────
    "targu jiu":         ("Gorj", "GJ", "Sud-Vest Oltenia"),
    "motru":             ("Gorj", "GJ", "Sud-Vest Oltenia"),
    "rovinari":          ("Gorj", "GJ", "Sud-Vest Oltenia"),
    # ── Harghita ──────────────────────────────────────────────────────────────
    "miercurea ciuc":    ("Harghita", "HR", "Centru"),
    "odorheiu secuiesc": ("Harghita", "HR", "Centru"),
    "toplita":           ("Harghita", "HR", "Centru"),
    # ── Hunedoara ─────────────────────────────────────────────────────────────
    "deva":              ("Hunedoara", "HD", "Vest"),
    "hunedoara":         ("Hunedoara", "HD", "Vest"),
    "petrosani":         ("Hunedoara", "HD", "Vest"),
    "vulcan":            ("Hunedoara", "HD", "Vest"),
    "orastie":           ("Hunedoara", "HD", "Vest"),
    "brad":              ("Hunedoara", "HD", "Vest"),
    # ── Ialomița ──────────────────────────────────────────────────────────────
    "slobozia":          ("Ialomița", "IL", "Sud-Muntenia"),
    "fetesti":           ("Ialomița", "IL", "Sud-Muntenia"),
    "urziceni":          ("Ialomița", "IL", "Sud-Muntenia"),
    "tandarei":          ("Ialomița", "IL", "Sud-Muntenia"),
    # ── Iași ──────────────────────────────────────────────────────────────────
    "iasi":              ("Iași", "IS", "Nord-Est"),
    "pascani":           ("Iași", "IS", "Nord-Est"),
    "harlau":            ("Iași", "IS", "Nord-Est"),
    "targu frumos":      ("Iași", "IS", "Nord-Est"),
    # ── Ilfov ─────────────────────────────────────────────────────────────────
    "voluntari":         ("Ilfov", "IF", "București-Ilfov"),
    "chiajna":           ("Ilfov", "IF", "București-Ilfov"),
    "otopeni":           ("Ilfov", "IF", "București-Ilfov"),
    "popesti leordeni":  ("Ilfov", "IF", "București-Ilfov"),
    "bragadiru":         ("Ilfov", "IF", "București-Ilfov"),
    "stefanestii de jos":("Ilfov", "IF", "București-Ilfov"),
    "domnesti":          ("Ilfov", "IF", "București-Ilfov"),
    "chisoda timisoara": ("Timiș", "TM", "Vest"),
    "mosnita noua":      ("Timiș", "TM", "Vest"),
    # ── Maramureș ─────────────────────────────────────────────────────────────
    "baia mare":         ("Maramureș", "MM", "Nord-Vest"),
    "sighetu marmatiei": ("Maramureș", "MM", "Nord-Vest"),
    "borsa":             ("Maramureș", "MM", "Nord-Vest"),
    # ── Mehedinți ─────────────────────────────────────────────────────────────
    "drobeta turnu severin": ("Mehedinți", "MH", "Sud-Vest Oltenia"),
    # ── Mureș ─────────────────────────────────────────────────────────────────
    "targu mures":       ("Mureș", "MS", "Centru"),
    "reghin":            ("Mureș", "MS", "Centru"),
    "sighisoara":        ("Mureș", "MS", "Centru"),
    "tarnaveni":         ("Mureș", "MS", "Centru"),
    "ludus":             ("Mureș", "MS", "Centru"),
    "sovata":            ("Mureș", "MS", "Centru"),
    # ── Neamț ─────────────────────────────────────────────────────────────────
    "piatra neamt":      ("Neamț", "NT", "Nord-Est"),
    "roman":             ("Neamț", "NT", "Nord-Est"),
    "targu neamt":       ("Neamț", "NT", "Nord-Est"),
    # ── Olt ───────────────────────────────────────────────────────────────────
    "slatina":           ("Olt", "OT", "Sud-Vest Oltenia"),
    "caracal":           ("Olt", "OT", "Sud-Vest Oltenia"),
    "bals":              ("Olt", "OT", "Sud-Vest Oltenia"),
    # ── Prahova ───────────────────────────────────────────────────────────────
    "ploiesti":          ("Prahova", "PH", "Sud-Muntenia"),
    "campina":           ("Prahova", "PH", "Sud-Muntenia"),
    "sinaia":            ("Prahova", "PH", "Sud-Muntenia"),
    # ── Satu Mare ─────────────────────────────────────────────────────────────
    "satu mare":         ("Satu Mare", "SM", "Nord-Vest"),
    "carei":             ("Satu Mare", "SM", "Nord-Vest"),
    # ── Sălaj ─────────────────────────────────────────────────────────────────
    "zalau":             ("Sălaj", "SJ", "Nord-Vest"),
    "jibou":             ("Sălaj", "SJ", "Nord-Vest"),
    # ── Sibiu ─────────────────────────────────────────────────────────────────
    "sibiu":             ("Sibiu", "SB", "Centru"),
    "medias":            ("Sibiu", "SB", "Centru"),
    "cisnadie":          ("Sibiu", "SB", "Centru"),
    "selimbar":          ("Sibiu", "SB", "Centru"),
    # ── Suceava ───────────────────────────────────────────────────────────────
    "suceava":           ("Suceava", "SV", "Nord-Est"),
    "falticeni":         ("Suceava", "SV", "Nord-Est"),
    "campulung moldovenesc": ("Suceava", "SV", "Nord-Est"),
    "radauti":           ("Suceava", "SV", "Nord-Est"),
    "vatra dornei":      ("Suceava", "SV", "Nord-Est"),
    "gura humorului":    ("Suceava", "SV", "Nord-Est"),
    "vicovu de sus":     ("Suceava", "SV", "Nord-Est"),
    # ── Teleorman ─────────────────────────────────────────────────────────────
    "alexandria":        ("Teleorman", "TR", "Sud-Muntenia"),
    "rosiori de vede":   ("Teleorman", "TR", "Sud-Muntenia"),
    "turnu magurele":    ("Teleorman", "TR", "Sud-Muntenia"),
    # ── Timiș ─────────────────────────────────────────────────────────────────
    "timisoara":         ("Timiș", "TM", "Vest"),
    "lugoj":             ("Timiș", "TM", "Vest"),
    "jimbolia":          ("Timiș", "TM", "Vest"),
    "dumbravita":        ("Timiș", "TM", "Vest"),
    "giroc":             ("Timiș", "TM", "Vest"),
    # ── Tulcea ────────────────────────────────────────────────────────────────
    "tulcea":            ("Tulcea", "TL", "Sud-Est"),
    # ── Vaslui ────────────────────────────────────────────────────────────────
    "vaslui":            ("Vaslui", "VS", "Nord-Est"),
    "barlad":            ("Vaslui", "VS", "Nord-Est"),
    "husi":              ("Vaslui", "VS", "Nord-Est"),
    # ── Vâlcea ────────────────────────────────────────────────────────────────
    "ramnicu valcea":    ("Vâlcea", "VL", "Sud-Vest Oltenia"),
    "dragasani":         ("Vâlcea", "VL", "Sud-Vest Oltenia"),
    "horezu":            ("Vâlcea", "VL", "Sud-Vest Oltenia"),
    "babeni":            ("Vâlcea", "VL", "Sud-Vest Oltenia"),
    # ── Vrancea ───────────────────────────────────────────────────────────────
    "focsani":           ("Vrancea", "VN", "Sud-Est"),
    "adjud":             ("Vrancea", "VN", "Sud-Est"),
}

REGIONS: dict[str, list[str]] = {
    "Nord-Est":          ["Bacău", "Botoșani", "Iași", "Neamț", "Suceava", "Vaslui"],
    "Sud-Est":           ["Brăila", "Buzău", "Constanța", "Galați", "Tulcea", "Vrancea"],
    "Sud-Muntenia":      ["Argeș", "Călărași", "Dâmbovița", "Giurgiu", "Ialomița", "Prahova", "Teleorman"],
    "Sud-Vest Oltenia":  ["Dolj", "Gorj", "Mehedinți", "Olt", "Vâlcea"],
    "Vest":              ["Arad", "Caraș-Severin", "Hunedoara", "Timiș"],
    "Nord-Vest":         ["Bihor", "Bistrița-Năsăud", "Cluj", "Maramureș", "Satu Mare", "Sălaj"],
    "Centru":            ["Alba", "Brașov", "Covasna", "Harghita", "Mureș", "Sibiu"],
    "București-Ilfov":   ["București", "Ilfov"],
}


def normalize_city(city: str) -> str:
    """
    Normalize a city name: lowercase → strip diacritics → hyphens to spaces
    → collapse whitespace → apply known aliases.
    This is the canonical normalization shared across all analysis modules.
    """
    if not city:
        return ""
    c = unicodedata.normalize("NFD", city.lower().strip())
    c = "".join(ch for ch in c if unicodedata.category(ch) != "Mn")
    c = c.replace("-", " ").strip()
    while "  " in c:
        c = c.replace("  ", " ")
    return _ALIASES.get(c, c)


def lookup(city: str) -> dict:
    """
    Return {"county": str, "county_code": str, "region": str} for a city,
    or {} if not found.
    """
    norm = normalize_city(city)
    entry = _LOOKUP.get(norm)
    if not entry:
        return {}
    county, code, region = entry
    return {"county": county, "county_code": code, "region": region}


def county_for_city(city: str) -> str:
    return lookup(city).get("county", "")


def region_for_city(city: str) -> str:
    return lookup(city).get("region", "")


_DISPLAY_NAMES: dict[str, str] = {
    "bucuresti": "București",
    "bucharest": "București",
    "constanta": "Constanța",
    "bacau": "Bacău",
    "brasov": "Brașov",
    "cluj napoca": "Cluj-Napoca",
    "ploiesti": "Ploiești",
    "timisoara": "Timișoara",
    "targu mures": "Târgu Mureș",
    "iasi": "Iași",
    "galati": "Galați",
    "braila": "Brăila",
    "pitesti": "Pitești",
    "buzau": "Buzău",
    "ramnicu valcea": "Râmnicu Vâlcea",
    "botosani": "Botoșani",
    "piatra neamt": "Piatra Neamț",
    "bistrita": "Bistrița",
    "zalau": "Zalău",
    "focsani": "Focșani",
    "sfantu gheorghe": "Sfântu Gheorghe",
    "targu jiu": "Târgu Jiu",
    "targu neamt": "Târgu Neamț",
    "targu frumos": "Târgu Frumos",
    "drobeta turnu severin": "Drobeta-Turnu Severin",
    "resita": "Reșița",
    "miercurea ciuc": "Miercurea Ciuc",
    "satu mare": "Satu Mare",
    "baia mare": "Baia Mare",
    "popesti leordeni": "Popești-Leordeni",
    "sighetu marmatiei": "Sighetu Marmației",
    "campulung moldovenesc": "Câmpulung Moldovenesc",
    "campulung muscel": "Câmpulung Muscel",
    "alba iulia": "Alba Iulia",
    "deva": "Deva",
    "arad": "Arad",
    "oradea": "Oradea",
    "sibiu": "Sibiu",
    "craiova": "Craiova",
    "suceava": "Suceava",
    "tulcea": "Tulcea",
    "giurgiu": "Giurgiu",
    "alexandria": "Alexandria",
    "slobozia": "Slobozia",
    "vaslui": "Vaslui",
    "medias": "Mediaș",
    "targoviste": "Târgoviște",
    "sighisoara": "Sighișoara",
    "odorheiu secuiesc": "Odorheiu Secuiesc",
    "targu secuiesc": "Târgu Secuiesc",
}


def display_city(city: str) -> str:
    """Return the canonical diacriticized display name for a city."""
    if not city:
        return city
    norm = normalize_city(city)
    return _DISPLAY_NAMES.get(norm, city)


def enrich_store(store: dict) -> dict:
    """Add county, county_code, region fields in-place (returns same dict)."""
    info = lookup(store.get("city", ""))
    store["county"] = info.get("county", "")
    store["county_code"] = info.get("county_code", "")
    store["region"] = info.get("region", "")
    return store


def enrich_stores(stores: list[dict]) -> list[dict]:
    return [enrich_store(s) for s in stores]
