# modules/simple_scripts/service_locations.py
# All rules and validations for service locations.

import logging
import json
import glob
import re
import modules.config
from modules.basic.log_configs import log_abbrev_header, log_issue_header
from modules.hard_scripts.distribution_walker import get_walk_order_index_map, get_walk_paths_map
from modules.basic.fiber_colors import FIBER_COLORS

logger = logging.getLogger(__name__)


def _validate_splice_colors(raw: str):
    """
    Return None if OK; otherwise a dict row with Issue='Misspelt Attribute'.

    Accepts:
      • Exact canonical names in _FIBER_COLORS (case-insensitive ok)
      • "N - Name" → Name (right-hand name must be canonical)
      • 1..12 (pure numbers) → map to _FIBER_COLORS[n-1]
      • "ColorName 1.2" → ColorName (numeric tail ignored)

    Dot-code ONLY tokens like '1.3' are NOT accepted.
    """
    s = _canonicalize(raw)
    if not s:
        return {"Attribute": "Splice Colors", "Value": "", "Issue": "Missing Attribute"}

    tokens = re.split(r"[,\n;/]+", s)
    bad = []

    def _by_num(nstr: str):
        try:
            n = int(nstr.strip())
        except Exception:
            return None
        return _FIBER_COLORS[n - 1] if 1 <= n <= len(_FIBER_COLORS) else None

    for tok in tokens:
        t = tok.strip()
        if not t:
            continue
        original = t

        # "N - Name" → Name (prefer right)
        if "-" in t:
            left, right = [p.strip() for p in t.split("-", 1)]
            if right in _FIBER_COLORS:
                continue
            if left.isdigit() and _by_num(left):
                continue
            bad.append(original)
            continue

        # startswith canonical color (handles "Black 1.1")
        tl = t.lower()
        if any(tl.startswith(c.lower()) for c in _FIBER_COLORS):
            continue

        # pure numeric 1..12 ok
        if t.isdigit() and _by_num(t):
            continue

        # anything else is invalid
        bad.append(original)

    if bad:
        return {
            "Attribute": "Splice Colors",
            "Value": f"{s} [invalid: {', '.join(bad)}]",
            "Issue": "Misspelt Attribute",
        }
    return None


# def _validate_splice_colors(raw: str) -> tuple[list[str], list[str]]:
#     """
#     Parse a 'Splice Colors' value and return (valid_colors, invalid_tokens).
#     Rules accepted:
#       • 1–12 (numbers) → map to FIBER_COLORS[0..11]
#       • dot-coded tokens like '04.AC01.HAR.12' → take the last '.12'
#       • canonical color names that exactly match FIBER_COLORS
#     Everything else (aliases like 'Purple', misspellings like 'Gry', etc.) is invalid.
#     """
#     if not isinstance(raw, str):
#         return [], []

#     valid, bad = [], []
#     # split on common separators
#     tokens = re.split(r'[,\n;/]+', raw)

#     def _by_num(nstr: str):
#         try:
#             n = int(nstr.strip())
#         except Exception:
#             return None
#         if 1 <= n <= len(FIBER_COLORS):
#             return FIBER_COLORS[n - 1]
#         return None

#     for tok in tokens:
#         s = tok.strip()
#         if not s:
#             continue
#         original = s

#         # If dot-coded, take the last segment and try number first (… .12)
#         if '.' in s:
#             last = s.split('.')[-1].strip()
#             if last.isdigit():
#                 color = _by_num(last)
#                 if color:
#                     valid.append(color); continue
#             # fall through to name handling on 'last'

#             # set s to last for downstream checks
#             s = last

#         # Handle "5 - Slate" style: prefer the explicit name on the right,
#         # else try the left number.
#         if '-' in s:
#             left, right = [p.strip() for p in s.split('-', 1)]
#             canon_right = right[:1].upper() + right[1:].lower() if right else ""
#             if canon_right in FIBER_COLORS:
#                 valid.append(canon_right); continue
#             if left.isdigit():
#                 color = _by_num(left)
#                 if color:
#                     valid.append(color); continue

#         # Pure number?
#         if s.isdigit():
#             color = _by_num(s)
#             if color:
#                 valid.append(color); continue
#             bad.append(original); continue

#         # Exact canonical name only (case-insensitive matching to canonical)
#         canon = s[:1].upper() + s[1:].lower()
#         if canon in FIBER_COLORS:
#             valid.append(canon)
#         else:
#             # Important: aliases like "Purple" (for Violet) are *not* accepted.
#             bad.append(original)

#     return valid, bad


