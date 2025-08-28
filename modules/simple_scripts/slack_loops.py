# modules/simple_scripts/slack_loops.py

import glob
import json
import re
import modules.config
from modules.basic.distance_utils import haversine, THRESHOLD_M
from modules.simple_scripts.distribution import _load_underground_distributions
from modules.simple_scripts.fiber_drop import load_fiber_drops


def load_slack_loops_with_labels():
    """
    Return list of (slack_vid, parent_vetro_id, fiber_label) tuples.
    """
    out = []
    for fn in glob.glob(f'{modules.config.DATA_DIR}/*slack-loop*.geojson'):
        with open(fn, encoding='utf-8') as f:
            gj = json.load(f)
        for feat in gj.get('features', []):
            props       = feat.get('properties', {})
            slack_vid   = props.get('vetro_id')
            parent_vid  = props.get('parent_vetro_id')
            fiber_lbl   = props.get('Fiber Label')
            if slack_vid and parent_vid and fiber_lbl:
                out.append((slack_vid, parent_vid, fiber_lbl))
    return out

def load_distribution_labels():
    """
    Return dict: parent_vetro_id → distribution ID.
    """
    mapping = {}
    for kind in ('fiber-distribution-aerial', 'fiber-distribution-underground'):
        for fn in glob.glob(f'{modules.config.DATA_DIR}/*{kind}*.geojson'):
            with open(fn, encoding='utf-8') as f:
                gj = json.load(f)
            for feat in gj.get('features', []):
                props   = feat.get('properties', {})
                vid     = props.get('vetro_id')
                raw_id  = props.get('ID', '')
                if vid and raw_id:
                    # drop any suffix after a slash
                    base_id = raw_id.split('/', 1)[0].strip()
                    mapping[vid] = base_id
    return mapping


def find_slack_dist_mismatches():
    """
    Compare each Slack Loop’s parent_vetro_id → distribution ID.
    Flags a row when the slack’s Fiber Label (normalized) does not match
    the parent distribution’s ID (normalized).

    Returns list of (slack_vid, fiber_label, dist_ID, issue) for mismatches.
    """
    def base_id(s: str) -> str:
        # Strip anything after the first " / " to compare on the canonical ID
        return (s or "").split(" / ", 1)[0].strip()

    slack = load_slack_loops_with_labels()  # (slack_vid, parent_vetro_id, fiber_lbl)
    dist  = load_distribution_labels()      # parent_vetro_id -> base distribution ID

    mismatches = []
    for slack_vid, parent_vid, fiber_lbl in slack:
        dist_id = dist.get(parent_vid, "")
        if base_id(fiber_lbl) != base_id(dist_id):
            mismatches.append(
                (slack_vid, fiber_lbl, dist_id or "", "Slack fiber label doesn't match parent distribution")
            )

    return mismatches


def _load_slack_loops_with_labels_and_coords():
    """
    Return list of (lat, lon, slack_vid, fiber_label, slack_loop_label).

    IMPORTANT: Do NOT require 'Fiber Label' to exist.
    Tail-End logic only needs the Slack Loop's vetro_id + 'Slack Loop' text + coords.
    """
    out = []
    for fn in glob.glob(f'{modules.config.DATA_DIR}/*slack-loop*.geojson'):
        with open(fn, encoding='utf-8') as f:
            gj = json.load(f)

        for feat in gj.get('features', []):
            props = (feat.get('properties') or {})
            slack_vid = props.get('vetro_id')
            # Accept missing/blank Fiber Label; normalize to empty string
            fl = (props.get('Fiber Label') or '').strip()
            sl = (props.get('Slack Loop') or '').strip()

            geom = (feat.get('geometry') or {})
            coords = (geom.get('coordinates') or [])

            # Only require an ID and usable [lon, lat] coords
            if not slack_vid or len(coords) < 2:
                continue

            lon, lat = coords[0], coords[1]
            out.append((lat, lon, slack_vid, fl, sl))
    return out


