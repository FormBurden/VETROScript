# modules/simple_scripts/nids.py

import logging
import glob
import json
import re
import modules.config
from modules.simple_scripts.geojson_loader import load_features
from modules.basic.distance_utils import haversine, THRESHOLD_M
from modules.basic.fiber_colors import FIBER_COLORS


logger = logging.getLogger(__name__)

# abbreviations per loose‐tube index
TUBE_MAP = {
    1: "BLT",
    2: "OLT",
    3: "GLT",
    4: "BRLT",
    5: "SLT",
    6: "WLT"
}

def _normalize_color(c: str) -> str:
    """
    Strip numeric prefix like '5 - Slate' → 'Slate'.
    """
    if not c:
        return ""
    parts = c.split(" - ", 1)
    if len(parts) == 2 and parts[0].strip().isdigit():
        return parts[1].strip()
    return c.strip()


# NEW: parse color *prefixes* from an SL "Splice Colors" string.
# Examples:
#   "Black 1.1, Black 1.2"         -> ["Black", "Black"]
#   "Slate 2.7; Slate 2.8"         -> ["Slate", "Slate"]
#   "5 - Slate 1.5, Slate 1.6"     -> ["Slate", "Slate"]
#   "Blue"                          -> ["Blue"]
def _parse_splice_prefix_colors(splice_str: str) -> list[str]:
    if not isinstance(splice_str, str) or not splice_str.strip():
        return []

    tokens = re.split(r"[,\n;/]+", splice_str)
    colors: list[str] = []

    # Try to match canonical names at the start of each token (case-insensitive).
    # Also tolerant of "5 - Slate 1.5" formats by peeking right side of "-".
    for tok in tokens:
        s = tok.strip()
        if not s:
            continue

        # If a dash is present, prefer right side (often "5 - Slate 1.5")
        if "-" in s:
            left, right = [p.strip() for p in s.split("-", 1)]
            # replace s with right side to try color name match
            if right:
                s = right

        # Find a canonical color name prefix
        low = s.lower()
        matched = None
        for name in FIBER_COLORS:
            if low.startswith(name.lower()):
                matched = name
                break

        if matched:
            colors.append(matched)

    return colors


def _token_to_color(token: str) -> str:
    """
    Resolve a splice token to canonical color:
      - "5 - Slate" → "Slate"
      - startswith color → that color ("Black 1.1" → "Black")
      - digits 1..12 → map via FIBER_COLORS
      - else ""
    """
    if not token:
        return ""
    s = str(token).strip()
    if " - " in s:
        left, right = [p.strip() for p in s.split(" - ", 1)]
        if right in FIBER_COLORS:
            return right
        s = left
    sl = s.lower()
    for name in FIBER_COLORS:
        if sl.startswith(name.lower()):
            return name
    if s.isdigit():
        i = int(s)
        if 1 <= i <= len(FIBER_COLORS):
            return FIBER_COLORS[i - 1]
    return ""


def load_nids():
    """
    Load NID points: returns list of (lat, lon, vetro_id).
    """
    out = []
    for fn in glob.glob(f"{modules.config.DATA_DIR}/*ni-ds-network-point*.geojson"):
        gj = json.load(open(fn, encoding="utf-8"))
        for feat in gj.get("features", []):
            props = feat.get("properties", {}) or {}
            lon, lat = feat["geometry"]["coordinates"][:2]
            out.append((round(lat, 6), round(lon, 6), props.get("vetro_id", "")))
    return out

def load_drops():
    """
    Load fiber‐drop segments.
    Returns list of (verts, color, drop_id), where verts is ordered [(lat, lon)…].
    """
    out = []
    for fn in glob.glob(f"{modules.config.DATA_DIR}/*fiber-drop*.geojson"):
        gj = json.load(open(fn, encoding="utf-8"))
        for feat in gj.get("features", []):
            props   = feat.get("properties", {}) or {}
            color   = _normalize_color(props.get("Color", ""))
            drop_id = props.get("vetro_id", "")
            geom    = feat.get("geometry", {})
            typ     = geom.get("type")
            coords  = geom.get("coordinates", [])
            segments = []

            if typ == "LineString":
                segments = [coords]
            elif typ == "MultiLineString":
                segments = coords
            else:
                continue

            for seg in segments:
                if len(seg) < 2:
                    continue
                verts = [(round(lat, 6), round(lon, 6)) for lon, lat in seg]
                out.append((verts, color, drop_id))
    return out