def _extract_nap_id_from_path(path: str) -> str:
    """
    Extract the NAP ID token from a walker path string.
    Accepts both formats:
      • '... → <NAP_ID> → <SVC_ID>'
      • '<NAP_ID> → <DROP_ID> → <SVC_ID>'
    Strategy:
      1) Prefer the token that looks like a NAP (contains '.N<digits>').
      2) Otherwise, if there are at least 2 tokens, use the penultimate (… → NAP → SVC).
      3) Otherwise, fall back to the first token.
    """
    if not path:
        return ""
    tokens = [t.strip() for t in path.split("→")]
    # Prefer the token that clearly looks like a NAP id (contains '.N')
    for tok in tokens:
        if ".N" in tok:
            return tok
    # Fallbacks (robust to either “… → NAP → SVC” or “NAP → … → SVC”)
    if len(tokens) >= 2:
        return tokens[-2]
    return tokens[0] if tokens else ""


def _sym(ok):
    """Return a checkmark for ok=True, an X for ok=False, and a dot for neutral."""
    if ok is True:
        return "✅"
    if ok is False:
        return "❌"
    return "•"


# Attributes that must not be empty on each Service Location feature
REQUIRED_ATTRIBUTES = [
    "Build Type",
    "Building Type",
    "Drop Type",
    "NAP #",
    "NAP Location",
    "Loose Tube",
    "Splice Colors",
]

def load_service_locations() -> list[tuple]:
    """
    Load Service Location features: returns list of
    (lat, lon, loose_tube, splice_colors, svc_id).
    """
    out = []
    pattern = f"{modules.config.DATA_DIR}/*service-location*.geojson"
    files = glob.glob(pattern)

    if getattr(modules.config, "LOG_SVCLOC_DEBUG", False):
        logger.debug(f"[SvcLoc] Scanning files: {files}")

    for fn in files:
        try:
            with open(fn, encoding='utf-8') as f:
                gj = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load Service Locations from {fn}: {e}")
            continue

        feats = gj.get("features", [])
        if getattr(modules.config, "LOG_SVCLOC_DEBUG", False):
            logger.debug(f"[SvcLoc] {fn}: {len(feats)} features")

        for feat in feats:
            props  = feat.get("properties", {}) or {}
            geom   = feat.get("geometry", {}) or {}
            coords = geom.get("coordinates", []) or []
            if len(coords) < 2:
                if getattr(modules.config, "LOG_SVCLOC_DEBUG", False):
                    logger.debug(f"[SvcLoc] Skipping bad geometry in {fn}: {coords!r}")
                continue

            lon, lat = coords[:2]
            loose    = (props.get("Loose Tube")    or "").strip()
            splice   = (props.get("Splice Colors") or "").strip()
            svc_id   = (props.get("Service Location ID") or props.get("ID") or "").strip()

            out.append((round(lat, 6), round(lon, 6), loose, splice, svc_id))

    if getattr(modules.config, "LOG_SVCLOC_DEBUG", False):
        logger.debug(f"[SvcLoc] Loaded {len(out)} total service-location tuples")

    return out

# ───────────────────────── helpers & constants ─────────────────────────
def _canonicalize(s: str) -> str:
    return (s or "").strip()

_ALLOWED_BUILD_TYPE = {
    "RSVD", "Future", "Aerial", "Underground",
}
_ALLOWED_BUILDING_TYPE = {
    "Residential", "Commercial", "MDU (Multi-Dwelling Unit)", "Government",
}
_ALLOWED_DROP_TYPE = {
    "Aerial",
    "Underground",
    "Aerial to Underground",
    "Underground to Aerial",
    "Aerial to Underground to Aerial",
    "Underground to Aerial to Underground",
    "Aerial Midspan",
}
_ALLOWED_NAP_LOCATION = {"Aerial", "Underground"}

# Loose Tube: only these 6 colors
_ALLOWED_LOOSE_TUBE = {"Blue", "Orange", "Green", "Brown", "Slate", "White"}

# Canonical 12-color order (already the project standard)
_FIBER_COLORS = [
    "Blue","Orange","Green","Brown","Slate","White",
    "Red","Black","Yellow","Violet","Rose","Aqua",
]

import re

def _validate_dropdown(attr_name: str, raw: str, allowed: set[str]):
    s = _canonicalize(raw)
    if not s:
        return {"Attribute": attr_name, "Value": "", "Issue": "Missing Attribute"}
    if s in allowed:
        return None
    return {"Attribute": attr_name, "Value": s, "Issue": "Invalid Choice"}

