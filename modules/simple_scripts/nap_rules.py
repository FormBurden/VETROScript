# modules/simple_scripts/nap_rules.py
import glob
import json
import re
import logging
import modules.config
from modules.simple_scripts.geojson_loader import load_features
from modules.basic.distance_utils import haversine, THRESHOLD_M
from modules.basic.fiber_colors import FIBER_COLORS


logger = logging.getLogger(__name__)

# De-dupe guard: only warn once per (NAP ID, token) for the whole run
_warned_case_tokens: set[tuple[str, str]] = set()

def _warn_unknown_color(nap_id: str, token: str) -> None:
    key = (str(nap_id), str(token).strip().lower())
    if key in _warned_case_tokens:
        return
    _warned_case_tokens.add(key)
    logger.error(f"Unknown splice color '{token}' for NAP {nap_id}")

def _warn_case_mismatch(nap_id: str, token: str, canonical: str) -> None:
    key = (str(nap_id), str(token).strip().lower())
    if key in _warned_case_tokens:
        return
    _warned_case_tokens.add(key)
    logger.error(f"Splice color case mismatch '{token}' -> '{canonical}' for NAP {nap_id}")

def load_nap_specs():
    """
    Load each NAP’s Fiber Count, Loose Tube groups, Splice Colors, and Tie Points.

    Returns:
      specs[nap_id] = {
        "fiber_count": <int>,
        "tube_specs":  [(<loose_abbrev>, [indices]), ...],
        "tie_points":  [
            {
              "left_ct": <int>,               # e.g., 48
              "left_range": (start, end),     # e.g., (2, 6)
              "right_ct": <int>,              # e.g., 24
              "right_range": (start, end),    # e.g., (9, 15)
              "right_indices": [ints...]      # convenience expansion of right_range
            }, ...
          ]
      }
    """
    specs: dict[str, dict] = {}

    # Build a case-insensitive color lookup once for splice parsing
    color_to_canon: dict[str, str] = {c.lower(): c for c in FIBER_COLORS}

    # Iterate all NAP geojson files
    for fn in glob.glob(f"{modules.config.DATA_DIR}/*nap*.geojson"):
        with open(fn, encoding="utf-8") as f:
            gj = json.load(f)

        for feat in gj.get("features", []):
            props = feat.get("properties", {}) or {}
            nap_id = (props.get("ID") or "").strip()
            if not nap_id:
                continue

            # Fiber count (e.g., "48ct" -> 48)
            fc_raw = (props.get("Fiber Count") or "").strip()
            try:
                fc = int(re.sub(r"[^0-9]", "", fc_raw)) if fc_raw else None
            except ValueError:
                fc = None

            # Loose Tube / Splice Colors -> tube_specs
            lt_raw = (props.get("Loose Tubes") or props.get("Loose Tube") or "").strip()
            sc_raw = (props.get("Splice Colors") or props.get("Splice Color") or "").strip()

            tube_specs: list[tuple[str, list[int]]] = []
            if lt_raw or sc_raw:
                # Split on commas or "/" separators
                loose_parts = [p.strip() for p in re.split(r"[,/]", lt_raw) if p.strip()] if lt_raw else []
                splice_parts = [p.strip() for p in sc_raw.split("/") if p.strip()] if sc_raw else []

                # Broadcast single splice part across multiple groups
                if loose_parts and len(splice_parts) == 1:
                    splice_parts = splice_parts * len(loose_parts)

                # If counts don't line up, skip tube_specs (keep tie_points)
                if loose_parts and splice_parts and len(loose_parts) != len(splice_parts):
                    logger.debug(
                        f"Skipping tube_specs for {nap_id}: loose/splice group count mismatch "
                        f"{len(loose_parts)}!={len(splice_parts)}"
                    )
                    loose_parts, splice_parts = [], []

                for loose_abbrev, splice_group in zip(loose_parts, splice_parts or []):
                    local_idxs: list[int] = []

                    # Cases:
                    #  - "5-12" -> range
                    #  - "7"    -> single index
                    #  - "Blue, Orange, Green" -> color names
                    #  - "1,4,12" -> indices as comma-list
                    m = re.match(r"^(\d+)\s*-\s*(\d+)$", splice_group)
                    if m:
                        start, end = int(m.group(1)), int(m.group(2))
                        local_idxs = list(range(min(start, end), max(start, end) + 1))
                    elif splice_group.isdigit():
                        local_idxs = [int(splice_group)]
                    elif "," in splice_group:
                        for token in (t.strip() for t in splice_group.split(",") if t.strip()):
                            # numeric index?
                            if token.isdigit():
                                local_idxs.append(int(token))
                                continue
                            # color name (case-insensitive) — cast to str for Pylance
                            canon = color_to_canon.get(str(token).lower())
                            if canon is None:
                                _warn_unknown_color(nap_id, token)
                                continue
                            if str(token) != canon:
                                _warn_case_mismatch(nap_id, token, canon)
                            local_idxs.append(FIBER_COLORS.index(canon) + 1)
                    else:
                        # single color name — cast to str for Pylance
                        canon = color_to_canon.get(str(splice_group).lower())
                        if canon is None:
                            _warn_unknown_color(nap_id, splice_group)
                        else:
                            if str(splice_group) != canon:
                                _warn_case_mismatch(nap_id, splice_group, canon)
                            local_idxs = [FIBER_COLORS.index(canon) + 1]

                    if local_idxs:
                        tube_specs.append((loose_abbrev, local_idxs))

            # ---- Tie Points (robust parser) ----
            # Prefer explicit field; fall back to ID (some files truncate the field or wrap it in parens).
            tie_src = (props.get("Tie Points") or props.get("Tie Point") or props.get("ID") or "").strip()
            if tie_src.startswith("(") and tie_src.endswith(")"):
                tie_src = tie_src[1:-1].strip()

            tie_points: list[dict] = []
            if tie_src:
                # Allow optional direction words between "to" and the right fiber count
                tp_re = re.compile(
                    r"(?:Tie\s*Point\s*)?"        # optional prefix
                    r"(?P<LCT>\d+)\s*ct\s*"
                    r"(?P<LS>\d+)\s*-\s*(?P<LE>\d+)\s*"
                    r"to\s*(?:[A-Za-z]+\s+)*"    # optional "South", "East", etc.
                    r"(?P<RCT>\d+)\s*ct\s*"
                    r"(?P<RS>\d+)\s*-\s*(?P<RE>\d+)",
                    flags=re.IGNORECASE,
                )
                for m in tp_re.finditer(tie_src):
                    LCT = int(m.group("LCT"))
                    LS  = int(m.group("LS"))
                    LE  = int(m.group("LE"))
                    RCT = int(m.group("RCT"))
                    RS  = int(m.group("RS"))
                    RE  = int(m.group("RE"))
                    L0, L1 = min(LS, LE), max(LS, LE)
                    R0, R1 = min(RS, RE), max(RS, RE)

                    tie_points.append({
                        "left_ct": LCT,
                        "left_range": (L0, L1),
                        "right_ct": RCT,
                        "right_range": (R0, R1),
                        "right_indices": list(range(R0, R1 + 1)),
                    })

            specs[nap_id] = {
                "fiber_count": fc,
                "tube_specs": tube_specs,
                "tie_points": tie_points,
            }

    return specs