def load_service_locations():
    """
    Load Service Locations: returns list of
    (lat, lon, loose_tube, splice_colors, svc_id).
    """
    out = []
    for fn in glob.glob(f"{modules.config.DATA_DIR}/*service-location*.geojson"):
        gj = json.load(open(fn, encoding="utf-8"))
        for feat in gj.get("features", []):
            props = feat.get("properties", {}) or {}
            lon, lat = feat["geometry"]["coordinates"][:2]
            loose    = (props.get("Loose Tube")    or "").strip()
            splice   = (props.get("Splice Colors") or "").strip()
            svc_id   = (props.get("ID")            or "").strip()
            out.append((round(lat, 6), round(lon, 6), loose, splice, svc_id))
    return out


def find_nid_mismatches():
    """
    NID Issues:
    - For each NID, determine the pre-NID drop color = the color of the drop that runs
      between the NID and a NAP (NID → NAP).
    - Then, for every drop that runs NID → Service Location (i.e., the far endpoint is NOT a NAP),
      check the Service Location's Splice Colors. The SL must include the *pre-NID* color (as a
      color prefix like "Black ..."), NOT the after-NID drop's color.

    Returns: list of dict rows with keys:
      nid, svc_id, svc_color, drop_color, issue
        - 'drop_color' is the *pre-NID* color we expected to see on the SL splice colors.
    """
    nids = load_nids()
    drops = load_drops()
    nap_coords, _nap_map = load_features('nap', modules.config.ID_COL)
    svcs = load_service_locations()

    issues: list[dict] = []

    # Build an easy predicate for "near a NAP"
    def _is_near_any_nap(latlon) -> bool:
        lat, lon = latlon
        return any(haversine(lat, lon, nlat, nlon) <= THRESHOLD_M for nlat, nlon in nap_coords)

    for nid_lat, nid_lon, nid_vid in nids:
        # 1) Find the *pre-NID* color for this NID (a drop whose far endpoint is a NAP).
        pre_color: str | None = None
        for verts, raw_color, _drop_id in drops:
            start, end = verts[0], verts[-1]

            if haversine(nid_lat, nid_lon, start[0], start[1]) <= THRESHOLD_M:
                far = end
            elif haversine(nid_lat, nid_lon, end[0], end[1]) <= THRESHOLD_M:
                far = start
            else:
                continue

            if _is_near_any_nap(far):
                pre_color = _normalize_color(raw_color)
                break

        if not pre_color:
            # No NAP-bound drop means we cannot derive the pre-NID color.
            issues.append({
                "nid": nid_vid,
                "svc_id": "",
                "svc_color": "",
                "drop_color": "",
                "issue": "No NAP-bound drop found for NID (cannot derive pre-NID color)"
            })
            # Still continue to next NID; nothing to check downstream.
            continue

        # 2) For each NID-connected drop whose far endpoint is NOT a NAP, match SL and verify.
        for verts, _raw_color_after, _drop_id in drops:
            start, end = verts[0], verts[-1]

            if haversine(nid_lat, nid_lon, start[0], start[1]) <= THRESHOLD_M:
                far = end
            elif haversine(nid_lat, nid_lon, end[0], end[1]) <= THRESHOLD_M:
                far = start
            else:
                continue

            # Skip if the far endpoint is a NAP — those are the pre-NID drops we used above.
            if _is_near_any_nap(far):
                continue

            # Find a service location at the far endpoint
            svc_match = next(
                (
                    (lat, lon, loose, splice_str, svc_id)
                    for lat, lon, loose, splice_str, svc_id in svcs
                    if haversine(far[0], far[1], lat, lon) <= THRESHOLD_M
                ),
                None
            )

            if not svc_match:
                issues.append({
                    "nid": nid_vid,
                    "svc_id": "",
                    "svc_color": "",
                    "drop_color": pre_color,
                    "issue": "No service location found at NID downstream endpoint"
                })
                continue

            _, _, _loose, splice_str, svc_id = svc_match
            prefixes = _parse_splice_prefix_colors(splice_str)
            ok = pre_color in prefixes

            if not ok:
                issues.append({
                    "nid": nid_vid,
                    "svc_id": svc_id,
                    "svc_color": ", ".join(prefixes),
                    "drop_color": pre_color,
                    "issue": "Splice Colors mismatch (missing pre-NID color)"
                })

    return issues


