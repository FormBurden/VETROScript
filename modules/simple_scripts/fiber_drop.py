# modules/simple_scripts/fiber_drop.py
# All rules and utilities for fiber drops.

import logging
import glob
import json
import re
import modules.config
from modules.basic.distance_utils import haversine, THRESHOLD_M
from modules.basic.fiber_colors import FIBER_COLORS
from modules.basic.log_configs import log_issue_header

logger = logging.getLogger(__name__)

# INFO one-shot guards to avoid duplicate INFO outputs if functions run multiple times
_INFO_COLOR_EMITTED = False
_INFO_MISSING_EMITTED = False

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

def _sym(ok):
    """
    Return a colored 'button' for pass/fail that renders in plain-text logs.
    True  -> green check button
    False -> red square + X
    None  -> neutral bullet
    """
    if ok is True:
        return "✅"      # green box with check
    if ok is False:
        return "❌"     # red box with X
    return "•"

def _color_emoji(name: str) -> str:
    """
    Return just a color glyph (no name) so logs read like:
      drop=🟧; splice=[🟧, 🟦]
    """
    m = {
        "Blue": "🟦",
        "Orange": "🟧",
        "Green": "🟩",
        "Brown": "🟫",
        "Slate": "◼️",
        "White": "⬜",
        "Red": "🟥",
        "Black": "⬛",
        "Yellow": "🟨",
        "Violet": "🟪",
        "Rose": "🩷",
        "Aqua": "💧",
    }
    return m.get(name, "◻️")


def _color_ansi(name: str) -> str:
    """
    256-color background with contrasting foreground; resets at end.
    """
    # bg is 48;5;N, fg is 38;5;N
    palette = {
        "Blue":   (231, 21),   # white on deep blue
        "Orange": (16, 208),   # black on orange
        "Green":  (16, 34),    # black on green
        "Brown":  (231, 94),   # white on brown
        "Slate":  (231, 240),  # white on gray
        "White":  (16, 15),    # black on white
        "Red":    (231, 196),  # white on red
        "Black":  (231, 0),    # white on black
        "Yellow": (16, 226),   # black on yellow
        "Violet": (231, 93),   # white on violet
        "Rose":   (16, 213),   # black on pink
        "Aqua":   (16, 51),    # black on cyan
    }
    fg, bg = palette.get(name, (231, 240))
    return f"\x1b[38;5;{fg};48;5;{bg}m{name}\x1b[0m"

def _colorize(name: str) -> str:
    mode = str(getattr(modules.config, "LOG_COLOR_MODE", "EMOJI")).upper()
    if mode == "OFF":
        return name
    if mode == "ANSI":
        return _color_ansi(name)
    return _color_emoji(name)  # EMOJI (default)

# Internal one-shot guards to avoid duplicate debug output when functions are called multiple times
_DEBUG_COLOR_EMITTED = False
_DEBUG_MISSING_EMITTED = False

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

def _splice_to_color(splice: str) -> str:
    """
    Convert a splice-color notation "X.Y" into the actual fiber color name,
    ignoring the leading X and using Y as a 1-based index into FIBER_COLORS.
    """
    m = re.match(r'^\d+\.(\d+)$', splice)
    if m:
        idx = int(m.group(1))
        if 1 <= idx <= len(FIBER_COLORS):
            return FIBER_COLORS[idx-1]
    return splice

