# modules/simple_scripts/network_statistics.py

from modules.simple_scripts.geojson_loader import load_features, load_t3_vaults
from modules.simple_scripts.fiber_drop import (
    load_fiber_drops,
    find_color_mismatches,
    find_missing_service_location_drops,
    load_service_locations as fd_load_service_locations,
)
from modules.simple_scripts.slack_loops import (
    find_slack_dist_mismatches,
    find_underground_slack_mismatches,
    _load_slack_loops_with_labels_and_coords,
    invalid_slack_loops,
    find_distribution_end_tail_issues,
)
from modules.simple_scripts.footage_issues import (
    find_missing_distribution_footage,
    find_overlength_drop_cables,
)
from modules.simple_scripts.nids import find_nid_mismatches, load_nids
from modules.simple_scripts.service_locations import check_all_service_location_attributes
from modules.simple_scripts.pole_issues import load_power_poles
from modules.simple_scripts.conduit_rules import run_all_conduit_checks
from modules.simple_scripts.vault_rules import run_all_vault_checks

from modules.simple_scripts.nap_rules import (
    find_nap_drop_mismatches,
    find_nap_id_format_issues,
    scan_nap_spec_warnings,
)
from modules.simple_scripts.pole_issues import (
    load_power_poles,
    load_aerial_distributions,
    load_messenger_wire,
    find_power_pole_issues,
)


def collect_network_statistics():
    """
    Gather counts and names for network components and issue totals.

    Returns a dict consumed by excel_writer.write_network_statistics(), including:
      - nap_mismatch_issues, nap_naming_issues, nap_spec_warnings
      - dist_nap_walker_issues is still set in main.py after the deep walk
    """
    # NAP count
    nap_coords, nap_map = load_features('nap', 'ID')
    nap_count = len(nap_coords)

    # Service Location count
    service_coords, _ = load_features('service-location', 'ID')
    service_location_count = len(service_coords)

    # NID count
    nids = load_nids()
    nid_count = len(nids)

    # T-3 vaults
    t3_coords, t3_map = load_t3_vaults()
    t3_names = sorted(set(t3_map.values()))

    # Power Poles count
    pole_coords, pole_map = load_features('power-pole', 'ID')
    power_pole_count = len(pole_coords)

    # Vaults (excluding T-3)
    vault_coords, vault_map = load_features('vault', 'vetro_id')
    t3_set = {(round(lat, 6), round(lon, 6)) for (lat, lon) in t3_coords}
    vault_count_excl_t3 = sum(
        1 for (lat, lon) in vault_coords
        if (round(lat, 6), round(lon, 6)) not in t3_set
    )

    # Fiber-Drop issues
    drops = load_fiber_drops()
    fiber_drop_issues = (
        len(find_color_mismatches(emit_info=False)) +
        len(find_missing_service_location_drops(fd_load_service_locations(), drops, emit_info=False))
    )

    # Slack-related issues
    slack_dist_issues = len(find_slack_dist_mismatches())
    underground_slack_issues = len(find_underground_slack_mismatches(nap_coords, vault_coords, vault_map))
    slack_raw = _load_slack_loops_with_labels_and_coords()
    slack_coords = {(lat, lon) for lat, lon, *_ in slack_raw}
    aerial_slack_issues = len(invalid_slack_loops(pole_coords, nap_coords, slack_coords))
    tail_end_slack_issues = len(find_distribution_end_tail_issues())

    # Footage issues (Distribution Note missing/invalid + Drops > 250 ft)
    footage_issues = (
        len(find_missing_distribution_footage()) +
        len(find_overlength_drop_cables(limit_ft=250.0) or [])
    )

    # NID & Service-Location attribute issues
    nid_drop_issues = len(find_nid_mismatches())
    svc_attr_issues = len(check_all_service_location_attributes(log_debug=False))

    # Conduit & Vault combined issue totals (for PON Statistics)
    _conduit_checks = run_all_conduit_checks()
    conduit_issues = sum(len(v) for v in _conduit_checks.values())
    _vault_checks = run_all_vault_checks()
    vault_issues = sum(len(v) for v in _vault_checks.values())

    # NEW: NAP totals for the PON sheet
    nap_mismatch_issues = len(find_nap_drop_mismatches() or [])
    nap_naming_issues  = len(find_nap_id_format_issues() or [])
    # De-dupe spec warnings so PON Statistics matches the NAP Issues sheet
    nap_spec_warnings = len({
        (str(w.get('NAP ID', '')), str(w.get('Field', '')), str(w.get('Value', '')).strip().lower())
        for w in (scan_nap_spec_warnings() or [])
    })

    # NEW: Power Pole anchor issues (same logic main.py uses to build the sheet)
    poles = load_power_poles()
    distribution_features = load_aerial_distributions()
    messenger_segments = load_messenger_wire()
    messenger_graph = {}
    for seg_list in messenger_segments.values():
        for seg in seg_list:
            for a, b in zip(seg, seg[1:]):
                messenger_graph.setdefault(a, set()).add(b)
                messenger_graph.setdefault(b, set()).add(a)
    power_pole_issues = len(find_power_pole_issues(poles, distribution_features, messenger_graph))

    return {
        'nap_count': nap_count,
        'service_location_count': service_location_count,
        'nid_count': nid_count,
        't3_names': t3_names,
        'power_pole_count': power_pole_count,
        'vault_count_excluding_t3': vault_count_excl_t3,

        # Issue families
        'fiber_drop_issues': fiber_drop_issues,
        'slack_dist_issues': slack_dist_issues,
        'underground_slack_issues': underground_slack_issues,
        'aerial_slack_issues': aerial_slack_issues,
        'tail_end_slack_issues': tail_end_slack_issues,
        'footage_issues': footage_issues,
        'nid_drop_issues': nid_drop_issues,
        'svc_attr_issues': svc_attr_issues,
        'conduit_issues': conduit_issues,
        'vault_issues': vault_issues,

        # NEW: NAP-related totals expected by write_network_statistics()
        'nap_mismatch_issues': nap_mismatch_issues,
        'nap_naming_issues': nap_naming_issues,
        'nap_spec_warnings': nap_spec_warnings,

        # NEW: expose power pole issues
        'power_pole_issues': power_pole_issues,

        # Note: 'dist_nap_walker_issues' is added in main.py after the deep walk
    }
