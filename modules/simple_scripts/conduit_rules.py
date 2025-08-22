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


def _build_conduit_path_tokens(c: dict) -> list[tuple[str, str, str]]:
    """
    Build a tokenized representation of the conduit Path for logging with type headers.

    Returns a list of (sep, text, kind) where:
      • sep  in {"", " > ", " -- ", " / "}   (separator printed *before* this token)
      • text is the visible token text
      • kind in {"DF","VAULT","NAP"}

    Rules:
      • Start at the first Vault vertex if present, else first vertex.
      • At DF changes: emit (" > ", DFID, "DF").
      • At a vertex with assets (vault/nap) in same DF segment:
          - if first asset at this vertex and not a DF change: (" -- ", vault/nap, kind)
          - if first asset and a DF just emitted: (" / ", vault/nap, kind)
          - two assets at a vertex become: (" / ", "vault", "VAULT"), (" / ", "nap", "NAP")
    """
    verts: list[tuple[float,float]] = []
    for seg in (c.get("segments") or []):
        verts.extend(seg)
    if not verts:
        return []

    vault_coords, vault_map = _load_vault_points_map()
    nap_coords,   nap_map   = _load_nap_points_map()
    df_segments              = _iter_df_segments_with_id()
    tol_m                    = THRESHOLD_M

    # Gather assets + DF at each vertex
    per: list[dict] = []
    for pt in verts:
        v_id = ""; n_id = ""
        # vault
        for (vlat, vlon) in vault_coords:
            if haversine(pt[0], pt[1], vlat, vlon) <= tol_m:
                v_id = vault_map.get((vlat, vlon), "")
                break
        # nap
        for (nlat, nlon) in nap_coords:
            if haversine(pt[0], pt[1], nlat, nlon) <= tol_m:
                n_id = nap_map.get((nlat, nlon), "")
                break
        df_id = _nearest_df_id_for_point(pt, df_segments, tol_m)
        per.append({"pt": pt, "vault": v_id, "nap": n_id, "df": df_id})

    # start at first vault if any
    start = 0
    for i, a in enumerate(per):
        if a["vault"]:
            start = i
            break

    tokens: list[tuple[str, str, str]] = []  # (sep, text, kind)
    prev_df: str | None = None
    inside_df_run = False

    for i in range(start, len(per)):
        a = per[i]
        cur_df = a["df"]
        has_v  = bool(a["vault"])
        has_n  = bool(a["nap"])

        # DF change?
        if cur_df and (cur_df != prev_df or not inside_df_run):
            sep = " > " if tokens else ""  # nothing before the very first DF
            tokens.append((sep, cur_df, "DF"))
            prev_df = cur_df
            inside_df_run = True  # we're now in a DF run
            # If assets exist at this *same* vertex, first separator into assets is " / "
            first_asset_sep = " / "
        else:
            # Same DF as before
            first_asset_sep = " -- " if tokens else ""  # very first thing overall has no sep

        # assets on this vertex
        if has_v or has_n:
            emitted_any = False
            if has_v:
                tokens.append((first_asset_sep if not emitted_any else " / ", a["vault"], "VAULT"))
                emitted_any = True
            if has_n:
                tokens.append((" / " if emitted_any else first_asset_sep, a["nap"], "NAP"))
                emitted_any = True

    return tokens


def _wrap_tokens_for_width(tokens: list[tuple[str,str,str]], width: int) -> list[list[tuple[str,str,str]]]:
    """
    Break tokens into lines so that the rendered visible width <= width.
    We measure "sep+text" per token; we never split a token.
    """
    lines: list[list[tuple[str,str,str]]] = []
    cur: list[tuple[str,str,str]] = []
    cur_len = 0
    for sep, text, kind in tokens:
        add_len = len(sep) + len(text)
        if cur and (cur_len + add_len) > width:
            lines.append(cur)
            cur = [(sep, text, kind)]
            cur_len = add_len
        else:
            cur.append((sep, text, kind))
            cur_len += add_len
    if cur:
        lines.append(cur)
    return lines


