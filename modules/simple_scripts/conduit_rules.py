# modules/simple_scripts/conduit_rules.py
# Conduit-only rules & helpers

from __future__ import annotations

import glob
import json
from typing import List, Tuple

import modules.config
from modules.basic.distance_utils import haversine, THRESHOLD_M
from modules.simple_scripts.geojson_loader import load_features

M_TO_FT = 3.28084

_ALLOWED_CONDUIT_TYPES = {
    '1 x 1.25"',
    '1 x 1.25" Road Shot',
    '2 x 1.25"',
    '3 x 1.25"',
    '2 x 1.25" Road Shot',
    '1 x 1.25" Kaysville',
    'Pre-existing',
    '1 x 2"',
    '2 x 2"',
}
_ALLOWED_NORMALIZED = {s.strip().lower(): s for s in _ALLOWED_CONDUIT_TYPES}


def _load_conduits() -> List[dict]:
    feats: List[dict] = []
    for path in glob.glob(f"{modules.config.DATA_DIR}/*conduit*.geojson"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                gj = json.load(f)
        except Exception:
            continue

        for feat in gj.get("features", []) or []:
            props = (feat.get("properties") or {}) if isinstance(feat, dict) else {}
            geom  = (feat.get("geometry") or {}) if isinstance(feat, dict) else {}
            coords= geom.get("coordinates") or []
            gtyp  = (geom.get("type") or "").strip()

            segs = []
            if gtyp == "LineString":
                segs = [coords]
            elif gtyp == "MultiLineString":
                segs = coords
            else:
                continue

            poly: List[List[Tuple[float,float]]] = []
            for seg in segs:
                if not seg or len(seg) < 2:
                    continue
                poly.append([(round(lat, 6), round(lon, 6)) for lon, lat in seg])

            feats.append({
                "id":       (props.get("ID") or props.get("id") or "").strip(),
                "vetro_id": (props.get("vetro_id") or props.get("Vetro ID") or "").strip(),
                "type":     (props.get("Conduit Type") or "").strip(),
                "segments": poly,
            })

    return feats


def _load_underground_distributions_full() -> List[dict]:
    out: List[dict] = []
    for path in glob.glob(f"{modules.config.DATA_DIR}/*fiber-distribution-underground*.geojson"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                gj = json.load(f)
        except Exception:
            continue

        for feat in gj.get("features", []) or []:
            props = (feat.get("properties") or {}) if isinstance(feat, dict) else {}
            geom  = (feat.get("geometry") or {}) if isinstance(feat, dict) else {}
            coords= geom.get("coordinates") or []
            gtyp  = (geom.get("type") or "").strip()

            segs = []
            if gtyp == "LineString":
                segs = [coords]
            elif gtyp == "MultiLineString":
                segs = coords
            else:
                continue

            poly: List[List[Tuple[float,float]]] = []
            for seg in segs:
                if not seg or len(seg) < 2:
                    continue
                poly.append([(round(lat, 6), round(lon, 6)) for lon, lat in seg])

            out.append({
                "id":       (props.get("ID") or "").strip(),
                "vetro_id": (props.get("vetro_id") or props.get("Vetro ID") or "").strip(),
                "segments": poly,
            })
    return out


def _collect_conduit_vertices(conduits: List[dict]) -> List[Tuple[float,float]]:
    verts: List[Tuple[float,float]] = []
    for c in conduits:
        for seg in c.get("segments", []):
            verts.extend(seg)
    return verts

def find_conduits_without_distribution() -> List[dict]:
    """
    For each conduit (any type), require at least one underground Distribution vertex
    within THRESHOLD_M of any conduit vertex. If none, flag the conduit.

    Returns rows:
      { "Conduit ID": , "Conduit Vetro ID": , "Issue": "No Distribution fiber on conduit" }
    """
    conduits = _load_conduits()
    # Collect all underground distribution vertices once
    ug_dists = _load_underground_distributions_full()
    dist_vertices: List[Tuple[float, float]] = []
    for df in ug_dists:
        for seg in df.get("segments", []):
            dist_vertices.extend(seg)

    out: List[dict] = []
    for c in conduits:
        has_touch = False
        for seg in c.get("segments", []):
            for lat, lon in seg:
                if any(haversine(lat, lon, dlat, dlon) <= THRESHOLD_M for (dlat, dlon) in dist_vertices):
                    has_touch = True
                    break
            if has_touch:
                break
        if not has_touch:
            out.append({
                "Conduit ID": c.get("id", ""),
                "Conduit Vetro ID": c.get("vetro_id", ""),
                "Issue": "No Distribution fiber on conduit",
            })
    return out


# ---------------------------------------------
# Rule: Underground DF must have conduit below
# ---------------------------------------------


def find_distributions_without_conduit(tolerance_ft: float | None = None) -> List[dict]:
    """
    For each underground Distribution, require at least one conduit segment within tolerance
    of any vertex of the distribution geometry. Uses point-to-segment distance (like the
    vault rule) to avoid false negatives when vertices don't line up exactly.

    Returns rows:
      { "Distribution ID": , "Vetro ID": , "Issue": "No Conduit under distribution" }
    """
    from math import cos, radians, sqrt

    conduits = _load_conduits()
    ug_dists = _load_underground_distributions_full()

    # Allow an override, else fall back to the global threshold (~3 ft)
    tol_m = (float(tolerance_ft) / M_TO_FT) if tolerance_ft is not None else THRESHOLD_M

    def _ptseg_distance_m(p: Tuple[float, float], a: Tuple[float, float], b: Tuple[float, float]) -> float:
        """Approximate point-to-segment distance in meters via local equirectangular projection."""
        plat, plon = p
        alat, alon = a
        blat, blon = b

        lat0 = (plat + alat + blat) / 3.0
        m_per_deg_lat = 111320.0
        m_per_deg_lon = 111320.0 * cos(radians(lat0))

        ax, ay = alon * m_per_deg_lon, alat * m_per_deg_lat
        bx, by = blon * m_per_deg_lon, blat * m_per_deg_lat
        px, py = plon * m_per_deg_lon, plat * m_per_deg_lat

        vx, vy = (bx - ax), (by - ay)
        wx, wy = (px - ax), (py - ay)

        denom = (vx * vx + vy * vy)
        if denom <= 0.0:  # degenerate segment
            dx, dy = (px - ax), (py - ay)
            return sqrt(dx * dx + dy * dy)

        t = (wx * vx + wy * vy) / denom
        if t < 0.0:
            cx, cy = ax, ay
        elif t > 1.0:
            cx, cy = bx, by
        else:
            cx, cy = (ax + t * vx), (ay + t * vy)

        dx, dy = (px - cx), (py - cy)
        return sqrt(dx * dx + dy * dy)

    out: List[dict] = []

    for df in ug_dists:
        has_touch = False

        # For every vertex in the DF geometry, check distance to nearest conduit *segment*
        for df_seg in df.get("segments", []):
            for (dlat, dlon) in df_seg:
                # Early exit as soon as we find any close segment
                for c in conduits:
                    for cseg in c.get("segments", []):
                        if len(cseg) < 2:
                            continue
                        for i in range(1, len(cseg)):
                            if _ptseg_distance_m((dlat, dlon), cseg[i - 1], cseg[i]) <= tol_m:
                                has_touch = True
                                break
                        if has_touch:
                            break
                    if has_touch:
                        break
                if has_touch:
                    break
            if has_touch:
                break

        if not has_touch:
            out.append({
                "Distribution ID": df.get("id", ""),
                "Vetro ID": df.get("vetro_id", ""),
                "Issue": "No Conduit under distribution",
            })

    return out


# ------------------------------------------------------
# Rule: Conduit Type must be one of the allowed values
# ------------------------------------------------------
def find_conduit_type_issues() -> List[dict]:
    """
    Validate 'Conduit Type' against the allowed list (case-insensitive).
    Anything else — including blank — is flagged.
    """
    out: List[dict] = []
    for c in _load_conduits():
        raw = (c.get("type") or "").strip()
        ok = (raw.strip().lower() in _ALLOWED_NORMALIZED)
        if not ok:
            out.append({
                "Conduit ID": c.get("id", ""),
                "Conduit Vetro ID": c.get("vetro_id", ""),
                "Conduit Type": raw,
                "Issue": "Invalid Conduit Type",
            })
    return out


def find_conduits_without_distribution(tolerance_ft: float | None = None) -> List[dict]:
    """
    For each Conduit, require at least one underground Distribution segment within tolerance
    of any vertex of the conduit geometry. Uses point-to-segment distance for robustness.

    Returns rows:
      { "Conduit ID": , "Conduit Vetro ID": , "Issue": "No Distribution fiber on conduit" }
    """
    from math import cos, radians, sqrt

    conduits = _load_conduits()
    ug_dists = _load_underground_distributions_full()

    # Pre-collect DF segments to avoid recomputing
    df_segments: List[List[Tuple[float, float]]] = []
    for df in ug_dists:
        for seg in df.get("segments", []):
            if len(seg) >= 2:
                df_segments.append(seg)

    tol_m = (float(tolerance_ft) / M_TO_FT) if tolerance_ft is not None else THRESHOLD_M

    def _ptseg_distance_m(p: Tuple[float, float], a: Tuple[float, float], b: Tuple[float, float]) -> float:
        """Approximate point-to-segment distance in meters via local equirectangular projection."""
        plat, plon = p
        alat, alon = a
        blat, blon = b

        lat0 = (plat + alat + blat) / 3.0
        m_per_deg_lat = 111320.0
        m_per_deg_lon = 111320.0 * cos(radians(lat0))

        ax, ay = alon * m_per_deg_lon, alat * m_per_deg_lat
        bx, by = blon * m_per_deg_lon, blat * m_per_deg_lat
        px, py = plon * m_per_deg_lon, plat * m_per_deg_lat

        vx, vy = (bx - ax), (by - ay)
        wx, wy = (px - ax), (py - ay)

        denom = (vx * vx + vy * vy)
        if denom <= 0.0:
            dx, dy = (px - ax), (py - ay)
            return sqrt(dx * dx + dy * dy)

        t = (wx * vx + wy * vy) / denom
        if t < 0.0:
            cx, cy = ax, ay
        elif t > 1.0:
            cx, cy = bx, by
        else:
            cx, cy = (ax + t * vx), (ay + t * vy)

        dx, dy = (px - cx), (py - cy)
        return sqrt(dx * dx + dy * dy)

    out: List[dict] = []

    for c in conduits:
        has_touch = False

        for cseg in c.get("segments", []):
            for (clat, clon) in cseg:
                # Compare this conduit vertex to *distribution segments*
                for dfseg in df_segments:
                    for i in range(1, len(dfseg)):
                        if _ptseg_distance_m((clat, clon), dfseg[i - 1], dfseg[i]) <= tol_m:
                            has_touch = True
                            break
                    if has_touch:
                        break
                if has_touch:
                    break
            if has_touch:
                break

        if not has_touch:
            out.append({
                "Conduit ID": c.get("id", ""),
                "Conduit Vetro ID": c.get("vetro_id", ""),
                "Issue": "No Distribution fiber on conduit",
            })

    return out


def run_all_conduit_checks() -> dict[str, list[dict]]:
    return {
        # Underground Distribution without any nearby conduit
        "df_missing_conduit": find_distributions_without_conduit(),

        # Conduits that don’t carry any underground distribution
        "conduit_missing_distribution": find_conduits_without_distribution(),

        # Bad / blank conduit types
        "type_issues": find_conduit_type_issues(),
    }


def find_vaults_missing_conduit(tolerance_ft: float | None = None) -> List[dict]:
    """
    Every vault coordinate must have conduit *under it*.
    Now checks distance to the nearest *segment* (not only conduit vertices).

    Returns rows:
      { "Vault Vetro ID": <vetro_id>, "Issue": "No Conduit at vault" }
    """
    from math import cos, radians, sqrt

    conduits = _load_conduits()
    vault_coords, vault_map = load_features("vault", "vetro_id")

    # Allow an override, else fall back to the global threshold (~3 ft)
    M_TO_FT = 3.28084
    tol_m = (float(tolerance_ft) / M_TO_FT) if tolerance_ft is not None else THRESHOLD_M

    def _ptseg_distance_m(
        p: Tuple[float, float],
        a: Tuple[float, float],
        b: Tuple[float, float],
    ) -> float:
        """
        Approximate point-to-segment distance in meters by projecting to a local
        equirectangular plane (accurate to << 1 ft at these tolerances).
        """
        plat, plon = p
        alat, alon = a
        blat, blon = b
        lat0 = (plat + alat + blat) / 3.0
        m_per_deg_lat = 111320.0
        m_per_deg_lon = 111320.0 * cos(radians(lat0))

        ax, ay = alon * m_per_deg_lon, alat * m_per_deg_lat
        bx, by = blon * m_per_deg_lon, blat * m_per_deg_lat
        px, py = plon * m_per_deg_lon, plat * m_per_deg_lat

        vx, vy = (bx - ax), (by - ay)
        wx, wy = (px - ax), (py - ay)

        denom = (vx * vx + vy * vy)
        if denom <= 0.0:  # a and b are the same point; distance to that point
            dx, dy = (px - ax), (py - ay)
            return sqrt(dx * dx + dy * dy)

        t = (wx * vx + wy * vy) / denom
        if t < 0.0:
            cx, cy = ax, ay
        elif t > 1.0:
            cx, cy = bx, by
        else:
            cx, cy = (ax + t * vx), (ay + t * vy)

        dx, dy = (px - cx), (py - cy)
        return sqrt(dx * dx + dy * dy)

    out: List[dict] = []
    for (vlat, vlon) in vault_coords:
        on_conduit = False
        # Early-exit as soon as any segment is within tolerance
        for c in conduits:
            for seg in c.get("segments", []):
                if len(seg) < 2:
                    continue
                # walk segment edges
                for i in range(1, len(seg)):
                    if _ptseg_distance_m((vlat, vlon), seg[i - 1], seg[i]) <= tol_m:
                        on_conduit = True
                        break
                if on_conduit:
                    break
            if on_conduit:
                break

        if not on_conduit:
            out.append({
                "Vault Vetro ID": vault_map.get((round(vlat, 6), round(vlon, 6)), ""),
                "Issue": "No Conduit at vault",
            })

    return out

# --- ADD/REPLACE in modules/simple_scripts/conduit_rules.py ---

from typing import Dict, Optional

def _load_points_with_fallback(layer_keyword: str,
                               primary_field: str,
                               fallback_fields: list[str]) -> tuple[list[tuple[float,float]], Dict[tuple[float,float], str]]:
    """
    Loads points from *{layer_keyword}*.geojson using geojson_loader.load_features,
    but if the primary field is missing/blank at a feature, tries the fallbacks.
    Returns (coords_list, id_map[(lat,lon)] -> chosen_id).
    """
    import glob, json
    coords: list[tuple[float,float]] = []
    id_map: Dict[tuple[float,float], str] = {}

    # Mirror load_features file pattern logic
    pattern = '*vault*.geojson' if layer_keyword == 'vaults' else f'*{layer_keyword}*.geojson'
    for fn in glob.glob(f'{modules.config.DATA_DIR}/{pattern}'):
        try:
            with open(fn, encoding='utf-8') as f:
                gj = json.load(f)
        except Exception:
            continue
        for feat in gj.get('features', []):
            geom = feat.get('geometry', {}) or {}
            c = geom.get('coordinates', [])
            if not isinstance(c, (list, tuple)) or len(c) < 2:
                continue
            lon, lat = c[0], c[1]
            pt = (round(lat, 6), round(lon, 6))
            props = feat.get('properties', {}) or {}

            # choose first non-blank among primary + fallbacks
            raw = (props.get(primary_field) or "").strip()
            if not raw:
                for fld in fallback_fields:
                    alt = (props.get(fld) or "").strip()
                    if alt:
                        raw = alt
                        break

            coords.append(pt)
            id_map[pt] = raw
    return coords, id_map


def _load_vault_points_map() -> tuple[list[tuple[float,float]], Dict[tuple[float,float], str]]:
    """
    Vaults must display Vetro ID in the path.
    Many files store either 'vetro_id' or 'Vetro ID'. Try both.
    """
    # Try plural or singular keyword both match '*vault*.geojson' in our repo
    return _load_points_with_fallback(
        layer_keyword='vault',  # OK; matches *vault*.geojson
        primary_field='vetro_id',
        fallback_fields=['Vetro ID', 'ID']  # last-ditch 'ID' if needed
    )


def _load_nap_points_map() -> tuple[list[tuple[float,float]], Dict[tuple[float,float], str]]:
    """
    NAP path token should use the NAP ID (your big string like '04.AC01.HAR.N17 (...)').
    In your repo, the canonical key is modules.config.ID_COL (usually 'ID').
    """
    from modules.simple_scripts.geojson_loader import load_features
    return load_features('nap', modules.config.ID_COL)  # ('nap', 'ID')


# --- ADD/REPLACE in modules/simple_scripts/conduit_rules.py ---

def _iter_df_segments_with_id() -> list[tuple[str, list[tuple[float,float]]]]:
    """
    Returns a flat list of (dist_id, segment_vertices[[(lat,lon),...]]) for underground DF only.
    """
    out: list[tuple[str, list[tuple[float,float]]]] = []
    for df in _load_underground_distributions_full():
        for seg in df.get("segments", []):
            if len(seg) >= 2:
                out.append((df.get("id",""), seg))
    return out


def _ptseg_distance_m(p: tuple[float,float], a: tuple[float,float], b: tuple[float,float]) -> float:
    """
    Local equirectangular projection distance (meters) — identical to other uses in this file.
    """
    from math import cos, radians, sqrt
    plat, plon = p
    alat, alon = a
    blat, blon = b
    lat0 = (plat + alat + blat) / 3.0
    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * cos(radians(lat0))
    ax, ay = alon * m_per_deg_lon, alat * m_per_deg_lat
    bx, by = blon * m_per_deg_lon, blat * m_per_deg_lat
    px, py = plon * m_per_deg_lon, plat * m_per_deg_lat
    vx, vy = (bx - ax), (by - ay)
    wx, wy = (px - ax), (py - ay)
    denom = (vx * vx + vy * vy)
    if denom <= 0.0:
        dx, dy = (px - ax), (py - ay)
        return sqrt(dx * dx + dy * dy)
    t = (wx * vx + wy * vy) / denom
    if t < 0.0:
        cx, cy = ax, ay
    elif t > 1.0:
        cx, cy = bx, by
    else:
        cx, cy = (ax + t * vx), (ay + t * vy)
    dx, dy = (px - cx), (py - cy)
    return sqrt(dx * dx + dy * dy)


def _nearest_df_id_for_point(pt: tuple[float,float],
                             df_segments: list[tuple[str, list[tuple[float,float]]]],
                             max_m: float) -> Optional[str]:
    """
    Returns the underground Distribution ID whose segment is closest to pt within max_m.
    If none are within max_m, returns None.
    """
    best_id: Optional[str] = None
    best_d = float("inf")
    for dist_id, seg in df_segments:
        for i in range(1, len(seg)):
            d = _ptseg_distance_m(pt, seg[i-1], seg[i])
            if d < best_d:
                best_d = d
                best_id = dist_id
    if best_d <= max_m:
        return best_id
    return None


# --- ADD/REPLACE in modules/simple_scripts/conduit_rules.py ---

def _wrap_path(path: str, width: int = 250) -> list[str]:
    """
    Wrap only on ' > ', ' -- ', ' / ' boundaries. Returns a list of wrapped lines.
    """
    if not path:
        return [""]
    seps = [" > ", " -- ", " / "]
    tokens: list[str] = []
    s = path
    # split keeping separators attached to the RIGHT of tokens for readability
    while s:
        cut_idx = len(s)
        chosen_sep = None
        for sep in seps:
            idx = s.find(sep)
            if idx != -1 and idx < cut_idx:
                cut_idx = idx
                chosen_sep = sep
        if chosen_sep is None:
            tokens.append(s)
            break
        tokens.append(s[:cut_idx + len(chosen_sep)])
        s = s[cut_idx + len(chosen_sep):]

    lines: list[str] = []
    cur = ""
    for t in tokens:
        if len(cur) + len(t) <= width:
            cur += t
        else:
            if cur:
                lines.append(cur.rstrip())
            # if a single token is longer than width, still start new line with it
            cur = t
    if cur:
        lines.append(cur.rstrip())
    return lines


def _build_conduit_named_path(c: dict) -> list[str]:
    """
    Returns the **wrapped** path lines for this conduit, following your rules:

    • Start at the first Vault encountered on the conduit (if any).
    • At each vertex, collect Vault (Vetro ID) and NAP (ID) within threshold.
      - If both present at the same vertex: "VaultVetro / NAPID"
      - If only one present: just that token.
    • Determine nearest Underground Distribution ID at each vertex.
      - Insert ' > ' whenever the DF ID changes.
      - Inside a DF segment, separate successive tokens with ' -- '.
      - At the *first* token of a DF segment, prefix with "DFID / " if there is also an asset token there;
        otherwise, include the DFID as its own token for that cut.
    • Wrap lines at 250 chars on valid boundaries only.
    """
    # Collect ordered conduit vertices
    verts: list[tuple[float,float]] = []
    for seg in (c.get("segments") or []):
        verts.extend(seg)
    if not verts:
        return [""]

    # Load assets & DF references
    vault_coords, vault_map = _load_vault_points_map()
    nap_coords, nap_map = _load_nap_points_map()
    df_segments = _iter_df_segments_with_id()

    # Build per-vertex asset tokens and DF IDs
    tol_m = THRESHOLD_M  # keep same proximity as the rest of the codebase
    vert_assets: list[dict] = []
    for pt in verts:
        v_id = ""
        n_id = ""

        # Find any vault within tolerance
        for (vlat, vlon) in vault_coords:
            if haversine(pt[0], pt[1], vlat, vlon) <= tol_m:
                v_id = vault_map.get((vlat, vlon), "")
                break

        # Find any nap within tolerance
        for (nlat, nlon) in nap_coords:
            if haversine(pt[0], pt[1], nlat, nlon) <= tol_m:
                n_id = nap_map.get((nlat, nlon), "")
                break

        df_id = _nearest_df_id_for_point(pt, df_segments, tol_m)

        vert_assets.append({"pt": pt, "vault": v_id, "nap": n_id, "df": df_id})

    # Find the *first* index having a Vault
    start_idx = 0
    for i, a in enumerate(vert_assets):
        if a.get("vault"):
            start_idx = i
            break

    # Build the chain from start_idx forward
    pieces: list[str] = []
    prev_df: Optional[str] = None

    for i in range(start_idx, len(vert_assets)):
        a = vert_assets[i]
        token = ""
        # Skips if no asset here AND no DF change here
        has_asset = bool(a["vault"] or a["nap"])
        cur_df = a["df"]

        # decide separator
        sep = ""
        if pieces:
            sep = " > " if cur_df and prev_df and cur_df != prev_df else (" -- " if has_asset else "")
            if sep:
                pieces.append(sep)

        # when DF cut occurs (or the very first DF we see), emit DFID at the boundary
        if (not pieces) or (cur_df and cur_df != prev_df):
            if has_asset:
                token = f"{cur_df} / "
            else:
                token = f"{cur_df}"
        # add the local asset label
        if has_asset:
            if token and not token.endswith(" / "):
                token += " / "
            if a["vault"] and a["nap"]:
                token += f"{a['vault']} / {a['nap']}"
            elif a["vault"]:
                token += f"{a['vault']}"
            elif a["nap"]:
                token += f"{a['nap']}"

        if token:
            pieces.append(token)

        prev_df = cur_df if cur_df else prev_df

    # Remove accidental empty separators at ends
    chain = "".join(pieces).strip()
    # Clean double spaces around seps
    while "  " in chain:
        chain = chain.replace("  ", " ")

    # Wrap to lines of <= 250 chars, only at the allowed separators
    return _wrap_path(chain, 250)

# --- REPLACE the entire emit_conduit_logs() in modules/simple_scripts/conduit_rules.py ---

def emit_conduit_logs(emit_info: bool = True) -> None:
    """
    Console log rendering for Conduit:
    • Overview (every conduit): Conduit ID, Vetro ID, Type, #Segments, #Vertices, Path (wrapped)
    • Followed by the 3 issue sections that already exist.
    """
    import logging
    logger = logging.getLogger(__name__)

    conduits = _load_conduits()

    logger.info("===== [Conduit] Overview (all features) =====")
    header = "[Conduit] {:<10} | {:<19} | {:<18} | {:<8} | {:<9} | {}".format(
        "Conduit ID", "Vetro ID", "Conduit Type", "#Segments", "#Vertices", "Path"
    )
    logger.info(header)

    for c in conduits:
        segs = c.get("segments") or []
        nseg = len(segs)
        nvert = sum(len(s) for s in segs)
        path_lines = _build_conduit_named_path(c)  # list of wrapped lines

        # empty guard — always print at least one row
        if not path_lines:
            path_lines = [""]

        for li, line in enumerate(path_lines):
            if li == 0:
                logger.info(
                    "[Conduit] {:<10} | {:<19} | {:<18} | {:<8} | {:<9} | {}".format(
                        c.get("id", ""),
                        (c.get("vetro_id","")[:19] + "…") if len(c.get("vetro_id","")) > 19 else c.get("vetro_id",""),
                        c.get("type","")[:18],
                        str(nseg)[:8],
                        str(nvert)[:9],
                        line
                    )
                )
            else:
                # Continuation line: repeat cols so it lines up under "Path"
                logger.info(
                    "[Conduit] {:<10} | {:<19} | {:<18} | {:<8} | {:<9} | {}".format(
                        "", "", "", "", "", line
                    )
                )

    # Existing issue sections (unchanged)
    # 1) Distributions missing conduit
    missing_c = find_distributions_without_conduit()
    if missing_c:
        logger.error("==== Underground Distribution Without Conduit (%d) ====", len(missing_c))
        logger.error("[Conduit Issues] {:<12} | {:<36} | {}".format("Distribution", "Vetro ID", "Issue"))
        for row in missing_c:
            logger.error("[Conduit Issues] {:<12} | {:<36} | {}".format(
                row.get("Distribution ID",""), row.get("Vetro ID",""), row.get("Issue","")))
        logger.info("==== End Underground Distribution Without Conduit ====")

    # 2) Conduits without Underground Distribution
    missing_d = find_conduits_without_distribution()
    if missing_d:
        logger.error("==== Conduit Without Underground Distribution (%d) ====", len(missing_d))
        logger.error("[Conduit Issues] {:<10} | {:<36} | {:^30}".format("Conduit ID", "Conduit Vetro ID", "Issue"))
        for row in missing_d:
            logger.error("[Conduit Issues] {:<10} | {:<36} | {}".format(
                row.get("Conduit ID",""), row.get("Conduit Vetro ID",""), row.get("Issue","")))
        logger.info("==== End Conduit Without Underground Distribution ====")

    # 3) Conduit type problems
    types = find_conduit_type_issues()
    if types:
        logger.error("==== Conduit Type Issues (%d) ====", len(types))
        logger.error("[Conduit Issues] {:<10} | {:<36} | {:<12} | {:^22}".format(
            "Conduit ID", "Conduit Vetro ID", "Conduit Type", "Issue"))
        for row in types:
            logger.error("[Conduit Issues] {:<10} | {:<36} | {:<12} | {}".format(
                row.get("Conduit ID",""), row.get("Conduit Vetro ID",""), (row.get("Conduit Type","") or "")[:12], row.get("Issue","")))
        logger.info("==== End Conduit Type Issues ====")




# def emit_conduit_logs(emit_info: bool = True) -> None:
#     """
#     Emit log lines for all Conduit checks (mirror of the Excel 'Conduit' sheet),
#     and also an Overview of every conduit feature (attributes + optional path).

#     Does not alter any existing logic — only prints to the log using current
#     config (LOG_DETAIL, LOG_INCLUDE_WALK_PATH).

#     Overview columns:
#       Conduit ID | Conduit Vetro ID | Conduit Type | #Segments | #Vertices | Path (optional)
#     Issue groups logged at ERROR level:
#       • Distribution Without Conduit
#       • Conduit Without Underground Distribution
#       • Conduit Type Issues
#       • Vaults Missing Conduit
#     """
#     import logging
#     import modules.config as cfg
#     from modules.basic.log_configs import format_table_lines

#     log = logging.getLogger(__name__)

#     # Respect LOG_DETAIL for how "chatty" the overview is
#     detail = str(getattr(cfg, "LOG_DETAIL", "DEBUG")).upper()
#     info_emit = log.debug if detail == "DEBUG" else log.info

#     # ----------------------------
#     # A) Overview of ALL conduits
#     # ----------------------------
#     headers = ["Conduit ID", "Conduit Vetro ID", "Conduit Type", "#Segments", "#Vertices", "Path"]
#     rows: list[list[str]] = []

#     def _path_preview(segments):
#         """Compact path preview per segment: first, maybe one middle, and last point."""
#         if not bool(getattr(cfg, "LOG_INCLUDE_WALK_PATH", False)):
#             return ""
#         previews = []
#         for seg in (segments or []):
#             if not seg:
#                 continue
#             pts = []
#             # first
#             pts.append(f"{seg[0][0]:.6f},{seg[0][1]:.6f}")
#             # maybe one middle point (avoid huge prints)
#             if len(seg) > 2:
#                 mid = seg[len(seg)//2]
#                 pts.append(f"{mid[0]:.6f},{mid[1]:.6f}")
#             # last
#             if len(seg) > 1:
#                 pts.append(f"{seg[-1][0]:.6f},{seg[-1][1]:.6f}")
#             previews.append(" → ".join(pts))
#         return " | ".join(previews)

#     for c in _load_conduits():
#         segs = c.get("segments", [])
#         seg_count = len(segs)
#         vtx_count = sum(len(s) for s in segs)
#         rows.append([
#             c.get("id", ""),
#             c.get("vetro_id", ""),
#             c.get("type", ""),
#             str(seg_count),
#             str(vtx_count),
#             _path_preview(segs),
#         ])

#     if rows and emit_info:
#         info_emit("===== [Conduit] Overview (all features) =====")
#         for line in format_table_lines(headers, rows, max_col_widths=[32, 36, 24, 9, 9, 120]):
#             info_emit(f"[Conduit] {line}")
#         info_emit("===== End [Conduit] Overview =====")

#     # -----------------------------------
#     # B) Issue tables (mirror Excel bits)
#     # -----------------------------------
#     def _issue_table(title: str, headers: list[str], items: list[dict] | None):
#         if not items:
#             return
#         lines = format_table_lines(headers, [[str(it.get(h, "")) for h in headers] for it in items])
#         log.error(f"==== {title} ({len(items)}) ====")
#         for ln in lines:
#             log.error(f"[Conduit Issues] {ln}")
#         log.info(f"==== End {title} ====")

#     # Use your existing finders (no logic changes)
#     results = run_all_conduit_checks()  # df_missing_conduit, conduit_missing_distribution, type_issues  :contentReference[oaicite:1]{index=1}
#     vault_missing = find_vaults_missing_conduit()  # {"Vault Vetro ID", "Issue"} rows  :contentReference[oaicite:2]{index=2}

#     _issue_table(
#         "Distribution Without Conduit",
#         ["Distribution ID", "Vetro ID", "Issue"],
#         results.get("df_missing_conduit"),
#     )
#     _issue_table(
#         "Conduit Without Underground Distribution",
#         ["Conduit ID", "Conduit Vetro ID", "Issue"],
#         results.get("conduit_missing_distribution"),
#     )
#     _issue_table(
#         "Conduit Type Issues",
#         ["Conduit ID", "Conduit Vetro ID", "Conduit Type", "Issue"],
#         results.get("type_issues"),
#     )
#     _issue_table(
#         "Vaults Missing Conduit",
#         ["Vault Vetro ID", "Issue"],
#         vault_missing,
#     )
