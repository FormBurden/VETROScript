import glob
import json
import logging
import re
import modules.config

from modules.basic.distance_utils import haversine, THRESHOLD_M
from modules.simple_scripts.geojson_loader import load_t3_vaults, load_features
from modules.simple_scripts.nids import load_service_locations
from modules.simple_scripts.nap_rules import load_nap_specs
from modules.simple_scripts.nids import load_drops
from modules.basic.fiber_colors import FIBER_COLORS
from modules.basic.log_configs import log_abbrev_header

logger = logging.getLogger(__name__)

def _color_emoji(name: str) -> str:
    """Return a plain emoji square for a fiber color name."""
    mapping = {
        "Blue": "🟦", "Orange": "🟧", "Green": "🟩", "Brown": "🟫",
        "Slate": "◼️", "White": "⬜", "Red": "🟥", "Black": "⬛",
        "Yellow": "🟨", "Violet": "🟪", "Rose": "🩷", "Aqua": "💧",
    }
    return mapping.get(name, "◻️")

def _colorize(name: str) -> str:
    """
    Colorize a fiber color per config:
      • EMOJI → return emoji square
      • ANSI/OFF → return the plain name (no ANSI here for file-log cleanliness)
    """
    mode = str(getattr(modules.config, "LOG_COLOR_MODE", "OFF")).upper()
    if mode == "EMOJI":
        return _color_emoji(name)
    return name

def emit(msg: str, *args, **kwargs):
    """Level-aware emitter: DEBUG when LOG_DETAIL='DEBUG', else INFO."""
    detail = str(getattr(modules.config, "LOG_DETAIL", "DEBUG")).upper()
    (logger.debug if detail == "DEBUG" else logger.info)(msg, *args, **kwargs)

def _iter_service_locations(svc_locs):
    """
    Yield (svc_id, (lat, lon), splice_raw) from either:
      • dict mapping id -> {point:(lat,lon), splice...}, or
      • list of tuples (lat, lon, loose, splice, svc_id) as returned by nids.load_service_locations()
    """
    # dict-like
    if isinstance(svc_locs, dict):
        for sid, sdata in svc_locs.items():
            if not isinstance(sdata, dict):
                continue
            pt = sdata.get("point") or sdata.get("coords")
            splice = (
                sdata.get("splice_colors")
                or sdata.get("Splice Colors")
                or sdata.get("splice code")
                or sdata.get("splice")
                or ""
            )
            if pt and isinstance(pt, (list, tuple)) and len(pt) == 2:
                yield sid, (pt[0], pt[1]), splice
        return

    # list/tuple-like (nids.load_service_locations format)
    for itm in (svc_locs or []):
        if isinstance(itm, (list, tuple)) and len(itm) >= 5:
            lat, lon, _loose, splice, sid = itm[:5]
            yield sid, (lat, lon), (splice or "")

def _expected_colors_from_nap_meta(nap_id: str, nap_spec: dict | None) -> list[str]:
    """
    Compute expected drop colors at a NAP for the *pre-walk* phase.

    NEW rule for rare cases:
      If a NAP has a Tie Point and its title includes Loose Tube groups before
      " / Tie Point", then expected-at-NAP is the UNION of all indices that
      appear to the LEFT of " / Tie Point" (e.g., "BLT, 12 / OLT, 1-2").
      That covers two-LT + tie-point titles like:
        "... 48ct BLT, 12 / OLT, 1-2 / Tie Point 48ct 15-20 to 24ct 15-20"
      → expected = [12, 1-2] → Aqua, Blue, Orange.

    Otherwise:
      • If tube_specs exist (no Tie Point case), union all their indices.
      • If tube_specs are missing, parse both sides from the ID.
      • If Tie Point exists but we cannot parse left-of Tie Point indices,
        fall back to the legacy “left-hand indices” parse.

    Color mapping follows the canonical 12-color order (1–Blue … 12–Aqua).
    """
    from modules.basic.fiber_colors import FIBER_COLORS
    import re

    indices: list[int] = []

    # Detect tie point from spec or ID text
    has_tp = bool(nap_spec and (nap_spec.get("tie_points") or []))
    inside = ""
    if nap_id and "(" in nap_id:
        inside = nap_id.split("(", 1)[1].rstrip(")")
        if "tie point" in inside.lower():
            has_tp = True

    # Tie Point present AND we can see the " / Tie Point" delimiter in the title:
    # take the UNION of all LT indices to the LEFT of that delimiter.
    if has_tp and inside:
        parts = [p.strip() for p in inside.split("/")]
        left_parts = []
        for seg in parts:
            if seg.lower().startswith("tie point"):
                break
            left_parts.append(seg)

        # Extract numeric indices from each left-side segment (prefer after last comma)
        for part in left_parts:
            tail = part.rsplit(",", 1)[-1]
            for m in re.finditer(r"(\d+\s*-\s*\d+|\d+)", tail):
                token = m.group(0)
                end = m.end()
                rest = tail[end:].lstrip().lower()
                # skip fiber counts ("24ct") and any "Units" numbers
                if rest.startswith("ct") or rest.startswith("unit"):
                    continue
                if "-" in token:
                    a, b = [int(x) for x in token.split("-", 1)]
                    indices.extend(range(min(a, b), max(a, b) + 1))
                else:
                    i = int(token)
                    if i >= 1:
                        indices.append(i)

        if indices:
            # Dedup → colors
            seen = set()
            colors: list[str] = []
            for i in indices:
                c = FIBER_COLORS[(i - 1) % len(FIBER_COLORS)]
                if c not in seen:
                    seen.add(c)
                    colors.append(c)
            return colors

        # Fallback if nothing parsed to the left of Tie Point:
        # legacy behavior = "left-hand indices" (first indices block in title)
        return _parse_expected_from_nap_id(nap_id) or []

    # --- No Tie Point (or no parentheses to parse): union tube_specs or parse both sides ---
    if nap_spec and (nap_spec.get("tube_specs") or []):
        for _abbr, idxs in (nap_spec.get("tube_specs") or []):
            for i in (idxs or []):
                if isinstance(i, int) and i >= 1:
                    indices.append(i)

    if not indices and inside:
        # Parse both sides from the parentheses text
        for part in inside.split("/"):
            tail = part.rsplit(",", 1)[-1]
            for m in re.finditer(r"(\d+\s*-\s*\d+|\d+)", tail):
                token = m.group(0)
                end = m.end()
                rest = tail[end:].lstrip().lower()
                if rest.startswith("ct") or rest.startswith("unit"):
                    continue
                if "-" in token:
                    a, b = [int(x) for x in token.split("-", 1)]
                    indices.extend(range(min(a, b), max(a, b) + 1))
                else:
                    i = int(token)
                    if i >= 1:
                        indices.append(i)

    # Deduplicate preserving order → map to colors
    seen = set()
    colors: list[str] = []
    for i in indices:
        c = FIBER_COLORS[(i - 1) % len(FIBER_COLORS)]
        if c not in seen:
            seen.add(c)
            colors.append(c)
    return colors


