# modules/simple_scripts/fiber_drop.py
# All rules and utilities for fiber drops.

import logging
import glob
import json
import re
import modules.config
from modules.basic.distance_utils import haversine, THRESHOLD_M
from modules.basic.fiber_colors import FIBER_COLORS
from modules.basic.log_configs import log_issue_header, format_table_lines

logger = logging.getLogger(__name__)


def _load_sl_props_by_id() -> dict:
    """
    Load all service-location properties keyed by Service Location ID
    so we can pull 'NAP #' for sorting when an SL isn't in the walk.
    """
    props_by_id: dict[str, dict] = {}
    for fn in glob.glob(f"{modules.config.DATA_DIR}/*service-location*.geojson"):
        try:
            with open(fn, encoding="utf-8") as f:
                gj = json.load(f)
        except Exception:
            continue
        for feat in gj.get("features", []):
            props = (feat.get("properties") or {}) if isinstance(feat, dict) else {}
            sid = (props.get("Service Location ID")
                   or props.get("ID")
                   or props.get("vetro_id")
                   or "").strip()
            if sid:
                props_by_id[sid] = props
    return props_by_id


def _extract_nap_id_from_path(path: str) -> str:
    """
    Prefer a token that looks like a NAP ID (contains '.N<digits>').
    Fall back to the penultimate token if present.
    """
    if not path:
        return ""
    tokens = [t.strip() for t in path.split("→")]
    for tok in tokens:
        if ".N" in tok:
            return tok
    if len(tokens) >= 2:
        return tokens[-2]
    return tokens[0] if tokens else ""


def _nap_numeric(nap_id: str) -> int:
    """
    Extract the N number for sorting.
    Tries '... .N<digits>' first, then 'N <digits>'.
    """
    if not nap_id:
        return 10**9
    m = re.search(r'\.N(\d+)', nap_id)
    if m:
        return int(m.group(1))
    m = re.search(r'\bN\s*#?\s*(\d+)\b', nap_id, re.IGNORECASE)
    if m:
        return int(m.group(1))
    if nap_id.isdigit():
        return int(nap_id)
    return 10**9


# Helper to drill down nested lists until we find a [lon, lat] pair
def _get_lon_lat(coords):
    """
    Recursively unwrap nested lists/tuples until the elements are floats.
    Returns (lon, lat) or (None, None) if it fails.
    """
    pt = coords
    # unwrap until pt[0] is not a list/tuple
    while isinstance(pt, (list, tuple)) and pt and isinstance(pt[0], (list, tuple)):
        pt = pt[0]
    if isinstance(pt, (list, tuple)) and len(pt) >= 2:
        lon, lat = pt[0], pt[1]
        # ensure they're numbers
        if isinstance(lon, (int, float)) and isinstance(lat, (int, float)):
            return lon, lat
    return None, None

def _normalize_color(c: str) -> str:
    """
    Remove any leading number + dash prefix, e.g. "5 - Slate" → "Slate",
    and strip whitespace.
    """
    if not c:
        return ''
    parts = c.split(' - ', 1)
    if len(parts) == 2 and parts[0].strip().isdigit():
        return parts[1].strip()
    return c.strip()


def _token_to_canonical_color(token: str) -> str:
    """
    Convert a single splice token to a canonical color name.
    Rules:
      - "5 - Slate" → "Slate" (right-hand name wins)
      - If token starts with a canonical color name (case-insensitive), return that name
        (e.g., "Black 1.1" → "Black", "orange 1.3" → "Orange").
      - If the whole token is a number 1..12, map via FIBER_COLORS[n-1].
      - Otherwise return "" (unrecognized).
    """
    if not token:
        return ""
    s = str(token).strip()

    # Prefer "N - Name" right-hand name
    if " - " in s:
        left, right = [p.strip() for p in s.split(" - ", 1)]
        if right in FIBER_COLORS:
            return right
        s = left  # fall through

    # Starts with a color name? (handles "Black 1.1", "violet 2.4", etc.)
    sl = s.lower()
    for name in FIBER_COLORS:
        if sl.startswith(name.lower()):
            return name

    # Plain numeric 1..12?
    if s.isdigit():
        idx = int(s)
        if 1 <= idx <= len(FIBER_COLORS):
            return FIBER_COLORS[idx - 1]

    return ""


