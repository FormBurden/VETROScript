# geojson_loader.py

import logging

import glob
import json
import modules.config

logger = logging.getLogger(__name__)

def load_features(layer_keyword: str, id_field: str):
    """
    Load Point features from *{layer_keyword}*.geojson,
    returning (coords_list, mapping_dict).
    """
    coords, mapping = [], {}
    # for vaults grab both 'vaults' and 't-3-vault' files
    if layer_keyword == 'vaults':
        pattern = '*vault*.geojson'
    else:
        pattern = f'*{layer_keyword}*.geojson'
    for fn in glob.glob(f'{modules.config.DATA_DIR}/{pattern}'):
        with open(fn, encoding='utf-8') as f:
            gj = json.load(f)
        for feat in gj.get('features', []):
            geom = feat.get('geometry', {})
            c = geom.get('coordinates', [])
            if len(c) < 2:
                continue
            lon, lat = c[0], c[1]
            pt = (round(lat, 6), round(lon, 6))
            coords.append(pt)
            mapping[pt] = feat.get('properties', {}).get(id_field, '')
    return coords, mapping

def load_slack_loops():
    """
    excluding any slack loops labeled "30' Pole Anchor".
    """
    coords = []
    for fn in glob.glob(f'{modules.config.DATA_DIR}/*slack-loop*.geojson'):
        with open(fn, encoding='utf-8') as f:
            gj = json.load(f)
        for feat in gj.get('features', []):
            props = feat.get('properties', {})
            # skip the 30' Pole Anchor loops
            if props.get('Slack Loop') == "30' Pole Anchor":
                continue
            geom = feat.get('geometry', {})
            c = geom.get('coordinates', [])
            if len(c) < 2:
                continue
            lon, lat = c[0], c[1]
            coords.append((round(lat, 6), round(lon, 6)))
    return coords

def load_fiber_distribution():
    """
    Load all vertices from fiber-distribution-aerial*.geojson,
    handling Point, LineString, and MultiLineString.
    """
    coords = []
    for fn in glob.glob(f'{modules.config.DATA_DIR}/fiber-distribution-aerial*.geojson'):
        with open(fn, encoding='utf-8') as f:
            gj = json.load(f)
        for feat in gj.get('features', []):
            geom = feat.get('geometry', {})
            typ  = geom.get('type')
            c    = geom.get('coordinates', [])
            if typ == 'Point':
                lon, lat = c[:2]
                coords.append((lat, lon))
            elif typ == 'LineString':
                for lon, lat in c:
                    coords.append((lat, lon))
            elif typ == 'MultiLineString':
                for segment in c:
                    for lon, lat in segment:
                        coords.append((lat, lon))
    return coords

def load_t3_vaults():
    """
    Load T-3 Vault points (filename “*t-3-vault*.geojson”),
    returning (coords_list, id_map) keyed on the feature’s “ID” property.
    """
    coords, mapping = [], {}
    for fn in glob.glob(f'{modules.config.DATA_DIR}/*t-3-vault*.geojson'):
        gj = json.load(open(fn, encoding='utf-8'))
        for feat in gj.get('features', []):
            lon, lat = feat['geometry']['coordinates'][:2]
            pt = (round(lat,6), round(lon,6))
            coords.append(pt)
            mapping[pt] = feat.get('properties', {}).get('ID','')
    return coords, mapping
