# modules/basic/abbrev_header.py
import logging
import modules.config

# Prevent duplicate prints across a single run
_printed_once = False

def log_abbrev_header(force: bool = False, logger: logging.Logger | None = None) -> None:
    """
    Logs a small Legend / Abbreviations header (up to 5 lines) once per run.
    Controlled by modules.config:
      - LOG_SHOW_ABBREV_HEADER: bool
      - LOG_ABBREV_HEADER_LINES: list[str]  (only first 5 are printed)

    Args:
      force: if True, prints even if it was already printed earlier in the run.
      logger: optional logger to use; defaults to a module-scoped logger.
    """
    global _printed_once
    if logger is None:
        logger = logging.getLogger(__name__)

    show = bool(getattr(modules.config, "LOG_SHOW_ABBREV_HEADER", False))
    lines = list(getattr(modules.config, "LOG_ABBREV_HEADER_LINES", []))[:5]

    if not show or not lines:
        return
    if _printed_once and not force:
        return

    # Respect LOG_DETAIL: use DEBUG when LOG_DETAIL=="DEBUG", else INFO.
    detail = str(getattr(modules.config, "LOG_DETAIL", "DEBUG")).upper()
    emit = logger.debug if detail == "DEBUG" else logger.info

    emit("==== Legend / Abbreviations ====")
    for line in lines:
        emit(line if line is not None else "")
    emit("==== End Legend ====")

    _printed_once = True

def log_issue_header(title: str, lines: list[str], logger: logging.Logger | None = None) -> None:
    """
    Print a header block that summarizes issue lines again at the end of a pass.
    - 'title' becomes the banner (e.g., "[Drop Issues] Color Mismatches")
    - 'lines' are preformatted single-line strings
    """
    if logger is None:
        logger = logging.getLogger(__name__)
    if not lines:
        return

    # Respect LOG_DETAIL: use DEBUG when LOG_DETAIL=="DEBUG", else INFO.
    emit = logger.error

    emit(f"==== {title} ({len(lines)}) ====")
    for ln in lines:
        emit(ln if ln is not None else "")
    emit(f"==== End {title} ====")

def format_table_lines(headers: list[str], rows: list[list[str]], sep: str = " | ", max_col_widths: list[int] | None = None) -> list[str]:
    """
    Return aligned text lines for a simple table:
      • headers: column titles
      • rows:    list of rows, each a list of strings
      • sep:     column separator
      • max_col_widths: optional per-column max widths (truncate with …)

    This strips ANSI before measuring width so things align in the file log.
    """
    import re
    ansi_re = re.compile(r"\x1b\[[0-9;]*m")

    def vis_len(s: str) -> int:
        return len(ansi_re.sub("", str(s)))

    cols = len(headers)
    widths = [vis_len(h) for h in headers]

    for row in rows:
        for i in range(cols):
            cell = row[i] if i < len(row) else ""
            widths[i] = max(widths[i], vis_len(cell))

    if max_col_widths:
        widths = [
            min(widths[i], max_col_widths[i]) if i < len(max_col_widths) and max_col_widths[i] else widths[i]
            for i in range(cols)
        ]

    def fit(cell: str, w: int) -> str:
        # pad or truncate with ellipsis; measure width on ANSI-stripped text
        raw = ansi_re.sub("", str(cell))
        if len(raw) <= w:
            return raw + (" " * (w - len(raw)))
        if w <= 0:
            return ""
        ell = "…"
        keep = max(0, w - len(ell))
        return (raw[:keep] + ell) if keep else ell

    lines: list[str] = []
    lines.append(sep.join(fit(h, widths[i]) for i, h in enumerate(headers)))
    for row in rows:
        line = sep.join(fit(row[i] if i < len(row) else "", widths[i]) for i in range(cols))
        lines.append(line)
    return lines
