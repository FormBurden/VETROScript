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


def emit_conduit_logs(emit_info: bool = True) -> None:
    """
    Emit log lines for all Conduit checks (Excel 'Conduit' sheet mirror),
    plus an Overview of every conduit feature (attributes + a named Path chain).

    Path chain:
      • Walk order matches along-conduit geometry; ties for NAPs at same spot use
        the walker’s first-appearance order if available.
      • Items:
          - Distribution-on-conduit:  <Distribution ID>
          - Vault on conduit:         <Vault Vetro ID>
          - NAP on conduit:           <NAP ID (label)>
      • Separators:
          - ' / ' between Vault and NAP that are at the *same* location
          - ' > ' when the distribution changes
          - ' -- ' between successive locations under the same distribution
        If a distribution change happens exactly where Vault/NAP exist, they are
        combined:  `> <Distribution ID> / <Vault> / <NAP>`.
      • Start at the first Vault on that conduit (if any).

    Wrapping:
      • Path column wraps at ≤250 visible chars, only at ' > ', ' -- ', or ' / '.
      • Continuation lines repeat only the Path cell.

    NOTE: This is a *presentation-only* path builder; no business logic is changed.
    """
    import json
    import math
    import logging
    from pathlib import Path
    import modules.config as cfg
    from modules.basic.log_configs import format_table_lines

    log = logging.getLogger(__name__)

    # ---------------------------------------------------------
    # Utilities
    # ---------------------------------------------------------
    def _data_dir() -> Path:
        return Path(getattr(cfg, "DATA_DIR"))

    # Visible-length (strip ANSI if present)
    import re
    _ansi_re = re.compile(r"\x1b\[[0-9;]*m")
    def _vlen(s: str) -> int:
        return len(_ansi_re.sub("", str(s)))

    def _read_geojson_many(globs: list[str]) -> list[dict]:
        feats: list[dict] = []
        for pat in globs:
            for fp in sorted(_data_dir().glob(pat)):
                try:
                    with fp.open("r", encoding="utf-8") as f:
                        gj = json.load(f)
                    feats.extend(gj.get("features") or [])
                except Exception:
                    continue
        return feats

    def _as_point_coords(ft: dict) -> tuple[float, float] | None:
        g = (ft or {}).get("geometry") or {}
        if g.get("type") == "Point":
            c = g.get("coordinates") or []
            if len(c) >= 2 and c[0] is not None and c[1] is not None:
                return (float(c[0]), float(c[1]))
        return None

    def _as_lines_coords(ft: dict) -> list[list[tuple[float, float]]]:
        out: list[list[tuple[float, float]]] = []
        g = (ft or {}).get("geometry") or {}
        t = g.get("type")
        if t == "LineString":
            coords = g.get("coordinates") or []
            out.append([(float(x), float(y)) for x, y in coords if x is not None and y is not None])
        elif t == "MultiLineString":
            for line in g.get("coordinates") or []:
                out.append([(float(x), float(y)) for x, y in line if x is not None and y is not None])
        return out

    def _first_nonempty(*vals: str) -> str:
        for v in vals:
            if v:
                return str(v)
        return ""

    # Light-weight planar distance (lon scaled by cos(lat))
    def _planar_dx_dy(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
        ax, ay = a
        bx, by = b
        cy = math.radians((ay + by) * 0.5)
        scale = math.cos(cy)
        return ( (bx - ax) * scale, (by - ay) )

    def _dist2(a: tuple[float, float], b: tuple[float, float]) -> float:
        dx, dy = _planar_dx_dy(a, b)
        return dx*dx + dy*dy

    def _point_seg_distance2(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float, float]:
        """
        Return (d2, t, seg_len) where:
          • d2 = squared planar distance from point p to segment ab
          • t in [0,1] is the projection parameter along ab
          • seg_len is the planar length of ab (not squared)
        """
        dx, dy = _planar_dx_dy(a, b)
        seg_len2 = dx*dx + dy*dy
        if seg_len2 == 0.0:
            return (_dist2(p, a), 0.0, 0.0)
        px, py = _planar_dx_dy(a, p)
        t = (px*dx + py*dy) / seg_len2
        if t < 0.0:
            t = 0.0
        elif t > 1.0:
            t = 1.0
        proj = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        d2 = _dist2(p, proj)
        return (d2, t, math.sqrt(seg_len2))

    # Tolerances ~ meters → degrees (planar, since we scale lon)
    # tol_point_on_line ~3m; pos grouping ~1m
    DEG_PER_M = 1.0 / 111000.0
    TOL_D2 = (3.0 * DEG_PER_M) ** 2
    POS_TOL = 1.0 * DEG_PER_M

    # ---------------------------------------------------------
    # Data collectors (presentation only; use DATA_DIR)
    # ---------------------------------------------------------
    def _collect_conduits() -> list[dict]:
        # Use the internal loader so segment lists & attrs match existing logic
        return list(_load_conduits())

    def _collect_vault_points() -> list[tuple[tuple[float, float], str]]:
        feats = _read_geojson_many([
            "vault*.geojson", "vaults*.geojson", "t-3-vault*.geojson", "t3-vault*.geojson"
        ])
        out: list[tuple[tuple[float, float], str]] = []
        for ft in feats:
            pt = _as_point_coords(ft)
            if not pt:
                continue
            p = (ft.get("properties") or {})
            vetro = _first_nonempty(p.get("vetro_id", ""), p.get("Vetro ID", ""), p.get("id", ""), p.get("ID", ""))
            if vetro:
                out.append((pt, str(vetro)))
        return out

    def _collect_nap_points() -> list[tuple[tuple[float, float], str]]:
        feats = _read_geojson_many(["nap*.geojson", "NAP*.geojson"])
        out: list[tuple[tuple[float, float], str]] = []
        for ft in feats:
            pt = _as_point_coords(ft)
            if not pt:
                continue
            p = (ft.get("properties") or {})
            label = _first_nonempty(
                p.get("NAP ID", ""), p.get("NAP", ""), p.get("label", ""), p.get("Name", ""), p.get("id", ""), p.get("vetro_id", "")
            )
            if label:
                out.append((pt, str(label)))
        return out

    def _collect_ug_distributions() -> list[tuple[list[list[tuple[float, float]]], str]]:
        feats = _read_geojson_many(["fiber-distribution-underground*.geojson"])
        out: list[tuple[list[list[tuple[float, float]]], str]] = []
        for ft in feats:
            segs = _as_lines_coords(ft)
            if not segs:
                continue
            p = (ft.get("properties") or {})
            dist_label = _first_nonempty(p.get("Distribution ID", ""), p.get("label", ""), p.get("Name", ""))
            if dist_label:
                out.append((segs, str(dist_label)))
        return out

    # Optional: first-appearance order from walker (for tie-breaking of NAPs)
    def _nap_order_from_walker() -> dict[str, int]:
        order: dict[str, int] = {}
        try:
            from modules.hard_scripts.distribution_walker import get_walk_paths_map  # type: ignore
            pm = get_walk_paths_map() or {}
            nap_re = re.compile(r"\b\d{2}\.[A-Z0-9]+\.[A-Z0-9]+\.[Nn]\d+\b(?:\s*\([^)]*\))?")
            idx = 0
            for _sid, path in pm.items():
                for m in nap_re.finditer(str(path)):
                    t = m.group(0)
                    if t not in order:
                        order[t] = idx
                        idx += 1
        except Exception:
            pass
        return order

    NAP_ORDER = _nap_order_from_walker()

    # ---------------------------------------------------------
    # Geometry helpers on the conduit
    # ---------------------------------------------------------
    def _flatten_conduit_vertices(conduit: dict) -> tuple[list[tuple[float, float]], list[tuple[int, int]], list[float]]:
        """
        Return (verts, seg_idx_pairs, cumdist):
          • verts: [ (lon,lat) ... ] across all conduit parts (LineString/MultiLineString flattened)
          • seg_idx_pairs: [ (i, i+1) ... ] index pairs for each segment
          • cumdist: cumulative planar distance at each vertex index
        """
        verts: list[tuple[float, float]] = []
        seg_pairs: list[tuple[int, int]] = []
        cum: list[float] = []
        total = 0.0

        base_index = 0
        for part in conduit.get("segments") or []:
            coords = [(float(x), float(y)) for (x, y) in (part or [])]
            if not coords:
                continue
            # append, creating segment pairs
            for j, pt in enumerate(coords):
                verts.append(pt)
                if base_index + j > 0:
                    a = verts[base_index + j - 1]
                    b = pt
                    dx, dy = _planar_dx_dy(a, b)
                    total += math.hypot(dx, dy)
                    seg_pairs.append((base_index + j - 1, base_index + j))
                cum.append(total)
            base_index += len(coords)

        if not verts:
            cum = []
        return (verts, seg_pairs, cum)

    def _project_point_to_conduit(pt: tuple[float, float], verts: list[tuple[float, float]], pairs: list[tuple[int, int]], cum: list[float]) -> tuple[float, float] | None:
        """
        Project a point to the nearest segment of the conduit; return (pos_along, d2).
        """
        best = None
        best_d2 = 1e100
        best_pos = 0.0
        for (i, j) in pairs:
            a, b = verts[i], verts[j]
            d2, t, seg_len = _point_seg_distance2(pt, a, b)
            if d2 < best_d2:
                best_d2 = d2
                best_pos = (cum[i] + t * seg_len) if seg_len > 0 else cum[i]
                best = (best_pos, best_d2)
        if best is None:
            return None
        return best

    # ---------------------------------------------------------
    # Build one conduit path (presentation)
    # ---------------------------------------------------------
    def _build_conduit_named_path(conduit: dict) -> str:
        verts, seg_pairs, cum = _flatten_conduit_vertices(conduit)
        if not verts or not seg_pairs:
            return ""

        # Gather features projected onto this conduit
        vault_pts = _collect_vault_points()
        nap_pts   = _collect_nap_points()
        dists     = _collect_ug_distributions()

        # Events: (pos, kind, value)
        events: list[tuple[float, str, str]] = []

        # Vaults
        for pt, vetro in vault_pts:
            proj = _project_point_to_conduit(pt, verts, seg_pairs, cum)
            if proj and proj[1] <= TOL_D2:
                events.append((proj[0], "vault", vetro))

        # NAPs
        for pt, label in nap_pts:
            proj = _project_point_to_conduit(pt, verts, seg_pairs, cum)
            if proj and proj[1] <= TOL_D2:
                events.append((proj[0], "nap", label))

        # Distributions: project each vertex; keep nearest
        dist_first_touch: dict[str, float] = {}
        for segs, dlabel in dists:
            best: tuple[float, float] | None = None
            for line in segs:
                for p in line:
                    proj = _project_point_to_conduit(p, verts, seg_pairs, cum)
                    if not proj:
                        continue
                    if proj[1] <= TOL_D2 and (best is None or proj[1] < best[1]):
                        best = proj
            if best:
                pos, d2 = best
                events.append((pos, "dist", dlabel))
                # Remember first-touch position for tie-breaking
                if dlabel not in dist_first_touch:
                    dist_first_touch[dlabel] = pos

        if not events:
            return ""

        # Sort events along the conduit, stable tie-break:
        def _ev_key(ev: tuple[float, str, str]):
            pos, kind, val = ev
            # NAPs tie-broken by walker order if available
            nap_order = NAP_ORDER.get(val, 10_000) if kind == "nap" else 0
            # Prioritize 'dist' before vault/nap at the same spot (so we can coalesce 'dist / vault')
            pri = {"dist": 0, "vault": 1, "nap": 2}.get(kind, 9)
            return (pos, pri, nap_order, kind, val)

        events.sort(key=_ev_key)

        # Group events within ~1m along-conduit so co-located things merge
        grouped: list[tuple[float, list[tuple[str, str]]]] = []
        for pos, kind, val in events:
            if not grouped or abs(pos - grouped[-1][0]) > POS_TOL:
                grouped.append((pos, [(kind, val)]))
            else:
                grouped[-1][1].append((kind, val))

        # Choose starting point: first group that has a 'vault'; else first group
        start_idx = 0
        for idx, (_, items) in enumerate(grouped):
            if any(k == "vault" for k, _ in items):
                start_idx = idx
                break

        # Build path pieces
        chain_parts: list[tuple[str, str]] = []  # (sep, token)
        current_dist: str | None = None

        for gi in range(start_idx, len(grouped)):
            pos, items = grouped[gi]
            d_here = [v for (k, v) in items if k == "dist"]
            v_here = sorted([v for (k, v) in items if k == "vault"])
            n_here = sorted([v for (k, v) in items if k == "nap"], key=lambda n: NAP_ORDER.get(n, 10_000))

            # Pick deterministic distribution if multiple (closest first-touch)
            d_token = None
            if d_here:
                d_token = sorted(d_here, key=lambda d: dist_first_touch.get(d, pos))[0]

            # Cell tokens (vault/nap) joined with ' / '
            cell = " / ".join([*v_here, *n_here]) if (v_here or n_here) else ""

            if d_token and d_token != current_dist:
                # Distribution change
                if cell:
                    # combine at this hop: "> Dist / Vault / NAP"
                    chain_parts.append((" > ", f"{d_token} / {cell}"))
                else:
                    chain_parts.append((" > ", d_token))
                current_dist = d_token
            elif cell:
                # No dist change; under same distribution?
                sep = " -- " if current_dist else " > "
                chain_parts.append((sep, cell))
            # else: nothing to add at this position

        # Render into a single string
        chain = ""
        for sep, token in chain_parts:
            if not token:
                continue
            if not chain:
                chain = token  # drop leading sep
            else:
                chain += f"{sep}{token}"
        return chain

    def _wrap_chain(chain: str, width: int = 250) -> list[str]:
        if not chain:
            return [""]
        parts: list[tuple[str, str]] = []
        pat = re.compile(r"( > | -- | / )")
        toks = pat.split(chain)
        if toks:
            parts.append(("", toks[0]))
            i = 1
            while i + 1 < len(toks):
                parts.append((toks[i], toks[i + 1]))
                i += 2
        lines: list[str] = []
        cur = ""
        for sep, tok in parts:
            add = (sep + tok) if cur else tok
            if _vlen(cur) + _vlen(add) <= width:
                cur += add
            else:
                if cur:
                    lines.append(cur)
                cur = tok  # start new line at token (no leading sep at column 0)
        if cur:
            lines.append(cur)
        return lines

    # Respect LOG_DETAIL — Overview goes to INFO unless DEBUG is selected
    detail = str(getattr(cfg, "LOG_DETAIL", "DEBUG")).upper()
    info_emit = log.debug if detail == "DEBUG" else log.info

    # ------------------------------
    # A) Overview (all conduits)
    # ------------------------------
    headers = ["Conduit ID", "Conduit Vetro ID", "Conduit Type", "#Segments", "#Vertices", "Path"]
    rows_expanded: list[list[str]] = []

    conduits = list(_collect_conduits())
    for c in conduits:
        segs = c.get("segments", [])
        seg_ct = len(segs)
        vtx_ct = sum(len(s) for s in segs)
        chain = _build_conduit_named_path(c)
        wrapped = _wrap_chain(chain, 250)

        rows_expanded.append([
            c.get("id", ""),
            c.get("vetro_id", ""),
            c.get("type", ""),
            str(seg_ct),
            str(vtx_ct),
            wrapped[0],
        ])
        for cont in wrapped[1:]:
            rows_expanded.append(["", "", "", "", "", cont])

    if rows_expanded and emit_info:
        info_emit("===== [Conduit] Overview (all features) =====")
        for line in format_table_lines(headers, rows_expanded, max_col_widths=[32, 36, 24, 9, 9, 250], center_headers=True):
            info_emit(f"[Conduit] {line}")
        info_emit("===== End [Conduit] Overview =====")

    # ------------------------------
    # B) Issue tables (no logic changes)
    # ------------------------------
    def _issue_table(title: str, headers: list[str], items: list[dict] | None):
        if not items:
            return
        lines = format_table_lines(headers, [[str(it.get(h, "")) for h in headers] for it in items], center_headers=True)
        log.error(f"==== {title} ({len(items)}) ====")
        for ln in lines:
            log.error(f"[Conduit Issues] {ln}")
        log.info(f"==== End {title} ====")

    results = run_all_conduit_checks()  # existing
    from modules.simple_scripts.vault_rules import find_vaults_missing_conduit as _find_vaults_missing_conduit
    vault_missing = find_vaults_missing_conduit()  # existing

    _issue_table("Distribution Without Conduit", ["Distribution ID", "Vetro ID", "Issue"], results.get("df_missing_conduit"))
    _issue_table("Conduit Without Underground Distribution", ["Conduit ID", "Conduit Vetro ID", "Issue"], results.get("conduit_missing_distribution"))
    _issue_table("Conduit Type Issues", ["Conduit ID", "Conduit Vetro ID", "Conduit Type", "Issue"], results.get("type_issues"))
    _issue_table("Vaults Missing Conduit", ["Vault Vetro ID", "Issue"], vault_missing)

def run_all_conduit_checks() -> dict[str, List[dict]]:
    return {
        # Underground Distribution without any nearby conduit
        "df_missing_conduit": find_distributions_without_conduit(),
        # Conduits that don’t carry any underground distribution
        "conduit_missing_distribution": find_conduits_without_distribution(),
        # Bad / blank conduit types
        "type_issues": find_conduit_type_issues(),
    }



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