def scan_nap_spec_warnings():
    """
    Scan NAP features for conditions that appear as WARNINGs in logs and
    surface them for Excel. Currently checks Splice Colors for:
      • case-mismatches vs canonical names (e.g., 'yellow' -> 'Yellow')
      • completely unknown color names

    Returns:
      List[Dict[str, str]] with keys: 'NAP ID', 'Field', 'Value', 'Hint'
    """
    import os, glob, json
    from modules.basic.fiber_colors import FIBER_COLORS
    import modules.config

    warnings = []

    # canonical maps for color names
    lower_to_canon = {c.lower(): c for c in FIBER_COLORS}
    allowed_lower  = set(lower_to_canon.keys())

    # Find any NAP GeoJSONs
    files = []
    for pat in (
        os.path.join(modules.config.DATA_DIR, "*nap*.geojson"),
        os.path.join(modules.config.DATA_DIR, "*NAP*.geojson"),
    ):
        files.extend(glob.glob(pat))

    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                gj = json.load(f)
        except Exception:
            continue

        for feat in gj.get("features", []):
            props  = feat.get("properties", {}) or {}
            nap_id = (
                str(props.get("ID") or props.get(str(getattr(modules.config, "ID_COL", "ID"))) or "").strip()
            )

            sc_raw = props.get("Splice Colors", "")
            if not sc_raw:
                continue

            # Split by slash or comma, keep simple tokens
            tokens = [t.strip() for t in str(sc_raw).replace("/", ",").split(",") if t.strip()]

            for t in tokens:
                tl = t.lower()

                # Skip obvious non-color descriptors / numeric ranges
                if any(k in tl for k in ("tie point", "unit", "units", "ct", " to ")):
                    continue
                if any(ch.isdigit() for ch in t):
                    continue

                # Case-mismatch or unknown?
                if tl in allowed_lower:
                    canonical = lower_to_canon[tl]
                    if t != canonical:
                        warnings.append({
                            "NAP ID": nap_id,
                            "Field":  "Splice Colors",
                            "Value":  t,
                            "Hint":   canonical,
                        })
                else:
                    warnings.append({
                        "NAP ID": nap_id,
                        "Field":  "Splice Colors",
                        "Value":  t,
                        "Hint":   "",
                    })

    return warnings