def _validate_nap_number(raw):
    s = _canonicalize(str(raw))
    if not s:
        return {"Attribute": "NAP #", "Value": "", "Issue": "Missing Attribute"}
    # Only integers or .5 allowed, >= 1 (e.g., 24 / 24.5)
    if re.fullmatch(r"\d+(?:\.5)?", s):
        try:
            val = float(s)
            if val >= 1.0:
                return None  # OK
        except Exception:
            pass
    return {"Attribute": "NAP #", "Value": s, "Issue": "Invalid Number"}

def _validate_loose_tube(raw):
    s = _canonicalize(raw)
    if not s:
        return {"Attribute": "Loose Tube", "Value": "", "Issue": "Missing Attribute"}
    if s in _ALLOWED_LOOSE_TUBE:
        return None
    # treat everything else as “misspelt/invalid”
    return {"Attribute": "Loose Tube", "Value": s, "Issue": "Misspelt Attribute"}

def _validate_splice_colors(raw: str):
    """
    Return None if OK; otherwise a dict row with Issue='Misspelt Attribute'
    and 'Value' echoing the raw string plus a bracketed list of invalid tokens.
    Accepts:
      • 1–12 (numbers) → map to canonical _FIBER_COLORS[n-1]
      • dot-coded tokens like '04.AC01.HAR.12' → use the last '.12'
      • exact canonical names in _FIBER_COLORS
    Everything else → invalid.
    """
    s = _canonicalize(raw)
    if not s:
        return {"Attribute": "Splice Colors", "Value": "", "Issue": "Missing Attribute"}

    tokens = re.split(r"[,\n;/]+", s)
    bad = []

    def _by_num(nstr: str):
        try:
            n = int(nstr.strip())
        except Exception:
            return None
        return _FIBER_COLORS[n - 1] if 1 <= n <= len(_FIBER_COLORS) else None

    for tok in tokens:
        t = tok.strip()
        if not t:
            continue
        original = t

        # "num - Name" → prefer the right-side name; else the left number
        if "-" in t:
            left, right = [p.strip() for p in t.split("-", 1)]
            if right in _FIBER_COLORS:
                continue
            if left.isdigit() and _by_num(left):
                continue
            bad.append(original)
            continue

        # dot-coded: take last segment; try number first
        if "." in t:
            last = t.split(".")[-1].strip()
            if last.isdigit() and _by_num(last):
                continue
            t = last  # fall through as name below

        if t.isdigit() and _by_num(t):
            continue

        if t in _FIBER_COLORS:
            continue

        bad.append(original)

    if bad:
        # NOTE: we keep Attribute = 'Splice Colors'; Issue is the human label
        return {
            "Attribute": "Splice Colors",
            "Value": f"{s}  [invalid: {', '.join(bad)}]",
            "Issue": "Misspelt Attribute",
        }
    return None


