# main.py
from modules.config import setup_logging
from modules.basic.log_configs import log_abbrev_header

import glob
import sys
import json
import os
import re
import logging
import pandas as pd
from collections import Counter

import modules.config

from modules.basic.distance_utils import haversine, THRESHOLD_M

from modules.simple_scripts.geojson_loader import (
    load_features,
    load_slack_loops,
    load_fiber_distribution,
    load_t3_vaults,
)

from modules.simple_scripts.slack_loops import (
    needs_slack,
    invalid_slack_loops,
    find_slack_dist_mismatches,
    find_underground_slack_mismatches,
    find_distribution_end_tail_issues,
)

from modules.simple_scripts.footage_issues import find_missing_distribution_footage, find_overlength_fiber_cables


from modules.simple_scripts.fiber_drop import (
    find_color_mismatches,
    load_service_locations as load_fd_service_locations,
    load_fiber_drops as load_fd_drops,
    find_missing_service_location_drops,
    sort_service_location_ids_like_attributes,
)

from modules.simple_scripts.nids import find_nid_mismatches

from modules.simple_scripts.network_statistics import collect_network_statistics

from modules.simple_scripts.service_locations import check_service_location_attributes, check_all_service_location_attributes

from modules.simple_scripts.excel_writer import (
    new_workbook,
    auto_size,
    write_geojson_summary,
    write_network_statistics,
    write_person_sheets,
    write_drop_issues_sheet,
    write_slack_loop_issues_sheet,
    write_footage_issues_sheet,
    write_nid_issues,
    write_service_location_attr_issues,
    save_workbook,
    write_nap_issues_sheet,
    write_power_pole_issues_sheet,
    write_distribution_and_nap_walker_sheet,
    write_conduit_sheet,
    write_vaults_sheet,
)

from modules.simple_scripts.nap_rules import (
    find_nap_drop_mismatches,
    find_nap_id_format_issues,
)

from modules.simple_scripts.pole_issues import (
    find_power_pole_issues,
    load_power_poles,
    load_aerial_distributions,
    load_messenger_wire,
)

from modules.hard_scripts.distribution_walker import find_deep_distribution_mismatches
from modules.simple_scripts.conduit_rules import run_all_conduit_checks
from modules.simple_scripts.vault_rules import run_all_vault_checks


logger = logging.getLogger(__name__)



def find_csv():
    """Return CSV path or None if none found."""
    files = glob.glob(f'{modules.config.DATA_DIR}/*.csv')
    return files[0] if files else None

# main.py — replace full function below

