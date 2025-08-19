# modules/config.py
import logging
import os
import sys
import re

# -----------------------------
# Logging configuration toggles
# -----------------------------
SHOW_ALL_SHEETS = True

# Where to write the log file (relative or absolute path)
LOG_FILE = "peercheck.log"
WRITE_LOG_FILE = False

# Detail level for log **content** and formatter
# - "DEBUG": verbose with [module:function:lineno] header
# - "INFO":  condensed one-liners (functions will emit summarized messages)
LOG_DETAIL = "INFO"

# show the actual walker path at the end of each line in INFO logs
LOG_INCLUDE_WALK_PATH = False

# Color mode for values inserted by code (e.g., drop/splice colors)
# - "ANSI":  use ANSI background colors in real terminals
# - "EMOJI": use color square emojis (portable; works anywhere)
# - "OFF":   no color decoration
LOG_COLOR_MODE = "OFF"
# Separate boolean toggles used throughout modules
LOG_NAP_TIEPOINTS = True
LOG_SVCLOC_DEBUG  = True
LOG_DROP_DEBUG    = True

LOG_DROP_SUMMARY_BLOCK = True
#check

LOG_MIRROR_SHEETS           = True   # master switch enabling mirroring of Excel sheet content to logs
LOG_SLACK_LOOP_DEBUG        = False  # when True, always emit Slack Loop mirror at DEBUG regardless of LOG_DETAIL
LOG_SLACK_LOOP_SHEET_TO_LOG = True   # when True (and not forced off), mirror 'Slack Loop Issues' sheet to the log

# NID sheet → log mirror & debug toggle
LOG_NID_DEBUG = False                 # Force DEBUG-level NID mirror regardless of LOG_DETAIL
LOG_NID_SHEET_TO_LOG = False           # Mirror the 'NID Issues' sheet to the log when True

 # Legend / Abbreviations header shown once at the start of deep-walk style logs
LOG_SHOW_ABBREV_HEADER = True
LOG_ABBREV_HEADER_LINES = [
    "",
    "",  # add more lines as needed (max 5 are printed)
    "SL = Service Locations",
    "",
    "",
]

# Effective root log level derived from LOG_DETAIL
LOG_LEVEL = logging.DEBUG if str(LOG_DETAIL).upper() == "DEBUG" else logging.INFO


# ---------------------------------
# ANSI-aware formatter for file log
# ---------------------------------
class _StripAnsiFormatter(logging.Formatter):
    """Formatter that strips ANSI escape codes from the final formatted string.

    Using a **formatter** (not a filter) keeps the original LogRecord intact, so
    the StreamHandler/console can still render colors while the FileHandler writes
    clean text.
    """
    _ansi_re = re.compile(r"\x1b\[[0-9;]*m")

    def format(self, record: logging.LogRecord) -> str:
        s = super().format(record)
        return self._ansi_re.sub("", s)

def setup_logging():
    """Configure root logging for both console and (optional) file handlers.

    - Chooses verbose vs. condensed format from LOG_DETAIL.
    - File handler strips ANSI codes; console keeps them.
    - Tries to enable ANSI on Windows terminals (via colorama).
    - Auto-falls back to EMOJI colors when not running on a TTY,
      but does **not** override an explicit LOG_COLOR_MODE="OFF".
    """
    # Prefer enabling ANSI on Windows terminals
    try:
        import colorama
        colorama.just_fix_windows_console()
        colorama.init()
    except Exception:
        pass

    detail = str(LOG_DETAIL).upper()
    fmt_verbose   = "%(asctime)s %(levelname)-8s [%(name)s:%(funcName)s:%(lineno)d] %(message)s"
    fmt_condensed = "%(asctime)s %(levelname)-8s %(message)s"
    fmt = fmt_verbose if detail == "DEBUG" else fmt_condensed

    # Reset the root logger
    root = logging.getLogger()
    root.setLevel(LOG_LEVEL)
    for h in list(root.handlers):
        root.removeHandler(h)

    # (Optional) File handler: strip ANSI from the final output
    fh = None
    if bool(globals().get("WRITE_LOG_FILE", True)):
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setFormatter(_StripAnsiFormatter(fmt))
        root.addHandler(fh)

    # Console handler: keep ANSI (if any)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter(fmt))
    root.addHandler(sh)

    # ---- Smart color fallback for non-TTY outputs (IDLE, Debug Console, etc.) ----
    try:
        is_tty = getattr(sh.stream, "isatty", lambda: False)()
    except Exception:
        is_tty = False

    if (not is_tty) or os.environ.get("TERM", "").lower() in ("", "dumb"):
        global LOG_COLOR_MODE
        if str(LOG_COLOR_MODE).upper() == "ANSI":
            LOG_COLOR_MODE = "EMOJI"

    # Optional: quiet noisy libs if needed
    # logging.getLogger("urllib3").setLevel(logging.WARNING)

import datetime
import traceback

def write_crash_log(exc: BaseException):
    """
    Write an unhandled exception (with traceback) to a timestamped crash log file.
    """
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    crash_file = os.path.join(
        os.path.dirname(LOG_FILE),
        f"crash_{ts}.log"
    )
    try:
        with open(crash_file, "w", encoding="utf-8") as f:
            f.write(f"Unhandled exception at {ts}\n")
            f.write("=" * 80 + "\n")
            traceback.print_exc(file=f)
        print(f"⚠ Crash log written to: {crash_file}")
    except Exception as log_err:
        print(f"Failed to write crash log: {log_err}")

# At bottom of modules/config.py (after write_crash_log)
import sys

def _global_excepthook(exc_type, exc_value, exc_traceback):
    # Skip logging for KeyboardInterrupt
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    try:
        write_crash_log(exc_value)
    except Exception:
        pass
    # Still print to stderr like normal
    sys.__excepthook__(exc_type, exc_value, exc_traceback)

# Install the hook
sys.excepthook = _global_excepthook



# ----------------
# Data directories
# ----------------
# Always reference as: modules.config.DATA_DIR
if getattr(sys, "frozen", False):
    # Running from a bundled executable — place data folder next to the .exe
    exe_dir = os.path.dirname(sys.executable)
    DATA_DIR = os.path.join(exe_dir, "data")
else:
    # Running from source — data folder lives one level up
    here = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.path.join(here, "..", "data")

DATA_DIR = os.path.abspath(DATA_DIR)


# --------------------------
# Other project-level config
# --------------------------
OUTPUT_XLSX = 'Layer_By_Person_Summary.xlsx'
ID_COL = 'ID'  # CSV’s SC-ID column header

PATTERNS = [
    'NAP Location attribute was added',
    'Build Type attribute was added',
    'Drop Type attribute was added',
    'Conduit Type attribute was added',
    'Size attribute was added',
    'Feature was created',
    'Anchor CD #1 attribute was added',
    'ID attribute was updated',
    'Drop Type attribute was updated',
    'Build Type attribute was updated',
    'NAP Location attribute was updated',
    'Building Type attribute was updated',
    'Type attribute was added',
    'Geometry changed',
    'Building Type attribute was added',
]