def _normalize_splice_to_colors(raw_splice: str) -> set[str]:
    """
    Parse a 'Splice Colors' field into a set of canonical color names.
    Dot-code suffixes are IGNORED. Examples:
      - "Black 1.1, Black 1.2"  → {"Black"}
      - "5 - Slate, 7"          → {"Slate", "Red"}
      - "Orange"                → {"Orange"}
      - "1.3"                   → {}  (dot-code alone is NO LONGER accepted)
    """
    tokens = [t.strip() for t in (raw_splice or '').split(',') if t.strip()]
    out: set[str] = set()
    for t in tokens:
        c = _token_to_canonical_color(t)
        if c:
            out.add(c)
    return out


def load_service_locations():
    """
    Returns list of (lat, lon, svc_id) for each Service Location,
    along with its raw splice-colors string.
    """

    sl = []
    pattern = f'{modules.config.DATA_DIR}/*service-location*.geojson'
    for fn in glob.glob(pattern):
        gj = json.load(open(fn, encoding='utf-8'))
        for feat in gj.get('features', []):
            props = feat.get('properties', {}) or {}
            geom   = feat.get('geometry', {}) or {}
            coords = geom.get('coordinates', [])

            lon, lat = _get_lon_lat(coords)
            if lon is None or lat is None:
                logger.warning(f"Skipping bad geometry in {fn}: {coords!r}")
                continue

            pt = (round(lat, 6), round(lon, 6))
            sl.append((pt, props.get('Splice Colors', ''), props.get('ID', '')))
    return sl

def load_fiber_drops():
    """
    Returns dict mapping each drop-vertex pt → raw Color string.
    Handles Point, LineString, and MultiLineString.
    """
    drops = {}
    for fn in glob.glob(f'{modules.config.DATA_DIR}/*fiber-drop*.geojson'):
        gj = json.load(open(fn, encoding='utf-8'))
        for feat in gj.get('features', []):
            props = feat.get('properties', {}) or {}
            color = props.get('Color', '')
            geom = feat.get('geometry', {})
            typ = geom.get('type')
            coords = geom.get('coordinates', [])

            verts = []
            if typ == 'Point':
                verts = [coords]
            elif typ == 'LineString':
                verts = coords
            elif typ == 'MultiLineString':
                for seg in coords:
                    verts.extend(seg)

            for lon, lat in verts:
                pt = (round(lat, 6), round(lon, 6))
                drops[pt] = color
    return drops