def _compress_indices(idxs: list[int]) -> str:
    """
    Compress a list of 1-based indices into human-readable ranges:
      [1,2,3,5,7,8] -> "1-3, 5, 7-8"
    """
    if not idxs:
        return ""
    s = sorted(set(int(i) for i in idxs if isinstance(i, int) and i > 0))
    out = []
    a = b = s[0]
    for x in s[1:]:
        if x == b + 1:
            b = x
        else:
            out.append(f"{a}-{b}" if a != b else f"{a}")
            a = b = x
    out.append(f"{a}-{b}" if a != b else f"{a}")
    return ", ".join(out)



# ---------------------------
# Loading & normalization
# ---------------------------

def _normalize_color(c: str | None) -> str | None:
    """Accept '5 - Slate', 'Slate', '5', etc. Return canonical color or None."""
    if not c:
        return None
    s = str(c).strip()
    # split "5 - Slate"
    if "-" in s:
        left, right = [x.strip() for x in s.split("-", 1)]
        # prefer the name if it matches
        if right in FIBER_COLORS:
            return right
        s = left
    # digits?
    if s.isdigit():
        idx = int(s)
        if 1 <= idx <= len(FIBER_COLORS):
            return FIBER_COLORS[idx - 1]
    # already a color name?
    if s in FIBER_COLORS:
        return s
    # capitalize first letter fallback (if they typed 'slate')
    cap = s.capitalize()
    if cap in FIBER_COLORS:
        return cap
    return s  # last resort: return as-is

def _parse_expected_from_nap_id(nap_id: str) -> list[str]:
    """
    From '04.AC01.HAR.N2 (24ct, 2-4)' -> ['Orange','Green','Brown'].
    Handles single 'k', range 'i-j', or 'list like 2,4,6'.
    """
    if not nap_id or "(" not in nap_id:
        return []
    try:
        inside = nap_id.split("(", 1)[1].rstrip(")")
        # e.g. '24ct, 2-4' or '48ct, 1' or '24ct, 2,4,6'
        parts = [p.strip() for p in inside.split(",")]
        if len(parts) < 2:
            return []
        idxs_str = parts[1].split("/", 1)[0].strip()
        idxs: list[int] = []
        if "-" in idxs_str:
            a, b = [int(x) for x in idxs_str.split("-", 1)]
            idxs = list(range(a, b + 1))
        else:
            # could be "2" or "2,4,6"
            idxs = [int(x.strip()) for x in idxs_str.split() if x.strip().isdigit()]
            if not idxs:  # try comma split
                idxs = [int(x.strip()) for x in idxs_str.split(",") if x.strip().isdigit()]
        colors: list[str] = []
        seen = set()
        for i in idxs:
            if i <= 0:
                continue
            col = FIBER_COLORS[(i - 1) % len(FIBER_COLORS)]
            if col not in seen:
                seen.add(col)
                colors.append(col)
        return colors
    except Exception:
        return []