def find_nap_drop_mismatches():
    """
    For each NAP:
      1) Read its tube_specs (list of (loose_abbrev, [indices])) from load_nap_specs()
      2) Compute expected colors via FIBER_COLORS[index-1]
      3) Load all drops (verts, color, drop_id) and find those anchored at the NAP
      4) Compare found colors to expected and record any missing
    Returns a list of dicts:
      {
        'nap': <NAP ID>,
        'loose_abbrev': <tube abbrev>,
        'missing_indices': [7, 8, …],
        'missing_colors': ['Red', 'Black', …]
      }
    """
    from modules.simple_scripts.nids import load_drops
    mismatches = []

    # 1) load NAP coords & mapping
    nap_coords, nap_map = load_features('nap', modules.config.ID_COL)

    # 2) load specs for each NAP
    nap_specs = load_nap_specs()

    # 3) load all fiber‐drop segments
    drops = load_drops()

    # 4) iterate over each NAP
    for (lat_n, lon_n) in nap_coords:
        nap_id = nap_map.get((lat_n, lon_n))
        spec = nap_specs.get(str(nap_id or ""))
        if not spec:
            continue

        # for each tube‐group in this NAP
        for loose_abbrev, indices in spec['tube_specs']:
            # map splice indices → color names
            expected = [FIBER_COLORS[i-1] for i in indices if 1 <= i <= len(FIBER_COLORS)]

            # find which drop‐colors actually sit on this NAP
            found = set()
            for verts, color, _ in drops:
                start, end = verts[0], verts[-1]
                if (
                    haversine(lat_n, lon_n, start[0], start[1]) <= THRESHOLD_M or
                    haversine(lat_n, lon_n, end[0], end[1])   <= THRESHOLD_M
                ):
                    found.add(color)

            # any expected color not present?
            missing_idxs = [i for i, col in zip(indices, expected) if col not in found]
            if missing_idxs:
                mismatches.append({
                    'nap': nap_id,
                    'loose_abbrev': loose_abbrev,
                    'missing_indices': missing_idxs,
                    'missing_colors': [FIBER_COLORS[i-1] for i in missing_idxs]
                })

    return mismatches

def find_nap_id_format_issues():
    """
    Identify any NAP features whose ID starts with SC-000.
    Returns a list of (nap_id, vetro_id) tuples for each flagged NAP.
    """
    issues = []
    pattern = re.compile(r'^SC-000')
    for fn in glob.glob(f"{modules.config.DATA_DIR}/*nap*.geojson"):
        with open(fn, encoding='utf-8') as f:
            gj = json.load(f)
        for feat in gj.get('features', []):
            props     = feat.get('properties', {}) or {}
            nap_id    = (props.get('ID') or '').strip()
            vetro_id  = (props.get('vetro_id') or '').strip()
            if pattern.match(nap_id):
                issues.append((nap_id, vetro_id))
    return issues