def find_color_mismatches(emit_info: bool = True) -> list[str]:
    """[Drop Issues] Color checks (match vs SL splice colors) — ordered like svc-attr checks.

    Emits:
      • ✅/❌ one line per Service Location (always when emit_info=True)
      • Errors-only recap table at the bottom (when emit_info=True and there are errors)

    Returns:
      list[str]: Service Location IDs where the compared drop color is not present in the
      Service Location's Splice Colors.

    IMPORTANT (NID behavior):
      - If the SL is downstream of a NID, compare against the NID's *upstream* (NAP→NID) drop color,
        not the local NID→SL segment color.
      - Otherwise, compare the drop color found exactly at the SL point.
    """
    # --- local helpers: emoji decoration (no external deps) ---
    def _emoji_for_color(name: str) -> str:
        n = (name or "").strip().lower()
        mapping = {
            "blue": "🟦", "orange": "🟧", "green": "🟩", "brown": "🟫",
            "slate": "◾️", "white": "⬜", "red": "🟥", "black": "⬛",
            "yellow": "🟨", "violet": "🟪", "rose": "🌹", "aqua": "🟦",
        }
        return mapping.get(n, name or "")

    def _decorate_color(name: str) -> str:
        mode = str(getattr(modules.config, "LOG_COLOR_MODE", "OFF")).upper()
        return _emoji_for_color(name) if mode == "EMOJI" else name

    from modules.hard_scripts.distribution_walker import get_walk_order_index_map
    try:
        from modules.hard_scripts.distribution_walker import get_walk_paths_map
        _paths = get_walk_paths_map()
    except Exception:
        _paths = {}

    # Map SL → upstream (NAP→NID) color, if behind a NID
    try:
        from modules.simple_scripts.nids import build_sid_upstream_drop_color_map
        _upstream_map = build_sid_upstream_drop_color_map()
    except Exception:
        _upstream_map = {}

    sl_props_by_id = _load_sl_props_by_id()

    # NEW: inclusion toggles for RSVD/Future SLs (default False)
    _include_rsvd   = bool(modules.config.get_pref("include_rsvd_sl", False))
    _include_future = bool(modules.config.get_pref("include_future_sl", False))

    # NOTE: Per-SL lines are now controlled *only* by emit_info
    detail    = str(getattr(modules.config, "LOG_DETAIL", "DEBUG")).upper()
    do_info   = emit_info
    do_debug  = bool(getattr(modules.config, "LOG_DROP_DEBUG", False))
    show_path = bool(getattr(modules.config, "LOG_INCLUDE_WALK_PATH", False))

    service_raw = load_service_locations()  # [((lat, lon), splice_raw, sid)]
    drops_map   = load_fiber_drops()        # {(lat, lon): '2 - Orange', ...}
    order_map   = get_walk_order_index_map()

    def _nap_for_sid(sid: str) -> str:
        from_path = _extract_nap_id_from_path(_paths.get(sid, ""))
        if from_path:
            return from_path
        props = sl_props_by_id.get(sid) or {}
        return str(props.get("NAP #") or props.get("NAP Number") or "").strip()

    def _sort_key(row):
        _pt, _splice, sid = row
        idx = order_map.get(sid)
        if idx is not None:
            return (0, idx, sid)
        return (1, _nap_numeric(_nap_for_sid(sid)), sid)

    rows_sorted = sorted(service_raw, key=_sort_key)

    if do_info:
        logger.info("==== [Drop Issues] Color Mismatches (svc-attr ordering) ====")

    mismatches: list[str] = []
    recap_rows: list[tuple[str, str, str, str]] = []
    next_ordinal = (max(order_map.values()) if order_map else 0) + 1

    for (pt, raw_splice, sid) in rows_sorted:
        # NEW: filter by Build Type unless explicitly included from Settings
        props = sl_props_by_id.get(sid) or {}
        bt = str(props.get("Build Type") or "").strip().lower()
        if (bt == "rsvd" and not _include_rsvd) or (bt == "future" and not _include_future):
            continue

        # Skip SLs with no drop snapped to the SL point;
        # those are handled by "Missing Service Location Drops".
        if pt not in drops_map:
            if do_debug:
                logger.debug(f"• [Drop] {sid}: no drop at {pt} (handled in Missing Drops)")
            continue

        # Effective compared color: pre-NID (if present) else local drop color
        raw_drop_local = str(drops_map.get(pt, "")).strip()
        pre_nid_color = _upstream_map.get(sid, "")  # empty if not behind NID
        if pre_nid_color:
            compared_color = pre_nid_color
            compared_tag = " [pre-NID]"
        else:
            compared_color = _normalize_color(raw_drop_local)  # e.g., '2 - Orange' -> 'Orange'
            compared_tag = ""

        # Normalize splice-colors on SL (dot-codes ignored; only canonical names)
        splice_colors = _normalize_splice_to_colors(raw_splice)  # {'Orange', ...}
        is_match = bool(compared_color) and (compared_color in splice_colors)

        # --- decorate for logging (emoji if enabled) ---
        drop_disp = _decorate_color(compared_color) + compared_tag if compared_color else "(none)"
        splice_txt = "[" + ", ".join(_decorate_color(c) for c in sorted(splice_colors)) + "]"
        path_part = f" — path={_paths[sid]}" if (show_path and _paths.get(sid)) else ""

        sl_num = order_map.get(sid)
        if sl_num is None:
            sl_num = next_ordinal
            next_ordinal += 1

        # Per-SL ✅/❌ line
        if do_info:
            if is_match:
                logger.info(f"[Drop Issues] ✅ SL # {sl_num}: {sid} — drop={drop_disp}; splice={splice_txt}{path_part}")
            else:
                logger.error(f"[Drop Issues] ❌ SL # {sl_num}: {sid} — drop={drop_disp} not in {splice_txt}{path_part}")

        # Collect recap rows (errors only)
        if not is_match:
            mismatches.append(sid)
            if show_path and _paths.get(sid):
                recap_rows.append((str(sl_num), sid, drop_disp, f"{splice_txt} | {_paths[sid]}"))
            else:
                recap_rows.append((str(sl_num), sid, drop_disp, splice_txt))

    if do_info:
        logger.info("==== End [Drop Issues] Color Mismatches (svc-attr ordering) ====")

    # Errors-only recap table at the bottom
    if emit_info and recap_rows:
        logger.info("===== Drop Issues (%d) =====", len(recap_rows))
        if show_path:
            headers = ["❌ SL #", "Service Location ID", "Drop Color", "SL Colors", "Path"]
            table_rows = []
            for sl_num, sid, dcol, sps in recap_rows:
                if " | " in sps:
                    sp, path = sps.split(" | ", 1)
                else:
                    sp, path = sps, ""
                table_rows.append([sl_num, sid, dcol, sp, path])
        else:
            headers = ["❌ SL #", "Service Location ID", "Drop Color", "SL Colors"]
            table_rows = [[sl_num, sid, dcol, sps] for (sl_num, sid, dcol, sps) in recap_rows]

        for line in format_table_lines(headers, table_rows, center_headers=True):
            logger.error(f"[Drop Issues] {line}")
        logger.info("===== End Drop Issues =====")

    if do_debug:
        logger.debug(f"• [Drop] Color-mismatch count: {len(mismatches)}")
    return mismatches