def _parse_svc_splice_colors(value: str | None) -> list[str]:
    """
    Accepts:
    - names: 'Red, Green'
    - indices: '5' or '5,7'
    - dot codes: '1.3, 1.4' (take suffix -> 3,4 -> colors)
    Returns canonical color names.
    """
    if not value:
        return []
    parts = [p.strip() for p in str(value).split(",") if p.strip()]
    out: list[str] = []
    seen = set()
    for p in parts:
        c = None
        if "." in p:
            # '1.3' -> 3
            suf = p.rsplit(".", 1)[-1]
            if suf.isdigit():
                idx = int(suf)
                if idx > 0:
                    c = FIBER_COLORS[(idx - 1) % len(FIBER_COLORS)]
        elif p.isdigit():
            c = _normalize_color(p)
        else:
            c = _normalize_color(p)
        if c and c in FIBER_COLORS and c not in seen:
            seen.add(c)
            out.append(c)
    return out

def _expected_colors_for_branch(nap_spec, fiber_indices):
    """
    Compute the expected splice color(s) at this NAP for the current branch.

    Behavior:
      - If the NAP is a tie-point and any of our branch indices map to right-hand
        indices (e.g., “… / OLT, 1-2”), prefer those right-hand indices.
      - Otherwise, use the branch's own indices as-is.

    This aligns with the project fiber table (1–Blue, 2–Orange, …, 12–Aqua) and
    prevents false “Drop color not expected at NAP” when titles include two
    loose-tube segments like “BLT, 12 / OLT, 1-2”.
    """
    from modules.basic.fiber_colors import FIBER_COLORS  # canonical order

    # Start with the branch's left-hand indices
    indices = list(fiber_indices or [])

    # If tie-points exist, try mapping our indices to the right-hand side
    mapped_right = set()
    if nap_spec and nap_spec.get("tie_points"):
        for tp in nap_spec["tie_points"]:
            mapped = _map_indices_to_tie_point(indices, nap_spec, tp) or []
            for i in mapped:
                if isinstance(i, int) and i >= 1:
                    mapped_right.add(i)

    # Prefer right-hand mapping if we found any
    if mapped_right:
        indices = sorted(mapped_right)

    # Convert indices → color names via the 1-based fiber table
    seen = set()
    colors = []
    for i in indices:
        if isinstance(i, int) and i >= 1:
            c = FIBER_COLORS[(i - 1) % len(FIBER_COLORS)]
            if c not in seen:
                colors.append(c)
                seen.add(c)
    return colors

def _map_indices_to_tie_point(fiber_indices, nap_spec, tp):
    """
    Given current branch indices (left side) and a tie-point spec:
      left_range (L0..L1) maps positionally to right_range (R0..R1).
    We return the child indices on the right side that correspond to the
    subset of left indices we are currently walking.
    """
    L0, L1 = tp.get("left_range", (0, -1))
    R0, R1 = tp.get("right_range", (0, -1))
    right_seq = list(range(R0, R1 + 1))

    mapped: list[int] = []
    for i in (fiber_indices or []):
        if L0 <= i <= L1:
            k = i - L0
            if 0 <= k < len(right_seq):
                mapped.append(right_seq[k])

    # de-dup, keep order
    out: list[int] = []
    seen = set()
    for i in mapped:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out

def load_all_distributions():
    """
    Returns dict: dist_id → list of segments (each a list of [lon, lat] points),
    including both aerial and underground.
    """
    mapping = {}
    for pattern in ('fiber-distribution-aerial*.geojson', 'fiber-distribution-underground*.geojson'):
        for fn in glob.glob(f'{modules.config.DATA_DIR}/{pattern}'):
            with open(fn, encoding='utf-8') as f:
                gj = json.load(f)
            for feat in gj.get('features', []):
                props = feat.get('properties', {}) or {}
                dist_id = props.get('ID')
                geom = feat.get('geometry', {}) or {}
                coords = geom.get('coordinates', []) or []
                typ = geom.get('type')
                if not dist_id or not coords:
                    continue
                if typ == 'LineString':
                    mapping.setdefault(dist_id, []).append(coords)
                elif typ == 'MultiLineString':
                    for seg in coords:
                        mapping.setdefault(dist_id, []).append(seg)
    return mapping

def normalize_pt(pt):
    if isinstance(pt, tuple):
        return (round(pt[0], 6), round(pt[1], 6))
    lon, lat = pt
    return (round(lat, 6), round(lon, 6))

def collect_drops_by_pt():
    """
    Returns dict: (lat, lon) → [color,...] for all points in all drops.
    """
    raw_segments = load_drops()
    drops_by_pt = {}
    for verts, color, _ in raw_segments:
        for lat, lon in verts:
            key = (round(lat, 6), round(lon, 6))
            drops_by_pt.setdefault(key, []).append(color)
    return drops_by_pt

# ---------------------------
# Helpers: NAP proximity & DF resolution
# ---------------------------

def find_nearby_nap(pt, nap_coords, nap_map):
    """
    Given a point (lat,lon), return (nap_id, nap_pt) if any nap is within THRESHOLD_M.
    Else return (None, None).
    """
    plat, plon = pt
    for nlat, nlon in nap_coords:
        if haversine(plat, plon, nlat, nlon) <= THRESHOLD_M:
            return nap_map.get((nlat, nlon)), (nlat, nlon)
    return None, None