def find_underground_slack_mismatches(nap_coords, vault_coords, vault_map):
    """
    For each allowed Vault/NAP anchor:
      1) Gather *all* underground Distribution IDs that physically touch it.
      2) Gather all Slack Loops at that point.
      3) Consider it OK if EITHER of these overlaps is non-empty:
         • base_id(touching Distribution IDs) ∩ base_id(Slack Fiber Label[s])
         • base_id(touching Distribution IDs) ∩ base_id(parent Distribution ID[s])
           where parent Distribution ID is looked up from slack-loop.parent_vetro_id.
      4) For every allowed Vault, also flag if there is NO slack loop present at all.

    Returns rows shaped for Excel:
      (joined_touching_ids, "underground",
       joined_fiber_labels, joined_slack_labels, joined_slack_vids,
       vault_vetro_id, issue)
    """

    # ——— helpers ———
    def base_id(s: str) -> str:
        """Compare on canonical ID: strip anything after the first ' / '."""
        return (s or "").split(" / ", 1)[0].strip()

    def seg_touches_point(seg, pt_lat, pt_lon) -> bool:
        for lon, lat in seg:
            if haversine(pt_lat, pt_lon, lat, lon) <= THRESHOLD_M:
                return True
        return False

    # 0) Data prep
    dist_map  = _load_underground_distributions()                 # {dist_id: [segments]}
    slack_pts = _load_slack_loops_with_labels_and_coords()        # [(lat, lon, slack_vid, fiber_label, slack_loop_label)]
    slack_parent_rows = load_slack_loops_with_labels()            # [(slack_vid, parent_vetro_id, fiber_label)]
    parent_by_slack = {vid: parent for vid, parent, _ in slack_parent_rows}

    # Map parent_vetro_id -> distribution ID (already normalized to base ID in loader)
    dist_id_by_parent_vid = load_distribution_labels()            # {parent_vetro_id: base_dist_id}

    # 1) Filter allowed Vaults/NAPs by Size (unchanged from your version)
    vault_features = []
    for fn in glob.glob(f"{modules.config.DATA_DIR}/*vault*.geojson"):
        gj = json.load(open(fn, encoding="utf-8"))
        vault_features.extend(gj.get('features', []))
    vault_size_map = {
        (round(feat['geometry']['coordinates'][1], 6),
         round(feat['geometry']['coordinates'][0], 6)): (feat.get('properties', {}) or {}).get('Size')
        for feat in vault_features
    }
    ALLOWED_SIZES = {"DV", "LDV", "LDV Traffic Rated", "T1 Concrete", "T2 Concrete"}
    filtered_vault_coords = [
        coord for coord in vault_coords
        if vault_size_map.get((round(coord[0], 6), round(coord[1], 6))) in ALLOWED_SIZES
    ]

    nap_features = []
    for fn in glob.glob(f"{modules.config.DATA_DIR}/*nap*.geojson"):
        gj = json.load(open(fn, encoding="utf-8"))
        nap_features.extend(gj.get('features', []))
    nap_size_map = {
        (round(feat['geometry']['coordinates'][1], 6),
         round(feat['geometry']['coordinates'][0], 6)): (feat.get('properties', {}) or {}).get('Size')
        for feat in nap_features
    }
    filtered_nap_coords = [
        coord for coord in nap_coords
        if nap_size_map.get((round(coord[0], 6), round(coord[1], 6))) in ALLOWED_SIZES
    ]

    anchor_pts = set(filtered_vault_coords + filtered_nap_coords)

    issues = []

    # 2) Walk each anchor and compare
    for pt_lat, pt_lon in anchor_pts:
        # A) All underground Distribution IDs that touch this anchor
        touching_ids = set()
        for dist_id, segments in dist_map.items():
            if any(seg_touches_point(seg, pt_lat, pt_lon) for seg in segments):
                touching_ids.add(dist_id)

        if not touching_ids:
            # No underground DF at this anchor → nothing to compare
            continue

        touching_base = {base_id(x) for x in touching_ids}

        # B) Collect Slack Loops at this anchor
        fiber_labels, slack_labels, slack_vids = [], [], []
        for sl_lat, sl_lon, slack_vid, fiber_lbl, slack_loop_label in slack_pts:
            if haversine(pt_lat, pt_lon, sl_lat, sl_lon) <= THRESHOLD_M:
                fiber_labels.append(fiber_lbl)
                slack_labels.append(slack_loop_label)
                slack_vids.append(slack_vid)

        is_vault = (pt_lat, pt_lon) in filtered_vault_coords

        # If vault has no slack loops at all – always flag (existing behavior)
        if is_vault and not fiber_labels and not slack_labels:
            issues.append((
                " / ".join(sorted(touching_ids)),
                "underground",
                "", "", "",                                   # no fiber/slack info present
                vault_map.get((round(pt_lat, 6), round(pt_lon, 6)), ""),
                "No slack loop present at allowed Vault/NAP anchor"
            ))
            continue

        # C) Build overlap sets TWO ways:
        #    1) By the *Fiber Label* written on the slack
        fiber_base = {base_id(fl) for fl in fiber_labels if fl}

        #    2) By the parent_vetro_id → parent Distribution ID
        #       (only for slack loops physically at this point)
        parent_base = set()
        for vid in slack_vids:
            parent_vid = parent_by_slack.get(vid, "")
            parent_dist_id = dist_id_by_parent_vid.get(parent_vid, "")
            if parent_dist_id:
                parent_base.add(base_id(parent_dist_id))

        overlap_by_fiber  = touching_base & fiber_base
        overlap_by_parent = touching_base & parent_base

        # D) If EITHER overlap is non-empty, it's a match → no issue.
        if overlap_by_fiber or overlap_by_parent:
            continue

        # Otherwise, flag a mismatch row for this anchor
        issues.append((
            " / ".join(sorted(touching_ids)),
            "underground",
            " / ".join(sorted(fiber_labels)),
            " / ".join(sorted([s for s in slack_labels if s])),
            " / ".join(sorted(slack_vids)),
            vault_map.get((round(pt_lat, 6), round(pt_lon, 6)), ""),
            "No matching slack at anchor for touching Distribution(s)"
        ))

    return issues