# def find_color_mismatches(emit_info: bool = True) -> list[str]:
#     """[Drop Issues] Color checks (match vs SL splice colors) — ordered like svc-attr checks.

#     Emits:
#       • ✅/❌ one line per Service Location (always when emit_info=True)
#       • Errors-only recap table at the bottom (when emit_info=True and there are errors)

#     Returns:
#       list[str]: Service Location IDs where the compared drop color is not present
#                  in the Service Location's Splice Colors.

#     IMPORTANT (NID behavior):
#       - If the SL is downstream of a NID, compare against the NID's *upstream* (NAP→NID)
#         drop color, not the local NID→SL segment color.
#       - Otherwise, compare the drop color found exactly at the SL point.
#     """
#     # --- local helpers: emoji decoration (no external deps) ---
#     def _emoji_for_color(name: str) -> str:
#         n = (name or "").strip().lower()
#         mapping = {
#             "blue": "",
#             "orange": "",
#             "green": "",
#             "brown": "",
#             "slate": "◾️",
#             "white": "⬜",
#             "red": "",
#             "black": "⬛",
#             "yellow": "",
#             "violet": "",
#             "rose": "",
#             "aqua": "",
#         }
#         return mapping.get(n, name or "")

#     def _decorate_color(name: str) -> str:
#         mode = str(getattr(modules.config, "LOG_COLOR_MODE", "OFF")).upper()
#         return _emoji_for_color(name) if mode == "EMOJI" else name

#     from modules.hard_scripts.distribution_walker import get_walk_order_index_map
#     try:
#         from modules.hard_scripts.distribution_walker import get_walk_paths_map
#         _paths = get_walk_paths_map()
#     except Exception:
#         _paths = {}

#     # Map SL → upstream (NAP→NID) color, if behind a NID
#     try:
#         from modules.simple_scripts.nids import build_sid_upstream_drop_color_map
#         _upstream_map = build_sid_upstream_drop_color_map()
#     except Exception:
#         _upstream_map = {}

#     sl_props_by_id = _load_sl_props_by_id()

#     # NOTE: Per-SL lines are now controlled *only* by emit_info
#     detail = str(getattr(modules.config, "LOG_DETAIL", "DEBUG")).upper()
#     do_info = emit_info
#     do_debug = bool(getattr(modules.config, "LOG_DROP_DEBUG", False))
#     show_path = bool(getattr(modules.config, "LOG_INCLUDE_WALK_PATH", False))