def main(data_dir=None, gui_out_path=None):
    """
    Run the checks and write an Excel workbook whose filename includes a timestamp.
    The peercheck log file name will match the Excel base name ('.log' instead of '.xlsx').
    """
    import os, re, glob, json
    from datetime import datetime

    # 1) Ensure DATA_DIR is set for this run (GUI passes this in)
    if data_dir:
        modules.config.DATA_DIR = data_dir  # always reference modules.config.DATA_DIR

    # 2) Decide the output folder (directory that will contain both XLSX and LOG)
    #    - If gui_out_path is a directory, use it
    #    - If gui_out_path is a file path, use its parent directory
    #    - Otherwise, fall back to the directory of modules.config.OUTPUT_XLSX
    if gui_out_path:
        if os.path.isdir(gui_out_path):
            out_dir = gui_out_path
        else:
            out_dir = os.path.dirname(gui_out_path) or os.getcwd()
        os.makedirs(out_dir, exist_ok=True)
    else:
        # Fall back to OUTPUT_XLSX's folder (create if needed)
        output_dir = os.path.dirname(getattr(modules.config, "OUTPUT_XLSX", "output.xlsx"))
        os.makedirs(output_dir, exist_ok=True)
        out_dir = output_dir

    # 3) Determine v_plan (used in the Excel/log base name)
    sl_files = glob.glob(os.path.join(modules.config.DATA_DIR, "service-location*.geojson"))
    if sl_files:
        try:
            with open(sl_files[0], "r", encoding="utf-8") as f:
                sl_gj = json.load(f)
            v_plan = (
                sl_gj.get("v_plan")
                or sl_gj.get("features", [{}])[0].get("properties", {}).get("v_plan")
                or "output"
            )
        except Exception:
            v_plan = "output"
    else:
        v_plan = "output"

    # 4) Build a timestamped base name and sanitize it for file safety
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    raw_base = f"{v_plan} Peer Check - {ts}"

    def sanitize_filename(name: str) -> str:
        return re.sub(r'[<>:\"/\\\\|?*]', '_', str(name))

    base = sanitize_filename(raw_base)

    # 5) Compute final file paths (Excel + Log) and (re)configure logging to that log path
    xlsx_path = os.path.join(out_dir, f"{base}.xlsx")
    modules.config.LOG_FILE = os.path.join(out_dir, f"{base}.log")
    modules.config.setup_logging()  # reconfigure so subsequent logs go to the matching name
    log_abbrev_header(force=True)

    # (Optional) minimal console context prints
    print(f"▶ EXE lives at: {sys.executable}")
    print(f"▶ DATA_DIR:   {modules.config.DATA_DIR}")
    print(f"▶ OUT_DIR:    {out_dir}")
    print(f"▶ RUN BASE:   {base}")

    try:
        # =========================
        # Existing workload begins
        # =========================

        # 1) find CSV
        csvfile = find_csv()

        # 2) Load GeoJSON layers
        nap_coords,   nap_map   = load_features('nap',        'ID')
        vault_coords, vault_map = load_features('vault',     'vetro_id')
        t3_coords,    t3_map    = load_t3_vaults()
        power_coords, power_map = load_features('power-pole', 'ID')
        slack_raw               = load_slack_loops()
        dist_coords             = load_fiber_distribution()

        # 3) Analyze Slack Loops
        slack_counter  = Counter(slack_raw)
        slack_coords   = set(slack_counter.keys())
        stacked_coords = {c: cnt for c, cnt in slack_counter.items() if cnt > 1}

        # classify where loops sit
        slack_on_nap, slack_on_vault, slack_on_power = set(), set(), set()
        for lat_s, lon_s in slack_coords:
            for lat_n, lon_n in nap_coords:
                if haversine(lat_s, lon_s, lat_n, lon_n) <= THRESHOLD_M:
                    slack_on_nap.add((lat_s, lon_s)); break
            for lat_v, lon_v in vault_coords:
                if haversine(lat_s, lon_s, lat_v, lon_v) <= THRESHOLD_M:
                    slack_on_vault.add((lat_s, lon_s)); break
            for lat_p, lon_p in power_coords:
                if haversine(lat_s, lon_s, lat_p, lon_p) <= THRESHOLD_M:
                    slack_on_power.add((lat_s, lon_s)); break

        slack_missing = slack_coords - slack_on_nap - slack_on_vault - slack_on_power

        # 4) Read CSV if present
        if csvfile:
            df = (
                pd.read_csv(
                    csvfile,
                    dtype=str,
                    usecols=[modules.config.ID_COL, 'Type of Edit', 'Layer Name', 'Edited By']
                )
                .fillna('')
            )
        else:
            df = None

        # 5) Build Workbook & Sheets
        wb, default = new_workbook()

        # GeoJSON Summary (always)
        ws0 = wb.create_sheet(title='Slack Loop Summary', index=0)
        write_geojson_summary(
            ws0,
            slack_counter,
            stacked_coords,
            slack_missing,
            nap_map,
            vault_map,
            t3_map,
            power_map
        )

        # Deep-walk (Distribution & NAP) → sheet + count
        issues = find_deep_distribution_mismatches()
        if modules.config.SHOW_ALL_SHEETS or issues:
            write_distribution_and_nap_walker_sheet(wb, issues)

        # Network Statistics (first sheet) — include walker count
        stats = collect_network_statistics()
        stats['dist_nap_walker_issues'] = len(issues)
        write_network_statistics(wb, stats)

        # Conduit + Vault rule checks and sheets
        conduit_results = run_all_conduit_checks()
        vault_results   = run_all_vault_checks()
        # DEBUG: dump only the two vaults we care about into DATA_DIR/_debug_vaults_missing.csv
        from modules import config
        import pandas as pd

        # DEBUG: dump only the two vaults we care about (GUI-safe)
        from modules import config
        from pathlib import Path
        import pandas as pd

        ### DEBUG FOR VAULTS ###
        # _debug_ids = {
        #     "f6d14ff8-fa96-40d9-ab22-4c6c2014a6b4",
        #     "62726729-eec0-48f9-97a5-73cd865695ef",
        # }

        # _df = pd.DataFrame(vault_results.get("vaults_missing_conduit") or [])
        # out_csv = Path(config.DATA_DIR) / "_debug_vaults_missing.csv"

        # if "Vault Vetro ID" in _df.columns:
        #     _df[_df["Vault Vetro ID"].isin(_debug_ids)].to_csv(out_csv, index=False)
        # else:
        #     # create an empty file so you know the key/columns didn't match
        #     pd.DataFrame().to_csv(out_csv, index=False)





        # Only create the sheets if there’s data, unless SHOW_ALL_SHEETS is on
        if modules.config.SHOW_ALL_SHEETS or any(conduit_results.values()):
            write_conduit_sheet(wb, conduit_results)

        if modules.config.SHOW_ALL_SHEETS or any(vault_results.values()):
            write_vaults_sheet(wb, vault_results)

        # Per-layer person-summary sheets
        if df is not None:
            write_person_sheets(wb, df, modules.config.PATTERNS, modules.config.ID_COL)

        # Consolidated Slack Loop Issues
        sd_issues     = find_slack_dist_mismatches()
        ug_slack      = find_underground_slack_mismatches(nap_coords, vault_coords, vault_map)
        aerial_loops  = invalid_slack_loops(power_coords, nap_coords, slack_coords)
        tails_issues  = find_distribution_end_tail_issues()
        aerial_issues_data = [
            (
                power_map.get((round(lat,6), round(lon,6)), ''),
                f'{lat:.6f}',
                f'{lon:.6f}',
                'Pole has Drop(s) + NAP but no Slack Loop'
            )
            for lat, lon in aerial_loops
        ]
        if (sd_issues or ug_slack or aerial_issues_data or tails_issues or modules.config.SHOW_ALL_SHEETS):
            write_slack_loop_issues_sheet(wb, sd_issues, ug_slack, aerial_issues_data, tails_issues)

        # Fiber-Drop Validation
        service_raw    = load_fd_service_locations()
        service_coords = [(pt[0], pt[1], svc_id) for pt, _, svc_id in service_raw]
        drop_dict      = load_fd_drops()
        drop_coords    = list(drop_dict.keys())

        mismatches = find_color_mismatches()
        missing    = find_missing_service_location_drops(service_coords, drop_coords)
        combined   = sort_service_location_ids_like_attributes(list(set(mismatches) | set(missing)))
        if modules.config.SHOW_ALL_SHEETS or combined:
            write_drop_issues_sheet(wb, service_coords, drop_coords, combined)

        # Footage Issues (Distribution Note + Fiber Cable > 250 ft)
        footage_issues = find_missing_distribution_footage()
        overlength = find_overlength_fiber_cables()
        if modules.config.SHOW_ALL_SHEETS or footage_issues or overlength:
            write_footage_issues_sheet(wb, footage_issues)


        # NID-Drop Validation
        nid_issues = find_nid_mismatches()
        if modules.config.SHOW_ALL_SHEETS or nid_issues:
            write_nid_issues(wb, nid_issues)

        # Service-Location Attribute Validation
        for _sl_path in glob.glob(f"{modules.config.DATA_DIR}/service-location*.geojson"):
            sl_attr_issues = check_all_service_location_attributes()
            if modules.config.SHOW_ALL_SHEETS or sl_attr_issues:
                write_service_location_attr_issues(wb, sl_attr_issues)

        # NAP-Drop / ID-Format Validation
        nap_issues       = find_nap_drop_mismatches()
        id_format_issues = find_nap_id_format_issues()
        if modules.config.SHOW_ALL_SHEETS or nap_issues or id_format_issues:
            write_nap_issues_sheet(wb, nap_issues, id_format_issues)

        # Power Pole anchor‐check
        poles                  = load_power_poles()
        distribution_features  = load_aerial_distributions()
        messenger_segments     = load_messenger_wire()
        messenger_graph = {}
        for seg_list in messenger_segments.values():
            for seg in seg_list:
                for a, b in zip(seg, seg[1:]):
                    messenger_graph.setdefault(a, set()).add(b)
                    messenger_graph.setdefault(b, set()).add(a)
        pole_issues = find_power_pole_issues(poles, distribution_features, messenger_graph)
        if modules.config.SHOW_ALL_SHEETS or pole_issues:
            write_power_pole_issues_sheet(wb, pole_issues)

        # Remove empty default sheet, autosize columns, and save to the timestamped path
        if (
            default.title == 'Sheet'
            and default.max_row    == 1
            and default.max_column == 1
            and default['A1'].value is None
        ):
            wb.remove(default)
        auto_size(wb)

        from modules.simple_scripts.excel_writer import drop_empty_issue_sheets
        drop_empty_issue_sheets(wb)

        # Save
        save_workbook(wb, xlsx_path)

    except Exception as e:
        logger.critical("Unhandled exception in main(): %s", e, exc_info=True)
        try:
            modules.config.write_crash_log(e)
        except Exception:
            pass
        raise

if __name__ == "__main__":
    modules.config.setup_logging()
    issues = find_deep_distribution_mismatches()
    for issue in issues:
        ((logging.getLogger(__name__).debug) if str(getattr(modules.config, "LOG_DETAIL", "DEBUG")).upper() == "DEBUG" else logging.getLogger(__name__).info)(issue)


