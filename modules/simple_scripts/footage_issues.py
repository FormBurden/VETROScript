# modules/simple_scripts/footage_issues.py
# Footage-related checks

import logging
import glob
import os
import json
import re
import modules.config

logger = logging.getLogger(__name__)


def find_missing_distribution_footage() -> list[tuple[str, str, str, str]]:
    """
    Scan both aerial & underground distribution GeoJSONs for a Note like “1234 ft”
    (allowing commas or dots in the number, any casing of FT, and an optional trailing period).

    Returns:
        List of 4-tuples: (dist_id, kind, vetro_id, issue_text)

    Issue buckets:
        - Missing "Note"
        - Note missing "ft" unit
        - Note missing numeric value
        - Extra text in Note (only "#### ft" allowed)
        - Uses ' (foot) symbol without "ft" unit
        - Invalid Note format
    """
    mismatches: list[tuple[str, str, str, str]] = []

    # VALID pattern (strict): "<number>[optional '][space optional]ft[optional .]" with nothing else
    # - number: digits with optional commas and an optional decimal part
    full_ok_re = re.compile(r"^\s*([\d,]+(?:\.\d+)?)'?[\s]*[Ff][Tt]\.?\s*$")

    # Helpers to detect partials
    num_re = re.compile(r"[\d,]+(?:\.\d+)?")
    ft_re  = re.compile(r"\b[Ff][Tt]\.?(?:\b|$)")

    for kind in ('fiber-distribution-aerial', 'fiber-distribution-underground'):
        for fn in glob.glob(f'{modules.config.DATA_DIR}/*{kind}*.geojson'):
            try:
                with open(fn, encoding='utf-8') as f:
                    gj = json.load(f)
            except Exception as e:
                logger.warning("[Footage] Unable to read %s: %s", fn, e)
                continue

            for feat in gj.get('features', []):
                props    = (feat or {}).get('properties', {}) or {}
                note_raw = props.get('Note', None)
                note     = (note_raw or '').strip()
                dist_id  = props.get('ID', '') or ''
                vetro_id = props.get('vetro_id', '') or ''

                # Only flag entries that have a distribution ID
                if not dist_id:
                    continue

                # Classify
                if not note:
                    issue = 'Missing "Note"'
                elif full_ok_re.match(note):
                    # Looks good – no issue
                    continue
                else:
                    has_num = bool(num_re.search(note))
                    has_ft  = bool(ft_re.search(note))

                    # Specific buckets
                    if not has_ft:
                        # Special case: they used only a foot (') symbol like "1234'"
                        if re.search(r"\d\s*'\s*$", note):
                            issue = 'Uses \' (foot) symbol without "ft" unit'
                        else:
                            issue = 'Note missing "ft" unit'
                    elif not has_num:
                        issue = 'Note missing numeric value'
                    else:
                        # Has both pieces but still not matching strict shape => extra text/formatting
                        # Only "#### ft" (with optional comma/decimal/apostrophe/period) is allowed.
                        # Anything else is considered "extra text".
                        if not full_ok_re.match(note):
                            issue = 'Extra text in Note (only "#### ft" allowed)'
                        else:
                            issue = 'Invalid Note format'  # fallback, should rarely hit

                mismatches.append((str(dist_id), str(kind), str(vetro_id), issue))

    return mismatches


def find_overlength_fiber_cables(limit_ft: float = 250.0):
    """
    Scan fiber GeoJSON layers for a numeric 'Total Length' and flag any feature
    whose length exceeds `limit_ft` feet.

    Returns:
        List[Tuple[str, str, str, float]] as (cable_id, TYPE, vetro_id, total_length_ft)

    Notes:
      - Accepts numeric or string values for 'Total Length', e.g. 261.61575 or "261.61575".
      - Looks in drop + distribution + common cable patterns.
      - TYPE is derived from 'Placement' first, else from filename.
    """

    logger = logging.getLogger(__name__)

    def _to_feet(raw):
        # Fast path for numeric values
        if isinstance(raw, (int, float)):
            try:
                return float(raw)
            except Exception:
                return None
        if raw is None:
            return None
        s = str(raw)
        m = re.search(r'([\d,]+(?:\.\d+)?)', s)
        if not m:
            return None
        try:
            return float(m.group(1).replace(',', ''))
        except Exception:
            return None

    over = []
    patterns = [
        "*fiber-drop*.geojson",
        "*fiber-distribution-*.geojson",
        "*fiber-feeder*.geojson",
        "*fiber-trunk*.geojson",
        "*fiber-backbone*.geojson",
        "*fiber-cable*.geojson",
    ]

    for patt in patterns:
        for path in glob.glob(os.path.join(modules.config.DATA_DIR, patt)):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    gj = json.load(f)
            except Exception as e:
                logger.warning("[Footage] Unable to read %s: %s", path, e)
                continue

            fname = os.path.basename(path).lower()

            for feat in gj.get("features", []) or []:
                props = (feat or {}).get("properties", {}) or {}
                raw_len = props.get("Total Length")
                length_ft = _to_feet(raw_len)
                if length_ft is None:
                    continue
                if length_ft > float(limit_ft):
                    cable_id = (
                        props.get("ID")
                        or props.get("Name")
                        or props.get("Drop Name")
                        or props.get("label")
                        or ""
                    )
                    vetro_id = props.get("vetro_id") or props.get("Vetro ID") or ""
                    placement = (props.get("Placement") or props.get("Type") or "") or ""
                    type_str = ""
                    if isinstance(placement, str):
                        pl = placement.lower()
                        if "underground" in pl:
                            type_str = "UNDERGROUND"
                        elif "aerial" in pl:
                            type_str = "AERIAL"
                    if not type_str:
                        if "underground" in fname:
                            type_str = "UNDERGROUND"
                        elif "aerial" in fname:
                            type_str = "AERIAL"
                    over.append((str(cable_id), str(type_str or ""), str(vetro_id), float(length_ft)))

    return over

def find_overlength_drop_cables(limit_ft: float = 250.0):
    """
    Scan *fiber-drop*.geojson only for a numeric 'Total Length' and flag any
    drop whose length exceeds `limit_ft` feet.

    Returns:
        List[Tuple[str, str, float]] as (vetro_id, type_str, total_length_ft)

    Notes:
      - Accepts raw numeric (int/float) or string values for 'Total Length',
        e.g. 261.61575 or "261.61575".
      - Uses modules.config.DATA_DIR for files.
      - 'Type' comes from properties['Type'] when present; defaults to 'Fiber - Drop'.
    """

    def _to_feet(raw):
        if isinstance(raw, (int, float)):
            try:
                return float(raw)
            except Exception:
                return None
        if raw is None:
            return None
        s = str(raw).strip()
        try:
            return float(s.replace(',', ''))
        except Exception:
            return None

    over: list[tuple[str, str, float]] = []

    for path in glob.glob(f"{modules.config.DATA_DIR}/*fiber-drop*.geojson"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                gj = json.load(f)
        except Exception:
            continue

        for feat in (gj.get("features") or []):
            props = (feat or {}).get("properties", {}) or {}
            raw_len = props.get("Total Length")
            length_ft = _to_feet(raw_len)
            if length_ft is None or length_ft <= float(limit_ft):
                continue

            vetro_id = props.get("vetro_id") or props.get("Vetro ID") or props.get("ID") or ""
            type_str = props.get("Placement")

            over.append((str(vetro_id), str(type_str), float(length_ft)))

    logging.getLogger(__name__).info(
        "[Footage] Overlength fiber DROPS (> %.1f ft): %d", limit_ft, len(over)
    )
    return over
