# modules/distribution.py
# Everything to do with distribution lines; including Aerial and Underground.
import logging
import glob
import json
import re
import modules.config

logger = logging.getLogger(__name__)

def find_missing_distribution_footage():
    """
    MOVED: use modules.simple_scripts.footage_issues.find_missing_distribution_footage().
    Keeping this shim so existing imports continue to work.
    """
    from modules.simple_scripts.footage_issues import find_missing_distribution_footage as _impl
    logger.debug("find_missing_distribution_footage() shim → modules.simple_scripts.footage_issues")
    return _impl()


def _load_underground_distributions():
    """
    Returns dict: dist_id -> list of segments (each segment is a list of [lon, lat] points).
    """
    mapping = {}

    for fn in glob.glob(f'{modules.config.DATA_DIR}/*fiber-distribution-underground*.geojson'):
        with open(fn, encoding='utf-8') as f:
            gj = json.load(f)
        for feat in gj.get('features', []):
            props = feat.get('properties', {})
            dist_id = props.get('ID')
            geom    = feat.get('geometry', {})
            coords  = geom.get('coordinates', [])
            typ     = geom.get('type')

            if not dist_id or not coords:
                continue

            if typ == 'LineString':
                mapping.setdefault(dist_id, []).append(coords)
            elif typ == 'MultiLineString':
                for segment in coords:
                    mapping.setdefault(dist_id, []).append(segment)

    return mapping
