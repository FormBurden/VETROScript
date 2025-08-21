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


def run_all_conduit_checks() -> dict[str, List[dict]]:
    return {
        # Underground Distribution without any nearby conduit
        "df_missing_conduit": find_distributions_without_conduit(),
        # Conduits that don’t carry any underground distribution (NEW)
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

    # Allow an override, else fall back to the global threshold (~3 ft).
    M_TO_FT = 3.28084
    tol_m = (float(tolerance_ft) / M_TO_FT) if tolerance_ft is not None else THRESHOLD_M

    def _ptseg_distance_m(p: Tuple[float, float],
                          a: Tuple[float, float],
                          b: Tuple[float, float]) -> float:
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
        px, py =  plon * m_per_deg_lon,  plat * m_per_deg_lat

        vx, vy = (bx - ax), (by - ay)
        wx, wy = (px - ax), (py - ay)

        denom = (vx * vx + vy * vy)
        if denom <= 0.0:
            # a and b are the same point; distance to that point
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