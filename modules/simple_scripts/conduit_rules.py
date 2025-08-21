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


# modules/simple_scripts/conduit_rules.py  — replace the entire function

def emit_conduit_logs(emit_info: bool = True) -> None:
    """
    Emit log lines for all Conduit checks (mirror of the Excel 'Conduit' sheet),
    plus an Overview of every conduit feature (attributes + a named Path chain).

    Path chain rules (instead of coordinate breadcrumbs):
      • Use the same end-to-end flow as the Distribution/NAP walker, in practice
        by ordering NAPs according to walker order when available, and otherwise
        by along-conduit vertex order.
      • Tokens included on the chain:
          - Distribution lines "on" the conduit:  <Distribution ID> / <Vetro ID>
          - Vaults on the conduit:                 <Vault Vetro ID>
          - NAPs on the conduit:                   <NAP ID>
      • When a Vault and NAP share the same point, join with " / " (e.g.,
        "VetroID / NAPID").
      • Use " > " when the *distribution line changes*.
      • Within an unbroken distribution, separate the successive Vault/NAP
        points using " -- ".
      • Start the chain at the **first Vault** encountered on the conduit if one
        exists.

    Wrapping:
      • The Path column is wrapped to lines of up to 250 visible characters.
      • Wrap can only occur at: " > ", " -- ", " / ".
      • Continuation lines repeat all non-Path columns (aligned), so the block
        reads vertically.

    This function only logs; it does not modify the underlying results used for
    Excel.
    """
    import json
    import logging
    from pathlib import Path

    import modules.config as cfg
    from modules.basic.log_configs import format_table_lines

    log = logging.getLogger(__name__)

    # ---------------------------------------------------------
    # Small helpers (kept local; no change to business logic)
    # ---------------------------------------------------------
    def _data_dir() -> Path:  # Always use project-wide DATA_DIR
        return Path(getattr(cfg, "DATA_DIR"))

    def _read_geojson_many(globs: list[str]) -> list[dict]:
        """Load features from DATA_DIR using one or more glob patterns."""
        feats: list[dict] = []
        for pat in globs:
            for fp in sorted(_data_dir().glob(pat)):
                try:
                    with fp.open("r", encoding="utf-8") as f:
                        gj = json.load(f)
                    for ft in (gj.get("features") or []):
                        feats.append(ft)
                except Exception:
                    continue
        return feats

    def _as_point_coords(feat: dict) -> tuple[float, float] | None:
        try:
            g = feat.get("geometry") or {}
            if g.get("type") == "Point":
                lon, lat = g.get("coordinates", [None, None])
                if lon is not None and lat is not None:
                    return (float(lon), float(lat))
        except Exception:
            pass
        return None

    def _as_lines_coords(feat: dict) -> list[list[tuple[float, float]]]:
        """Return list of segments: for LineString → [coords], for MultiLineString → [coords1, coords2, ...]."""
        out: list[list[tuple[float, float]]] = []
        g = feat.get("geometry") or {}
        t = g.get("type")
        if t == "LineString":
            coords = g.get("coordinates") or []
            out.append([(float(x), float(y)) for x, y in coords if x is not None and y is not None])
        elif t == "MultiLineString":
            for line in (g.get("coordinates") or []):
                out.append([(float(x), float(y)) for x, y in line if x is not None and y is not None])
        return out

    def _first_nonempty(*vals: str) -> str:
        for v in vals:
            if v:
                return v
        return ""

    # Visible-length (strip ANSI if any)
    import re
    _ansi_re = re.compile(r"\x1b\[[0-9;]*m")
    def _vlen(s: str) -> int:
        return len(_ansi_re.sub("", s))

    # Soft equality for coordinates when snapping points to polyline vertices.
    # (We intentionally avoid geometric libs; this is only for log presentation.)
    EPS = 1e-7  # ~1 cm at mid-lat in lon/lat degrees; tweak if needed
    def _pt_eq(a: tuple[float, float], b: tuple[float, float]) -> bool:
        return abs(a[0] - b[0]) <= EPS and abs(a[1] - b[1]) <= EPS

    def _collect_conduits() -> list[dict]:  # Existing internal loader already used elsewhere in this module
        return list(_load_conduits())

    def _collect_vault_points() -> list[tuple[tuple[float, float], str]]:
        """Return [(pt, vault_vetro_id)] including both standard vaults and T3."""
        feats = _read_geojson_many([
            "vault*.geojson", "vaults*.geojson", "t-3-vault*.geojson", "t3-vault*.geojson"
        ])
        out: list[tuple[tuple[float, float], str]] = []
        for ft in feats:
            pt = _as_point_coords(ft)
            if not pt:
                continue
            p = ft.get("properties") or {}
            vetro = _first_nonempty(str(p.get("vetro_id") or ""), str(p.get("Vetro ID") or ""))
            if not vetro:
                vetro = str(p.get("id") or p.get("ID") or "")
            if pt:
                out.append((pt, vetro))
        return out

    def _collect_nap_points() -> list[tuple[tuple[float, float], str]]:
        """Return [(pt, nap_label)] using the friendly NAP ID label."""
        feats = _read_geojson_many(["nap*.geojson", "NAP*.geojson"])
        out: list[tuple[tuple[float, float], str]] = []
        for ft in feats:
            pt = _as_point_coords(ft)
            if not pt:
                continue
            p = ft.get("properties") or {}
            # Try common fields used in this project
            nap_label = _first_nonempty(
                str(p.get("NAP ID") or ""),
                str(p.get("NAP") or ""),
                str(p.get("label") or ""),
                str(p.get("Name") or ""),
            )
            if not nap_label:  # Last resort: use vetro + type
                nap_label = _first_nonempty(str(p.get("id") or ""), str(p.get("vetro_id") or ""))
            out.append((pt, nap_label))
        return out

    def _collect_ug_distributions() -> list[tuple[list[list[tuple[float, float]]], str, str]]:
        """Return [(segments, dist_label, dist_vetro)] from Underground Distribution layer."""
        feats = _read_geojson_many(["fiber-distribution-underground*.geojson"])
        out: list[tuple[list[list[tuple[float, float]]], str, str]] = []
        for ft in feats:
            segs = _as_lines_coords(ft)
            if not segs:
                continue
            p = ft.get("properties") or {}
            dist_label = _first_nonempty(
                str(p.get("Distribution ID") or ""),
                str(p.get("label") or ""),
                str(p.get("Name") or ""),
            )
            dist_vetro = _first_nonempty(str(p.get("vetro_id") or ""), str(p.get("Vetro ID") or ""), str(p.get("id") or ""))
            out.append((segs, dist_label, dist_vetro))
        return out

    # Walker-derived NAP order (optional, best-effort)
    def _nap_order_from_walker() -> dict[str, int]:
        """
        Build a first-appearance order index for NAP labels by parsing the
        Distribution/NAP walker paths when available.
        """
        order: dict[str, int] = {}
        idx = 0
        try:
            # Present in your hard_scripts walker
            from modules.hard_scripts.distribution_walker import get_walk_paths_map  # type: ignore
            pm = get_walk_paths_map() or {}
            # NAP IDs look like '##.AAAA.BBB.N##' optionally followed by ' (...)'
            nap_re = re.compile(r"\b\d{2}\.[A-Z0-9]+\.[A-Z0-9]+\.[Nn]\d+\b(?:\s*\([^)]*\))?")
            for _sid, path in pm.items():
                for m in nap_re.finditer(str(path)):
                    nap = m.group(0)
                    if nap not in order:
                        order[nap] = idx
                        idx += 1
        except Exception:
            pass
        return order

    NAP_ORDER = _nap_order_from_walker()

    def _build_conduit_named_path(conduit: dict) -> str:
        """
        Construct the named chain for one conduit, following the rules above.
        We use:
          • vertex-based snapping to detect Vaults/NAPs 'on' the conduit (EPS tolerance),
          • distribution touches when any distribution vertex equals a conduit vertex,
          • NAP ordering from walker when available; otherwise conduit vertex order.
        """
        segs = conduit.get("segments") or []
        # Flatten conduit vertices with index → [(i,(lon,lat))]
        verts: list[tuple[int, tuple[float, float]]] = []
        v_i = 0
        for seg in segs:
            for pt in (seg or []):
                verts.append((v_i, (float(pt[0]), float(pt[1]))))
                v_i += 1  # do not duplicate between segments; index just keeps increasing
        if not verts:
            return ""

        # Snap Vaults and NAPs by vertex proximity (EPS)
        vault_pts = _collect_vault_points()
        nap_pts = _collect_nap_points()
        dists = _collect_ug_distributions()

        # index of conduit vertex -> {'vault':[...], 'nap':[...], 'dist':[...]}
        at: dict[int, dict[str, list[str]]] = {}

        def _push(kind: str, idx: int, txt: str):
            at.setdefault(idx, {}).setdefault(kind, []).append(txt)

        # map Distribution first-touch index
        dist_first_touch: dict[str, int] = {}

        # NAPs
        for (nap_xy, nap_label) in nap_pts:
            for vi, vxy in verts:
                if _pt_eq(nap_xy, vxy):
                    _push("nap", vi, nap_label)
                    break

        # Vaults
        for (vt_xy, vt_vetro) in vault_pts:
            for vi, vxy in verts:
                if _pt_eq(vt_xy, vxy):
                    _push("vault", vi, vt_vetro)
                    break

        # Distributions: consider a distribution 'touching' at the first shared vertex
        for seg_list, dlabel, dv in dists:
            found_vi = None
            for seg in seg_list:
                for p in seg:
                    for vi, vxy in verts:
                        if _pt_eq(p, vxy):
                            found_vi = vi
                            break
                    if found_vi is not None:
                        break
                if found_vi is not None:
                    break
            if found_vi is not None:
                token = f"{_first_nonempty(dlabel)} / {dv}" if dv else _first_nonempty(dlabel)
                _push("dist", found_vi, token)
                # Remember the first time this distribution appears
                dist_first_touch.setdefault(token, found_vi)

        if not at and not dist_first_touch:
            return ""

        # Choose starting index: first vertex that has a Vault, else 0
        start_idx = None
        for vi in sorted(at.keys()):
            if "vault" in at[vi]:
                start_idx = vi
                break
        if start_idx is None:
            start_idx = 0

        # Build ordered sequence across vertices, using walker NAP order (if present) to
        # sort multiple NAPs that land on the same vertex.
        current_dist: str | None = None
        pieces: list[str] = []

        for vi, vxy in verts:
            if vi < start_idx:
                continue

            items = at.get(vi, {})

            # Distribution switch?
            dist_tokens = items.get("dist", [])
            sw_token = None
            # If multiple dists touch at this vertex, keep the one that 'starts' earliest
            if dist_tokens:
                sw_token = sorted(dist_tokens, key=lambda t: dist_first_touch.get(t, vi))[0]

            if sw_token and sw_token != current_dist:
                # Change in distribution → use " > "
                pieces.append((" > ", sw_token))
                current_dist = sw_token

            # Collocate Vault/NAP at the same vertex → " / " cell_tokens
            cell_tokens: list[str] = []
            # Vaults first (deterministic)
            for vv in sorted(items.get("vault", [])):
                if vv:
                    cell_tokens.append(vv)
            # NAPs next, ordered by walker index if available
            naps_here = items.get("nap", []) or []
            naps_here_sorted = sorted(naps_here, key=lambda n: NAP_ORDER.get(n, 10_000))
            for nv in naps_here_sorted:
                if nv:
                    cell_tokens.append(nv)

            if cell_tokens:
                # If already in a distribution, use " -- " between points within same dist
                sep = " -- " if current_dist else " > "
                pieces.append((sep, " / ".join(cell_tokens)))

        # Render to a single string with separators
        chain = ""
        for sep, token in pieces:
            if not token:
                continue
            if not chain:  # strip leading ' > ' if present
                chain = token
            else:
                chain += f"{sep}{token}"
        return chain

    def _wrap_chain(chain: str, width: int = 250) -> list[str]:
        """
        Wrap a chain on boundaries ' > ', ' -- ', ' / ' so that each line is <= width
        visible characters.

        Returns list of lines.
        """
        if not chain:
            return [""]

        # Tokenize by the three separators, but keep them associated.
        # We build a list of (sep, token) where the first item may have "" sep.
        parts: list[tuple[str, str]] = []
        pat = re.compile(r"( > | -- | / )")
        tokens = pat.split(chain)  # tokens like [chunk, sep, chunk, sep, chunk, ...]
        if tokens:
            first = tokens[0]
            parts.append(("", first))
            i = 1
            while i + 1 < len(tokens):
                parts.append((tokens[i], tokens[i + 1]))
                i += 2

        lines: list[str] = []
        cur = ""
        for sep, tok in parts:
            add = (sep + tok) if cur else tok
            if _vlen(cur) + _vlen(add) <= width:
                cur += add
            else:
                # push current line, start a new one with token (without splitting it)
                if cur:
                    lines.append(cur)
                cur = tok  # start new line without carrying separator visually at column 0
        if cur:
            lines.append(cur)
        return lines

    # Respect LOG_DETAIL for how "chatty" the overview is
    detail = str(getattr(cfg, "LOG_DETAIL", "DEBUG")).upper()
    info_emit = log.debug if detail == "DEBUG" else log.info

    # --------------------------------
    # A) Overview of ALL conduit rows
    # --------------------------------
    headers = ["Conduit ID", "Conduit Vetro ID", "Conduit Type", "#Segments", "#Vertices", "Path"]
    rows_expanded: list[list[str]] = []

    conduits = _collect_conduits()
    for c in conduits:
        segs = c.get("segments", [])
        seg_count = len(segs)
        vtx_count = sum(len(s) for s in segs)

        path_chain = _build_conduit_named_path(c)
        wrapped = _wrap_chain(path_chain, width=250) or [""]

        # First (main) line
        rows_expanded.append([
            c.get("id", ""),
            c.get("vetro_id", ""),
            c.get("type", ""),
            str(seg_count),
            str(vtx_count),
            wrapped[0],
        ])
        # Continuations: blank the fixed cols except keep them aligned by padding
        for cont in wrapped[1:]:
            rows_expanded.append(["", "", "", "", "", cont])

    if rows_expanded and emit_info:
        info_emit("===== [Conduit] Overview (all features) =====")
        for line in format_table_lines(
            headers,
            rows_expanded,
            max_col_widths=[32, 36, 24, 9, 9, 250],
            center_headers=True,   # center the headers (like the Walker log)
        ):
            info_emit(f"[Conduit] {line}")
        info_emit("===== End [Conduit] Overview =====")

    # -----------------------------------
    # B) Issue tables (mirror Excel bits)
    # -----------------------------------
    def _issue_table(title: str, headers: list[str], items: list[dict] | None):
        if not items:
            return
        lines = format_table_lines(
            headers,
            [[str(it.get(h, "")) for h in headers] for it in items],
            center_headers=True,   # center issue-table headers too
        )
        log.error(f"==== {title} ({len(items)}) ====")
        for ln in lines:
            log.error(f"[Conduit Issues] {ln}")
        log.info(f"==== End {title} ====")

    # Use your existing finders (no logic changes)
    results = run_all_conduit_checks()      # existing function producing Excel’s three issue lists
    vault_missing = find_vaults_missing_conduit()  # existing function

    _issue_table(
        "Distribution Without Conduit",
        ["Distribution ID", "Vetro ID", "Issue"],
        results.get("df_missing_conduit"),
    )
    _issue_table(
        "Conduit Without Underground Distribution",
        ["Conduit ID", "Conduit Vetro ID", "Issue"],
        results.get("conduit_missing_distribution"),
    )
    _issue_table(
        "Conduit Type Issues",
        ["Conduit ID", "Conduit Vetro ID", "Conduit Type", "Issue"],
        results.get("type_issues"),
    )
    _issue_table(
        "Vaults Missing Conduit",
        ["Vault Vetro ID", "Issue"],
        vault_missing,
    )




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