def _normalize_splice_to_colors(raw_splice: str) -> set[str]:
    """
    Parse a 'Splice Colors' field into a set of canonical color names.
    - Accepts entries like '1.1, 1.2, 1.12' and normalizes to names using FIBER_COLORS.
    - Also strips any leading index label '5 - Slate' → 'Slate' before mapping.
    """
    tokens = [t.strip() for t in (raw_splice or '').split(',') if t.strip()]
    return { _splice_to_color(_normalize_color(t)) for t in tokens }


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
    """
    [Drop Issues] Color mismatches — same deep-walk ordering as svc-attr checks.

    Emits:
      • Top section (if LOG_DETAIL == "INFO" and emit_info=True):
          one line per SL: ✅ or ❌
      • Bottom recap (errors-only) with header and end-header *only when emit_info=True*:
          lines start with: "[Drop Issues] ❌ SL #"

    Returns:
      list[str]: Service Location IDs where the drop color is not present in the
                 Service Location's Splice Colors.
    """
    # --- local helpers: emoji decoration (no external deps) ---
    def _emoji_for_color(name: str) -> str:
        """Map canonical fiber color names to emojis."""
        n = (name or "").strip().lower()
        # Canonical order reference:
        # 1–Blue, 2–Orange, 3–Green, 4–Brown, 5–Slate, 6–White,
        # 7–Red, 8–Black, 9–Yellow, 10–Violet, 11–Rose, 12–Aqua
        mapping = {
            "blue":   "🟦",
            "orange": "🟧",
            "green":  "🟩",
            "brown":  "🟫",
            "slate":  "◼️",   # dark gray (distinct from pure black)
            "white":  "⬜",
            "red":    "🟥",
            "black":  "⬛",
            "yellow": "🟨",
            "violet": "🟪",
            "rose":   "🩷",   # pink heart (closest portable pink)
            "aqua":   "💧",   # blue diamond to differentiate from Blue
        }
        return mapping.get(n, name or "")

    def _decorate_color(name: str) -> str:
        """Return emoji for color when LOG_COLOR_MODE='EMOJI', else the plain name."""
        mode = str(getattr(modules.config, "LOG_COLOR_MODE", "OFF")).upper()
        if mode == "EMOJI":
            return _emoji_for_color(name)
        return name

    from modules.hard_scripts.distribution_walker import get_walk_order_index_map
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

    service_raw = load_service_locations()   # [((lat, lon), splice_raw, sid)]
    drops_map   = load_fiber_drops()         # {(lat, lon): '2 - Orange', ...}

    order_map = get_walk_order_index_map()

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
    recap_rows: list[tuple[str, str, str, str]] = []  # (sl_num, sid, drop_c, splice_txt [and path optionally])
    next_ordinal = (max(order_map.values()) if order_map else 0) + 1

    for (pt, raw_splice, sid) in rows_sorted:
        # Skip SLs that have no drop point at all; those are handled by "Missing Service Location Drops"
        if pt not in drops_map:
            if do_debug:
                logger.debug(f"• [Drop] {sid}: no drop at {pt} (handled in Missing Drops)")
            continue

        raw_drop = str(drops_map.get(pt, "")).strip()
        drop_c = _normalize_color(raw_drop)  # e.g., '2 - Orange' -> 'Orange'
        splice_colors = _normalize_splice_to_colors(raw_splice)  # {'Orange', ...}

        is_match = bool(drop_c) and (drop_c in splice_colors)

        # --- decorate for logging (emoji if enabled) ---
        drop_disp = _decorate_color(drop_c) if drop_c else "(none)"
        splice_txt = "[" + ", ".join(_decorate_color(c) for c in sorted(splice_colors)) + "]"

        path_part = ""
        if show_path and _paths.get(sid):
            path_part = f" — path={_paths[sid]}"

        sl_num = order_map.get(sid)
        if sl_num is None:
            sl_num = next_ordinal
            next_ordinal += 1

        if do_info:
            if is_match:
                logger.info(f"[Drop Issues] ✅ SL # {sl_num}: {sid} — drop={drop_disp}; splice={splice_txt}{path_part}")
            else:
                logger.error(f"[Drop Issues] ❌ SL # {sl_num}: {sid} — drop={drop_disp} not in {splice_txt}{path_part}")

        if not is_match:
            mismatches.append(sid)
            if show_path and _paths.get(sid):
                recap_rows.append((str(sl_num), sid, (drop_disp), f"{splice_txt} | {_paths[sid]}"))
            else:
                recap_rows.append((str(sl_num), sid, (drop_disp), splice_txt))

    if do_info:
        logger.info("==== End [Drop Issues] Color Mismatches (svc-attr ordering) ====")

    # Errors-only recap at the bottom of [Drop Issues] — gated by emit_info so stats pass won't duplicate it
    if emit_info and recap_rows:
        # Header
        if show_path:
            logger.info("===== Drop Issues (%d) =====", len(recap_rows))
            logger.error("[Drop Issues] ❌ SL # | Service Location ID | Drop Color | SL Colors | Path")
        else:
            logger.info("===== Drop Issues (%d) =====", len(recap_rows))
            logger.error("[Drop Issues] ❌ SL # | Service Location ID | Drop Color | SL Colors")

        # Rows
        for sl_num, sid, dcol, sps in recap_rows:
            if show_path and " | " in sps:
                # sps already includes " | Path"
                logger.error("[Drop Issues] ❌ SL # %s: %s | %s | %s", sl_num, sid, dcol, sps)
            else:
                logger.error("[Drop Issues] ❌ SL # %s: %s | %s | %s", sl_num, sid, dcol, sps)

        # Footer
        logger.info("===== End Drop Issues =====")

    if do_debug:
        logger.debug(f"• [Drop] Color-mismatch count: {len(mismatches)}")

    return mismatches



# def find_color_mismatches(emit_info: bool = True) -> list[str]:
#     """
#     [Drop Issues] Color mismatches — same deep-walk ordering as svc-attr checks.

#     Emits:
#       • Top section (if LOG_DETAIL == "INFO" and emit_info=True):
#           one line per SL: ✅ or ❌
#       • Bottom recap (errors-only) with header and end-header *only when emit_info=True*:
#           lines start with: "[Drop Issues] ❌ SL #"

#     Returns:
#       list[str]: Service Location IDs where the drop color is not present in the
#                  Service Location's Splice Colors.
#     """
#     from modules.hard_scripts.distribution_walker import get_walk_order_index_map
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

#     service_raw = load_service_locations()   # [((lat, lon), splice_raw, sid)]
#     drops_map   = load_fiber_drops()         # {(lat, lon): '2 - Orange', ...}

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
#     recap_rows: list[tuple[str, str, str, str]] = []  # (sl_num, sid, drop_c, splice_txt [and path optionally])
#     next_ordinal = (max(order_map.values()) if order_map else 0) + 1

