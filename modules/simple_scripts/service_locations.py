# modules/service_locations.py
# All rules and validations for service locations.

import logging
import json
import glob
import modules.config
from modules.basic.log_configs import log_abbrev_header, log_issue_header
from modules.hard_scripts.distribution_walker import get_walk_order_index_map, get_walk_paths_map


logger = logging.getLogger(__name__)

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

def check_service_location_attributes(service_locations_by_id_or_path, logger=None, log_debug: bool = True):
    """
    Validate required attributes on Service Locations and log them in the **exact
    deep-walk path order** used by [Distribution and NAP Walker].

    Accepts either:
      • a dict {svc_id: properties}, or
      • a file path to a single service-location*.geojson

    Ordering rules:
      1) SLs that appear in the deep-walk: by walk ordinal (SL #1, SL #2, …).
      2) SLs not in the walk: placed *after* all known ones, grouped stably by NAP numeric then id.

    Returns: list[dict] of missing-attribute rows, each with keys:
      - "Service Location ID"
      - "Attribute"
      - "Value"
    """
    import json, os, re, logging
    import modules
    from modules.basic.log_configs import log_abbrev_header, log_issue_header
    from modules.hard_scripts.distribution_walker import (
        get_walk_order_index_map, get_walk_paths_map
    )

    logger = logger or logging.getLogger(__name__)

    # Log switches
    detail    = str(getattr(modules.config, "LOG_DETAIL", "DEBUG")).upper()
    do_info   = (detail == "INFO" and log_debug)
    do_debug  = bool(log_debug and getattr(modules.config, "LOG_SVCLOC_DEBUG", False))
    show_path = bool(getattr(modules.config, "LOG_INCLUDE_WALK_PATH", False))

    # Required attributes (same list used elsewhere)
    REQUIRED = list(globals().get("REQUIRED_ATTRIBUTES", [
        "Build Type", "Building Type", "Drop Type", "NAP #", "NAP Location",
        "Loose Tube", "Splice Colors",
    ]))

    # Load either from dict or from a single file path
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
                sid = (props.get("Service Location ID")
                       or props.get("ID")
                       or props.get("vetro_id")
                       or "").strip()
                if sid:
                    sl_props_by_id[sid] = props
        except Exception as e:
            logger.error(f"[SvcLoc] Failed to load {src_path}: {e}")
            return []
    else:
        logger.error(f"[SvcLoc] Unsupported input type: {type(service_locations_by_id_or_path)}")
        return []

    # One-time header
    log_abbrev_header()

    # Walker-provided global order and path strings
    order_map = get_walk_order_index_map()  # {sid: 1..N}
    try:
        _paths = get_walk_paths_map()       # {sid: "T3 → DF → NAP …"}
    except Exception:
        _paths = {}

    BIG = 10**9

    def _extract_nap_id_from_path(path_str: str) -> str:
        if not path_str:
            return ""
        tokens = [t.strip() for t in path_str.split("→")]
        for t in tokens:
            if re.search(r"\bN\s*#?\s*\d+\b", t):  # contains "N <num>"
                return t
        return ""

    def _nap_from_props(sid: str) -> str:
        props = sl_props_by_id.get(sid) or {}
        return str(props.get("NAP #") or props.get("NAP Number") or "").strip()

    def _nap_numeric(nap_id: str) -> int:
        """
        Try to extract the trailing N number from a token like '04.AC01.HAR.N58 ...'
        or a bare 'N 58'. Fall back to BIG on failure.
        """
        if not nap_id:
            return BIG
        # Common case: '... N58' or '... N 58'
        m = re.search(r"\bN\s*#?\s*(\d+)\b", nap_id, re.IGNORECASE)
        if m:
            return int(m.group(1))
        # Sometimes we may only have the number
        if nap_id.isdigit():
            return int(nap_id)
        return BIG

    def _nap_for_sid(sid: str) -> str:
        # Prefer nap from walker path; fallback to properties
        return _extract_nap_id_from_path(_paths.get(sid, "")) or _nap_from_props(sid)

    # PRIMARY: path-order sort (exact deep-walk). Unknowns come after, stably grouped.
    def _sort_key(sid: str):
        idx = order_map.get(sid)
        if idx is not None:
            return (0, idx, sid)  # known walk index first, exact order
        nap_id = _nap_for_sid(sid)
        return (1, _nap_numeric(nap_id), sid)  # unknowns after, stable grouping

    # Sort the IDs we were given by the rules above
    ids = sorted(sl_props_by_id.keys(), key=_sort_key)

    # Numbering: use walker ordinal for known; continue counting for unknowns
    next_ordinal = (max(order_map.values()) if order_map else 0) + 1

    missing_list: list[dict] = []
    header_lines: list[str] = []

    for sid in ids:
        props = sl_props_by_id.get(sid) or {}
        sl_num = order_map.get(sid)
        if sl_num is None:
            sl_num = next_ordinal
            next_ordinal += 1

        missing_attrs_for_sl: list[str] = []
        for attr in REQUIRED:
            val = props.get(attr)
            if val is None or (isinstance(val, str) and not val.strip()):
                missing_list.append({
                    "Service Location ID": sid,
                    "Attribute": attr,
                    "Value": val if val is not None else ""
                })
                missing_attrs_for_sl.append(attr)
                if do_debug:
                    logger.debug(f"❌ [SvcLoc]   -> Missing '{attr}' on {sid}")

        # Log per-line in path order
        if do_info:
            path_part = f" — path={_paths[sid]}" if (show_path and _paths.get(sid)) else ""
            if missing_attrs_for_sl:
                logger.info(f"[Check Service Location Attributes] ❌ SL # {sl_num}: {sid} — Missing: {', '.join(missing_attrs_for_sl)}{path_part}")
                header_lines.append(f"SL # {sl_num}: {sid} — Missing: {', '.join(missing_attrs_for_sl)}{path_part}")
            else:
                logger.info(f"[Check Service Location Attributes] ✅ SL # {sl_num}: {sid}{path_part}")

        if do_debug and not missing_attrs_for_sl:
            logger.debug(f"✅ [SvcLoc] {sid}: all required attributes present")

    # Also mirror the missing lines in a header block (keeps per-line + summary)
    if header_lines:
        log_issue_header(
            "[Check Service Location Attributes] Missing Attributes",
            header_lines,
            logger=logger
        )

    if do_debug:
        logger.debug(f"• [SvcLoc] Total missing-attribute rows: {len(missing_list)}")

    return missing_list