def _render_header_and_path_for_segment(
    seg: list[tuple[str, str, str]],
    suppress_leading_bar: bool = False,
    add_trailing_bar: bool = True
) -> tuple[str, str]:
    """
    Render a header (with bars) and the matching path line for one width-safe token segment.

    - If suppress_leading_bar=True, we do NOT start the header with ' | ' or ' || ' even if the
      first token's visible separator is ' / ' or ' -- '. This prevents a blank opening bar on wrapped lines.
    - Always centers headers over each token's text width.
    - If add_trailing_bar=True, append a single ' |' at the end so we can size the dash rule perfectly.

    Returns (header_line, path_line).
    """
    header_line = ""
    path_line = ""
    first = True

    def label_for(kind: str) -> str:
        if kind == "DF":
            return "Distribution Fiber"
        if kind == "VAULT":
            return "Vault"
        if kind == "NAP":
            return "NAP"
        return ""

    for sep, text, kind in seg:
        # HEADER: draw separator *only* if it's not the first visible item on a wrapped line
        if sep == " / ":
            if not (first and suppress_leading_bar):
                header_line += " | "
            path_line += " / "
        elif sep == " -- ":
            if not (first and suppress_leading_bar):
                header_line += " || "
            path_line += " -- "
        elif sep == " > ":
            # Spaces keep header aligned where ' > ' exists in the path.
            header_line += "   "
            path_line += " > "
        else:
            # No separator at the very first token for both lines.
            pass

        first = False

        # Token text
        path_line += text

        # Center label above this token's width
        w = len(text)
        lbl = label_for(kind)
        if w <= 0:
            hdr = ""
        else:
            if len(lbl) > w:
                hdr = lbl[:w]
            else:
                pad = w - len(lbl)
                left = pad // 2
                right = pad - left
                hdr = (" " * left) + lbl + (" " * right)

        header_line += hdr

    if add_trailing_bar and seg:
        header_line += " |"

    return (header_line, path_line)