def _df_letter(dist_id):
    """
    Extract trailing letter from final token (e.g., '24B' -> 'B'), or '' if none.
    """
    tail = dist_id.split('.')[-1]
    m = re.match(r'^\d+(?P<letter>[A-Za-z])$', tail)
    return m.group('letter').upper() if m else ''

def resolve_child_distribution(parent_dist_id, nap_pt, target_ct, dist_map, used_branches_for_nap):
    """
    From a parent like '04.AC01.HAR.DF1.48A', find a child DF that:
      • starts with parent prefix '04.AC01.HAR.DF1'
      • has '.{target_ct}{Letter}' tail (e.g., '.24A', '.24B', ...)
      • has at least one segment vertex within THRESHOLD_M of nap_pt
      • has a letter not yet used at this NAP (A, then B, then C…)
    Returns the chosen child dist_id or None.
    """
    parts = parent_dist_id.split('.')
    if len(parts) < 5:
        return None
    # parent prefix through DFx (drop the last token '48A')
    parent_prefix = '.'.join(parts[:-1])  # e.g., '04.AC01.HAR.DF1'

    # collect candidates of pattern: <prefix>.<target_ct><LETTER>
    candidates = []
    for did in dist_map.keys():
        if not did.startswith(parent_prefix + f'.{target_ct}'):
            continue
        # must end with something like '24A', '24B', etc.
        tail = did.split('.')[-1]
        if not re.match(rf'^{target_ct}[A-Za-z]$', tail):
            continue
        # Filter by physical touch at NAP
        segs = dist_map.get(did, [])
        touches = False
        for seg in segs:
            for p in seg:
                plat, plon = normalize_pt(p)
                if haversine(plat, plon, nap_pt[0], nap_pt[1]) <= THRESHOLD_M:
                    touches = True
                    break
            if touches:
                break
        if touches:
            candidates.append(did)

    if not candidates:
        return None

    # sort by letter A, B, C ...
    candidates.sort(key=lambda x: _df_letter(x))

    # choose the first whose letter is not used
    for did in candidates:
        letter = _df_letter(did)
        if letter and letter not in used_branches_for_nap:
            used_branches_for_nap.add(letter)
            return did

    # if all are used (unexpected), just return the first
    return candidates[0]

# ---------------------------
# Core walker
# ---------------------------