def check_service_location_attributes(service_locations_by_id_or_path, logger=None, log_debug: bool = True):
    """
    VALIDATION + ORDERING:
      • Maintains deep-walk path order (then stable NAP grouping for unknowns).
      • Returns rows with: 'Service Location ID', 'Attribute', 'Value', 'Issue'.

    Rules:
      - Build Type, Building Type, Drop Type, NAP Location:
          * must be exactly one of the allowed values; empty → Missing Attribute
      - NAP #:
          * must be numeric (N or N.5), >= 1; empty → Missing Attribute
      - Loose Tube:
          * must be one of 6 canonical colors; empty → Missing Attribute; else Misspelt Attribute
      - Splice Colors:
          * must parse to 1–12 canonical colors; empty → Missing Attribute; else Misspelt Attribute on bad tokens
    """
    import json, re, logging
    import modules.config
    from modules.basic.log_configs import log_abbrev_header, log_issue_header
    from modules.hard_scripts.distribution_walker import (
        get_walk_order_index_map, get_walk_paths_map
    )

    logger = logger or logging.getLogger(__name__)
    detail = str(getattr(modules.config, "LOG_DETAIL", "DEBUG")).upper()
    do_info = (detail == "INFO" and log_debug)
    do_debug = bool(log_debug and getattr(modules.config, "LOG_SVCLOC_DEBUG", False))
    show_path = bool(getattr(modules.config, "LOG_INCLUDE_WALK_PATH", False))

    # Load either dict or single file
    sl_props_by_id: dict[str, dict] = {}
    if isinstance(service_locations_by_id_or_path, dict):
        sl_props_by_id = service_locations_by_id_or_path
    elif isinstance(service_locations_by_id_or_path, str):
        src_path = service_locations_by_id_or_path
        try:
            with open(src_path, "r", encoding="utf-8") as f:
                gj = json.load(f)
            for feat in gj.get("features", []):
                props = (feat.get("properties") or {}) if isinstance(feat, dict) else {}
                sid = (props.get("Service Location ID") or props.get("ID") or props.get("vetro_id") or "").strip()
                if sid:
                    sl_props_by_id[sid] = props
        except Exception as e:
            logger.error(f"[SvcLoc] Failed to load {src_path}: {e}")
            return []
    else:
        logger.error(f"[SvcLoc] Unsupported input type: {type(service_locations_by_id_or_path)}")
        return []

    log_abbrev_header()

    # Walker order & paths
    order_map = get_walk_order_index_map()  # {sid: 1..N}
    try:
        _paths = get_walk_paths_map()        # {sid: "T3 → DF → NAP …"}
    except Exception:
        _paths = {}

    BIG = 10**9
    def _extract_n_from_path(path_str: str) -> str:
        if not path_str:
            return ""
        tokens = [t.strip() for t in path_str.split("→")]
        for t in tokens:
            if re.search(r"\bN\s*#?\s*\d+\b", t):
                return t
        return ""

    def _nap_from_props(sid: str) -> str:
        props = sl_props_by_id.get(sid) or {}
        return str(props.get("NAP #") or props.get("NAP Number") or "").strip()

    def _nap_numeric(nap_id: str) -> int:
        if not nap_id:
            return BIG
        m = re.search(r"\bN\s*#?\s*(\d+)\b", nap_id, re.IGNORECASE)
        if m:
            return int(m.group(1))
        if nap_id.isdigit():
            return int(nap_id)
        return BIG

    def _nap_for_sid(sid: str) -> str:
        return _extract_n_from_path(_paths.get(sid, "")) or _nap_from_props(sid)

    def _sort_key(sid: str):
        idx = order_map.get(sid)
        if idx is not None:
            return (0, idx, sid)
        return (1, _nap_numeric(_nap_for_sid(sid)), sid)

    ids = sorted(sl_props_by_id.keys(), key=_sort_key)
    next_ordinal = (max(order_map.values()) if order_map else 0) + 1

    rows: list[dict] = []
    header_lines: list[str] = []

    for sid in ids:
        props = sl_props_by_id.get(sid) or {}
        sl_num = order_map.get(sid)
        if sl_num is None:
            sl_num = next_ordinal
            next_ordinal += 1

        # 1) Pick-list attributes
        for attr, allowed in (
            ("Build Type", _ALLOWED_BUILD_TYPE),
            ("Building Type", _ALLOWED_BUILDING_TYPE),
            ("Drop Type", _ALLOWED_DROP_TYPE),
            ("NAP Location", _ALLOWED_NAP_LOCATION),
        ):
            res = _validate_dropdown(attr, props.get(attr), allowed)
            if res:
                rows.append({"Service Location ID": sid, **res})

        # 2) NAP #
        res = _validate_nap_number(props.get("NAP #"))
        if res:
            rows.append({"Service Location ID": sid, **res})

        # 3) Loose Tube
        res = _validate_loose_tube(props.get("Loose Tube"))
        if res:
            rows.append({"Service Location ID": sid, **res})

        # 4) Splice Colors
        res = _validate_splice_colors(props.get("Splice Colors"))
        if res:
            rows.append({"Service Location ID": sid, **res})

        # Per-line logging (INFO mode)
        if do_info:
            path_part = f" — path={_paths[sid]}" if (show_path and _paths.get(sid)) else ""
            if any(r["Service Location ID"] == sid for r in rows):
                # collect issues for this SID for the summary line
                kinds = ", ".join(sorted({r["Issue"] for r in rows if r["Service Location ID"] == sid}))
                logger.info(f"[Check Service Location Attributes] ❌ SL # {sl_num}: {sid} — {kinds}{path_part}")
                header_lines.append(f"SL # {sl_num}: {sid} — {kinds}{path_part}")
            else:
                logger.info(f"[Check Service Location Attributes] ✅ SL # {sl_num}: {sid}{path_part}")

    if header_lines:
        log_issue_header("[Check Service Location Attributes] Attribute Problems", header_lines, logger=logger)

    if do_debug:
        logger.debug(f"• [SvcLoc] Total SL attribute rows (incl. spelling/choice/number): {len(rows)}")

    return rows