def invalid_slack_loops(power_coords: list[tuple],
                        nap_coords: list[tuple],
                        slack_coords: set[tuple]) -> list[tuple]:
    """
    AERIAL RULE (updated):

    Flag a power pole ONLY when:
      • There is at least one Fiber Drop on (or effectively on) the pole, AND
      • There is a NAP on (or effectively on) the same pole, AND
      • There is NO Slack Loop on that pole.

    Rationale:
      - Drops on a pole WITHOUT a NAP and without a Slack Loop are acceptable.
      - Poles without any Drops are ignored.

    Returns:
      List[(lat, lon)] for poles that violate the rule. Coordinates are rounded to 6 decimals.
    """
    try:
        drop_points = list(load_fiber_drops().keys())  # [(lat, lon), ...]
    except Exception:
        drop_points = []

    issues: list[tuple] = []
    for lat_p, lon_p in power_coords:
        # Require a fiber drop to consider this pole
        has_drop  = any(haversine(lat_p, lon_p, lat_d, lon_d) <= THRESHOLD_M
                        for (lat_d, lon_d) in drop_points)
        if not has_drop:
            continue  # No drop => do not enforce slack

        # New carve-out: Drop present but NO NAP => OK (do not flag)
        has_nap   = any(haversine(lat_p, lon_p, lat_n, lon_n) <= THRESHOLD_M
                        for (lat_n, lon_n) in nap_coords)
        if not has_nap:
            continue

        # Drop + NAP present ⇒ Slack Loop is REQUIRED
        has_slack = any(haversine(lat_p, lon_p, lat_s, lon_s) <= THRESHOLD_M
                        for (lat_s, lon_s) in slack_coords)

        if not has_slack:
            issues.append((round(lat_p, 6), round(lon_p, 6)))

    return issues


