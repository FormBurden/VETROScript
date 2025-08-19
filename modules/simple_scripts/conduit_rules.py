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


# ---------------------------------------------
# Rule: Underground DF must have conduit below
# ---------------------------------------------
def find_distributions_without_conduit() -> List[dict]:
    """
    For each underground Distribution, require at least one conduit vertex within THRESHOLD_M
    of any vertex of the distribution geometry. If none, flag it.
    """
    conduits = _load_conduits()
    conduit_vertices = _collect_conduit_vertices(conduits)

    out: List[dict] = []
    for df in _load_underground_distributions_full():
        has_touch = False
        for seg in df["segments"]:
            for lat, lon in seg:
                if any(haversine(lat, lon, clat, clon) <= THRESHOLD_M
                       for (clat, clon) in conduit_vertices):
                    has_touch = True
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


def run_all_conduit_checks() -> dict[str, List[dict]]:
    return {
        "df_missing_conduit": find_distributions_without_conduit(),
        "type_issues":        find_conduit_type_issues(),
    }