def walk_distribution_branch(
    dist_map, nap_coords, nap_map, nap_specs, drops_by_pt, svc_locs, drops,
    dist_id, fiber_indices, path_so_far, issues, walked_dists, depth=0, evt_cb=None
):
    """
    Core DF walker. Emits per-DROP and per-SVC lines using:
      • DEBUG level when LOG_DETAIL == 'DEBUG' (verbose, includes far_end)
      • INFO  level when LOG_DETAIL == 'INFO'  (condensed)

    Also logs internal traversal details (branching, expected/found, etc.) at DEBUG only.
    """
    pad = "  " * depth

    segments = dist_map.get(dist_id, [])
    if not segments:
        logger.error(f"{pad}  [WARN] Distribution {dist_id} not found in GeoJSON!")
        issues.append({
            "path": " → ".join(str(p) for p in path_so_far if p is not None),
            "dist_id": dist_id,
            "issue": "Distribution not found"
        })
        return

    for seg in segments:
        # ... (unchanged traversal up to NAP checks)

        for nap_id in seg.get("naps", []):
            nap_pt = nap_map.get(nap_id)
            if not nap_pt:
                continue

            nap_spec = nap_specs.get(nap_id)
            if not nap_spec:
                logger.error(f"{pad}  [WARN] NAP {nap_id} missing specs!")
                issues.append({
                    "path": " → ".join(str(p) for p in path_so_far + [nap_id] if p is not None),
                    "nap_id": nap_id,
                    "issue": "NAP missing specs"
                })
                continue

            expected_colors = _expected_colors_for_branch(nap_spec, fiber_indices)
            found_drops = []
            for (dlat, dlon), data in drops_by_pt.items():
                if haversine(nap_pt[0], nap_pt[1], dlat, dlon) <= THRESHOLD_M:
                    norm_col = _normalize_color(data["color"])
                    found_drops.append({
                        "drop_id": data["drop_id"],
                        "color": norm_col,
                        "distance_m": round(haversine(nap_pt[0], nap_pt[1], dlat, dlon), 3)
                    })

            # Internal traversal chatter → DEBUG
            logger.debug(f"{pad}  NAP {nap_id} expected {expected_colors}, found drops {found_drops}")

            # Missing colors for this branch at this NAP
            found_colors = [d["color"] for d in found_drops]
            missing = [c for c in expected_colors if c not in found_colors]
            if missing:
                logger.error(f"{pad}    [ISSUE] NAP {nap_id}: missing colors {missing}")
                issues.append({
                    "path": " → ".join(str(p) for p in path_so_far + [nap_id] if p is not None),
                    "nap_id": nap_id,
                    "expected_colors": expected_colors,
                    "found_drops": found_drops,
                    "missing_colors": missing,
                    "issue": "Missing drop colors at NAP"
                })

            # For each DROP touching this NAP, validate color belongs to branch
            for data in found_drops:
                drop_color = data["color"]

                # Issue: DROP color not expected for THIS branch at this NAP
                if drop_color not in expected_colors:
                    logger.error(f"{pad}    [ISSUE] NAP {nap_id}: drop={drop_color} not in expected {expected_colors}")
                    issues.append({
                        "path": " → ".join(str(p) for p in path_so_far + [nap_id] if p is not None),
                        "nap_id": nap_id,
                        "expected_colors": expected_colors,
                        "found_drop_color": drop_color,
                        "issue": "Drop color not expected at NAP"
                    })

                # ... (unchanged code for SVC parsing)
                svc_id   = data.get("svc_id")
                splice   = data.get("splice", "")
                if svc_id:
                    svc_colors = _parse_svc_splice_colors(splice)

                    # Issue: SVC splice mismatch vs drop color
                    if drop_color and svc_colors and drop_color not in svc_colors:
                        logger.error(f"{pad}    [ISSUE] {svc_id}: drop={drop_color} not in SvcLoc splice {svc_colors}")
                        issues.append({
                            "path": " → ".join(str(p) for p in path_so_far + [nap_id, svc_id] if p is not None),
                            "nap_id": nap_id,
                            "svc_id": svc_id,
                            "drop_color": drop_color,
                            "svc_colors": svc_colors,
                            "issue": "SVC splice mismatch"
                        })

                # Emit user-facing lines as before (DEBUG/INFO for non-issue telemetry)
                tag = f"[{dist_id}] "
                show_debug = (str(getattr(modules.config, "LOG_DETAIL", "DEBUG")).upper() == "DEBUG")
                if "drop_id" in data:
                    meta_str = f" ( {nap_id_meta(nap_spec)} )" if nap_spec else ""
                    msg_with_far = (
                        f"{tag}✅  DROP {data['drop_id']}: color={data['color']} "
                        f"touches {nap_id}{meta_str}; far_end={data['far_end']}"
                    )
                    msg_no_far = (
                        f"{tag}✅  DROP {data['drop_id']}: color={data['color']} "
                        f"touches {nap_id}{meta_str}"
                    )
                    if show_debug:
                        logger.debug(msg_with_far)
                    else:
                        logger.info(msg_no_far)
                else:
                    # SVC row
                    if str(getattr(modules.config, "LOG_COLOR_MODE", "OFF")).upper() == "EMOJI":
                        color_list = "[" + ", ".join(_colorize(c) for c in data["svc_colors"]) + "]"
                        splice_show = _colorize(_normalize_color(data["splice_raw"]))
                    else:
                        color_list = "[" + ", ".join(data["svc_colors"]) + "]"
                        splice_show = _normalize_color(data["splice_raw"])
                    msg = f"{tag}    SVC {data['svc_id']}: splice={splice_show} → colors={color_list}"
                    if show_debug:
                        logger.debug(msg)
                    else:
                        logger.info(msg)

            # ── Recurse via tie-points (new dict shape from nap_rules.py)
            tie_points = nap_spec.get("tie_points", []) or []
            if not tie_points:
                logger.debug(f"{pad}  NAP {nap_id}: No tie-points. Continuing down {dist_id}.")
            else:
                used_branches_for_nap = set()
                for tp in tie_points:
                    target_ct = int(tp.get("right_ct", 0) or 0)
                    idxs = _map_indices_to_tie_point(fiber_indices, nap_spec, tp)
                    child_id = resolve_child_distribution(dist_id, nap_pt, target_ct, dist_map, used_branches_for_nap)
                    if child_id:
                        if child_id not in walked_dists:
                            walked_dists.add(child_id)
                            logger.debug(f"{pad}  Tie-point at {nap_id}: branching to {child_id} with fibers {idxs}")
                            walk_distribution_branch(
                                dist_map, nap_coords, nap_map, nap_specs, drops_by_pt, svc_locs, drops,
                                child_id, idxs, path_so_far + [nap_id], issues, walked_dists, depth+1, evt_cb
                            )
                            logger.debug(f"{pad}  Finished branch {child_id}, returning to {dist_id}")
                        else:
                            logger.debug(f"{pad}  Skipping already-walked branch {child_id}")
                    else:
                        logger.warning(f"{pad}  [WARN] At {nap_id}: could not resolve a child DF for {target_ct}ct with fibers {idxs}")

    logger.debug(f"{pad}Finished walking {dist_id} branch.")

