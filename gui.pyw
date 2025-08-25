# gui.py

import os
import sys
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Project imports
import modules.config
import main  # keep as in existing file
run_main = main.main  # alias, matches your current usage

# --- UI helpers for first-time Logs tooltip and periodic nudge popups ---
import tkinter as tk
from tkinter import Toplevel, Label, Button
from typing import Callable, Optional
import modules.config as cfg

def _ui_show_mouse_popover(parent: tk.Widget,
                           text: str,
                           dismiss_after_ms: int = 5000,
                           include_dont_show: bool = False,
                           on_never: Optional[Callable[[], None]] = None) -> None:
    """
    Small, tooltip-like popup near the current mouse position.

    - parent: any widget in the target toplevel (used to locate screen coords)
    - text: message to display (you'll fill in your real copy later)
    - dismiss_after_ms: auto-close delay; set to 0 to require manual close
    - include_dont_show: if True, shows a 'Do not show again' button
    - on_never: callback invoked if 'Do not show again' is clicked
    """
    # Find current pointer position
    try:
        x = parent.winfo_pointerx()
        y = parent.winfo_pointery()
    except Exception:
        parent.update_idletasks()
        x = parent.winfo_rootx() + 40
        y = parent.winfo_rooty() + 40

    tip = Toplevel(parent)
    tip.wm_overrideredirect(True)  # borderless
    tip.attributes("-topmost", True)

    # Basic styling
    frame = tk.Frame(tip, bd=1, relief="solid", bg="#111")
    frame.pack(fill="both", expand=True)
    lbl = Label(frame, text=text, fg="#fff", bg="#111", justify="left", padx=10, pady=8)
    lbl.pack(anchor="w")

    if include_dont_show:
        btn_row = tk.Frame(frame, bg="#111")
        btn_row.pack(fill="x", padx=8, pady=(0, 8))

        def _never():
            try:
                if on_never:
                    on_never()
            finally:
                tip.destroy()

        Button(btn_row, text="Do not show again", command=_never).pack(side="left")
        Button(btn_row, text="Dismiss", command=tip.destroy).pack(side="right")  # <-- was 'Close'

    tip.update_idletasks()
    # Offset so the tooltip doesn't hide the cursor
    tip.geometry(f"+{x + 12}+{y + 12}")
    tip.update()  # ensure the window is mapped now

    if dismiss_after_ms and not include_dont_show:
        tip.after(int(dismiss_after_ms), tip.destroy)


def _maybe_show_logs_nag(parent: tk.Widget) -> None:
    """
    Called when 'Run Checks' is pressed.
    Increments a run counter until user clicks Logs.
      - On the 5th run: soft reminder (auto-dismiss 10s) with your exact text.
      - On the 10th run and every 5 thereafter (15, 20, ...): reminder with
        'Do not show again' and 'Dismiss'. 'Dismiss' means it'll return every 5 runs.
        'Do not show again' sets a permanent suppress flag.
    Stops entirely once the user clicks Logs (logs_clicked_once=True).
    """
    suppress = bool(cfg.get_pref("suppress_logs_nag", False))
    if suppress:
        return

    clicked = bool(cfg.get_pref("logs_clicked_once", False))
    runs = int(cfg.get_pref("runs_without_logs_click", 0))

    if clicked:
        return

    # User still hasn't clicked Logs — count this run
    runs += 1
    cfg.set_pref("runs_without_logs_click", runs)

    if runs == 5:
        # 5th attempt: soft reminder (auto-dismiss) — KEEP EXACT TEXT/TIMER
        _ui_show_mouse_popover(
            parent,
            text="Remember, you can click on Logs, and you will get more detailed info and see exactly how this program works on finding the errors.",
            dismiss_after_ms=10000,
            include_dont_show=False
        )
    elif runs >= 10 and runs % 5 == 0:
        # 10th, 15th, 20th, ... : stronger reminder with 'Do not show again'
        def _never():
            cfg.set_pref("suppress_logs_nag", True)

        _ui_show_mouse_popover(
            parent,
            text="You haven't opened Logs yet. Want to stop these reminders? these reminders.",
            dismiss_after_ms=0,  # require user action
            include_dont_show=True,
            on_never=_never
        )


APP_TITLE = "Peer Checking GUI"

import tkinter as tk
from tkinter import ttk, messagebox
import traceback
import modules.config