def find_distribution_end_tail_issues() -> list[tuple[str, str, str, str, str]]:
    """
    At each *terminal endpoint* of every Distribution (AERIAL & UNDERGROUND), check nearby Slack Loops.
    If at least one nearby slack loop's "Slack Loop" attribute contains the word "Tail" (case-insensitive
    substring), it's OK. Otherwise, flag it.

    Returns rows shaped for Excel "Tail End" block (one slack per row when wrong):
      (
        slack_loop_vetro_id,  # string ('' when no slack is present)
        TYPE,                 # 'AERIAL' or 'UNDERGROUND' (uppercased)
        slack_loop_label,     # raw "Slack Loop" attribute ('' if none)
        expected_label,       # e.g., "30' Tail" (or just "Tail" if length unknown)
        issue                 # brief reason, e.g., "No slack loop near distribution end" or "Slack loop label missing 'Tail'"
      )

    Side-effect:
      Sets a module-level parallel list `_LAST_TAIL_END_DIST_IDS` aligned to the returned rows,
      where each element is the Distribution *vetro_id* for "No slack..." rows, or "" otherwise.
    """

    # Sidecar for excel_writer to optionally include "Distribution ID" column
    global _LAST_TAIL_END_DIST_IDS
    _LAST_TAIL_END_DIST_IDS = []

    # --- Load all Slack Loops once: (lat, lon, vetro_id, fiber_label, slack_loop_label)
    slack_pts: list[tuple[float, float, str, str, str]] = _load_slack_loops_with_labels_and_coords()

    def nearby_slacks(lat_e: float, lon_e: float) -> list[tuple[str, str]]:
        """Return [(vetro_id, slack_loop_label)] within THRESHOLD_M of endpoint."""
        hits: list[tuple[str, str]] = []
        for sl_lat, sl_lon, sl_vid, _fiber_lbl, sl_label in slack_pts:
            if haversine(lat_e, lon_e, sl_lat, sl_lon) <= THRESHOLD_M:
                hits.append((sl_vid or "", (sl_label or "").strip()))
        return hits

    def _expected_from_label(label: str) -> str:
        """Pull the leading footage (e.g., 30, 60, 70, 90) from the *found* label and express expectation as "' Tail".
        If no number: return "Tail".
        """
        if not label:
            return "Tail"
        m = re.search(r"(\d+)\s*'?\s*", label)
        if m:
            return f"{m.group(1)}' Tail"
        return "Tail"

    def _load_dist(kind: str) -> tuple[dict[str, list[list[list[float]]]], dict[str, str]]:
        """
        kind: 'fiber-distribution-aerial' | 'fiber-distribution-underground'
        Returns:
          mapping:     dist_id -> [segments], each segment is a list of [lon, lat]
          vetro_map:   dist_id -> vetro_id (from properties 'vetro_id' or 'Vetro ID' if present)
        """
        mapping: dict[str, list[list[list[float]]]] = {}
        vetro_map: dict[str, str] = {}
        for fn in glob.glob(f"{modules.config.DATA_DIR}/*{kind}*.geojson"):
            with open(fn, encoding="utf-8") as f:
                gj = json.load(f)
            for feat in gj.get("features", []):
                props = feat.get("properties", {}) or {}
                dist_id = props.get("ID")
                dist_vetro = props.get("vetro_id") or props.get("Vetro ID") or props.get("vetroid") or ""
                geom = feat.get("geometry", {}) or {}
                typ = geom.get("type")
                coords = geom.get("coordinates", []) or []
                if not dist_id or not coords:
                    continue
                if typ == "LineString":
                    mapping.setdefault(dist_id, []).append(coords)
                elif typ == "MultiLineString":
                    for seg in coords:
                        mapping.setdefault(dist_id, []).append(seg)
                # Keep first seen vetro_id if present
                if dist_id and dist_vetro and dist_id not in vetro_map:
                    vetro_map[dist_id] = dist_vetro
        return mapping, vetro_map

    def _terminal_ends(segments: list[list[list[float]]]) -> list[tuple[float, float]]:
        """Compute terminal endpoints (points that appear once among segment endpoints)."""
        from collections import Counter

        def rnd(lat: float, lon: float) -> tuple[float, float]:
            return (round(lat, 6), round(lon, 6))

        counts = Counter()
        for seg in segments or []:
            if not seg:
                continue
            first = seg[0]
            last = seg[-1]
            counts[rnd(first[1], first[0])] += 1  # [lon, lat] -> (lat, lon)
            counts[rnd(last[1], last[0])] += 1
        return [pt for pt, c in counts.items() if c == 1]

    rows: list[tuple[str, str, str, str, str]] = []

    for kind, type_uc in (("fiber-distribution-aerial", "Aerial"), ("fiber-distribution-underground", "Underground")):
        dist_map, vetro_map = _load_dist(kind)
        for _dist_id, segments in dist_map.items():
            dist_vetro = vetro_map.get(_dist_id, "")
            for lat_e, lon_e in _terminal_ends(segments):
                hits = nearby_slacks(lat_e, lon_e)

                # If any nearby slack has "Tail" anywhere in its label ⇒ OK
                if any("tail" in (lbl.lower()) for _vid, lbl in hits if lbl):
                    continue

                # Otherwise, flag: one row per non-tail slack if present; else a single 'no slack' row
                if hits:
                    for vid, lbl in hits:
                        expected = _expected_from_label(lbl)
                        rows.append((vid, type_uc, lbl, expected, "Slack loop label missing 'Tail'"))
                        _LAST_TAIL_END_DIST_IDS.append("")  # Dist ID column should stay blank for this issue
                else:
                    rows.append(("", type_uc, "", "Tail", "No slack loop near distribution end"))
                    _LAST_TAIL_END_DIST_IDS.append(dist_vetro or "")

    return rows