#     service_raw = load_service_locations()  # [((lat, lon), splice_raw, sid)]
#     drops_map = load_fiber_drops()          # {(lat, lon): '2 - Orange', ...}
#     order_map = get_walk_order_index_map()

#     def _nap_for_sid(sid: str) -> str:
#         from_path = _extract_nap_id_from_path(_paths.get(sid, ""))
#         if from_path:
#             return from_path
#         props = sl_props_by_id.get(sid) or {}
#         return str(props.get("NAP #") or props.get("NAP Number") or "").strip()

#     def _sort_key(row):
#         _pt, _splice, sid = row
#         idx = order_map.get(sid)
#         if idx is not None:
#             return (0, idx, sid)
#         return (1, _nap_numeric(_nap_for_sid(sid)), sid)

#     rows_sorted = sorted(service_raw, key=_sort_key)

#     if do_info:
#         logger.info("==== [Drop Issues] Color Mismatches (svc-attr ordering) ====")

#     mismatches: list[str] = []
#     recap_rows: list[tuple[str, str, str, str]] = []

#     next_ordinal = (max(order_map.values()) if order_map else 0) + 1

#     for (pt, raw_splice, sid) in rows_sorted:
#         # Skip SLs with no drop snapped to the SL point;
#         # those are handled by "Missing Service Location Drops".
#         if pt not in drops_map:
#             if do_debug:
#                 logger.debug(f"• [Drop] {sid}: no drop at {pt} (handled in Missing Drops)")
#             continue

#         # Effective compared color: pre-NID (if present) else local drop color
#         raw_drop_local = str(drops_map.get(pt, "")).strip()
#         pre_nid_color = _upstream_map.get(sid, "")  # empty if not behind NID
#         if pre_nid_color:
#             compared_color = pre_nid_color
#             compared_tag = " [pre-NID]"
#         else:
#             compared_color = _normalize_color(raw_drop_local)  # e.g., '2 - Orange' -> 'Orange'
#             compared_tag = ""

#         # Normalize splice-colors on SL (dot-codes ignored; only canonical names)
#         splice_colors = _normalize_splice_to_colors(raw_splice)  # {'Orange', ...}
#         is_match = bool(compared_color) and (compared_color in splice_colors)

#         # --- decorate for logging (emoji if enabled) ---
#         drop_disp = _decorate_color(compared_color) + compared_tag if compared_color else "(none)"
#         splice_txt = "[" + ", ".join(_decorate_color(c) for c in sorted(splice_colors)) + "]"
#         path_part = f" — path={_paths[sid]}" if (show_path and _paths.get(sid)) else ""

#         sl_num = order_map.get(sid)
#         if sl_num is None:
#             sl_num = next_ordinal
#             next_ordinal += 1

#         # Per-SL ✅/❌ line
#         if do_info:
#             if is_match:
#                 logger.info(f"[Drop Issues] ✅ SL # {sl_num}: {sid} — drop={drop_disp}; splice={splice_txt}{path_part}")
#             else:
#                 logger.error(f"[Drop Issues] ❌ SL # {sl_num}: {sid} — drop={drop_disp} not in {splice_txt}{path_part}")

#         # Collect recap rows (errors only)
#         if not is_match:
#             mismatches.append(sid)
#             if show_path and _paths.get(sid):
#                 recap_rows.append((str(sl_num), sid, drop_disp, f"{splice_txt} | {_paths[sid]}"))
#             else:
#                 recap_rows.append((str(sl_num), sid, drop_disp, splice_txt))

#     if do_info:
#         logger.info("==== End [Drop Issues] Color Mismatches (svc-attr ordering) ====")

#     # Errors-only recap table at the bottom
#     if emit_info and recap_rows:
#         logger.info("===== Drop Issues (%d) =====", len(recap_rows))
#         if show_path:
#             headers = ["❌ SL #", "Service Location ID", "Drop Color", "SL Colors", "Path"]
#             table_rows = []
#             for sl_num, sid, dcol, sps in recap_rows:
#                 if " | " in sps:
#                     sp, path = sps.split(" | ", 1)
#                 else:
#                     sp, path = sps, ""
#                 table_rows.append([sl_num, sid, dcol, sp, path])
#         else:
#             headers = ["❌ SL #", "Service Location ID", "Drop Color", "SL Colors"]
#             table_rows = [[sl_num, sid, dcol, sps] for (sl_num, sid, dcol, sps) in recap_rows]