def collect_service_locations_in_path_order() -> list[str]:
    """
    Walk T3 → DF → NAPs and return Service Location IDs in *exact traversal order*.
    Duplicates suppressed (first-seen wins).
    """
    dist_map = load_all_distributions()
    t3_coords, t3_map = load_t3_vaults()
    nap_coords, nap_map = load_features('nap', modules.config.ID_COL)
    nap_specs = load_nap_specs()
    drops_by_pt = collect_drops_by_pt()
    # NOTE: use the NIDs loader to avoid circular imports
    from modules.simple_scripts.nids import load_service_locations as _load_svcs
    svc_locs = _load_svcs()
    drops = load_drops()

    ordered: list[str] = []
    seen: set[str] = set()

    def _evt_cb(kind: str, data: dict):
        if kind == "SVC":
            sid = (data.get("svc_id") or "").strip()
            if sid and sid not in seen:
                seen.add(sid)
                ordered.append(sid)

    # Helper to normalize points
    def _norm(pt):
        lat, lon = pt
        return (round(lat, 6), round(lon, 6))

    # Start at each T3, find its DF?.48A trunk that touches it, and walk
    for t3_pt in t3_coords:
        vault_id = t3_map.get(t3_pt)
        if not vault_id:
            continue

        for dist_id, segments in dist_map.items():
            # Only the primary DF trunk from this T3
            if not re.match(rf"^{re.escape(vault_id)}\.DF\d+\.48A$", dist_id):
                continue

            # Must physically touch the T3
            touches = False
            for seg in segments:
                ep0 = _norm(normalize_pt(seg[0]))
                ep1 = _norm(normalize_pt(seg[-1]))
                if (
                    haversine(ep0[0], ep0[1], t3_pt[0], t3_pt[1]) <= THRESHOLD_M
                    or haversine(ep1[0], ep1[1], t3_pt[0], t3_pt[1]) <= THRESHOLD_M
                ):
                    touches = True
                    break
            if not touches:
                continue

            # IMPORTANT: start with the same 12 tracked colors the deep walker uses
            walk_distribution_branch(
                dist_map, nap_coords, nap_map, nap_specs, drops_by_pt, svc_locs, drops,
                dist_id, list(range(1, 13)),
                [vault_id, dist_id],
                issues=[], walked_dists=set(), depth=0, evt_cb=_evt_cb
            )

    return ordered

def get_walk_order_index_map() -> dict[str, int]:
    """
    Returns {svc_id: ordinal_index} in the exact walk order,
    starting at 1. Use this to print 'SL # N' consistently across modules.
    """
    ids = collect_service_locations_in_path_order()
    return {sid: i for i, sid in enumerate(ids, start=1)}

def get_walk_paths_map() -> dict[str, str]:
    """
    Returns {svc_id: path_string} using the same T3 → DF → NAP traversal
    as collect_service_locations_in_path_order(). The path string matches
    what the deep walker uses in its 'path' fields.
    """
    dist_map = load_all_distributions()
    t3_coords, t3_map = load_t3_vaults()
    nap_coords, nap_map = load_features('nap', modules.config.ID_COL)
    nap_specs = load_nap_specs()
    drops_by_pt = collect_drops_by_pt()
    from modules.simple_scripts.nids import load_service_locations as _load_svcs
    svc_locs = _load_svcs()
    drops = load_drops()

    path_by_id: dict[str, str] = {}
    seen: set[str] = set()

    def _evt_cb(kind: str, data: dict):
        if kind == "SVC":
            sid = (data.get("svc_id") or "").strip()
            if sid and sid not in seen:
                seen.add(sid)
                path_by_id[sid] = data.get("path", "")

    def _norm(pt):
        lat, lon = pt
        return (round(lat, 6), round(lon, 6))

    for t3_pt in t3_coords:
        vault_id = t3_map.get(t3_pt)
        if not vault_id:
            continue

        for dist_id, segments in dist_map.items():
            if not re.match(rf"^{re.escape(vault_id)}\.DF\d+\.48A$", dist_id):
                continue

            touches = False
            for seg in segments:
                ep0 = _norm(normalize_pt(seg[0]))
                ep1 = _norm(normalize_pt(seg[-1]))
                if (
                    haversine(ep0[0], ep0[1], t3_pt[0], t3_pt[1]) <= THRESHOLD_M
                    or haversine(ep1[0], ep1[1], t3_pt[0], t3_pt[1]) <= THRESHOLD_M
                ):
                    touches = True
                    break
            if not touches:
                continue

            # IMPORTANT: start with the same 12 tracked colors as the deep walker
            walk_distribution_branch(
                dist_map, nap_coords, nap_map, nap_specs, drops_by_pt, svc_locs, drops,
                dist_id, list(range(1, 13)),
                [vault_id, dist_id],
                issues=[], walked_dists=set(), depth=0, evt_cb=_evt_cb
            )

    return path_by_id


# ---------------------------
# Entry point
# ---------------------------