# def find_nid_mismatches():
#     """
#     For each NID, find all its fiber drops, extract the Drop Color, match the far
#     endpoint to a service location (skipping NAPs), then verify that the service
#     location’s Splice Colors include that Drop Color.
#     NOTE: Dot-codes like '1.3' are NOT interpreted; names win (e.g., 'Black 1.1' -> 'Black').
#     """
#     nids = load_nids()
#     drops = load_drops()
#     nap_coords, _nap_map = load_features('nap', modules.config.ID_COL)
#     svcs = load_service_locations()
#     issues = []

#     for nid_lat, nid_lon, nid_vid in nids:
#         for verts, raw_color, drop_id in drops:
#             start, end = verts[0], verts[-1]
#             if haversine(nid_lat, nid_lon, start[0], start[1]) <= THRESHOLD_M:
#                 endpoint = end
#             elif haversine(nid_lat, nid_lon, end[0], end[1]) <= THRESHOLD_M:
#                 endpoint = start
#             else:
#                 continue

#             # Skip if the far endpoint is a NAP
#             if any(haversine(endpoint[0], endpoint[1], lat, lon) <= THRESHOLD_M for lat, lon in nap_coords):
#                 continue

#             drop_color = _normalize_color(raw_color)

#             # Find the service location that this endpoint lands on
#             svc_match = next(
#                 (((lat, lon, loose, splice_str, svc_id))
#                  for lat, lon, loose, splice_str, svc_id in svcs
#                  if haversine(endpoint[0], endpoint[1], lat, lon) <= THRESHOLD_M),
#                 None
#             )

#             if not svc_match:
#                 issues.append({
#                     "nid": nid_vid, "svc_id": "", "svc_color": "",
#                     "drop_color": drop_color, "issue": "No service location found at end of drop"
#                 })
#                 continue

#             _, _, _, splice_str, svc_id = svc_match

#             # Parse colors from Splice Colors (names win)
#             tokens = [s.strip() for s in (splice_str or "").replace("/", ",").split(",") if s.strip()]
#             svc_colors = []
#             for tok in tokens:
#                 col = _token_to_color(tok)
#                 if col:
#                     svc_colors.append(col)

#             if drop_color not in svc_colors:
#                 issues.append({
#                     "nid": nid_vid,
#                     "svc_id": svc_id,
#                     "svc_color": ", ".join(svc_colors),
#                     "drop_color": drop_color,
#                     "issue": "Splice Colors mismatch",
#                 })

#     return issues