class SettingsDialog(tk.Toplevel):
    """
    A modal window that lets you adjust most runtime toggles in modules.config.
    Changes apply immediately (and reconfigure logging) when you click Apply.
    """

    # Internal keys (unchanged)
    BOOL_KEYS = [
        "LOG_SVCLOC_DEBUG",
        "LOG_DROP_DEBUG",
        "LOG_MIRROR_SHEETS",
        "LOG_SLACK_LOOP_SHEET_TO_LOG",
        "LOG_NID_SHEET_TO_LOG",
    ]
    CHOICE_KEYS = {
        "LOG_DETAIL": ["INFO", "DEBUG"],
        "LOG_COLOR_MODE": ["OFF", "EMOJI", "ANSI"],
    }

    # 👇 NEW: customizable labels for what appears in the UI
    DISPLAY_LABELS = {
        # Examples — customize freely:
        "LOG_SVCLOC_DEBUG": "Service Locations",
        "LOG_DROP_DEBUG": "Fiber Drops",
        "LOG_MIRROR_SHEETS": "NAP Distribution Walker",
        "LOG_SLACK_LOOP_SHEET_TO_LOG": "Slack Loops",
        "LOG_NID_SHEET_TO_LOG": "NIDs",
        # You can also add choice keys if you want nicer labels in the Modes section:
        # "LOG_DETAIL": "Log Detail",
        # "LOG_COLOR_MODE": "Log Color Mode",
    }

    def __init__(self, master):
        super().__init__(master)
        self.title("Settings")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        pad = {"padx": 10, "pady": 6}

        # ------ Boolean toggles ------
        self._bool_vars = {}
        bool_frame = ttk.LabelFrame(self, text="Logs")
        bool_frame.grid(row=0, column=0, sticky="nsew", **pad)
        for r, key in enumerate(self.BOOL_KEYS):
            var = tk.BooleanVar(value=bool(getattr(modules.config, key, False)))
            # 👇 use friendly label
            label = self._label_for(key)
            ck = ttk.Checkbutton(bool_frame, text=label, variable=var)
            ck.grid(row=r, column=0, sticky="w", padx=8, pady=3)
            self._bool_vars[key] = var

        # ------ Choice toggles ------
        self._choice_vars = {}
        choice_frame = ttk.LabelFrame(self, text="Modes")
        choice_frame.grid(row=0, column=1, sticky="nsew", **pad)

        r = 0
        for key, options in self.CHOICE_KEYS.items():
            # optional: friendlier label for choice keys too
            choice_label = self._label_for(key)
            ttk.Label(choice_frame, text=choice_label).grid(row=r, column=0, sticky="w", padx=8, pady=3)
            var = tk.StringVar(value=str(getattr(modules.config, key, options[0])).upper())
            cb = ttk.Combobox(choice_frame, textvariable=var, values=options, state="readonly", width=10)
            cb.grid(row=r, column=1, sticky="w", padx=8, pady=3)
            self._choice_vars[key] = var
            r += 1

        # --- LOG_INCLUDE_WALK_PATH checkbox in Modes (bottom-left)
        self.var_log_include_walk_path = tk.BooleanVar(
            value=bool(getattr(modules.config, "LOG_INCLUDE_WALK_PATH", True))
        )
        ttk.Checkbutton(
            choice_frame,
            text="Include walk path in INFO logs",
            variable=self.var_log_include_walk_path
        ).grid(row=r, column=0, sticky="w", padx=8, pady=(8, 3))


        # ------ Buttons ------
        btns = ttk.Frame(self)
        btns.grid(row=1, column=0, columnspan=2, sticky="ew", **pad)
        btns.columnconfigure(0, weight=1)
        ttk.Button(btns, text="Reset to Defaults", command=self._reset_defaults).grid(row=0, column=0, sticky="w")
        ttk.Button(btns, text="Apply", command=self._apply).grid(row=0, column=1, padx=6)
        ttk.Button(btns, text="Close", command=self._close).grid(row=0, column=2)

        self.bind("<Return>", lambda e: self._apply())
        self.bind("<Escape>", lambda e: self._close())

    # 👇 NEW: helper that returns a friendly label or a title-cased fallback
    def _label_for(self, key: str) -> str:
        if key in self.DISPLAY_LABELS:
            return self.DISPLAY_LABELS[key]
        # Fallback: turn "LOG_INCLUDE_WALK_PATH" → "Log Include Walk Path"
        return key.replace("_", " ").title()

    def _reset_defaults(self):
        # Reasonable built-in defaults; mirrors modules/config.py initial values
        defaults = {
            "LOG_SVCLOC_DEBUG": True,
            "LOG_DROP_DEBUG": True,
            "LOG_MIRROR_SHEETS": True,
            "LOG_SLACK_LOOP_SHEET_TO_LOG": True,
            "LOG_NID_SHEET_TO_LOG": False,
            "LOG_DETAIL": "INFO",
            "LOG_COLOR_MODE": "OFF",
        }
        for k, v in defaults.items():
            if k in self._bool_vars:
                self._bool_vars[k].set(bool(v))
            elif k in self._choice_vars:
                self._choice_vars[k].set(str(v).upper())
        self.var_log_include_walk_path.set(False)

    def _apply(self):
        # Push values back into modules.config
        for k, var in self._bool_vars.items():
            setattr(modules.config, k, bool(var.get()))
        for k, var in self._choice_vars.items():
            setattr(modules.config, k, str(var.get()).upper())
        # Also push the Modes checkbox
        modules.config.LOG_INCLUDE_WALK_PATH = bool(self.var_log_include_walk_path.get())

        # Recompute the effective LOG_LEVEL in modules.config and re-setup logging
        try:
            modules.config.LOG_DETAIL = str(getattr(modules.config, "LOG_DETAIL", "INFO")).upper()
            modules.config.LOG_LEVEL = 10 if modules.config.LOG_DETAIL == "DEBUG" else 20  # logging.DEBUG/INFO
        except Exception:
            traceback.print_exc()

        # === NEW: Persist Settings → <Output>/user_prefs.json (the same file used by config.py) ===
        # Build a minimal payload of the settings this dialog controls (what the user just chose).
        settings_payload = {}
        for k, var in self._bool_vars.items():
            settings_payload[k] = bool(var.get())
        for k, var in self._choice_vars.items():
            settings_payload[k] = str(var.get()).upper()
        settings_payload["LOG_INCLUDE_WALK_PATH"] = bool(self.var_log_include_walk_path.get())


        # Read/merge/save via modules.config prefs helpers (writes to <Output>/user_prefs.json).
        try:
            from modules import config as _cfg
            prefs = _cfg._load_prefs() or {}
            prefs["settings"] = settings_payload
            _cfg._save_prefs(prefs)
        except Exception:
            traceback.print_exc()

        messagebox.showinfo("Settings", "Settings applied.\nLog output will reflect new configuration.")


    def _close(self):
        self.grab_release()
        self.destroy()