def check_all_service_location_attributes(log_debug: bool = True):
    """
    Batch version over all service-location*.geojson in modules.config.DATA_DIR.
    Returns rows with: 'Service Location ID', 'Attribute', 'Value', 'Issue'.
    Ordering and logging match the single-file variant.
    """
    import json, glob, re, logging
    import modules.config
    from modules.basic.log_configs import log_abbrev_header, log_issue_header
    from modules.hard_scripts.distribution_walker import (
        get_walk_order_index_map, get_walk_paths_map
    )

    logger = logging.getLogger(__name__)
    detail = str(getattr(modules.config, "LOG_DETAIL", "DEBUG")).upper()
    do_info = (detail == "INFO" and log_debug)
    do_debug = bool(log_debug and getattr(modules.config, "LOG_SVCLOC_DEBUG", False))
    show_path = bool(getattr(modules.config, "LOG_INCLUDE_WALK_PATH", False))

    log_abbrev_header()

    # Aggregate all SLs across files
    sl_props_by_id: dict[str, dict] = {}
    for fn in glob.glob(f"{modules.config.DATA_DIR}/service-location*.geojson"):
        try:
            with open(fn, "r", encoding="utf-8") as f:
                gj = json.load(f)
        except Exception as e:
            logger.error(f"[SvcLoc] Failed to load {fn}: {e}")
            continue
        for feat in gj.get("features", []):
            props = (feat.get("properties") or {}) if isinstance(feat, dict) else {}
            sid = (props.get("Service Location ID") or props.get("ID") or props.get("vetro_id") or "").strip()
            if sid:
                sl_props_by_id[sid] = props

    # Walker order & paths
    order_map = get_walk_order_index_map()
    try:
        _paths = get_walk_paths_map()
    except Exception:
        _paths = {}

    BIG = 10**9
    def _extract_n(path_str: str) -> str:
        if not path_str:
            return ""
        tokens = [t.strip() for t in path_str.split("→")]
        for t in tokens:
            if re.search(r"\bN\s*#?\s*\d+\b", t):
                return t
        return ""

    def _nap_from_props(sid: str) -> str:
        props = sl_props_by_id.get(sid) or {}
        return str(props.get("NAP #") or props.get("NAP Number") or "").strip()

    def _nap_numeric(nap_id: str) -> int:
        if not nap_id:
            return BIG
        m = re.search(r"\bN\s*#?\s*(\d+)\b", nap_id, re.IGNORECASE)
        if m:
            return int(m.group(1))
        if nap_id.isdigit():
            return int(nap_id)
        return BIG

    def _nap_for_sid(sid: str) -> str:
        return _extract_n(_paths.get(sid, "")) or _nap_from_props(sid)

    def _sort_key(sid: str):
        idx = order_map.get(sid)
        if idx is not None:
            return (0, idx, sid)
        return (1, _nap_numeric(_nap_for_sid(sid)), sid)

    ids = sorted(sl_props_by_id.keys(), key=_sort_key)
    next_ordinal = (max(order_map.values()) if order_map else 0) + 1

    rows: list[dict] = []
    header_lines: list[str] = []

    for sid in ids:
        props = sl_props_by_id.get(sid) or {}
        sl_num = order_map.get(sid)
        if sl_num is None:
            sl_num = next_ordinal
            next_ordinal += 1

        for attr, allowed in (
            ("Build Type", _ALLOWED_BUILD_TYPE),
            ("Building Type", _ALLOWED_BUILDING_TYPE),
            ("Drop Type", _ALLOWED_DROP_TYPE),
            ("NAP Location", _ALLOWED_NAP_LOCATION),
        ):
            res = _validate_dropdown(attr, props.get(attr), allowed)
            if res:
                rows.append({"Service Location ID": sid, **res})

        res = _validate_nap_number(props.get("NAP #"))
        if res:
            rows.append({"Service Location ID": sid, **res})

        res = _validate_loose_tube(props.get("Loose Tube"))
        if res:
            rows.append({"Service Location ID": sid, **res})

        res = _validate_splice_colors(props.get("Splice Colors"))
        if res:
            rows.append({"Service Location ID": sid, **res})

        if do_info:
            path_part = f" — path={_paths[sid]}" if (show_path and _paths.get(sid)) else ""
            if any(r["Service Location ID"] == sid for r in rows):
                kinds = ", ".join(sorted({r["Issue"] for r in rows if r["Service Location ID"] == sid}))
                logger.info(f"[Check Service Location Attributes] ❌ SL # {sl_num}: {sid} — {kinds}{path_part}")
                header_lines.append(f"SL # {sl_num}: {sid} — {kinds}{path_part}")
            else:
                logger.info(f"[Check Service Location Attributes] ✅ SL # {sl_num}: {sid}{path_part}")

    if header_lines:
        log_issue_header("[Check Service Location Attributes] Attribute Problems", header_lines, logger=logger)

    if do_debug:
        logger.debug(f"• [SvcLoc] Total SL attribute rows (incl. spelling/choice/number): {len(rows)}")

    return rows