#         for line in format_table_lines(headers, table_rows, center_headers=True):
#             logger.error(f"[Drop Issues] {line}")
#         logger.info("===== End Drop Issues =====")

#     if do_debug:
#         logger.debug(f"• [Drop] Color-mismatch count: {len(mismatches)}")

#     return mismatches


def find_missing_service_location_drops(service_coords=None, drop_coords=None, emit_info: bool = True):
    """ [Drop Issues] Missing Service Location Drops — same ordering as [Check Service Location Attributes]
    Returns list[str] of Service Location IDs with no drop within THRESHOLD_M.
    """
    from modules.hard_scripts.distribution_walker import get_walk_order_index_map
    # Optional: path trailer lookup
    try:
        from modules.hard_scripts.distribution_walker import get_walk_paths_map
        _paths = get_walk_paths_map()
    except Exception:
        _paths = {}

    sl_props_by_id = _load_sl_props_by_id()

    detail    = str(getattr(modules.config, "LOG_DETAIL", "DEBUG")).upper()
    do_info   = (detail == "INFO" and emit_info)
    do_debug  = bool(getattr(modules.config, "LOG_DROP_DEBUG", False))
    show_path = bool(getattr(modules.config, "LOG_INCLUDE_WALK_PATH", False))

    # Inclusion toggles for RSVD/Future
    _include_rsvd   = bool(modules.config.get_pref("include_rsvd_sl", False))
    _include_future = bool(modules.config.get_pref("include_future_sl", False))

    # ----------------- normalize caller inputs (unchanged) -----------------
    if service_coords is not None:
        norm_sl = []
        for item in service_coords:
            # Shape A: ((lat, lon), splice_colors, svc_id)
            if isinstance(item, (list, tuple)) and item and isinstance(item[0], (list, tuple,)):
                pt = item[0]
                sid = item[-1]
                norm_sl.append((float(pt[0]), float(pt[1]), sid))
            else:
                # Shape B: (lat, lon, svc_id[, ...])
                lat, lon, sid = item[:3]
                norm_sl.append((float(lat), float(lon), sid))
        service_coords = norm_sl

    if drop_coords is not None:
        # Dict → keys; ensure list of numeric pairs
        if isinstance(drop_coords, dict):
            drop_coords = list(drop_coords.keys())
        drop_coords = [(float(d[0]), float(d[1])) for d in drop_coords]

    # If callers didn't pass precomputed coords, load them here.
    # service_coords: list of (lat, lon, svc_id)
    # drop_coords: list of (lat, lon)
    if service_coords is None:
        # load_service_locations() → [( (lat,lon), splice, sid ), ...]
        service_coords = [(pt[0], pt[1], sid) for pt, _, sid in load_service_locations()]
    if drop_coords is None:
        drop_coords = list(load_fiber_drops().keys())

    order_map = get_walk_order_index_map()
    BIG = 10**9

    # First: find the missing ones (unsorted)
    missing: list[tuple[float, float, str]] = []
    for (lat, lon, svc_id) in service_coords:
        # NEW: filter by Build Type unless explicitly included from Settings
        props = sl_props_by_id.get(svc_id) or {}
        bt = str(props.get("Build Type") or "").strip().lower()
        if (bt == "rsvd" and not _include_rsvd) or (bt == "future" and not _include_future):
            continue

        found = any(haversine(lat, lon, dlat, dlon) <= THRESHOLD_M for dlat, dlon in drop_coords)
        if not found:
            missing.append((lat, lon, svc_id))

    def _nap_for_sid(sid: str) -> str:
        from_path = _extract_nap_id_from_path(_paths.get(sid, ""))
        if from_path:
            return from_path
        props = sl_props_by_id.get(sid) or {}
        return str(props.get("NAP #") or props.get("NAP Number") or "").strip()

    def _sort_key(tup):
        _lat, _lon, sid = tup
        idx = order_map.get(sid)
        if idx is not None:
            return (0, idx, sid)
        return (1, _nap_numeric(_nap_for_sid(sid)), sid)

    # Then: sort using svc-attr’s logic
    missing_sorted = sorted(missing, key=_sort_key)

    header_lines: list[str] = []
    if do_info:
        logger.info("==== [Drop Issues] Missing Service Location Drops (svc-attr ordering) ====")

    next_ordinal = (max(order_map.values()) if order_map else 0) + 1
    for (_lat, _lon, sid) in missing_sorted:
        sl_num = order_map.get(sid)
        if sl_num is None:
            sl_num = next_ordinal
            next_ordinal += 1
        path_part = "" if not (show_path and _paths.get(sid)) else f" — path={_paths[sid]}"
        # Flagged problems must log at ERROR regardless of LOG_DETAIL
        if do_info:
            logger.error("[Drop Issues] ❌ SL # %s: %s — no drop within %.2f m%s",
                         sl_num, sid, THRESHOLD_M, path_part)
        head_line = f"SL # {sl_num}: {sid} — no drop within {THRESHOLD_M:.2f} m"
        if show_path and _paths.get(sid):
            head_line += f" — path={_paths[sid]}"
        header_lines.append(head_line)

    if header_lines:
        log_issue_header("[Drop Issues] Missing Drops", header_lines, logger=logger)

    if do_info:
        logger.info("==== End [Drop Issues] Missing Service Location Drops (svc-attr ordering) ====")

    if do_debug:
        logger.debug(f"• [Drop] Missing-drop count: {len(missing_sorted)}")
    return [sid for (_lat, _lon, sid) in missing_sorted]