# --- Settings → user_prefs.json snapshot helper (gui.pyw) ---
def _collect_current_settings_for_prefs() -> dict:
    """
    Snapshot Settings currently applied to modules.config into a JSON-safe dict.
    Only whitelists stable, GUI-exposed options so we don’t dump the entire config.
    Extend this list anytime you surface a new toggle in the Settings window.
    """
    import modules.config as cfg

    WHITELIST = [
        # Booleans
        "SHOW_ALL_SHEETS",
        "LOG_SHOW_ABBREV_HEADER",
        "LOG_INCLUDE_WALK_PATH",   # <-- NEW: persist the Modes checkbox

        # Strings / simple values
        "LOG_DETAIL",            # "DEBUG" / "INFO"
        "OUTPUT_XLSX",           # if you expose this in Settings
        "ID_COL",                # "ID" or custom

        # Lists (kept JSON-friendly)
        "LOG_ABBREV_HEADER_LINES",
        "PATTERNS",
    ]

    snap = {}
    for key in WHITELIST:
        if hasattr(cfg, key):
            val = getattr(cfg, key)
            # Make lists and tuples JSON-safe
            if isinstance(val, tuple):
                val = list(val)
            snap[key] = val
    return snap


# --- NEW: persist GUI Settings in user_prefs.json ----------------------------
def _prefs_json_path() -> str:
    """
    Returns the full path to user_prefs.json.
    Reuses modules.config.USER_PREFS_JSON if present; otherwise, keeps
    your current file next to the GUI (backward-compatible).
    """
    import os, modules.config
    # If your project already defines USER_PREFS_JSON, use it
    p = getattr(modules.config, "USER_PREFS_JSON", None)
    if isinstance(p, str) and p.strip():
        return p

    # Fallback to a file near the executable / script, same as before
    here = os.path.dirname(__file__)
    return os.path.join(here, "user_prefs.json")