def find_deep_distribution_mismatches():
    """
    Runs the deep distribution walk and also emits the condensed/expanded
    DROP/SVC listing *after* the '==== END NAP Tie-Points Dump ====' section.

    New in this revision:
      • DROP lines include a leading ✅ and a tag "[Distribution and NAP Walker]"
      • After the NAP ID we print: ( <FiberCount>ct, <fiber#> / Tie Point … / … )
        where <fiber#> is the index for the DROP color in standard order.
      • Prints a configurable 5-line abbreviations header at the top of the walk.
    """
    # logger.info(">>> Starting deep distribution walk")
    emit(">>> Starting deep distribution walk")

    global FIBER_COLORS

    detail = str(getattr(modules.config, "LOG_DETAIL", "DEBUG")).upper()
    show_debug_style = (detail == "DEBUG")

    dist_map = load_all_distributions()
    t3_coords, t3_map = load_t3_vaults()
    nap_coords, nap_map = load_features('nap', modules.config.ID_COL)
    nap_specs = load_nap_specs()
    drops_by_pt = collect_drops_by_pt()
    drops = load_drops()  # [(verts[(lat,lon)…], color, drop_id), …]
    svc_locs = load_service_locations()
    issues = []
    walked_dists = set()

    def nap_num_key(napid):
        if isinstance(napid, str):
            m = re.search(r'\.N(\d+)', napid)
            if m:
                return int(m.group(1))
        return float('inf')

    def natural_key(s: str):
        parts = re.split(r'(\d+)', s)
        return [int(p) if p.isdigit() else p.lower() for p in parts]

    # --- NAP Tie-Points dump (sorted) ---
    if getattr(modules.config, "LOG_NAP_TIEPOINTS", True):
        emit("==== NAP Tie-Points Dump ====")
        for napid, spec in sorted(nap_specs.items(), key=lambda kv: nap_num_key(kv[0])):
            tps = spec.get("tie_points") or []
            if not tps:
                continue
            nap_spec_parts = []
            for tp in tps:
                LCT = tp.get("left_ct")
                (LS, LE) = tp.get("left_range", (None, None))
                RCT = tp.get("right_ct")
                (RS, RE) = tp.get("right_range", (None, None))
                if None not in (LCT, LS, LE, RCT, RS, RE):
                    nap_spec_parts.append(
                        f"Tie Point {LCT}ct {LS}-{LE} to {RCT}ct {RS}-{RE}"
                    )
            nap_spec_str = " / ".join(nap_spec_parts)

            # Format tie-points list as [(24ct, [2,3,]), …]
            pretty = []
            for tp in tps:
                rct = tp.get("right_ct")
                rix = tp.get("right_indices") or []
                if rct and isinstance(rix, list) and rix:
                    pretty.append((f"{rct}ct", rix))

            emit(f"NAP {napid}{f' ({nap_spec_str})' if nap_spec_str else ''} tie-points: {pretty}")

        emit("==== END NAP Tie-Points Dump ====")
        emit("===== Distribution and NAP Walker =====")

    # Distance threshold
    try:
        THRESHOLD = THRESHOLD_M
    except NameError:
        THRESHOLD = 1.0

    def near(a, b) -> bool:
        try:
            return haversine(a[1], a[0], b[1], b[0]) <= THRESHOLD
        except Exception:
            return False

    # Visit each NAP in numeric order
    # Sort NAP coordinate points by their NAP ID (natural order: N1, N2, …)
    sorted_nap_coords = sorted(nap_coords, key=lambda pt: natural_key(nap_map.get(pt, "")))
    for nap_lat, nap_lon in sorted_nap_coords:
        nap_pt = (nap_lat, nap_lon)
        nap_id = nap_map.get(nap_pt, "")
        if not nap_id:
            continue

        nap_spec = nap_specs.get(nap_id)

        # Fiber count (for display only)
        fiber_ct = (nap_spec.get("fiber_count") if nap_spec else None)

        # Loose Tube chunks like "BLT, 12" / "OLT, 1-2"
        lt_chunks = []
        if nap_spec:
            for abbr, idxs in (nap_spec.get("tube_specs") or []):
                rng = _compress_indices(idxs or [])
                if abbr and rng:
                    lt_chunks.append(f"{abbr}, {rng}")
                elif abbr:
                    lt_chunks.append(f"{abbr}")
                elif rng:
                    lt_chunks.append(rng)

        # Tie Point chunks like "Tie Point 48ct 15-20 to 24ct 15-20"
        tie_points_meta = []
        if nap_spec:
            for tp in (nap_spec.get("tie_points") or []):
                LCT = tp.get("left_ct")
                (LS, LE) = tp.get("left_range", (None, None))
                RCT = tp.get("right_ct")
                (RS, RE) = tp.get("right_range", (None, None))
                if None not in (LCT, LS, LE, RCT, RS, RE):
                    tie_points_meta.append(f"Tie Point {LCT}ct {LS}-{LE} to {RCT}ct {RS}-{RE}")

        # Expected-at-NAP colors:
        #   If a Tie Point exists, stick to the left-hand indices parsed from the NAP ID.
        #   Otherwise, union all LT indices (supports two+ LTs).
        expected_at_nap = _expected_colors_from_nap_meta(nap_id, nap_spec)


        entries = []
        bad_drops = set()  # any drop ids that triggered an issue at this NAP


        # Walk phase already handles distribution colors; here we only pass through for DROP/SVC emit
        # Find any drop touching this NAP
        if drops:
            for verts, d_color_raw, d_id in drops:
                if not verts:
                    continue
                start, end = verts[0], verts[-1]
                at_start = near(nap_pt, start)
                at_end   = near(nap_pt, end)
                if not (at_start or at_end):
                    continue

                drop_color = _normalize_color(d_color_raw)
                far = end if at_start else start

                # Issue 1: Drop color not expected at this NAP (pre-walk)
                if expected_at_nap and drop_color not in expected_at_nap:
                    issues.append({
                        "path": f"{nap_id} → {d_id}",
                        "nap_id": nap_id,
                        "found_drop_color": drop_color,
                        "expected_colors": expected_at_nap,
                        "issue": "Drop color not expected at NAP"
                    })
                    bad_drops.add(d_id)

                # Record DROP entry
                entries.append(("DROP", {
                    "drop_id": d_id,
                    "color": drop_color,
                    "far_end": (round(far[0], 6), round(far[1], 6)),
                }))

                # Match SVC at far end (if any)
                svc_id = ""
                svc_colors = []
                splice = ""
                for sid, svc_pt, splice_raw in _iter_service_locations(svc_locs):
                    if near(svc_pt, far):
                        svc_id = sid
                        splice = splice_raw
                        svc_colors = _parse_svc_splice_colors(splice)
                        break

                # If we had an SVC at the far end: validate and record
                if svc_id:
                    # Issue 2: DROP vs SVC mismatch
                    if svc_colors and drop_color not in svc_colors:
                        issues.append({
                            "path": f"{nap_id} → {d_id} → {svc_id}",
                            "nap_id": nap_id,
                            "svc_id": svc_id,
                            "drop_color": drop_color,
                            "svc_colors": svc_colors,
                            "issue": "Service Location splice color mismatch"
                        })
                        bad_drops.add(d_id)

                    # Record SVC entry
                    entries.append(("SVC", {
                        "svc_id": svc_id,
                        "splice_raw": splice or "",
                        "svc_colors": svc_colors,
                    }))

        # Emit entries using the new, tagged format
        for kind, data in entries:
            if kind == "DROP":
                icon = "❌" if data["drop_id"] in bad_drops else "✅"

                # Compose "(48ct, <fiber#> / BLT, 12 / OLT, 1-2 / Tie Point 48ct 15-20 to 24ct 15-20)"
                if data["color"] in FIBER_COLORS:
                    fiber_idx = FIBER_COLORS.index(data["color"]) + 1
                else:
                    fiber_idx = "?"

                segments = []
                if fiber_ct:
                    # Keep the original "<FiberCount>ct, <fiber#>" lead-in
                    if fiber_idx == "?":
                        segments.append(f"{fiber_ct}ct")
                    else:
                        segments.append(f"{fiber_ct}ct, {fiber_idx}")

                # Add both Loose Tubes (if present)
                if lt_chunks:
                    segments.extend(lt_chunks)

                # Add Tie Point(s) (if present)
                if tie_points_meta:
                    segments.extend(tie_points_meta)

                meta_str = f" ({' / '.join(segments)})" if segments else ""


                tag = "[Distribution and NAP Walker] "
                if show_debug_style:
                    emit(
                        f"{tag}{icon}  DROP {data['drop_id']}: color={data['color']} "
                        f"touches {nap_id}{meta_str}; far_end={data['far_end']}"
                    )
                else:
                    emit(
                        f"{tag}{icon}  DROP {data['drop_id']}: color={data['color']} "
                        f"touches {nap_id}{meta_str}"
                    )
            else:
                tag = "[Distribution and NAP Walker] "
                if str(getattr(modules.config, "LOG_COLOR_MODE", "OFF")).upper() == "EMOJI":
                    color_list = "[" + ", ".join(_colorize(c) for c in data["svc_colors"]) + "]"
                    splice_show = _colorize(_normalize_color(data["splice_raw"]))
                else:
                    color_list = "[" + ", ".join(data["svc_colors"]) + "]"
                    splice_show = _normalize_color(data["splice_raw"])
                emit(
                    f"{tag}    SVC {data['svc_id']}: splice={splice_show} → colors={color_list}"
                )
        

    # ------------------------------
    # Deep walk from each T-3 vault
    # ------------------------------
    for t3_pt in t3_coords:
        vault_id = t3_map.get(t3_pt)
        if not vault_id:
            continue
        for dist_id, segments in dist_map.items():
            if not re.match(rf"^{re.escape(vault_id)}\.DF\d+\.48A$", dist_id):
                continue
            touch = False
            for seg in segments:
                ep0, ep1 = normalize_pt(seg[0]), normalize_pt(seg[-1])
                if haversine(ep0[0], ep0[1], t3_pt[0], t3_pt[1]) <= THRESHOLD or \
                   haversine(ep1[0], ep1[1], t3_pt[0], t3_pt[1]) <= THRESHOLD:
                    touch = True
                    break
            if not touch:
                continue

            logger.debug(f"→ walking {dist_id} from T3 vault {vault_id}")
            initial_indices = list(range(1, 49))
            walk_distribution_branch(
                dist_map, nap_coords, nap_map, nap_specs, drops_by_pt, svc_locs, drops,
                dist_id, initial_indices,
                [vault_id, dist_id], issues, walked_dists, depth=0
            )
    emit("===== End Distribution and NAP Walker =====")
    # Sort issues by NAP numeric suffix for readability
    def nap_sort_key(issue):
        nap_id = issue.get("nap_id", "")
        if isinstance(nap_id, str):
            m = re.search(r'\.N(\d+)', nap_id)
            if m:
                return int(m.group(1))
        return float('inf')

    issues.sort(key=nap_sort_key)
    emit(f"Deep distribution walk found {len(issues)} issues.")
    return issues