# def find_missing_service_location_drops(service_coords=None, drop_coords=None, emit_info: bool = True):
#     """
#     [Drop Issues] Missing Service Location Drops — same ordering as
#     [Check Service Location Attributes] (see find_color_mismatches docstring).
#     Returns list[str] of Service Location IDs with no drop within THRESHOLD_M.
#     """
#     from modules.hard_scripts.distribution_walker import get_walk_order_index_map
#     # Optional: path trailer lookup
#     try:
#         from modules.hard_scripts.distribution_walker import get_walk_paths_map
#         _paths = get_walk_paths_map()
#     except Exception:
#         _paths = {}

#     sl_props_by_id = _load_sl_props_by_id()

#     detail    = str(getattr(modules.config, "LOG_DETAIL", "DEBUG")).upper()
#     do_info   = (detail == "INFO" and emit_info)
#     do_debug  = bool(getattr(modules.config, "LOG_DROP_DEBUG", False))
#     show_path = bool(getattr(modules.config, "LOG_INCLUDE_WALK_PATH", False))

#     # ---------------------------------------------------------------------
#     # NEW: normalize inputs so callers can pass either raw loader outputs or
#     # already-flattened coords without crashing haversine().
#     #
#     # service_coords may be:
#     #   • [(lat, lon, svc_id), ...]
#     #   • [((lat, lon), splice_colors, svc_id), ...]  (from load_service_locations())
#     #
#     # drop_coords may be:
#     #   • [(lat, lon), ...]
#     #   • {(lat, lon): color, ...}                     (from load_fiber_drops())
#     # ---------------------------------------------------------------------
#     if service_coords is not None:
#         norm_sl = []
#         for item in service_coords:
#             # Shape A: ((lat, lon), splice_colors, svc_id)
#             if isinstance(item, (list, tuple)) and item and isinstance(item[0], (list, tuple,)):
#                 pt = item[0]
#                 sid = item[-1]
#                 norm_sl.append((float(pt[0]), float(pt[1]), sid))
#             else:
#                 # Shape B: (lat, lon, svc_id[, ...])
#                 lat, lon, sid = item[:3]
#                 norm_sl.append((float(lat), float(lon), sid))
#         service_coords = norm_sl

#     if drop_coords is not None:
#         # Dict → keys; ensure list of numeric pairs
#         if isinstance(drop_coords, dict):
#             drop_coords = list(drop_coords.keys())
#         drop_coords = [(float(d[0]), float(d[1])) for d in drop_coords]