def _load_prefs_json() -> dict:
    """Load user_prefs.json (returns {} if missing or invalid)."""
    import json, os
    path = _prefs_json_path()
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _save_prefs_json(prefs: dict) -> None:
    """Write user_prefs.json atomically (best-effort)."""
    import json, os, tempfile
    path = _prefs_json_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # atomic-ish write
    fd, tmp = tempfile.mkstemp(prefix="._prefs_", suffix=".json",
                               dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


class InstructionsDialog(tk.Toplevel):
    """
    Simple modal window to display project instructions.
    Replace the placeholder text with your actual instructions.
    """
    def __init__(self, master):
        super().__init__(master)
        self.title("Instructions")
        self.resizable(True, True)
        self.transient(master)
        self.grab_set()

        pad = {"padx": 10, "pady": 8}
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        frame = ttk.Frame(self)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

        header = ttk.Label(frame, text="Peer Check — Instructions", font=("", 12, "bold"))
        header.grid(row=0, column=0, sticky="w", **pad)

        text = tk.Text(frame, wrap="word")
        text.grid(row=1, column=0, sticky="nsew", **pad)
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        yscroll.grid(row=1, column=1, sticky="ns")
        text.configure(yscrollcommand=yscroll.set)

        # TODO: replace this with your real instructions
        text.insert("1.0", "If first time running. \n\n• You will want to create a new folder.  \n     • Put in /Downloads or /Desktop or /Documents\n     • Name: Peerchecking\n• Create another folder inside 'Peerchecking'\n     • Name: Data\n\n• Export PON from VETRO\n     • Project & Plans > PON you're doing > Elipses (three dots) > Export > GeoJSON\n• Extract files from the ZIP into that data folder we created earlier.\n\n• Browse to that /Data folder you created\n• Output - have it in same /Data folder, or one folder up: 'Peerchecking'\n\n• Run Checks\n\n• View the Excel document and/or logs (if you enabled any from the settings) for your errors. ")
        text.config(state="disabled")

        btn = ttk.Button(frame, text="Close", command=self._close)
        btn.grid(row=2, column=0, sticky="e", **pad)

        self.protocol("WM_DELETE_WINDOW", self._close)

    def _close(self):
        self.grab_release()
        self.destroy()

class HoverTooltip:
    """
    Minimal tooltip helper with wrapping.
      - wraplength_px controls where text wraps (in pixels).
      - Use: HoverTooltip(widget, text="...", wraplength_px=360)
    """
    def __init__(self, widget, text="", wraplength_px=320):
        self.widget = widget
        self.text = text
        self.wraplength_px = int(wraplength_px)
        self._tip = None
        self._after_id = None
        self._dx = 12  # offset so we don't cover the cursor
        self._dy = 14

        self.widget.bind("<Enter>", self._on_enter, add="+")
        self.widget.bind("<Leave>", self._on_leave, add="+")
        self.widget.bind("<Motion>", self._on_motion, add="+")

    def _on_enter(self, e):
        # small delay to avoid flicker on quick passes
        self._after_id = self.widget.after(300, lambda: self._show(e))

    def _on_leave(self, _e):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
        self._hide()

    def _on_motion(self, e):
        # keep tooltip near the pointer
        if self._tip:
            x = self.widget.winfo_rootx() + e.x + self._dx
            y = self.widget.winfo_rooty() + e.y + self._dy
            self._tip.geometry(f"+{x}+{y}")

    def _show(self, e):
        if self._tip or not self.text:
            return
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)  # no window chrome
        self._tip.attributes("-topmost", True)
        x = self.widget.winfo_rootx() + e.x + self._dx
        y = self.widget.winfo_rooty() + e.y + self._dy
        self._tip.geometry(f"+{x}+{y}")

        bubble = ttk.Frame(self._tip, borderwidth=1, relief="solid")
        bubble.pack(fill="both", expand=True)

        # WRAPPED label (key bit): wraplength in pixels
        lbl = ttk.Label(
            bubble,
            text=self.text,
            padding=(8, 6),
            wraplength=self.wraplength_px,
            justify="left",
        )
        lbl.pack()

    def _hide(self):
        if self._tip:
            try:
                self._tip.destroy()
            finally:
                self._tip = None