def iterate_nid_checks(include_ok: bool = True) -> list[dict]:
    """
    Expanded rows for NID checks (for sheet/log output).

    One logical row per NID→(downstream)ServiceLocation check:
      - expected_splice: the *pre-NID* drop color (from the NID→NAP drop)
      - actual_splice:   equal to expected_splice when OK; '' when mismatch
      - svc_color:       comma-joined color prefixes parsed from SL "Splice Colors"
      - issue:           '' when OK; diagnostic string otherwise
    """
    nids = load_nids()
    drops = load_drops()
    nap_coords, _nap_map = load_features('nap', modules.config.ID_COL)
    svcs = load_service_locations()

    rows: list[dict] = []

    def _is_near_any_nap(latlon) -> bool:
        lat, lon = latlon
        return any(haversine(lat, lon, nlat, nlon) <= THRESHOLD_M for nlat, nlon in nap_coords)

    for nid_lat, nid_lon, nid_vid in nids:
        # A) Determine pre-NID color (NID→NAP drop)
        pre_color: str | None = None
        for verts, raw_color, _drop_id in drops:
            start, end = verts[0], verts[-1]
            if haversine(nid_lat, nid_lon, start[0], start[1]) <= THRESHOLD_M:
                far = end
            elif haversine(nid_lat, nid_lon, end[0], end[1]) <= THRESHOLD_M:
                far = start
            else:
                continue
            if _is_near_any_nap(far):
                pre_color = _normalize_color(raw_color)
                break

        if not pre_color:
            # If no pre-NID color can be derived, record one row and continue.
            rows.append({
                "nid": nid_vid,
                "svc_id": "",
                "svc_color": "",
                "drop_color": "",
                "expected_splice": "",
                "actual_splice": "",
                "issue": "No NAP-bound drop found for NID (cannot derive pre-NID color)",
            })
            continue

        # B) Check each NID-connected downstream SL (far endpoint not a NAP)
        for verts, _raw_color_after, _drop_id in drops:
            start, end = verts[0], verts[-1]

            if haversine(nid_lat, nid_lon, start[0], start[1]) <= THRESHOLD_M:
                far = end
            elif haversine(nid_lat, nid_lon, end[0], end[1]) <= THRESHOLD_M:
                far = start
            else:
                continue

            if _is_near_any_nap(far):
                # This is the pre-NID drop we used to compute pre_color; skip reporting.
                continue

            # Match SL at downstream endpoint
            svc_match = next(
                (
                    (lat, lon, loose, splice_str, svc_id)
                    for lat, lon, loose, splice_str, svc_id in svcs
                    if haversine(far[0], far[1], lat, lon) <= THRESHOLD_M
                ),
                None
            )

            if not svc_match:
                row = {
                    "nid": nid_vid,
                    "svc_id": "",
                    "svc_color": "",
                    "drop_color": pre_color,
                    "expected_splice": pre_color,
                    "actual_splice": "",
                    "issue": "No service location found at NID downstream endpoint",
                }
                rows.append(row)
                continue

            _, _, _loose, splice_str, svc_id = svc_match
            prefixes = _parse_splice_prefix_colors(splice_str)
            ok = pre_color in prefixes

            row = {
                "nid": nid_vid,
                "svc_id": svc_id,
                "svc_color": ", ".join(prefixes),
                "drop_color": pre_color,            # we are checking PRE-NID color
                "expected_splice": pre_color if ok else pre_color,
                "actual_splice": pre_color if ok else "",
                "issue": "" if ok else "Splice Colors mismatch (missing pre-NID color)",
            }
            if ok or include_ok:
                rows.append(row)

    return rows


# def iterate_nid_checks(include_ok: bool = True) -> list[dict]:
#     """
#     Return one row per NID→drop→service-location check.
#     Names dominate; dot-codes like '1.3' are not interpreted.
#     """
#     nids = load_nids()
#     drops = load_drops()
#     nap_coords, _nap_map = load_features('nap', modules.config.ID_COL)
#     svcs = load_service_locations()
#     rows: list[dict] = []

#     for nid_lat, nid_lon, nid_vid in nids:
#         for verts, raw_color, drop_id in drops:
#             start, end = verts[0], verts[-1]
#             if haversine(nid_lat, nid_lon, start[0], start[1]) <= THRESHOLD_M:
#                 endpoint = end
#             elif haversine(nid_lat, nid_lon, end[0], end[1]) <= THRESHOLD_M:
#                 endpoint = start
#             else:
#                 continue

#             # skip NAP endpoints
#             if any(haversine(endpoint[0], endpoint[1], lat, lon) <= THRESHOLD_M for lat, lon in nap_coords):
#                 continue