#     # If callers didn't pass precomputed coords, load them here.
#     # service_coords: list of (lat, lon, svc_id)
#     # drop_coords:    list of (lat, lon)
#     if service_coords is None:
#         # load_service_locations() → [( (lat,lon), splice, sid ), ...]
#         service_coords = [(pt[0], pt[1], sid) for pt, _, sid in load_service_locations()]
#     if drop_coords is None:
#         drop_coords = list(load_fiber_drops().keys())

#     order_map = get_walk_order_index_map()
#     BIG = 10**9

#     # First: find the missing ones (unsorted)
#     missing: list[tuple[float, float, str]] = []
#     for (lat, lon, svc_id) in service_coords:
#         found = any(haversine(lat, lon, dlat, dlon) <= THRESHOLD_M for dlat, dlon in drop_coords)
#         if not found:
#             missing.append((lat, lon, svc_id))

#     def _nap_for_sid(sid: str) -> str:
#         from_path = _extract_nap_id_from_path(_paths.get(sid, ""))
#         if from_path:
#             return from_path
#         props = sl_props_by_id.get(sid) or {}
#         return str(props.get("NAP #") or props.get("NAP Number") or "").strip()

#     def _sort_key(tup):
#         _lat, _lon, sid = tup
#         idx = order_map.get(sid)
#         if idx is not None:
#             return (0, idx, sid)
#         return (1, _nap_numeric(_nap_for_sid(sid)), sid)

#     # Then: sort using svc-attr’s logic
#     missing_sorted = sorted(missing, key=_sort_key)

#     header_lines: list[str] = []
#     if do_info:
#         logger.info("==== [Drop Issues] Missing Service Location Drops (svc-attr ordering) ====")
#     next_ordinal = (max(order_map.values()) if order_map else 0) + 1
#     for (_lat, _lon, sid) in missing_sorted:
#         sl_num = order_map.get(sid)
#         if sl_num is None:
#             sl_num = next_ordinal
#             next_ordinal += 1

#         path_part = ""
#         if show_path and _paths.get(sid):
#             path_part = f" — path={_paths[sid]}"

#         # Flagged problems must log at ERROR regardless of LOG_DETAIL
#         if do_info:
#             logger.error("[Drop Issues] ❌ SL # %s: %s — no drop within %.2f m%s",
#                          sl_num, sid, THRESHOLD_M, path_part)

#         head_line = f"SL # {sl_num}: {sid} — no drop within {THRESHOLD_M:.2f} m"
#         if show_path and _paths.get(sid):
#             head_line += f" — path={_paths[sid]}"
#         header_lines.append(head_line)

#     if header_lines:
#         # This banner stays at its configured level inside log_issue_header();
#         # individual issue rows above are already ERROR.
#         log_issue_header("[Drop Issues] Missing Drops", header_lines, logger=logger)

#     if do_info:
#         logger.info("==== End [Drop Issues] Missing Service Location Drops (svc-attr ordering) ====")

#     if do_debug:
#         logger.debug(f"• [Drop] Missing-drop count: {len(missing_sorted)}")

#     return [sid for (_lat, _lon, sid) in missing_sorted]

def sort_service_location_ids_like_attributes(sids: list[str]) -> list[str]:
    """
    Utility: sort a list of Service Location IDs using the same ordering as
    [Check Service Location Attributes].
    """
    from modules.hard_scripts.distribution_walker import get_walk_order_index_map
    try:
        from modules.hard_scripts.distribution_walker import get_walk_paths_map
        _paths = get_walk_paths_map()
    except Exception:
        _paths = {}

    sl_props_by_id = _load_sl_props_by_id()
    order_map = get_walk_order_index_map()

    def _nap_for_sid(sid: str) -> str:
        from_path = _extract_nap_id_from_path(_paths.get(sid, ""))
        if from_path:
            return from_path
        props = sl_props_by_id.get(sid) or {}
        return str(props.get("NAP #") or props.get("NAP Number") or "").strip()

    def _sort_key(sid: str):
        idx = order_map.get(sid)
        if idx is not None:
            return (0, idx, sid)
        return (1, _nap_numeric(_nap_for_sid(sid)), sid)

    return sorted(sids, key=_sort_key)
