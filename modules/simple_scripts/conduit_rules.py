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

# NEW: line-proximity based vault/conduit check
def find_vaults_missing_conduit_by_line(vaults_gdf, conduit_gdf, tolerance_ft=5.0, emit_details=True):
    """
    Return a DataFrame of vaults that do not have conduit LINES within `tolerance_ft`.
    This solves false positives where a vault sits on top of a conduit segment
    but not near a 'conduit point' feature.

    Parameters
    ----------
    vaults_gdf : GeoDataFrame
        Must contain columns: 'vetro_id' (or 'Vetro ID') and geometry (POINT).
    conduit_gdf : GeoDataFrame
        Must be LINESTRING/MULTILINESTRING geometry with an id column like 'vetro_id'.
    tolerance_ft : float
        Maximum allowed separation between the vault point and the nearest conduit line.
    emit_details : bool
        If True, include nearest conduit id and measured distance.

    Returns
    -------
    pandas.DataFrame
        Columns: ['Vault Vetro ID', 'Issue', 'Nearest Conduit Vetro ID', 'Distance (ft)']
        (the last two only if emit_details=True)
    """
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import LineString, MultiLineString

    # Normalize id columns
    def _norm_id_col(gdf):
        for c in ['vetro_id', 'Vetro ID', 'VetroID', 'id']:
            if c in gdf.columns:
                return c
        raise KeyError("Expected an id column like 'vetro_id'/'Vetro ID' in input GeoDataFrame.")

    v_id = _norm_id_col(vaults_gdf)
    c_id = _norm_id_col(conduit_gdf)

    # Keep only valid geometries
    vaults = vaults_gdf[[v_id, 'geometry']].dropna(subset=['geometry']).copy()
    conduit = conduit_gdf[[c_id, 'geometry']].dropna(subset=['geometry']).copy()
    conduit = conduit[conduit.geometry.type.isin(['LineString', 'MultiLineString'])].copy()
    if conduit.empty or vaults.empty:
        cols = ['Vault Vetro ID', 'Issue']
        if emit_details:
            cols += ['Nearest Conduit Vetro ID', 'Distance (ft)']
        return pd.DataFrame(columns=cols)

    # CRS handling: work in meters for accurate distances
    # (if CRS is missing, assume WGS84 then project)
    if vaults.crs is None:
        vaults.set_crs(4326, inplace=True)
    if conduit.crs is None:
        conduit.set_crs(vaults.crs, inplace=True)

    if vaults.crs.to_epsg() != 3857:
        vaults_m = vaults.to_crs(3857)
    else:
        vaults_m = vaults

    if conduit.crs.to_epsg() != 3857:
        conduit_m = conduit.to_crs(3857)
    else:
        conduit_m = conduit

    # Spatial nearest within tolerance
    tol_m = float(tolerance_ft) * 0.3048

    # GeoPandas >=0.12 has sjoin_nearest with max_distance
    # It returns nearest row even if beyond max_distance unless we filter; so filter by distance after join.
    joined = gpd.sjoin_nearest(
        vaults_m,
        conduit_m,
        how='left',
        distance_col='__dist_m',
        max_distance=tol_m
    )

    # Mark vaults with no conduit within tolerance
    no_match = joined[joined[c_id].isna()][[v_id, '__dist_m']].copy()
    no_match['Issue'] = 'No Conduit at vault'

    # Prepare output
    if emit_details:
        # For matched rows, we’re not reporting them; for unmatched, fill detail cols as empty
        no_match['Nearest Conduit Vetro ID'] = ''
        no_match['Distance (ft)'] = no_match['__dist_m'].fillna(pd.NA).astype(float) / 0.3048
        out = no_match[[v_id, 'Issue', 'Nearest Conduit Vetro ID', 'Distance (ft)']].copy()
    else:
        out = no_match[[v_id, 'Issue']].copy()

    out.rename(columns={v_id: 'Vault Vetro ID'}, inplace=True)
    # Sort for stable output
    if 'Distance (ft)' in out.columns:
        out.sort_values(['Distance (ft)', 'Vault Vetro ID'], inplace=True, kind='mergesort', na_position='last')
    else:
        out.sort_values(['Vault Vetro ID'], inplace=True, kind='mergesort')
    out.reset_index(drop=True, inplace=True)
    return out
