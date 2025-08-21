# modules/simple_scripts/vault_rules.py
# Vault rules that (by necessity) reference conduit geometry.
#
# Data sources:
#   - Conduits:  *conduit*.geojson
#   - Vaults:    *vault*.geojson  (via geojson_loader.load_features('vault','vetro_id'))
#
# Rules implemented:
#   A) Vaults must sit on Conduit (flag any vault point with no conduit touching it).
#   B) Vault spacing along the same conduit run must be ≤ 500 ft between consecutive vaults.
#      • If a conduit run has <2 vaults and its length > 500 ft, flag that run.
#   C) Sharp bends (included angle < 130°) must have a vault at the bend OR a vault within
#      300 ft along the run from that bend.
#
# Notes:
#   • All file I/O uses modules.config.DATA_DIR.
#   • Proximity checks use THRESHOLD_M (≈ 3 ft).
#   • Distances are geodesic (haversine) and converted to feet.

from __future__ import annotations

import glob
import json
from typing import Dict, List, Tuple, Iterable

import modules.config
from modules.simple_scripts.geojson_loader import load_features
from modules.basic.distance_utils import haversine, THRESHOLD_M, bearing

M_TO_FT = 3.28084


# -----------------------------
# Conduit geometry helpers
# -----------------------------
def _load_conduits() -> List[dict]:
    """
    Load every *conduit*.geojson feature.
    Each returned dict has:
      {
        'id': <ID or ''>,
        'vetro_id': <vetro_id or ''>,
        'segments': [ [(lat,lon), ...], ... ]   # polyline segments
      }
    """
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

            segs: List[List[Tuple[float,float]]] = []
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
                # GeoJSON lon,lat → (lat,lon)
                poly.append([(round(lat, 6), round(lon, 6)) for lon, lat in seg])

            feats.append({
                "id":       (props.get("ID") or props.get("id") or "").strip(),
                "vetro_id": (props.get("vetro_id") or props.get("Vetro ID") or "").strip(),
                "segments": poly,
            })

    return feats


def _collect_conduit_vertices(conduits: Iterable[dict]) -> List[Tuple[float,float]]:
    verts: List[Tuple[float,float]] = []
    for c in conduits:
        for seg in c.get("segments", []):
            verts.extend(seg)
    return verts


def _polyline_length_m(seg: List[Tuple[float,float]]) -> float:
    if not seg or len(seg) < 2:
        return 0.0
    dist = 0.0
    for i in range(1, len(seg)):
        a = seg[i-1]; b = seg[i]
        dist += haversine(a[0], a[1], b[0], b[1])
    return dist


def _closest_vertex_index(seg: List[Tuple[float,float]], pt: Tuple[float,float]) -> int:
    """Return index of vertex in seg that is closest (by haversine) to pt."""
    lat, lon = pt
    best_i, best_d = 0, float("inf")
    for i, (slat, slon) in enumerate(seg):
        d = haversine(lat, lon, slat, slon)
        if d < best_d:
            best_d, best_i = d, i
    return best_i


def _distance_along(seg: List[Tuple[float,float]], i0: int, i1: int) -> float:
    """Path distance (meters) along seg from vertex i0 to i1 (inclusive)."""
    if i0 == i1:
        return 0.0
    lo, hi = (i0, i1) if i0 <= i1 else (i1, i0)
    # sum edges between lo..hi
    dist = 0.0
    for j in range(lo+1, hi+1):
        a, b = seg[j-1], seg[j]
        dist += haversine(a[0], a[1], b[0], b[1])
    return dist


def _angle_diff(a: float, b: float) -> float:
    """Smallest absolute difference between two bearings (deg in [0,180])."""
    d = abs((a - b + 180) % 360 - 180)
    return d


# ---------------------------------
# A) Vault must sit on conduit
# ---------------------------------

def find_vaults_missing_conduit(tolerance_ft: float | None = None) -> List[dict]:
    """
    Every vault coordinate must have conduit *under it*.

    Now checks distance to the nearest *segment* (not only conduit vertices).

    Returns rows:
      { "Vault Vetro ID": <str>, "Issue": "No Conduit at vault" }
    """
    from math import cos, radians, sqrt

    conduits = _load_conduits()
    vault_coords, vault_map = load_features("vault", "vetro_id")

    # Allow an override, else fall back to the global threshold (~3 ft).
    M_TO_FT = 3.28084
    tol_m = (float(tolerance_ft) / M_TO_FT) if tolerance_ft is not None else THRESHOLD_M

    def _ptseg_distance_m(
        p: Tuple[float, float],
        a: Tuple[float, float],
        b: Tuple[float, float],
    ) -> float:
        """
        Approximate point-to-segment distance in meters by projecting to a local
        equirectangular plane (very accurate at these small tolerances).
        """
        plat, plon = p
        alat, alon = a
        blat, blon = b

        lat0 = (plat + alat + blat) / 3.0
        m_per_deg_lat = 111320.0
        m_per_deg_lon = 111320.0 * cos(radians(lat0))

        # project to a flat plane around this latitude
        ax, ay = alon * m_per_deg_lon, alat * m_per_deg_lat
        bx, by = blon * m_per_deg_lon, blat * m_per_deg_lat
        px, py = plon * m_per_deg_lon, plat * m_per_deg_lat

        vx, vy = (bx - ax), (by - ay)
        wx, wy = (px - ax), (py - ay)

        denom = (vx * vx + vy * vy)
        if denom <= 0.0:
            # a and b are the same point; distance to that point
            dx, dy = (px - ax), (py - ay)
            return sqrt(dx * dx + dy * dy)

        # projection parameter t clamped to [0,1]
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