def emit_conduit_logs(emit_info: bool = True) -> None:
    """
    Console log rendering for Conduit.

    When modules.config.LOG_INCLUDE_WALK_PATH is True:
      • Wider fixed columns (less truncation)
      • Path rendered via tokens with a centered "type header" above it
      • Wrap at 400 chars on token boundaries
      • ' | ' above ' / ' separators, ' || ' above ' -- ', blanks above ' > '
      • No leading bar at the start of wrapped header lines
      • Dash rule sized exactly to the end of the last '|' of the header row
      • Path lines include a *blank* Paths cell so the text aligns under the dynamic header.
    """
    import logging
    logger = logging.getLogger(__name__)
    import modules.config as cfg

    conduits = _load_conduits()
    fancy = bool(getattr(cfg, "LOG_INCLUDE_WALK_PATH", False))

    if not fancy:
        # ---------- Simple Overview (fallback when fancy off) ----------
        logger.info("[Conduit] {:<12} | {:<36} | {:<24} | {:<9} | {:<9} | {}".format(
            "Conduit ID", "Vetro ID", "Conduit Type", "#Segments", "#Vertices", "Path"
        ))
        for c in conduits:
            segs = c.get("segments") or []
            nseg = len(segs)
            nvert = sum(len(s) for s in segs)
            chain_lines = _wrap_path(_build_conduit_named_path(c), width=250)
            for li, line in enumerate(chain_lines or [""]):
                if li == 0:
                    logger.info("[Conduit] {:<12} | {:<36} | {:<24} | {:<9} | {:<9} | {}".format(
                        c.get("id",""),
                        c.get("vetro_id",""),
                        (c.get("type","") or "")[:24],
                        str(nseg), str(nvert), line
                    ))
                else:
                    logger.info("[Conduit] {:<12} | {:<36} | {:<24} | {:<9} | {:<9} | {}".format(
                        "", "", "", "", "", line
                    ))
        return

    # ---------- Fancy Overview with walk-path headers ----------
    CID_W, VID_W, TYP_W, SEG_W, VTX_W = 12, 36, 24, 9, 9
    PATH_W = 400

    # Keep these EXACT so spacing matches the log examples
    PATHS_LABEL = "Paths -->  "      # note two spaces after the arrow
    PATHS_CELL_HDR = PATHS_LABEL + "| "    # used on metadata+header rows
    PATHS_CELL_BLANK = (" " * len(PATHS_LABEL)) + "| "  # same width, but blank label (for path lines)

    # Top static header
    logger.info("[Conduit] {:<{}} | {:<{}} | {:<{}} | {:<{}} | {:<{}} | {}"
        .format("Conduit ID", CID_W,
                "Vetro ID",  VID_W,
                "Conduit Type", TYP_W,
                "#Segments", SEG_W,
                "#Vertices", VTX_W,
                PATHS_LABEL + "|"))

    for c in conduits:
        segs = c.get("segments") or []
        nseg = len(segs)
        nvert = sum(len(s) for s in segs)

        tokens = _build_conduit_path_tokens(c)

        # Left padding for subsequent lines (blank metadata columns)
        meta_pad = "[Conduit] {:<{}} | {:<{}} | {:<{}} | {:<{}} | {:<{}} | ".format(
            "", CID_W, "", VID_W, "", TYP_W, "", SEG_W, "", VTX_W
        )

        if not tokens:
            # No path tokens—still show metadata + header cell, then a dash rule
            first_row = "[Conduit] {:<{}} | {:<{}} | {:<{}} | {:<{}} | {:<{}} | {}".format(
                c.get("id",""), CID_W,
                c.get("vetro_id",""), VID_W,
                (c.get("type","") or ""), TYP_W,
                str(nseg), SEG_W,
                str(nvert), VTX_W,
                PATHS_LABEL + "|"
            )
            logger.info(first_row)
            logger.info("-" * len(first_row))
            continue

        # Wrap tokens to the PATH_W width
        lines = _wrap_tokens_for_width(tokens, PATH_W)

        # ---- First segment: metadata row + dynamic header
        hdr_line, path_line = _render_header_and_path_for_segment(
            lines[0],
            suppress_leading_bar=False,  # first header segment follows the 'Paths' cell
            add_trailing_bar=True
        )

        first_row = "[Conduit] {:<{}} | {:<{}} | {:<{}} | {:<{}} | {:<{}} | {}{}".format(
            c.get("id",""), CID_W,
            c.get("vetro_id",""), VID_W,
            (c.get("type","") or ""), TYP_W,
            str(nseg), SEG_W,
            str(nvert), VTX_W,
            PATHS_CELL_HDR, hdr_line
        )
        logger.info(first_row)

        # Second line: blank Paths cell + the path text (so it lines up under the header)
        second_row = meta_pad + PATHS_CELL_BLANK + path_line
        logger.info(second_row)

        # Dash rule to the end of the header row
        logger.info("-" * len(first_row))

        # ---- Continuations (wrapped segments) ----
        for seg in lines[1:]:
            cont_hdr, cont_path = _render_header_and_path_for_segment(
                seg,
                suppress_leading_bar=True,   # no leading bar at wrapped start
                add_trailing_bar=True
            )
            # Continuation header + line both include a blank Paths cell for alignment
            cont_hdr_row = meta_pad + PATHS_CELL_BLANK + cont_hdr
            cont_path_row = meta_pad + PATHS_CELL_BLANK + cont_path

            logger.info(cont_hdr_row)
            logger.info(cont_path_row)
            logger.info("-" * len(cont_hdr_row))

    # -----------------------------------
    # Issue sections (unchanged)
    # -----------------------------------
    missing_c = find_distributions_without_conduit()
    if missing_c:
        logger.error("==== Underground Distribution Without Conduit (%d) ====", len(missing_c))
        logger.error("[Conduit Issues] {:<12} | {:<36} | {}".format("Distribution", "Vetro ID", "Issue"))
        for row in missing_c:
            logger.error("[Conduit Issues] {:<12} | {:<36} | {}".format(
                row.get("Distribution ID",""), row.get("Vetro ID",""), row.get("Issue","")))
        logger.info("==== End Underground Distribution Without Conduit ====")

    missing_d = find_conduits_without_distribution()
    if missing_d:
        logger.error("==== Conduit Without Underground Distribution (%d) ====", len(missing_d))
        logger.error("[Conduit Issues] {:<10} | {:<36} | {:^30}".format("Conduit ID", "Conduit Vetro ID", "Issue"))
        for row in missing_d:
            logger.error("[Conduit Issues] {:<10} | {:<36} | {}".format(
                row.get("Conduit ID",""), row.get("Conduit Vetro ID",""), row.get("Issue","")))
        logger.info("==== End Conduit Without Underground Distribution ====")

    types = find_conduit_type_issues()
    if types:
        logger.error("==== Conduit Type Issues (%d) ====", len(types))
        logger.error("[Conduit Issues] {:<10} | {:<36} | {:<12} | {:^22}".format(
            "Conduit ID", "Conduit Vetro ID", "Conduit Type", "Issue"))
        for row in types:
            logger.error("[Conduit Issues] {:<10} | {:<36} | {:<12} | {}".format(
                row.get("Conduit ID",""), row.get("Conduit Vetro ID",""), (row.get("Conduit Type","") or "")[:12], row.get("Issue","")))
        logger.info("==== End Conduit Type Issues ====")