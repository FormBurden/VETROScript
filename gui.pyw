import os
import sys
import traceback
import tkinter as tk
from tkinter import ttk
from tkinter import filedialog, messagebox

# Project imports
import modules.config
import main  # keep as in existing file
run_main = main.main  # alias, matches your current usage

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
            "LOG_NID_SHEET_TO_LOG": True,
            "LOG_DETAIL": "INFO",
            "LOG_COLOR_MODE": "OFF",
        }
        for k, v in defaults.items():
            if k in self._bool_vars:
                self._bool_vars[k].set(bool(v))
            elif k in self._choice_vars:
                self._choice_vars[k].set(str(v).upper())

    def _apply(self):
        # Push values back into modules.config
        for k, var in self._bool_vars.items():
            setattr(modules.config, k, bool(var.get()))
        for k, var in self._choice_vars.items():
            setattr(modules.config, k, str(var.get()).upper())

        # Recompute the effective LOG_LEVEL in modules.config and re-setup logging
        try:
            modules.config.LOG_DETAIL = str(getattr(modules.config, "LOG_DETAIL", "INFO")).upper()
            modules.config.LOG_LEVEL = 10 if modules.config.LOG_DETAIL == "DEBUG" else 20  # logging.DEBUG/INFO
        except Exception:
            traceback.print_exc()

        messagebox.showinfo("Settings", "Settings applied. Log output will reflect new configuration.")

    def _close(self):
        self.grab_release()
        self.destroy()

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
        # Prefill Data from prefs stored in <Output>/user_prefs.json; fallback to modules.config.DATA_DIR
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
        self.include_logs_var = tk.BooleanVar(value=bool(getattr(modules.config, "WRITE_LOG_FILE", True)))
        self.logs_ck = ttk.Checkbutton(right_group, text="Logs", variable=self.include_logs_var)
        self.logs_ck.grid(row=0, column=1, sticky="e")

        HoverTooltip(
            self.logs_help,
            text=(
                "When checked, a separate log file is created. This log shows the exact path of each error "
                "found in the Excel document, as well as how the script aggregates the data—useful for verifying "
                "that it’s working correctly. You can customize what details appear in the logs by adjusting the settings."
            ),
            wraplength_px=360
        )

        self.run_btn = ttk.Button(btn_frame, text="Run Checks", command=self._run)
        self.run_btn.grid(row=0, column=3, sticky="e")

        # Status line
        row += 1
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status_var).grid(row=row, column=0, columnspan=3, sticky="w", **pad)


    # def _create_widgets(self):
    #     pad = {"padx": 12, "pady": 8}

    #     # --- Data Folder ---
    #     row = 0
    #     ttk.Label(self, text="Data Folder").grid(row=row, column=0, sticky="w", **pad)
    #     # Prefill from saved "data" dir, fallback to current modules.config.DATA_DIR
    #     last_data = modules.config.get_last_dir("data", default=str(getattr(modules.config, "DATA_DIR", "")))
    #     self.data_dir_var = tk.StringVar(value=last_data)
    #     self.data_dir_entry = ttk.Entry(self, textvariable=self.data_dir_var, width=70)
    #     self.data_dir_entry.grid(row=row, column=1, sticky="w", **pad)
    #     ttk.Button(self, text="Browse…", command=self._browse_data_dir).grid(row=row, column=2, **pad)

    #     # --- Output Folder ---
    #     row += 1
    #     ttk.Label(self, text="Output Folder:").grid(row=row, column=0, sticky="w", **pad)
    #     # Prefill from saved "output" dir, fallback to CWD
    #     last_out = modules.config.get_last_dir("output", default=os.path.abspath(os.getcwd()))
    #     self.out_dir_var = tk.StringVar(value=last_out)
    #     self.out_dir_entry = ttk.Entry(self, textvariable=self.out_dir_var, width=70)
    #     self.out_dir_entry.grid(row=row, column=1, sticky="w", **pad)
    #     ttk.Button(self, text="Browse…", command=self._browse_out_dir).grid(row=row, column=2, **pad)

    #     # --- Bottom row: Settings (left), Instructions (center), (?)+Logs (right), Run (far-right) ---
    #     row += 1
    #     btn_frame = ttk.Frame(self)
    #     btn_frame.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)

    #     btn_frame.columnconfigure(1, weight=1)

    #     style = ttk.Style(self)
    #     style.configure("Bold.TButton", font=("", 10, "bold"))
    #     style.configure("Help.TLabel", foreground="#666")

    #     # Left: Settings
    #     self.settings_btn = ttk.Button(btn_frame, text="Settings…", command=self._open_settings)
    #     self.settings_btn.grid(row=0, column=0, sticky="w")

    #     # Center: Instructions
    #     self.instructions_btn = ttk.Button(btn_frame, text="Instructions", style="Bold.TButton", command=self._open_instructions)
    #     self.instructions_btn.grid(row=0, column=1)

    #     # Right: ( ? ) + Logs
    #     right_group = ttk.Frame(btn_frame)
    #     right_group.grid(row=0, column=2, sticky="e")
    #     self.logs_help = ttk.Label(right_group, text="(?)", style="Help.TLabel", cursor="question_arrow")
    #     self.logs_help.grid(row=0, column=0, sticky="e", padx=(0, 6))
    #     self.include_logs_var = tk.BooleanVar(value=bool(getattr(modules.config, "WRITE_LOG_FILE", True)))
    #     self.logs_ck = ttk.Checkbutton(right_group, text="Logs", variable=self.include_logs_var)
    #     self.logs_ck.grid(row=0, column=1, sticky="e")

    #     HoverTooltip(
    #         self.logs_help,
    #         text=(
    #             "When checked, a separate log file is created. This log shows the exact path of each error "
    #             "found in the Excel document, as well as how the script aggregates the data—useful for verifying "
    #             "that it’s working correctly. You can customize what details appear in the logs by adjusting the settings."
    #         ),
    #         wraplength_px=360
    #     )

    #     # Far-right: Run
    #     self.run_btn = ttk.Button(btn_frame, text="Run Checks", command=self._run)
    #     self.run_btn.grid(row=0, column=3, sticky="e")

    #     # Status line
    #     row += 1
    #     self.status_var = tk.StringVar(value="Ready.")
    #     ttk.Label(self, textvariable=self.status_var).grid(row=row, column=0, columnspan=3, sticky="w", **pad)

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


    def _browse_data_dir(self):
        start = modules.config.get_last_dir(
            "data",
            default=str(getattr(modules.config, "DATA_DIR", ""))
        )
        path = filedialog.askdirectory(
            title="Select Data Folder",
            mustexist=True,
            initialdir=start
        )
        if path:
            self.data_dir_var.set(path)
            modules.config.update_last_dir("data", path)

    def _open_settings(self):
        SettingsDialog(self)

    def _open_instructions(self):
        InstructionsDialog(self)

    def _run(self):
        data_dir = os.path.abspath(self.data_dir_var.get().strip())
        out_dir  = os.path.abspath(self.out_dir_var.get().strip())

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
