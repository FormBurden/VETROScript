# modules/simple_scripts/pole_issues.py

import glob
import json
import re
from typing import Dict, List, Set, Tuple

import modules.config
from modules.basic.distance_utils import bearing

def load_power_poles() -> Dict[Tuple[float,float], dict]:
    poles: Dict[Tuple[float,float], dict] = {}
    for fn in glob.glob(f"{modules.config.DATA_DIR}/*power-pole*.geojson"):
        with open(fn, encoding="utf-8") as f:
            gj = json.load(f)
        for feat in gj.get("features", []):
            props   = feat.get("properties", {}) or {}
            pole_id = props.get("ID") or props.get("vetro_id") or ""
            att_ht  = props.get("Att Ht:") or ""
            has_anchor = bool(re.search(r'\banchor\b', att_ht, re.IGNORECASE))
            geom   = feat.get("geometry", {}) or {}
            coords = geom.get("coordinates", [])
            if len(coords) < 2:
                continue
            lon, lat = coords[:2]
            poles[(round(lat,6), round(lon,6))] = {
                "id":     pole_id,
                "anchor": has_anchor,
                "coords": (lat, lon),
            }
    return poles

def load_aerial_distributions() -> Dict[str, List[List[Tuple[float,float]]]]:
    mapping: Dict[str, List[List[Tuple[float,float]]]] = {}
    for fn in glob.glob(f"{modules.config.DATA_DIR}/*fiber-distribution-aerial*.geojson"):
        with open(fn, encoding="utf-8") as f:
            gj = json.load(f)
        for feat in gj.get("features", []):
            props   = feat.get("properties", {}) or {}
            dist_id = props.get("ID")
            geom    = feat.get("geometry", {}) or {}
            coords  = geom.get("coordinates", [])
            typ     = geom.get("type")
            if not dist_id or not coords:
                continue

            segs: List[List[Tuple[float,float]]] = []
            if typ == "LineString":
                segs = [coords]
            elif typ == "MultiLineString":
                segs = coords
            else:
                continue

            for seg in segs:
                latlon = [(round(lat,6), round(lon,6)) for lon, lat in seg]
                mapping.setdefault(dist_id, []).append(latlon)
    return mapping

def load_messenger_wire() -> Dict[str, List[List[Tuple[float,float]]]]:
    mapping: Dict[str, List[List[Tuple[float,float]]]] = {}
    for fn in glob.glob(f"{modules.config.DATA_DIR}/*messenger-wire*.geojson"):
        with open(fn, encoding="utf-8") as f:
            gj = json.load(f)
        for feat in gj.get("features", []):
            props = feat.get("properties", {}) or {}
            mw_id = props.get("ID") or props.get("id") or ""
            geom  = feat.get("geometry", {}) or {}
            coords= geom.get("coordinates", [])
            typ   = geom.get("type")
            if not mw_id or not coords:
                continue

            segs: List[List[Tuple[float,float]]] = []
            if typ == "LineString":
                segs = [coords]
            elif typ == "MultiLineString":
                segs = coords
            else:
                continue

            for seg in segs:
                latlon = [(round(lat,6), round(lon,6)) for lon, lat in seg]
                mapping.setdefault(mw_id, []).append(latlon)
    return mapping


# thresholds
ANGLE_THRESHOLD = 40.0
MAX_MESSENGER_HOPS = 4

def _angle_diff(a, b):
    d = abs((a - b + 180) % 360 - 180)
    return d

def _anchor_reachable(start_pt, poles, messenger_graph, max_hops=MAX_MESSENGER_HOPS):
    # BFS on messenger_graph vertices (lat,lon) to find any pole with anchor=True
    seen = {start_pt}
    frontier = [(start_pt, 0)]
    while frontier:
        node, hops = frontier.pop(0)
        if hops > max_hops:
            continue
        if poles.get(node, {}).get("anchor"):
            return True
        for nbr in messenger_graph.get(node, ()):
            if nbr not in seen:
                seen.add(nbr)
                frontier.append((nbr, hops + 1))
    return False


def find_power_pole_issues(poles, distribution_features, messenger_graph):
    issues = []
    for dist_id, segments in distribution_features.items():
        for coords in segments:
            if len(coords) < 3:
                continue
            # coords are [(lat,lon), ...]
            for i in range(1, len(coords) - 1):
                a = coords[i - 1]
                b = coords[i]
                c = coords[i + 1]
                pole = poles.get(b)
                if not pole:
                    continue
                b1 = bearing(a[0], a[1], b[0], b[1])
                b2 = bearing(b[0], b[1], c[0], c[1])
                diff = _angle_diff(b1, b2)
                if diff >= ANGLE_THRESHOLD and not pole.get("anchor"):
                    if not _anchor_reachable(b, poles, messenger_graph):
                        issues.append({
                            "pole_id": pole["id"],
                            "dist_id": dist_id,
                            "angle": round(diff, 1),
                            "note": "sharp bend ≥ 40° and no anchor within 4 hops",
                        })
    return issues