def check_all_service_location_attributes(log_debug: bool = True):
    """
    Load ALL `service-location*.geojson` from modules.config.DATA_DIR, validate attributes,
    and log them in the **exact deep-walk path order** used by [Distribution and NAP Walker].
    Any Service Locations not encountered in the walk are listed after those, grouped stably.

    Returns: list[dict] of missing-attribute rows (same schema as single-file variant).
    """
    import json, glob, re, logging
    import modules
    from modules.basic.log_configs import log_abbrev_header, log_issue_header
    from modules.hard_scripts.distribution_walker import (
        get_walk_order_index_map, get_walk_paths_map
    )

    logger = logging.getLogger(__name__)

    detail    = str(getattr(modules.config, "LOG_DETAIL", "DEBUG")).upper()
    do_info   = (detail == "INFO" and log_debug)
    do_debug  = bool(log_debug and getattr(modules.config, "LOG_SVCLOC_DEBUG", False))
    show_path = bool(getattr(modules.config, "LOG_INCLUDE_WALK_PATH", False))

    REQUIRED = list(globals().get("REQUIRED_ATTRIBUTES", [
        "Build Type", "Building Type", "Drop Type", "NAP #", "NAP Location",
        "Loose Tube", "Splice Colors",
    ]))

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
            sid = (props.get("Service Location ID")
                   or props.get("ID")
                   or props.get("vetro_id")
                   or "").strip()
            if sid:
                sl_props_by_id[sid] = props

    # Walker order + paths
    order_map = get_walk_order_index_map()
    try:
        _paths = get_walk_paths_map()
    except Exception:
        _paths = {}

    BIG = 10**9

    def _extract_nap_id_from_path(path_str: str) -> str:
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
        return _extract_nap_id_from_path(_paths.get(sid, "")) or _nap_from_props(sid)

    # PRIMARY: deep-walk order; unknown SIDs come after
    def _sort_key(sid: str):
        idx = order_map.get(sid)
        if idx is not None:
            return (0, idx, sid)
        return (1, _nap_numeric(_nap_for_sid(sid)), sid)

    ids = sorted(sl_props_by_id.keys(), key=_sort_key)

    next_ordinal = (max(order_map.values()) if order_map else 0) + 1

    missing_list: list[dict] = []
    header_lines: list[str] = []

    for sid in ids:
        props = sl_props_by_id.get(sid) or {}
        sl_num = order_map.get(sid)
        if sl_num is None:
            sl_num = next_ordinal
            next_ordinal += 1

        missing_attrs_for_sl: list[str] = []
        for attr in REQUIRED:
            val = props.get(attr)
            if val is None or (isinstance(val, str) and not val.strip()):
                missing_list.append({
                    "Service Location ID": sid,
                    "Attribute": attr,
                    "Value": val if val is not None else ""
                })
                missing_attrs_for_sl.append(attr)
                if do_debug:
                    logger.debug(f"❌ [SvcLoc]   -> Missing '{attr}' on {sid}")

        if do_info:
            path_part = f" — path={_paths[sid]}" if (show_path and _paths.get(sid)) else ""
            if missing_attrs_for_sl:
                logger.info(f"[Check Service Location Attributes] ❌ SL # {sl_num}: {sid} — Missing: {', '.join(missing_attrs_for_sl)}{path_part}")
                header_lines.append(f"SL # {sl_num}: {sid} — Missing: {', '.join(missing_attrs_for_sl)}{path_part}")
            else:
                logger.info(f"[Check Service Location Attributes] ✅ SL # {sl_num}: {sid}{path_part}")

        if do_debug and not missing_attrs_for_sl:
            logger.debug(f"✅ [SvcLoc] {sid}: all required attributes present")

    if header_lines:
        log_issue_header(
            "[Check Service Location Attributes] Missing Attributes",
            header_lines,
            logger=logger
        )

    if do_debug:
        logger.debug(f"• [SvcLoc] Total missing-attribute rows: {len(missing_list)}")

    return missing_list