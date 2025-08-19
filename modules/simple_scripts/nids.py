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
    For each NID, find all its fiber drops, extract the Drop Color,
    match the far endpoint to a service location, then verify the
    service location’s splice‐colors include that Drop Color.
    """
    nids  = load_nids()
    drops = load_drops()
    # 1) load NAPs so we can skip any drop‐end that lands on a NAP
    nap_coords, _nap_map = load_features('nap', modules.config.ID_COL)
    svcs  = load_service_locations()
    issues = []

    for nid_lat, nid_lon, nid_vid in nids:
        for verts, raw_color, drop_id in drops:
            # pick the endpoint opposite the NID
            start, end = verts[0], verts[-1]
            if haversine(nid_lat, nid_lon, start[0], start[1]) <= THRESHOLD_M:
                endpoint = end
            elif haversine(nid_lat, nid_lon, end[0], end[1]) <= THRESHOLD_M:
                endpoint = start
            else:
                continue

            # 2) SKIP if that endpoint is actually a NAP
            if any(
                haversine(endpoint[0], endpoint[1], lat, lon) <= THRESHOLD_M
                for lat, lon in nap_coords
            ):
                continue

            # normalize "3 - Green" → "Green"
            drop_color = _normalize_color(raw_color)

            # find matching service location (within threshold), or None
            if svcs:
                svc_match = next(
                    (
                        (lat, lon, loose, splice_str, svc_id)
                        for lat, lon, loose, splice_str, svc_id in svcs
                        if haversine(endpoint[0], endpoint[1], lat, lon) <= THRESHOLD_M
                    ),
                    None
                )
            else:
                svc_match = None

            if not svc_match:
                issues.append({
                    "nid":        nid_vid,
                    "svc_id":     "",
                    "svc_color":  "",
                    "drop_color": drop_color,
                    "issue":      "No service location found at end of drop"
                })
            else:
                _, _, _, splice_str, svc_id = svc_match

                # parse splice codes like "1.3,2.3" → ["1.3","2.3"]
                # then extract the ".3" → ["3","3"] → map to ["Green","Green"]
                splice_codes = [s.strip() for s in splice_str.split(',') if s.strip()]
                svc_colors = []
                for code in splice_codes:
                    pos = code.split('.')[-1]
                    if pos.isdigit():
                        idx = int(pos) - 1
                        if 0 <= idx < len(FIBER_COLORS):
                            svc_colors.append(FIBER_COLORS[idx])

                # only flag if the drop_color isn't listed on the service location
                if drop_color not in svc_colors:
                    issues.append({
                        "nid":        nid_vid,
                        "svc_id":     svc_id,
                        "svc_color":  ", ".join(svc_colors),
                        "drop_color": drop_color,
                        "issue":      "Splice Colors mismatch"
                    })

    return issues

def iterate_nid_checks(include_ok: bool = True) -> list[dict]:
    """
    Return one row per NID→drop→service-location check.

    Keys per row:
      - nid: NID vetro_id
      - svc_id: matched Service Location ID ('' if none)
      - svc_color: comma-joined list of colors parsed from Splice Colors ('' if none)
      - drop_color: normalized drop color name
      - expected_splice: drop_color when OK; '' when not OK/unknown
      - actual_splice: drop_color (what the drop actually used)
      - issue: '' when OK; otherwise a short reason
    """
    nids  = load_nids()
    drops = load_drops()
    nap_coords, _nap_map = load_features('nap', modules.config.ID_COL)
    svcs  = load_service_locations()

    rows: list[dict] = []

    for nid_lat, nid_lon, nid_vid in nids:
        for verts, raw_color, drop_id in drops:
            # choose the endpoint opposite the NID
            start, end = verts[0], verts[-1]
            if haversine(nid_lat, nid_lon, start[0], start[1]) <= THRESHOLD_M:
                endpoint = end
            elif haversine(nid_lat, nid_lon, end[0], end[1]) <= THRESHOLD_M:
                endpoint = start
            else:
                continue

            # skip if the far endpoint is actually a NAP
            if any(
                haversine(endpoint[0], endpoint[1], lat, lon) <= THRESHOLD_M
                for lat, lon in nap_coords
            ):
                continue

            drop_color = _normalize_color(raw_color)

            # find matching service location (within threshold), or None
            if svcs:
                svc_match = next(
                    (
                        (lat, lon, loose, splice_str, svc_id)
                        for lat, lon, loose, splice_str, svc_id in svcs
                        if haversine(endpoint[0], endpoint[1], lat, lon) <= THRESHOLD_M
                    ),
                    None
                )
            else:
                svc_match = None

            if not svc_match:
                row = {
                    "nid": nid_vid,
                    "svc_id": "",
                    "svc_color": "",
                    "drop_color": drop_color,
                    "expected_splice": "",
                    "actual_splice": drop_color,
                    "issue": "No service location found at end of drop",
                }
                rows.append(row)
                continue

            # parse splice colors on the matched service location
            _, _, _, splice_str, svc_id = svc_match
            splice_codes = [s.strip() for s in (splice_str or "").split(",") if s.strip()]
            svc_colors: list[str] = []
            for code in splice_codes:
                pos = code.split(".")[-1]
                if pos.isdigit():
                    idx = int(pos) - 1
                    if 0 <= idx < len(FIBER_COLORS):
                        svc_colors.append(FIBER_COLORS[idx])

            ok = drop_color in svc_colors
            if ok or include_ok:
                rows.append({
                    "nid": nid_vid,
                    "svc_id": svc_id,
                    "svc_color": ", ".join(svc_colors),
                    "drop_color": drop_color,
                    "expected_splice": drop_color if ok else "",
                    "actual_splice": drop_color,
                    "issue": "" if ok else "Splice Colors mismatch",
                })

    return rows