#     for (pt, raw_splice, sid) in rows_sorted:
#         # Skip SLs that have no drop point at all; those are handled by "Missing Service Location Drops"
#         if pt not in drops_map:
#             if do_debug:
#                 logger.debug(f"• [Drop] {sid}: no drop at {pt} (handled in Missing Drops)")
#             continue

#         raw_drop = str(drops_map.get(pt, "")).strip()
#         drop_c = _normalize_color(raw_drop)  # e.g., '2 - Orange' -> 'Orange'
#         splice_colors = _normalize_splice_to_colors(raw_splice)  # {'Orange', ...}

#         is_match = bool(drop_c) and (drop_c in splice_colors)

#         splice_txt = "[" + ", ".join(sorted(splice_colors)) + "]"
#         path_part = ""
#         if show_path and _paths.get(sid):
#             path_part = f" — path={_paths[sid]}"

#         sl_num = order_map.get(sid)
#         if sl_num is None:
#             sl_num = next_ordinal
#             next_ordinal += 1

#         if do_info:
#             if is_match:
#                 logger.info(f"[Drop Issues] ✅ SL # {sl_num}: {sid} — drop={drop_c}; splice={splice_txt}{path_part}")
#             else:
#                 logger.error(f"[Drop Issues] ❌ SL # {sl_num}: {sid} — drop={drop_c or '(none)'} not in {splice_txt}{path_part}")

#         if not is_match:
#             mismatches.append(sid)
#             if show_path and _paths.get(sid):
#                 recap_rows.append((str(sl_num), sid, (drop_c or "(none)"), f"{splice_txt} | {_paths[sid]}"))
#             else:
#                 recap_rows.append((str(sl_num), sid, (drop_c or "(none)"), splice_txt))

#     if do_info:
#         logger.info("==== End [Drop Issues] Color Mismatches (svc-attr ordering) ====")

#     # Errors-only recap at the bottom of [Drop Issues] — gated by emit_info so stats pass won't duplicate it
#     if emit_info and recap_rows:
#         # Header
#         if show_path:
#             logger.info("===== Drop Issues (%d) =====", len(recap_rows))
#             logger.error("[Drop Issues] ❌ SL # | Service Location ID | Drop Color | SL Colors | Path")
#         else:
#             logger.info("===== Drop Issues (%d) =====", len(recap_rows))
#             logger.error("[Drop Issues] ❌ SL # | Service Location ID | Drop Color | SL Colors")

#         # Rows
#         for sl_num, sid, dcol, sps in recap_rows:
#             if show_path and " | " in sps:
#                 # sps already includes " | Path"
#                 logger.error("[Drop Issues] ❌ SL # %s: %s | %s | %s", sl_num, sid, dcol, sps)
#             else:
#                 logger.error("[Drop Issues] ❌ SL # %s: %s | %s | %s", sl_num, sid, dcol, sps)

#         # Footer
#         logger.info("===== End Drop Issues =====")

#     if do_debug:
#         logger.debug(f"• [Drop] Color-mismatch count: {len(mismatches)}")

#     return mismatches


def find_missing_service_location_drops(service_coords=None, drop_coords=None, emit_info: bool = True):
    """
    [Drop Issues] Missing Service Location Drops — same ordering as
    [Check Service Location Attributes] (see find_color_mismatches docstring).
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

    # ---------------------------------------------------------------------
    # NEW: normalize inputs so callers can pass either raw loader outputs or
    # already-flattened coords without crashing haversine().
    #
    # service_coords may be:
    #   • [(lat, lon, svc_id), ...]
    #   • [((lat, lon), splice_colors, svc_id), ...]  (from load_service_locations())
    #
    # drop_coords may be:
    #   • [(lat, lon), ...]
    #   • {(lat, lon): color, ...}                     (from load_fiber_drops())
    # ---------------------------------------------------------------------
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
    # drop_coords:    list of (lat, lon)
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

        path_part = ""
        if show_path and _paths.get(sid):
            path_part = f" — path={_paths[sid]}"

        # Flagged problems must log at ERROR regardless of LOG_DETAIL
        if do_info:
            logger.error("[Drop Issues] ❌ SL # %s: %s — no drop within %.2f m%s",
                         sl_num, sid, THRESHOLD_M, path_part)

        head_line = f"SL # {sl_num}: {sid} — no drop within {THRESHOLD_M:.2f} m"
        if show_path and _paths.get(sid):
            head_line += f" — path={_paths[sid]}"
        header_lines.append(head_line)

    if header_lines:
        # This banner stays at its configured level inside log_issue_header();
        # individual issue rows above are already ERROR.
        log_issue_header("[Drop Issues] Missing Drops", header_lines, logger=logger)

    if do_info:
        logger.info("==== End [Drop Issues] Missing Service Location Drops (svc-attr ordering) ====")

    if do_debug:
        logger.debug(f"• [Drop] Missing-drop count: {len(missing_sorted)}")

    return [sid for (_lat, _lon, sid) in missing_sorted]

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