def find_vault_spacing_issues(max_gap_ft: float = 500.0) -> List[dict]:
    """
    Walk each conduit polyline; project touching vaults to the *nearest segment*
    (not just the nearest vertex); compute along-run distances between consecutive
    vaults; flag when the gap > max_gap_ft.

    If a run has <2 vaults and its total length > max_gap_ft, flag the entire run.

    Returns rows:
      {
        "Conduit ID": ,
        "Conduit Vetro ID": ,
        "From Vault": ,
        "To Vault": ,
        "Distance (ft)": ,
        "Limit (ft)": ,
        "Issue": "Vault spacing exceeds 500 ft"
      }
    """
    from math import cos, radians, sqrt

    def _ptseg_distance_and_t_m(
        p: Tuple[float, float],
        a: Tuple[float, float],
        b: Tuple[float, float],
    ) -> Tuple[float, float]:
        """
        Point-to-segment perpendicular distance (meters) and the clamped
        projection parameter t in [0,1] using a local equirectangular plane.
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
        if denom <= 0.0:
            # a and b coincide – distance to that point, t=0
            dx, dy = (px - ax), (py - ay)
            return sqrt(dx * dx + dy * dy), 0.0

        t = (wx * vx + wy * vy) / denom
        if t < 0.0:
            cx, cy = ax, ay
            t = 0.0
        elif t > 1.0:
            cx, cy = bx, by
            t = 1.0
        else:
            cx, cy = (ax + t * vx), (ay + t * vy)

        dx, dy = (px - cx), (py - cy)
        return sqrt(dx * dx + dy * dy), t

    vault_coords, vault_map = load_features("vault", "vetro_id")
    out: List[dict] = []
    lim_m = float(max_gap_ft) / M_TO_FT

    for c in _load_conduits():
        for seg in c.get("segments", []):
            if not seg or len(seg) < 2:
                continue

            # Precompute cumulative along-run vertex distances (meters)
            cum: List[float] = [0.0]
            for i in range(1, len(seg)):
                a, b = seg[i - 1], seg[i]
                cum.append(cum[-1] + haversine(a[0], a[1], b[0], b[1]))

            run_len_m = cum[-1]
            run_len_ft = run_len_m * M_TO_FT

            # Project every vault to this polyline; keep those within tolerance
            touching: List[tuple[float, str]] = []  # (along_m, vault_id)
            for (vlat, vlon) in vault_coords:
                best_dist_m = float("inf")
                best_along_m = None

                # scan each edge
                for i in range(1, len(seg)):
                    a, b = seg[i - 1], seg[i]
                    d_m, t = _ptseg_distance_and_t_m((vlat, vlon), a, b)
                    if d_m < best_dist_m:
                        # along = cum up to edge-start + t * edge length (geodesic)
                        edge_len_m = haversine(a[0], a[1], b[0], b[1])
                        along_m = cum[i - 1] + t * edge_len_m
                        best_dist_m = d_m
                        best_along_m = along_m

                if best_along_m is not None and best_dist_m <= THRESHOLD_M:
                    v_id = vault_map.get((round(vlat, 6), round(vlon, 6)), "")
                    touching.append((best_along_m, v_id))

            # Sort by along-run position
            touching.sort(key=lambda t: t[0])

            if len(touching) < 2 and run_len_ft > float(max_gap_ft):
                out.append({
                    "Conduit ID": c.get("id", ""),
                    "Conduit Vetro ID": c.get("vetro_id", ""),
                    "From Vault": touching[0][1] if touching else "(none)",
                    "To Vault": "(none)",
                    "Distance (ft)": round(run_len_ft, 1),
                    "Limit (ft)": float(max_gap_ft),
                    "Issue": "Vault spacing exceeds 500 ft",
                })
                continue

            # Check gaps between consecutive projected positions
            for i in range(1, len(touching)):
                a_along_m, v0 = touching[i - 1]
                b_along_m, v1 = touching[i]
                gap_m = b_along_m - a_along_m
                if gap_m > lim_m:
                    out.append({
                        "Conduit ID": c.get("id", ""),
                        "Conduit Vetro ID": c.get("vetro_id", ""),
                        "From Vault": v0 or "(unknown)",
                        "To Vault": v1 or "(unknown)",
                        "Distance (ft)": round(gap_m * M_TO_FT, 1),
                        "Limit (ft)": float(max_gap_ft),
                        "Issue": "Vault spacing exceeds 500 ft",
                    })

    return out


# ------------------------------------------------------------------------
# C) Sharp bends (<130° included) need a vault at bend or within 300 ft
# ------------------------------------------------------------------------
def find_bend_vault_issues(angle_threshold_deg: float = 130.0, max_distance_ft: float = 300.0) -> List[dict]:
    """
    For every interior vertex in each conduit run:
      - Compute included_angle = 180 - |bearing_diff|.
      - If included_angle < angle_threshold_deg (sharp bend), require:
          • a vault at the bend (within THRESHOLD_M), OR
          • the nearest vault along the run within max_distance_ft.
      - Otherwise flag.

    Returns rows:
      {
        "Conduit ID": <id>,
        "Conduit Vetro ID": <vetro_id>,
        "Bend Angle (deg)": <float>,
        "Nearest Vault": <vetro_id or "(none)">,
        "Distance (ft)": <float or ''>,
        "Limit (ft)": <max_distance_ft>,
        "Issue": "Sharp bend without nearby vault"
      }
    """
    vault_coords, vault_map = load_features("vault", "vetro_id")
    lim_m = float(max_distance_ft) / M_TO_FT

    out: List[dict] = []

    for c in _load_conduits():
        for seg in c.get("segments", []):
            if len(seg) < 3:
                continue

            # precompute bearings for consecutive edges
            bearings: List[float] = []
            for i in range(1, len(seg)):
                a = seg[i-1]; b = seg[i]
                bearings.append(bearing(a[0], a[1], b[0], b[1]))

            # interior vertices only
            for i in range(1, len(seg) - 1):
                b1 = bearings[i-1]
                b2 = bearings[i]
                turn = _angle_diff(b1, b2)     # 0 straight, 180 U-turn
                included = 180.0 - turn        # smaller = sharper

                if included >= float(angle_threshold_deg):
                    continue  # not sharp

                bend_pt = seg[i]

                # vault exactly at the bend?
                has_vault_here = any(haversine(bend_pt[0], bend_pt[1], vlat, vlon) <= THRESHOLD_M
                                     for (vlat, vlon) in vault_coords)
                if has_vault_here:
                    continue

                # nearest vault along-run from this vertex
                touching_ix_to_id: Dict[int, str] = {}
                for (vlat, vlon) in vault_coords:
                    j = _closest_vertex_index(seg, (vlat, vlon))
                    if haversine(vlat, vlon, seg[j][0], seg[j][1]) <= THRESHOLD_M:
                        touching_ix_to_id[j] = vault_map.get((round(vlat, 6), round(vlon, 6)), "")

                nearest_d_m = float("inf")
                nearest_v_id = "(none)"

                # search left
                for j in range(i-1, -1, -1):
                    if j in touching_ix_to_id:
                        d = _distance_along(seg, j, i)
                        nearest_d_m = d
                        nearest_v_id = touching_ix_to_id[j] or "(unknown)"
                        break
                # search right
                for j in range(i+1, len(seg)):
                    if j in touching_ix_to_id:
                        d = _distance_along(seg, i, j)
                        if d < nearest_d_m:
                            nearest_d_m = d
                            nearest_v_id = touching_ix_to_id[j] or "(unknown)"
                        break

                if nearest_d_m <= lim_m:
                    continue

                out.append({
                    "Conduit ID": c.get("id", ""),
                    "Conduit Vetro ID": c.get("vetro_id", ""),
                    "Bend Angle (deg)": round(included, 1),
                    "Nearest Vault": nearest_v_id,
                    "Distance (ft)": (round(nearest_d_m * M_TO_FT, 1) if nearest_d_m != float("inf") else ""),
                    "Limit (ft)": float(max_distance_ft),
                    "Issue": "Sharp bend without nearby vault",
                })

    return out


# ---------------------------------------
# Aggregator (for convenience)
# ---------------------------------------
def run_all_vault_checks() -> dict[str, List[dict]]:
    return {
        "vaults_missing_conduit":   find_vaults_missing_conduit(),
        "vault_spacing_issues":     find_vault_spacing_issues(),
        "bend_vault_issues":        find_bend_vault_issues(),
    }