# -----------------------------
# Main window
# -----------------------------
class PeerCheckGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("700x260")
        self.resizable(False, False)
        self._create_widgets()

    # NOTE: this replaces your previous _create_widgets (full method provided)

    def _create_widgets(self):
        pad = {"padx": 12, "pady": 8}

        # Determine last Output first (from local bootstrap), then set prefs base
        last_out = modules.config.get_bootstrap_last_output_dir()
        modules.config.set_prefs_base_dir(last_out)

        # --- Data Folder ---
        row = 0
        ttk.Label(self, text="Data Folder").grid(row=row, column=0, sticky="w", **pad)
        # Prefill Data from prefs stored in /user_prefs.json; fallback to modules.config.DATA_DIR
        last_data = modules.config.get_last_dir("data", default=str(getattr(modules.config, "DATA_DIR", "")))
        self.data_dir_var = tk.StringVar(value=last_data)
        self.data_dir_entry = ttk.Entry(self, textvariable=self.data_dir_var, width=70)
        self.data_dir_entry.grid(row=row, column=1, sticky="w", **pad)
        ttk.Button(self, text="Browse…", command=self._browse_data_dir).grid(row=row, column=2, **pad)

        # --- Output Folder ---
        row += 1
        ttk.Label(self, text="Output Folder:").grid(row=row, column=0, sticky="w", **pad)
        self.out_dir_var = tk.StringVar(value=last_out)
        self.out_dir_entry = ttk.Entry(self, textvariable=self.out_dir_var, width=70)
        self.out_dir_entry.grid(row=row, column=1, sticky="w", **pad)
        ttk.Button(self, text="Browse…", command=self._browse_out_dir).grid(row=row, column=2, **pad)

        # --- Buttons, Help, Logs, Run ---
        row += 1
        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)
        btn_frame.columnconfigure(1, weight=1)

        style = ttk.Style(self)
        style.configure("Bold.TButton", font=("", 10, "bold"))
        style.configure("Help.TLabel", foreground="#666")

        self.settings_btn = ttk.Button(btn_frame, text="Settings…", command=self._open_settings)
        self.settings_btn.grid(row=0, column=0, sticky="w")

        self.instructions_btn = ttk.Button(btn_frame, text="Instructions", style="Bold.TButton", command=self._open_instructions)
        self.instructions_btn.grid(row=0, column=1)

        right_group = ttk.Frame(btn_frame)
        right_group.grid(row=0, column=2, sticky="e")

        self.logs_help = ttk.Label(right_group, text="(?)", style="Help.TLabel", cursor="question_arrow")
        self.logs_help.grid(row=0, column=0, sticky="e", padx=(0, 6))

        # --- CHANGED: initialize Logs checkbox from user_prefs.json, persist on toggle ---
        # Load saved preference if any; otherwise fall back to modules.config default
        _saved_logs = modules.config.get_pref("include_logs", None)
        _initial_logs = bool(_saved_logs) if _saved_logs is not None else bool(getattr(modules.config, "WRITE_LOG_FILE", True))
        self.include_logs_var = tk.BooleanVar(value=_initial_logs)

        # Keep runtime config in sync immediately
        modules.config.WRITE_LOG_FILE = bool(self.include_logs_var.get())

        self.logs_ck = ttk.Checkbutton(
            right_group,
            text="Logs",
            variable=self.include_logs_var,
            command=self._on_toggle_logs,              # <-- persist when toggled
        )
        self.logs_ck.grid(row=0, column=1, sticky="e")

        HoverTooltip(
            self.logs_help,
            text=(
                "When checked, a separate log file is created. "
                "This log shows the exact path of each error found in the Excel document, "
                "as well as how the script aggregates the data—useful for verifying that it’s working correctly. "
                "You can customize what details appear in the logs by adjusting the settings."
            ),
            wraplength_px=360
        )

        self.run_btn = ttk.Button(btn_frame, text="Run Checks", command=self._run)
        self.run_btn.grid(row=0, column=3, sticky="e")

        # Status line
        row += 1
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status_var).grid(row=row, column=0, columnspan=3, sticky="w", **pad)


    # gui.pyw — early in app startup (e.g., end of PeerCheckGUI.__init__)
    def _apply_settings_from_prefs_on_startup():
        import modules.config as cfg
        from modules import config as _cfg
        prefs = _cfg._load_prefs()
        settings = prefs.get("settings") or {}
        # only set known, safe keys to avoid surprises
        for k, v in settings.items():
            try:
                setattr(cfg, k, v)
            except Exception:
                pass

    # call this once during init:
    _apply_settings_from_prefs_on_startup()


    # --- callbacks ---
    def _browse_out_dir(self):
        start = modules.config.get_bootstrap_last_output_dir()
        path = filedialog.askdirectory(
            title="Select Output Folder",
            mustexist=True,
            initialdir=start
        )
        if path:
            self.out_dir_var.set(path)
            # Point prefs at the new Output folder and remember it for next launch
            modules.config.set_prefs_base_dir(path)
            modules.config.set_bootstrap_last_output_dir(path)
            # Also store this choice inside <Output>/user_prefs.json
            modules.config.update_last_dir("output", path)

    def _on_toggle_logs(self):
        """Persist the Logs checkbox to /user_prefs.json and sync runtime config.
        Also shows a first-time mouse popover and resets the run counter.
        """
        val = bool(self.include_logs_var.get())
        modules.config.WRITE_LOG_FILE = val
        modules.config.set_pref("include_logs", val)

        # First-time "Logs" click: show the popover near mouse, then mark it as seen
        if not modules.config.get_pref("logs_clicked_once", False):
            try:
                # Assumes _ui_show_mouse_popover() is defined as in step (1)
                _ui_show_mouse_popover(
                    parent=self,
                    text="Hover mouse over (?) for more info, and go to Settings for more Log settings.",
                    dismiss_after_ms=5000,
                    include_dont_show=False,
                )
            except Exception:
                # Don’t block toggling if the helper isn’t present
                pass
            # Remember the first click and reset the run counter
            modules.config.set_pref("logs_clicked_once", True)
            modules.config.set_pref("runs_without_logs_click", 0)


    def _browse_data_dir(self):
        start = modules.config.get_last_dir("data")
        path = filedialog.askdirectory(
            title="Select Data Folder",
            mustexist=True,
            initialdir=start
        )
        if not path:
            return

        # 1) Update the Data field and persist
        self.data_dir_var.set(path)
        modules.config.update_last_dir("data", path)

        # 2) Immediately refresh the Output field to reflect first-run auto-adjust
        #    (first run: Output becomes the parent of the chosen Data folder)
        try:
            new_out = modules.config.get_last_dir("output")
        except Exception:
            new_out = modules.config.get_bootstrap_last_output_dir()

        self.out_dir_var.set(new_out)

        # 3) Make sure subsequent prefs writes go to the new Output base
        try:
            modules.config.set_prefs_base_dir(new_out)
        except Exception:
            pass




    def _open_settings(self):
        SettingsDialog(self)

    def _open_instructions(self):
        InstructionsDialog(self)


    def _run(self):
        # Increment + maybe nag (5th/10th) if Logs hasn't been clicked yet.
        # Safe if helpers aren't present yet (wrapped in try/except).
        try:
            _maybe_show_logs_nag(parent=self)
            self.update_idletasks(); self.update()
        except Exception:
            pass

        data_dir = os.path.abspath(self.data_dir_var.get().strip())
        out_dir = os.path.abspath(self.out_dir_var.get().strip())

        if not data_dir or not os.path.isdir(data_dir):
            messagebox.showerror("Error", "Please select a valid Data Folder.")
            return
        if not out_dir or not os.path.isdir(out_dir):
            messagebox.showerror("Error", "Please select a valid Output Folder.")
            return

        # Point prefs to this Output, persist bootstrap pointer, and save both dirs
        modules.config.set_prefs_base_dir(out_dir)
        modules.config.set_bootstrap_last_output_dir(out_dir)
        modules.config.update_last_dir("output", out_dir)
        modules.config.update_last_dir("data", data_dir)

        # Always reference modules.config.DATA_DIR for network data
        modules.config.DATA_DIR = data_dir

        try:
            self.status_var.set("Running…")
            self.update_idletasks()
            modules.config.WRITE_LOG_FILE = bool(self.include_logs_var.get())
            modules.config.set_pref("include_logs", modules.config.WRITE_LOG_FILE)
            run_main(data_dir, out_dir)
            self.status_var.set("Done.")
            messagebox.showinfo("Success", f"Checks complete!\nSaved to {out_dir}")
        except Exception as e:
            self.status_var.set("Error.")
            traceback.print_exc()
            messagebox.showerror("Error", f"Script failed:\n{e}")


if __name__ == "__main__":
    app = PeerCheckGUI()
    app.mainloop()