#             drop_color = _normalize_color(raw_color)

#             svc_match = next(
#                 (((lat, lon, loose, splice_str, svc_id))
#                  for lat, lon, loose, splice_str, svc_id in svcs
#                  if haversine(endpoint[0], endpoint[1], lat, lon) <= THRESHOLD_M),
#                 None
#             )

#             if not svc_match:
#                 rows.append({
#                     "nid": nid_vid, "svc_id": "", "svc_color": "",
#                     "drop_color": drop_color, "expected_splice": "",
#                     "actual_splice": drop_color, "issue": "No service location found at end of drop",
#                 })
#                 continue

#             _, _, _, splice_str, svc_id = svc_match

#             tokens = [s.strip() for s in (splice_str or "").replace("/", ",").split(",") if s.strip()]
#             svc_colors: list[str] = []
#             for tok in tokens:
#                 col = _token_to_color(tok)
#                 if col:
#                     svc_colors.append(col)

#             ok = drop_color in svc_colors
#             if ok or include_ok:
#                 rows.append({
#                     "nid": nid_vid,
#                     "svc_id": svc_id,
#                     "svc_color": ", ".join(svc_colors),
#                     "drop_color": drop_color,
#                     "expected_splice": drop_color if ok else "",
#                     "actual_splice": drop_color,
#                     "issue": "" if ok else "Splice Colors mismatch",
#                 })

#     return rows


def build_sid_upstream_drop_color_map() -> dict[str, str]:
    """
    Return a mapping of Service Location ID -> upstream drop color, where:
      - "upstream" is the color of the drop segment that runs from NID to NAP.
      - Only SLs fed by that NID are included.
    Uses THRESHOLD_M proximity to relate endpoints.
    """
    # Reuse existing loaders / constants
    nids = load_nids()
    drops = load_drops()
    nap_coords, _ = load_features('nap', modules.config.ID_COL)
    svcs = load_service_locations()

    def _near(a_lat, a_lon, b_lat, b_lon) -> bool:
        return haversine(a_lat, a_lon, b_lat, b_lon) <= THRESHOLD_M

    # 1) Determine the upstream (NAP→NID) color for each NID.
    upstream_by_nid: dict[str, str] = {}
    for nid_lat, nid_lon, nid_vid in nids:
        for verts, color, _drop_id in drops:
            if not verts:
                continue
            start, end = verts[0], verts[-1]
            nid_at_start = _near(nid_lat, nid_lon, start[0], start[1])
            nid_at_end   = _near(nid_lat, nid_lon, end[0], end[1])
            if not (nid_at_start or nid_at_end):
                continue
            other = end if nid_at_start else start
            # Other endpoint must be a NAP (upstream)
            if any(_near(other[0], other[1], lat, lon) for (lat, lon) in nap_coords):
                # First match wins; if multiple, we keep the first consistent with data snapping.
                upstream_by_nid[nid_vid or f"{nid_lat},{nid_lon}"] = color
                break

    if not upstream_by_nid:
        return {}

    # 2) For each NID that has an upstream color, map all SLs fed by that NID to that color.
    sid_to_upstream: dict[str, str] = {}
    for nid_lat, nid_lon, nid_vid in nids:
        up_color = upstream_by_nid.get(nid_vid)
        if not up_color:
            continue
        for verts, _color_downstream, _drop_id in drops:
            if not verts:
                continue
            start, end = verts[0], verts[-1]
            nid_at_start = _near(nid_lat, nid_lon, start[0], start[1])
            nid_at_end   = _near(nid_lat, nid_lon, end[0], end[1])
            if not (nid_at_start or nid_at_end):
                continue
            far = end if nid_at_start else start
            # If the far endpoint is a service location, assign its upstream color.
            match = next(
                (sid for (lat, lon, _loose, _splice, sid) in svcs if _near(far[0], far[1], lat, lon)),
                None
            )
            if match:
                sid_to_upstream[match] = up_color

    return sid_to_upstream
