#!/usr/bin/env python3
"""SHOBS-SCHED — Shiloh Hill Observatory Scheduler (the app).

Plans each night's targets for the three SHOBS-P modes (Variables, Transits, Discovery), for one night or a rolling
seven, and exports the plan for ASIAIR. Four pages, as the numbered steps in the blue sidebar:

  1 Setup     site, optics and camera, mount, limits, horizon, weather source
  2 Targets   the target list in four groups (Transits, Variables, Campaigns, Suggested)
  3 Schedule  one tab per night: weather strip, block bar with airmass bands, nightly table, didn't fit, free time
  4 Export    ASIAIR coordinate CSV and the printable plan card

The work is done by targets.py, planner.py, weather.py and export.py; they are imported lazily (mod()), so the app
still opens when one of them is missing and says which part cannot run. Lookups, builds and forecasts run in
worker threads (Jobs); only the main thread touches Tk. Every time shown is on the SITE's clock (sky.hhmm).

Run:  python sched_app.py            (the app)
      python sched_app.py --selftest-ui   (opens the app on a temporary home folder with the golden targets, builds,
                                           draws every page, copies the CSV, closes; exit code 0 = passed)"""
from __future__ import annotations

import csv
import importlib
import io
import math
import os
import queue
import sys
import threading
import time
import traceback
import webbrowser
from datetime import date, datetime, timedelta, timezone

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import numpy as np  # noqa: E402

import settings  # noqa: E402
import sky  # noqa: E402
import style  # noqa: E402
from models import (MODE_NAMES, PRIORITIES, STATUS_LABELS, Block, NightPlan, Planet, Schedule,  # noqa: E402
                    Target)
from style import px  # noqa: E402

STEPS = ("Setup", "Targets", "Schedule", "Export")
GROUPS = (("transit", "Transits"), ("variable", "Variables"), ("campaign", "Campaigns"), ("suggested", "Suggested"))
DARKNESS = (("Astronomical (Sun −18°)", -18.0), ("Nautical (Sun −12°)", -12.0))
MODE_CHOICES = (("Variables (V)", "V"), ("Transits (T)", "T"), ("Discovery (D)", "D"))
# what a star that is not (only) a known planet or VSX variable becomes: (label, kind, mode)
KIND_CHOICES = (("Campaign, reduced in Variables (V)", "campaign", "V"),
                ("Variable star (V)", "variable", "V"),
                ("Discovery field (D)", "campaign", "D"),
                ("Transit target, ephemeris typed later (T)", "transit", "T"))
_KIND_LEGACY = {"Variables (V)": ("campaign", "V"), "Discovery (D)": ("campaign", "D"),
                "Transits (T)": ("transit", "T")}
STAR_ITSELF = "The star itself (a campaign or a variable)"
GROUP_CHOICES = (("Transit", "transit"), ("Variable", "variable"), ("Campaign", "campaign"))


def kind_mode_of(label: str):
    for lab, kind, mode in KIND_CHOICES:
        if lab == label:
            return kind, mode
    return _KIND_LEGACY.get(label)
STATUS_CHOICES = tuple(STATUS_LABELS.values())          # Planned / Done / Partial / Clouded out
STATUS_KEY = {v: k for k, v in STATUS_LABELS.items()}


# ---- the worker modules, imported lazily ----------------------------------------------------------------------------

_MODULES: dict = {}


def mod(name: str):
    """targets / planner / weather / export, or None when that file is missing or broken (logged once)."""
    if name in _MODULES:
        return _MODULES[name]
    try:
        m = importlib.import_module(name)
    except Exception as exc:  # missing during development, or a syntax error in a copy
        if name not in _FAILED:
            settings.log(f"{name}.py could not be loaded: {exc!r}")
            _FAILED.add(name)
        return None
    _MODULES[name] = m
    return m


_FAILED: set = set()


def missing(name: str) -> str:
    return f"✖ This copy of SHOBS-SCHED is missing {name}.py (or it did not load), so this part cannot run."


# ---- small text helpers ---------------------------------------------------------------------------------------------

def fmt_num(v, digits: int = 6) -> str:
    if v is None or v == "":
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f != f:
        return ""
    return (f"%.{digits}f" % f).rstrip("0").rstrip(".") if f != int(f) else str(int(f))


def fmt_radec(ra, dec) -> str:
    if ra is None or dec is None:
        return "—"
    return f"{sky.fmt_ra(ra, 0)}  {sky.fmt_dec(dec)}"


def night_label(iso: str, site=None) -> str:
    try:
        d = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return str(iso)
    fmt = (getattr(site, "date_format", "") or "%a %d %b")
    return d.strftime(fmt)


def span_text(a, b, tz) -> str:
    return f"{sky.hhmm(a, tz)}–{sky.hhmm(b, tz)}"


def group_of(t: Target) -> str:
    if t.suggested:
        return "suggested"
    return t.kind if t.kind in ("transit", "variable", "campaign") else "variable"


def rules_summary(t: Target) -> str:
    parts = []
    planets = [str((r.planet or {}).get("name") or "a planet") for r in t.rules_obj() if r.kind == "no_transit"]
    if planets:
        parts.append("no data during " + ", ".join(planets) + (" transits" if len(planets) > 1 else " transit"))
    for r in t.rules_obj():
        if r.kind == "min_alt" and r.value not in (None, ""):
            parts.append(f"alt ≥ {fmt_num(r.value, 1)}°")
        elif r.kind == "min_moon_sep" and r.value not in (None, ""):
            parts.append(f"Moon ≥ {fmt_num(r.value, 1)}°")
        elif r.kind == "window" and r.value:
            parts.append(f"only {r.value}")
    return "; ".join(parts)


def temp_text(c, unit: str) -> str:
    if c is None:
        return "—"
    return f"{c * 9 / 5 + 32:.0f}°" if unit == "F" else f"{c:.0f}°"


def wind_value(kmh, length_unit: str):
    if kmh is None:
        return None
    return kmh / 1.609344 if length_unit == "ft" else kmh


def wind_unit(length_unit: str) -> str:
    return "mph" if length_unit == "ft" else "km/h"


def follow_wrap(label, container, pad: int = 0, least: int = 200) -> None:
    """Wrapped text follows its container's width (a wider window shows longer lines; no fixed pixel widths)."""
    def on(e):
        try:
            label.configure(wraplength=max(least, int(e.width) - pad))
        except tk.TclError:
            pass
    container.bind("<Configure>", on, add="+")


def set_msg(var: tk.StringVar, label, text: str) -> None:
    """A message in the white centre: ✖ red, ⚠ amber on yellow, ✔ / plain blue."""
    var.set(text or "")
    try:
        label.configure(style=style.status_style(style.kind_of(text)) if text else "Card.TLabel")
    except tk.TclError:
        pass


def fit_columns(tree, headings: dict, cap: int = 360, stretch: str | None = None, extra: dict | None = None):
    """Each column as wide as its heading or its widest value, in the fonts the table is drawn in (so it follows
    the display scaling); never more than `cap` points (scaled). `stretch` takes the spare room."""
    try:
        head = tkfont.Font(tree, font=style.font(10, "bold"))
        body = tkfont.Font(tree, font=style.font(10))
        scale = style.ui_scale(tree)
        cols = ["#0"] + list(tree["columns"]) if "tree" in str(tree.cget("show")) else list(tree["columns"])
        widest = {c: head.measure(headings.get(c, "")) + px(tree, 22) for c in cols}

        def walk(parent, depth):
            for iid in tree.get_children(parent):
                vals = list(tree.item(iid, "values") or [])
                for k, c in enumerate(tree["columns"]):
                    if k < len(vals):
                        widest[c] = max(widest[c], body.measure(str(vals[k])) + px(tree, 18))
                if "#0" in widest:
                    w0 = body.measure(str(tree.item(iid, "text"))) + px(tree, 40) + depth * px(tree, 20)
                    widest["#0"] = max(widest["#0"], w0)
                walk(iid, depth + 1)
        walk("", 0)
        for c in cols:
            limit = (extra or {}).get(c, cap)
            w = int(min(widest[c], limit * scale))
            tree.column(c, width=w, minwidth=min(w, px(tree, 40)), stretch=(c == stretch))
    except tk.TclError:
        pass


# ---- background jobs ------------------------------------------------------------------------------------------------

class Jobs:
    """Run work in a thread; its result comes back to the main thread through a queue (Tk is not thread-safe)."""

    def __init__(self, root):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.active = 0
        self._poll()

    def run(self, work, done=None, indicator=None, label: str = "Working", counted: bool = False):
        """work() (or work(progress) when counted; progress(i, n) moves the bar) runs in a thread; then
        done(result, exc) runs here. The indicator shows a spinner (count unknown) or a bar (counted)."""
        self.active += 1
        if indicator is not None:
            indicator.start(label)

        def progress(i, n):
            self.q.put(("progress", indicator, (i, n)))

        def target():
            try:
                res = work(progress) if counted else work()
                self.q.put(("done", (done, indicator, label), (res, None)))
            except Exception as exc:
                settings.log(f"{label} failed: {exc!r}\n{traceback.format_exc()}")
                self.q.put(("done", (done, indicator, label), (None, exc)))

        threading.Thread(target=target, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, a, b = self.q.get_nowait()
                if kind == "progress":
                    if a is not None:
                        a.count(*b)
                    continue
                done, ind, label = a
                res, exc = b
                self.active -= 1
                try:
                    if done is not None:
                        done(res, exc)
                    elif ind is not None:
                        ind.finish(f"✖ {exc}" if exc else "✔ Done")
                except Exception as e2:
                    settings.log(f"after {label}: {e2!r}\n{traceback.format_exc()}")
                    if ind is not None:
                        ind.finish(f"✖ {e2}")
        except queue.Empty:
            pass
        try:
            self.root.after(60, self._poll)
        except tk.TclError:
            pass

    def wait(self, timeout: float = 180.0) -> bool:
        """Pump the event loop until every job has finished (tests and the self-test)."""
        t0 = time.time()
        while (self.active or not self.q.empty()) and time.time() - t0 < timeout:
            self.root.update()
            time.sleep(0.02)
        self.root.update()
        return self.active == 0


class JobIndicator(ttk.Frame):
    """Spinner (or progress bar when the count is known) beside the job's button, then a brief ✔ Done / ✖ reason."""

    def __init__(self, parent):
        super().__init__(parent, style="Card.TFrame")
        self.bar = ttk.Progressbar(self, mode="indeterminate", length=px(parent, 110),
                                   style="Card.Horizontal.TProgressbar")
        self.var = tk.StringVar()
        self.label = ttk.Label(self, textvariable=self.var, style="Card.TLabel")
        self.label.pack(side=tk.LEFT)
        self._text = ""
        self._clear = None

    def start(self, text: str):
        if self._clear:
            self.after_cancel(self._clear)
            self._clear = None
        self._text = text
        self.bar.configure(mode="indeterminate", value=0)
        if not self.bar.winfo_manager():
            self.bar.pack(side=tk.LEFT, padx=(0, px(self, 6)), before=self.label)
        self.bar.start(15)
        self.var.set(text + "…")
        self.label.configure(style="Card.TLabel")

    def count(self, i, n):
        self.bar.stop()
        self.bar.configure(mode="determinate", maximum=max(1, n), value=i)
        self.var.set(f"{self._text}: {i} of {n}")

    def finish(self, msg: str = "✔ Done", linger_ms: int = 6000):
        try:
            self.bar.stop()
            self.bar.pack_forget()
            self.var.set(msg)
            kind = style.kind_of(msg)
            self.label.configure(style=style.status_style(kind))
            if kind == "ok" and linger_ms:
                self._clear = self.after(linger_ms, lambda: self.var.set(""))
        except tk.TclError:
            pass


# ---- a body that scrolls only when it does not fit (as SHOBS-P's ScrollBody) -------------------------------------------

def scrolls_itself(w) -> bool:
    """True for a widget that uses the mouse wheel itself (a drop-down, or anything with its own vertical
    scrollbar); the wheel over anything else (e.g. the nightly table, which shows all its rows) scrolls the page."""
    try:
        if str(w.winfo_class()) == "TCombobox":
            return True
        return bool(str(w.cget("yscrollcommand")))
    except (tk.TclError, AttributeError):
        return False


class ScrollBody:
    def __init__(self, parent, bg: str = style.CARD):
        self.outer = ttk.Frame(parent, style="Card.TFrame")
        self.canvas = tk.Canvas(self.outer, highlightthickness=0, borderwidth=0, background=bg)
        self.vbar = ttk.Scrollbar(self.outer, orient=tk.VERTICAL, command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, style="Card.TFrame")
        self._item = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self._bar)
        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._stretch)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.outer.bind("<Enter>", self._enter)
        self.outer.bind("<Leave>", self._leave)

    def _bar(self, first, last):
        try:
            whole = float(first) <= 0.0 and float(last) >= 1.0
        except (TypeError, ValueError):
            whole = True
        if whole:
            self.vbar.pack_forget()
        elif not self.vbar.winfo_manager():
            self.vbar.pack(side=tk.RIGHT, fill=tk.Y, before=self.canvas)
        self.vbar.set(first, last)

    def _stretch(self, event):
        try:
            self.canvas.itemconfigure(self._item, width=int(event.width))
        except tk.TclError:
            pass

    def _enter(self, _e=None):
        self.canvas.bind_all("<MouseWheel>", self._wheel)
        if sys.platform.startswith("linux"):
            self.canvas.bind_all("<Button-4>", lambda e: self._wheel(e, -1))
            self.canvas.bind_all("<Button-5>", lambda e: self._wheel(e, 1))

    def _leave(self, event):
        if str(getattr(event, "detail", "")) == "NotifyInferior":
            return
        try:
            self.canvas.unbind_all("<MouseWheel>")
            if sys.platform.startswith("linux"):
                self.canvas.unbind_all("<Button-4>")
                self.canvas.unbind_all("<Button-5>")
        except tk.TclError:
            pass

    def _wheel(self, event, units=None):
        try:
            w = event.widget
            if isinstance(w, str):
                w = self.canvas.nametowidget(w)
            if scrolls_itself(w):
                return
            if not self.vbar.winfo_manager():
                return
            if units is None:
                d = int(getattr(event, "delta", 0))
                units = -1 if d > 0 else 1
                if sys.platform == "win32" and abs(d) >= 120:
                    units = -int(d / 120) * 2
            self.canvas.yview_scroll(units, "units")
        except tk.TclError:
            pass


# ---- dialogs --------------------------------------------------------------------------------------------------------

def _dialog(app, title: str):
    win = tk.Toplevel(app)
    win.title(title)
    win.configure(bg=style.CARD)
    win.transient(app)
    body = ttk.Frame(win, style="Card.TFrame", padding=px(app, 16))
    body.pack(fill=tk.BOTH, expand=True)
    return win, body


def _keys(win, ok, cancel=None):
    """Return = OK, Escape = Cancel (also in every entry of the dialog)."""
    win.bind("<Return>", lambda _e: ok())
    win.bind("<KP_Enter>", lambda _e: ok())
    win.bind("<Escape>", lambda _e: (cancel or win.destroy)())


def _show_modal(app, win, focus=None):
    win.update_idletasks()
    try:
        sw, sh = app.winfo_screenwidth(), app.winfo_screenheight()
        w, h = min(win.winfo_reqwidth(), int(sw * 0.9)), min(win.winfo_reqheight(), int(sh * 0.85))
        x = app.winfo_rootx() + max(0, (app.winfo_width() - w) // 2)
        y = app.winfo_rooty() + max(0, (app.winfo_height() - h) // 3)
        x, y = min(max(0, x), max(0, sw - w)), min(max(0, y), max(0, sh - h - 40))
        win.geometry(f"{w}x{h}+{x}+{y}")
    except tk.TclError:
        pass
    try:
        win.grab_set()
    except tk.TclError:
        pass
    if focus is not None:
        win.after(10, lambda: focus.focus_set() if focus.winfo_exists() else None)
    app.wait_window(win)


def ask_choice(app, title: str, message: str, choices, ok_text: str = "OK"):
    """A drop-down question; returns the chosen text or None. Tests answer through app.auto_answers."""
    if app.auto_answers:
        return app.auto_answers.pop(0)
    win, body = _dialog(app, title)
    ttk.Label(body, text=message, style="Card.TLabel", wraplength=px(app, 420), justify=tk.LEFT).pack(
        anchor="w", pady=(0, px(app, 8)))
    var = tk.StringVar(value=choices[0] if choices else "")
    cb = ttk.Combobox(body, textvariable=var, values=list(choices), state="readonly",
                      width=max([len(str(c)) for c in choices] + [16]) + 2)
    cb.pack(anchor="w")
    out = {"v": None}
    row = ttk.Frame(body, style="Card.TFrame")
    row.pack(fill=tk.X, pady=(px(app, 12), 0))

    def ok():
        out["v"] = var.get()
        win.destroy()
    ttk.Button(row, text="Cancel", command=win.destroy).pack(side=tk.RIGHT)
    style.green_button(row, ok_text, ok).pack(side=tk.RIGHT, padx=(0, px(app, 8)))
    _keys(win, ok)
    _show_modal(app, win, cb)
    return out["v"]


def ask_yes_no(app, title: str, message: str, yes_text: str = "Continue") -> bool:
    """A yes/no question (Return = yes, Escape = no). Tests answer through app.auto_answers."""
    if app.auto_answers:
        return bool(app.auto_answers.pop(0))
    if app.testing:
        return True
    win, body = _dialog(app, title)
    ttk.Label(body, text=message, style="Card.TLabel", wraplength=px(app, 460), justify=tk.LEFT).pack(
        anchor="w", pady=(0, px(app, 8)))
    out = {"v": False}
    row = ttk.Frame(body, style="Card.TFrame")
    row.pack(fill=tk.X, pady=(px(app, 12), 0))

    def ok():
        out["v"] = True
        win.destroy()
    ttk.Button(row, text="Cancel", command=win.destroy).pack(side=tk.RIGHT)
    b = style.green_button(row, yes_text, ok)
    b.pack(side=tk.RIGHT, padx=(0, px(app, 8)))
    _keys(win, ok)
    _show_modal(app, win, b)
    return out["v"]


def ask_times(app, title: str, message: str, start: str, end: str):
    """Typed start and end (HH:MM, site clock); returns (start, end) or None."""
    if app.auto_answers:
        return app.auto_answers.pop(0)
    win, body = _dialog(app, title)
    ttk.Label(body, text=message, style="Card.TLabel", wraplength=px(app, 420), justify=tk.LEFT).grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, px(app, 8)))
    sv, ev = tk.StringVar(value=start), tk.StringVar(value=end)
    ttk.Label(body, text="Start (HH:MM)", style="Card.TLabel").grid(row=1, column=0, sticky="w")
    e1 = ttk.Entry(body, textvariable=sv, width=8)
    e1.grid(row=1, column=1, sticky="w", padx=px(app, 6), pady=px(app, 2))
    ttk.Label(body, text="End (HH:MM)", style="Card.TLabel").grid(row=2, column=0, sticky="w")
    ttk.Entry(body, textvariable=ev, width=8).grid(row=2, column=1, sticky="w", padx=px(app, 6), pady=px(app, 2))
    out = {"v": None}
    row = ttk.Frame(body, style="Card.TFrame")
    row.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(px(app, 12), 0))

    def ok():
        out["v"] = (sv.get().strip(), ev.get().strip())
        win.destroy()
    ttk.Button(row, text="Cancel", command=win.destroy).pack(side=tk.RIGHT)
    style.green_button(row, "Set times", ok).pack(side=tk.RIGHT, padx=(0, px(app, 8)))
    _keys(win, ok)
    e1.select_range(0, tk.END)
    _show_modal(app, win, e1)
    return out["v"]


def parse_hhmm(text: str):
    t = str(text).strip().replace(".", ":").replace("h", ":")
    if ":" not in t and t.isdigit() and len(t) in (3, 4):
        t = t[:-2] + ":" + t[-2:]
    h, m = t.split(":")
    h, m = int(h), int(m)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError
    return h, m


def night_clock_jd(night_date: date, hh: int, mm: int, tz: str) -> float:
    """A site clock time on the night that starts on night_date (morning hours belong to the next date)."""
    d = night_date if hh >= 12 else night_date + timedelta(days=1)
    return sky.local_jd(d, hh, mm, tz)


# ---- the app --------------------------------------------------------------------------------------------------------

APP_USER_MODEL_ID = "ShilohHill.SHOBS-SCHED"


def set_app_id() -> None:
    """Windows: our own taskbar identity (so the icon is SHOBS-SCHED's, not Python's). Call before the window."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self, testing: bool = False):
        style.enable_windows_dpi()
        set_app_id()                                   # before the window: the taskbar groups under our own ID
        super().__init__(className="SHOBS-SCHED")
        self.testing = testing
        self.auto_answers: list = []
        self.title(settings.APP_TITLE)
        self.configure(bg=style.FRAME_BG)
        self._set_window_icon()
        style.setup(self)
        # fonts made once (every night tab and drawing shares them; they follow the display scaling)
        self.fonts = {"small": tkfont.Font(self, font=style.font(9)), "norm": tkfont.Font(self, font=style.font(9)),
                      "bold": tkfont.Font(self, font=style.font(9, "bold")),
                      "head": tkfont.Font(self, font=style.font(10, "bold")),
                      "body": tkfont.Font(self, font=style.font(10))}
        self.jobs = Jobs(self)
        self._info_cache: dict = {}
        self._info_lock = threading.Lock()
        self.load_all()
        self._build()
        self._place_window()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        first = 0 if not self.site.complete() else (1 if not self.targets else 2)
        self.show_page(first)

    def destroy(self):
        """Cancel pending timers first (no "invalid command name" noise after the window closes)."""
        try:
            for aid in self.tk.splitlist(self.tk.call("after", "info")):
                try:
                    self.tk.call("after", "cancel", aid)     # (after_cancel would also delete the command a
                except tk.TclError:                          # widget still owns, and its destroy then fails)
                    pass
        except tk.TclError:
            pass
        super().destroy()

    def _set_window_icon(self):
        """SHOBS-SCHED's icon in the title bar and taskbar (as SHOBS-P's _set_window_icon)."""
        ico = os.path.join(APP_DIR, "shobs_sched.ico")
        png = os.path.join(APP_DIR, "shobs_sched.png")
        try:
            if sys.platform == "win32" and os.path.exists(ico):
                self.iconbitmap(default=ico)
            elif os.path.exists(png):
                self._icon_image = tk.PhotoImage(master=self, file=png)
                self.iconphoto(True, self._icon_image)
        except Exception:
            pass

    def report_callback_exception(self, exc, val, tb):
        """An error inside the window: log the traceback and say so on the status line (a desktop shortcut has
        no console to show it)."""
        text = "".join(traceback.format_exception(exc, val, tb))
        settings.log("Error in the window; traceback follows.\n" + text)
        try:
            self.set_status(f"✖ Something went wrong: {val}. Details are in {settings.log_path()}.")
        except Exception:
            pass

    def on_close(self):
        """Window close: unsaved Setup changes are saved first when they are valid (or the user is asked)."""
        if not self.pages[0].leave(closing=True):
            return
        self.destroy()

    # ---- data ----
    def load_all(self):
        s = settings.load_settings()
        self.shobs_p = settings.read_shobs_p()
        s, taken = settings.merge_shobs_p(s)
        self.settings = s
        self.p_taken = list(taken)
        self.p_values = self._shobs_p_values()
        self.targets: list[Target] = settings.load_targets()
        self.schedule: Schedule = settings.load_schedule()
        settings.log(f"opened; {len(self.targets)} targets, {len(self.schedule.nights)} nights"
                     + (f"; SHOBS-P settings read ({', '.join(taken)})" if taken else ""))

    def _shobs_p_values(self) -> dict:
        """SHOBS-P's own value of each field it can share ("site.lat" → 35.303), to tell an override from a value
        typed back to SHOBS-P's."""
        p = self.shobs_p or {}
        out = {}
        for key, (sec, field) in settings.SHOBS_P_FIELDS.items():
            if key in p:
                v = str(p[key] or "").strip() if field in ("name", "tz", "telescope") else settings._num(p[key])
                if v not in (None, ""):
                    out[f"{sec}.{field}"] = v
        mount = str(p.get("mount") or "").lower()
        if mount:
            out["site.mount"] = "altaz" if ("alt" in mount and "az" in mount) else "GEM"
        return out

    @property
    def site(self):
        return settings.site_of(self.settings)

    @property
    def optics(self):
        return settings.optics_of(self.settings)

    @property
    def options(self) -> dict:
        return self.settings.setdefault("options", dict(settings.DEFAULT_OPTIONS))

    def targets_by_id(self) -> dict:
        return {t.id: t for t in self.targets}

    def planner_targets(self) -> list[Target]:
        return [t for t in self.targets if t.enabled]

    def tonight(self) -> date:
        """The night that is running or coming, on the site's clock: before sunrise it is the night in progress
        (it started yesterday evening); after sunrise it is the coming evening's night."""
        site = self.site
        if not (site.tz and sky.zone_ok(site.tz)):
            return (datetime.now(timezone.utc) - timedelta(hours=12)).date()
        now = sky.now_jd()
        d = sky.night_date_of(now, site.tz)            # the noon-to-noon night containing now
        if site.lat is None or site.lon is None:
            return d
        if sky.to_local(now, site.tz).hour < 12:       # morning: has the Sun risen on night d?
            key = ("sunrise", d.isoformat(), site.lat, site.lon, site.tz)
            rise = self._info_cache.get(key, "?")
            if rise == "?":
                try:
                    w = sky.night_window(d, site, sky.SUN_RISE_ALT)
                    rise = w[1] if w else None
                except Exception:
                    rise = None
                self._info_cache[key] = rise
            if rise is None or now >= rise:             # after sunrise (or no night at all): the coming night
                return d + timedelta(days=1)
        return d

    def save_targets(self):
        if not settings.save_targets(self.targets):
            self.set_status("✖ The target list could not be saved (the file is not writable).")

    def save_schedule(self):
        if not settings.save_schedule(self.schedule):
            self.set_status("✖ The schedule could not be saved (the file is not writable).")

    def night_info(self, d: date) -> dict | None:
        """sky.night_info for the current site, cached (thread-safe; the build job fills it ahead)."""
        site = self.site
        if not site.complete():
            return None
        key = (d.isoformat(), site.lat, site.lon, site.tz, site.sun_alt)
        with self._info_lock:
            if key in self._info_cache:
                return self._info_cache[key]
        try:
            info = sky.night_info(d, site)
        except Exception as exc:
            settings.log(f"night_info {d}: {exc!r}")
            info = None
        with self._info_lock:
            self._info_cache[key] = info
        return info

    def fetch_weather(self, start: date, n: int) -> tuple[dict, str]:
        """Forecast per night (ISO date → night dict) from the chosen source, or ({}, reason). Never raises."""
        w = mod("weather")
        if w is None:
            return {}, "the weather part is missing"
        name = self.options.get("weather_source") or "Open-Meteo"
        cls = (getattr(w, "SOURCES", {}) or {}).get(name)
        if cls is None:
            return {}, f"unknown weather source “{name}”"
        try:
            src = cls()
            try:
                import inspect
                takes_options = len(inspect.signature(src.fetch).parameters) >= 4
            except (TypeError, ValueError):
                takes_options = True
            out = (src.fetch(self.site, start, int(n), dict(self.options)) if takes_options
                   else src.fetch(self.site, start, int(n)))
            out = out if isinstance(out, dict) else {}
            notes = {str(v.get("note")).strip() for v in out.values() if isinstance(v, dict)
                     and str(v.get("note") or "").strip()}         # every note, the stale-copy fallback too
            return out, "; ".join(sorted(notes))
        except Exception as exc:
            settings.log(f"weather fetch failed: {exc!r}")
            return {}, str(exc) or exc.__class__.__name__

    # ---- shell ----
    def _build(self):
        self.status_var = tk.StringVar(value="")
        status = ttk.Frame(self, style="Status.TFrame")
        status.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_label = ttk.Label(status, textvariable=self.status_var, style="Status.TLabel")
        self.status_label.pack(anchor="w", padx=px(self, 12), pady=px(self, 4))

        outer = ttk.Frame(self)
        outer.pack(fill=tk.BOTH, expand=True)
        side = ttk.Frame(outer, style="Side.TFrame")
        side.pack(side=tk.LEFT, fill=tk.Y)
        self.side = side
        ttk.Label(side, text="Shiloh Hill\nObservatory\nScheduler", style="SideTitle.TLabel", justify=tk.LEFT).pack(
            anchor="w", padx=px(self, 16), pady=(px(self, 18), px(self, 2)))
        ttk.Label(side, text="(SHOBS-SCHED)", style="SideHint.TLabel").pack(anchor="w", padx=px(self, 16))
        ttk.Label(side, text="Plans each night for\nVariables, Transits\nand Discovery", style="SideHint.TLabel",
                  justify=tk.LEFT).pack(anchor="w", padx=px(self, 16), pady=(px(self, 8), px(self, 12)))
        ttk.Label(side, text="STEPS", style="SideHead.TLabel").pack(anchor="w", padx=px(self, 16))
        self.step_buttons = []
        for i, name in enumerate(STEPS):
            b = ttk.Button(side, text=f"{i + 1}   {name}", style="Step.TButton", command=lambda n=i: self.show_page(n))
            b.pack(fill=tk.X, padx=px(self, 10), pady=px(self, 2))
            self.step_buttons.append(b)
        ttk.Label(side, text=f"Version {settings.APP_VERSION}", style="SideHint.TLabel").pack(
            side=tk.BOTTOM, anchor="w", padx=px(self, 16), pady=px(self, 10))
        legend = ttk.Frame(side, style="Side.TFrame")
        legend.pack(side=tk.BOTTOM, fill=tk.X, padx=px(self, 10), pady=(0, px(self, 6)))
        ttk.Label(legend, text="BLOCK COLORS (SHOBS-P MODE)", style="SideHead.TLabel").pack(
            anchor="w", padx=px(self, 6), pady=(0, px(self, 3)))
        for key in ("V", "T", "D"):
            ttk.Label(legend, text=f"{key}   {MODE_NAMES[key]}", style=f"Mode{key}.TLabel").pack(
                fill=tk.X, pady=1)

        right = ttk.Frame(outer)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        card = ttk.Frame(right, style="Card.TFrame", padding=(px(self, 16), px(self, 12)))
        card.pack(fill=tk.BOTH, expand=True, padx=px(self, 12), pady=px(self, 12))
        self.title_var = tk.StringVar()
        self.subtitle_var = tk.StringVar()
        ttk.Label(card, textvariable=self.title_var, style="Title.TLabel").pack(anchor="w")
        sub = ttk.Label(card, textvariable=self.subtitle_var, style="Hint.TLabel", justify=tk.LEFT)
        sub.pack(anchor="w", fill=tk.X, pady=(0, px(self, 8)))
        follow_wrap(sub, card, px(self, 40))
        holder = ttk.Frame(card, style="Card.TFrame")
        holder.pack(fill=tk.BOTH, expand=True)
        self.pages = [SetupPage(self, holder), TargetsPage(self, holder), SchedulePage(self, holder),
                      ExportPage(self, holder)]
        for p in self.pages:
            p.frame.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.page_index = None

    def _place_window(self):
        try:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            w, h = int(sw * 0.8), int(sh * 0.82)
            self.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 3)}")
            self.minsize(min(w, px(self, 900)), min(h, px(self, 560)))
        except tk.TclError:
            pass

    def show_page(self, i: int):
        if getattr(self, "page_index", None) == 0 and i != 0 and hasattr(self, "pages"):
            if not self.pages[0].leave():
                return
        self.page_index = i
        page = self.pages[i]
        self.title_var.set(page.title)
        self.subtitle_var.set(page.subtitle)
        for k, b in enumerate(self.step_buttons):
            b.configure(style="StepOn.TButton" if k == i else "Step.TButton")
        page.frame.tkraise()
        try:
            page.refresh()
        except Exception as exc:
            settings.log(f"page {page.title}: {exc!r}\n{traceback.format_exc()}")
            self.set_status(f"✖ The {page.title} page hit a problem: {exc}")

    def set_status(self, text: str):
        self.status_var.set(text)
        kind = style.kind_of(text)
        self.status_label.configure(style={"bad": "StatusBad.TLabel", "warn": "StatusWarn.TLabel"}.get(
            kind, "StatusOk.TLabel"))

    # ---- change notices ----
    def settings_changed(self):
        with self._info_lock:
            self._info_cache.clear()
        if self.schedule.nights:
            self.pages[2].setup_stale = True
        self.pages[1].dirty = True
        self.pages[2].dirty = True
        self.pages[3].dirty = True

    def targets_changed(self):
        self.pages[2].dirty = True

    def schedule_changed(self):
        self.save_schedule()
        self.pages[2].dirty = True
        self.pages[3].dirty = True


# ---- 1 Setup --------------------------------------------------------------------------------------------------------

class SetupPage:
    title = "Setup"
    subtitle = ("Your site and equipment, entered once. Every time SHOBS-SCHED shows is on the site's clock "
                "(its time zone below), whatever this computer's clock says.")

    def __init__(self, app: App, parent):
        self.app = app
        self.dirty = True
        self.frame = ttk.Frame(parent, style="Card.TFrame")
        self.vars: dict[str, tk.Variable] = {}
        self.tags: dict[str, ttk.Label] = {}
        self.prov_var = tk.StringVar()
        self.prov = ttk.Label(self.frame, textvariable=self.prov_var, style="Card.TLabel", justify=tk.LEFT)
        self.prov.pack(anchor="w", fill=tk.X, pady=(0, px(app, 6)))
        follow_wrap(self.prov, self.frame, px(app, 20))
        bottom = ttk.Frame(self.frame, style="Card.TFrame")
        bottom.pack(side=tk.BOTTOM, fill=tk.X, pady=(px(app, 8), 0))
        self.save_btn = style.green_button(bottom, "Save", self.save)
        self.save_btn.pack(side=tk.LEFT)
        self.msg_var = tk.StringVar()
        self.msg = ttk.Label(bottom, textvariable=self.msg_var, style="Card.TLabel", justify=tk.LEFT)
        self.msg.pack(side=tk.LEFT, padx=px(app, 10), fill=tk.X, expand=True)
        follow_wrap(self.msg, bottom, px(app, 140))
        body = ScrollBody(self.frame)
        body.outer.pack(fill=tk.BOTH, expand=True)
        self.body = body
        g = body.inner
        self.col_frames = [ttk.Frame(g, style="Card.TFrame"), ttk.Frame(g, style="Card.TFrame")]
        self.col_frames[0].grid(row=0, column=0, sticky="new", padx=(0, px(app, 14)))
        g.columnconfigure(0, weight=1, uniform="setup")
        g.columnconfigure(1, weight=1, uniform="setup")
        self.groups = []
        self.groups.append(self._site_group(g))
        self.groups.append(self._optics_group(g))
        self.groups.append(self._mount_group(g))
        self.groups.append(self._horizon_group(g))
        self.groups.append(self._weather_group(g))
        self._columns = 0
        g.bind("<Configure>", self._layout, add="+")
        self._layout()

    # layout: two columns when there is room, one otherwise
    def _layout(self, _e=None):
        try:
            avail = int(self.body.canvas.winfo_width())
        except tk.TclError:
            return
        need2 = 2 * max(int(w.winfo_reqwidth()) for w in self.groups) + px(self.app, 40)
        cols = 2 if (avail <= 1 or avail >= need2) else 1
        if cols == self._columns:
            return
        self._columns = cols
        for w in self.groups:
            w.pack_forget()
        left = (0, 2, 4) if cols == 2 else (0, 1, 2, 3, 4)
        for k in range(5):
            col = self.col_frames[0] if k in left else self.col_frames[1]
            self.groups[k].pack(in_=col, fill=tk.X, pady=(0, px(self.app, 10)))
        g = self.body.inner
        if cols == 2:
            self.col_frames[1].grid(row=0, column=1, sticky="new")
            g.columnconfigure(1, weight=1, uniform="setup")
        else:
            self.col_frames[1].grid_remove()
            g.columnconfigure(1, weight=0, uniform="")

    def _box(self, parent, title):
        return ttk.LabelFrame(parent, text=title, style="Group.TLabelframe", padding=(px(self.app, 12),
                                                                                       px(self.app, 6)))

    def _entry(self, box, row, label, key, unit="", width=10, tag=True):
        ttk.Label(box, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", pady=px(self.app, 2))
        var = tk.StringVar()
        self.vars[key] = var
        f = ttk.Frame(box, style="Card.TFrame")
        f.grid(row=row, column=1, sticky="w", padx=px(self.app, 6), pady=px(self.app, 2))
        e = ttk.Entry(f, textvariable=var, width=width)
        e.pack(side=tk.LEFT)
        u = ttk.Label(f, text=unit, style="Hint.TLabel")
        u.pack(side=tk.LEFT, padx=(px(self.app, 4), 0))
        if tag:
            t = ttk.Label(f, text="set here", style="Tag.TLabel")
            self.tags[key] = t
        return e, u

    def _radios(self, box, row, label, key, choices):
        ttk.Label(box, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", pady=px(self.app, 2))
        var = tk.StringVar()
        self.vars[key] = var
        f = ttk.Frame(box, style="Card.TFrame")
        f.grid(row=row, column=1, sticky="w", padx=px(self.app, 6))
        for text, value in choices:
            ttk.Radiobutton(f, text=text, value=value, variable=var, style="Card.TRadiobutton").pack(
                side=tk.LEFT, padx=(0, px(self.app, 10)))
        return var

    def _site_group(self, g):
        box = self._box(g, "Site")
        self._entry(box, 0, "Name", "site.name", width=24)
        self._entry(box, 1, "Latitude", "site.lat", "° (north +, south −)")
        self._entry(box, 2, "Longitude", "site.lon", "° (east +, west −)")
        _e, self.elev_unit = self._entry(box, 3, "Elevation", "site.elevation_m", "m")
        ttk.Label(box, text="Time zone", style="Card.TLabel").grid(row=4, column=0, sticky="w", pady=px(self.app, 2))
        self.vars["site.tz"] = tk.StringVar()
        f = ttk.Frame(box, style="Card.TFrame")
        f.grid(row=4, column=1, sticky="w", padx=px(self.app, 6), pady=px(self.app, 2))
        self.tz_box = ttk.Combobox(f, textvariable=self.vars["site.tz"], values=settings.all_zones(), width=26)
        self.tz_box.pack(side=tk.LEFT)
        self.tags["site.tz"] = ttk.Label(f, text="set here", style="Tag.TLabel")
        self.tz_hint = ttk.Label(box, text="", style="Hint.TLabel")
        self.tz_hint.grid(row=5, column=1, sticky="w", padx=px(self.app, 6))
        self._radios(box, 6, "Temperatures", "site.temp_unit", (("°C", "C"), ("°F", "F")))
        self._radios(box, 7, "Lengths", "site.length_unit", (("m", "m"), ("ft", "ft")))
        self.vars["site.temp_unit"].trace_add("write", lambda *_: self._units_changed())
        self.vars["site.length_unit"].trace_add("write", lambda *_: self._units_changed())
        return box

    def _optics_group(self, g):
        box = self._box(g, "Telescope and camera")
        self._entry(box, 0, "Telescope", "optics.telescope", width=24)
        self._entry(box, 1, "Aperture", "optics.aperture_mm", "mm")
        self._entry(box, 2, "Focal length", "optics.focal_mm", "mm")
        self._entry(box, 3, "Pixel size", "optics.pixel_um", "µm")
        ttk.Label(box, text="Binning", style="Card.TLabel").grid(row=4, column=0, sticky="w", pady=px(self.app, 2))
        self.vars["optics.binning"] = tk.StringVar()
        f = ttk.Frame(box, style="Card.TFrame")
        f.grid(row=4, column=1, sticky="w", padx=px(self.app, 6), pady=px(self.app, 2))
        ttk.Combobox(f, textvariable=self.vars["optics.binning"], values=("1", "2", "3", "4"), state="readonly",
                     width=4).pack(side=tk.LEFT)
        self.scale_hint = ttk.Label(f, text="", style="Hint.TLabel")
        self.scale_hint.pack(side=tk.LEFT, padx=(px(self.app, 8), 0))
        for k in ("optics.focal_mm", "optics.pixel_um", "optics.binning"):
            self.vars[k].trace_add("write", lambda *_: self._scale_hint())
        self._entry(box, 5, "Camera", "optics.camera", width=24)
        self._radios(box, 6, "Sensor", "optics.color", (("Color (one-shot color)", "color"), ("Mono", "mono")))
        self._entry(box, 7, "Gain setting", "optics.gain")
        self._entry(box, 8, "e⁻ per ADU", "optics.e_per_adu", "e⁻/ADU")
        self._entry(box, 9, "Full well", "optics.full_well_e", "e⁻")
        self._entry(box, 10, "Linear limit", "optics.linear_limit_adu", "ADU (raw)")
        self.est = {}
        for row, key, label in ((11, "full_well_e", "Full well is from the spec sheet"),
                                (12, "linear_limit_adu", "Linear limit is from the spec sheet")):
            var = tk.BooleanVar()
            self.est[key] = var
            f = ttk.Frame(box, style="Card.TFrame")
            f.grid(row=row, column=0, columnspan=2, sticky="w", pady=px(self.app, 1))
            ttk.Checkbutton(f, text=label, variable=var, style="Card.TCheckbutton").pack(side=tk.LEFT)
            warn = ttk.Label(f, text="⚠ estimate", style="Warn.TLabel")
            var.trace_add("write", lambda *_a, v=var, w=warn: self._est_tag(v, w))
            self._est_tag(var, warn)
        return box

    def _est_tag(self, var, warn):
        if var.get():
            warn.pack(side=tk.LEFT, padx=px(self.app, 8))
        else:
            warn.pack_forget()

    def _mount_group(self, g):
        box = self._box(g, "Mount and limits")
        self._radios(box, 0, "Mount", "site.mount", (("German equatorial (flips)", "GEM"), ("Alt-az", "altaz")))
        self._entry(box, 1, "Tracks past the meridian", "site.past_meridian_min", "min before it must flip")
        self._entry(box, 2, "Minimum altitude", "site.min_alt", "°")
        self._entry(box, 3, "Maximum airmass", "site.max_airmass", "")
        ttk.Label(box, text="Darkness to plan in", style="Card.TLabel").grid(row=4, column=0, sticky="w",
                                                                             pady=px(self.app, 2))
        self.vars["site.sun_alt"] = tk.StringVar()
        ttk.Combobox(box, textvariable=self.vars["site.sun_alt"], values=[d for d, _v in DARKNESS],
                     state="readonly", width=24).grid(row=4, column=1, sticky="w",
                                                      padx=px(self.app, 6), pady=px(self.app, 2))
        return box

    def _horizon_group(self, g):
        box = self._box(g, "Horizon profile")
        hint = ttk.Label(box, text="One “azimuth altitude” pair per line, in degrees (e.g. 120 25). Leave it empty "
                                   "for a flat horizon; the minimum altitude applies on top.",
                         style="Hint.TLabel", justify=tk.LEFT, wraplength=px(self.app, 340))
        hint.grid(row=0, column=0, columnspan=3, sticky="w")
        self.horizon = tk.Text(box, width=18, height=7, font=(style.MONO_FONT, 10), relief="solid", borderwidth=1,
                               highlightthickness=0)
        self.horizon.grid(row=1, column=0, rowspan=3, sticky="nw", pady=px(self.app, 4))
        ttk.Button(box, text="Import CSV…", command=self.import_horizon).grid(row=1, column=1, sticky="nw",
                                                                            padx=px(self.app, 8),
                                                                            pady=px(self.app, 4))
        f = ttk.Frame(box, style="Card.TFrame")
        f.grid(row=2, column=1, columnspan=2, sticky="nw", padx=px(self.app, 8))
        self.vars["site.horizon_north"] = tk.StringVar()
        ttk.Label(f, text="Azimuths measured from", style="Card.TLabel").pack(anchor="w")
        ttk.Radiobutton(f, text="true north", value="true", variable=self.vars["site.horizon_north"],
                        style="Card.TRadiobutton").pack(anchor="w")
        ttk.Radiobutton(f, text="magnetic north", value="magnetic", variable=self.vars["site.horizon_north"],
                        style="Card.TRadiobutton").pack(anchor="w")
        f2 = ttk.Frame(box, style="Card.TFrame")
        f2.grid(row=3, column=1, columnspan=2, sticky="nw", padx=px(self.app, 8), pady=(px(self.app, 4), 0))
        ttk.Label(f2, text="Declination", style="Card.TLabel").pack(side=tk.LEFT)
        self.vars["site.magnetic_declination"] = tk.StringVar()
        ttk.Entry(f2, textvariable=self.vars["site.magnetic_declination"], width=7).pack(side=tk.LEFT,
                                                                                        padx=px(self.app, 4))
        ttk.Label(f2, text="° (east +)", style="Hint.TLabel").pack(side=tk.LEFT)
        return box

    def _weather_group(self, g):
        box = self._box(g, "Weather")
        ttk.Label(box, text="Forecast source", style="Card.TLabel").grid(row=0, column=0, sticky="w",
                                                                         pady=px(self.app, 2))
        self.vars["options.weather_source"] = tk.StringVar()
        self.src_box = ttk.Combobox(box, textvariable=self.vars["options.weather_source"], state="readonly",
                                    width=16)
        self.src_box.grid(row=0, column=1, sticky="w", padx=px(self.app, 6), pady=px(self.app, 2))
        _e, self.spread_unit = self._entry(box, 1, "Dew when temp − dew point under",
                                           "options.dew_spread_c", "°C", width=6, tag=False)
        self.vars["options.dew_sensitive_end"] = tk.BooleanVar()
        ttk.Checkbutton(box, text="End dew-sensitive runs at the predicted dew onset",
                        variable=self.vars["options.dew_sensitive_end"], style="Card.TCheckbutton").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=px(self.app, 2))
        return box

    # ---- fill / read ----
    def refresh(self):
        if self.dirty:
            self.load_form()
            self.dirty = False
        self._layout()

    def load_form(self):
        s = self.app.settings
        site, op, opts = s.get("site") or {}, s.get("optics") or {}, s.get("options") or {}
        self._loading = True
        v = self.vars
        v["site.name"].set(site.get("name") or "")
        v["site.lat"].set(fmt_num(site.get("lat")))
        v["site.lon"].set(fmt_num(site.get("lon")))
        lu = site.get("length_unit") or "m"
        tu = site.get("temp_unit") or "C"
        v["site.length_unit"].set(lu)
        v["site.temp_unit"].set(tu)
        self._shown_len, self._shown_temp = lu, tu
        elev = float(site.get("elevation_m") or 0.0)
        v["site.elevation_m"].set(fmt_num(elev / 0.3048 if lu == "ft" else elev, 1))
        tz = site.get("tz") or ""
        if not tz:
            tz = settings.guess_tz()
            self.tz_hint.configure(text="Guessed from this computer's clock; check it and Save.")
        else:
            self.tz_hint.configure(text="")
        v["site.tz"].set(tz)
        for k in ("aperture_mm", "focal_mm", "pixel_um", "gain", "e_per_adu", "full_well_e", "linear_limit_adu"):
            v[f"optics.{k}"].set(fmt_num(op.get(k)))
        v["optics.binning"].set(str(int(op.get("binning") or 1)))
        v["optics.camera"].set(op.get("camera") or "")
        v["optics.telescope"].set(op.get("telescope") or "")
        v["optics.color"].set("color" if op.get("color_camera", True) else "mono")
        for key, var in self.est.items():
            var.set(key in (op.get("estimates") or []))
        v["site.mount"].set("altaz" if site.get("mount") == "altaz" else "GEM")
        v["site.past_meridian_min"].set(fmt_num(site.get("past_meridian_min", 5.0)))
        v["site.min_alt"].set(fmt_num(site.get("min_alt", 30.0)))
        v["site.max_airmass"].set(fmt_num(site.get("max_airmass", 2.0)))
        sa = float(site.get("sun_alt", -18.0))
        v["site.sun_alt"].set(DARKNESS[1][0] if sa > -15 else DARKNESS[0][0])
        self.horizon.delete("1.0", tk.END)
        self.horizon.insert("1.0", "\n".join(f"{fmt_num(a, 2)} {fmt_num(h, 2)}" for a, h in site.get("horizon") or []))
        v["site.horizon_north"].set(site.get("horizon_north") or "true")
        v["site.magnetic_declination"].set(fmt_num(site.get("magnetic_declination", 0.0)))
        w = mod("weather")
        sources = list(getattr(w, "SOURCES", {}) or {}) if w else []
        self.src_box.configure(values=sources or ["Open-Meteo"])
        v["options.weather_source"].set(opts.get("weather_source") or (sources[0] if sources else "Open-Meteo"))
        spread = float(opts.get("dew_spread_c", 2.5))
        v["options.dew_spread_c"].set(fmt_num(spread * 1.8 if tu == "F" else spread, 1))
        v["options.dew_sensitive_end"].set(bool(opts.get("dew_sensitive_end", True)))
        self._loading = False
        self._units_changed()
        self._scale_hint()
        self._show_provenance()
        self._snapshot = self.form_state()

    def _show_provenance(self):
        app = self.app
        if app.shobs_p is not None and app.p_taken:
            when = app.settings.get("shobs_p_read") or "?"
            set_msg(self.prov_var, self.prov, f"✔ Site, optics and camera read from SHOBS-P (settings of {when}). "
                                              "Change them there, or override here.")
            here = set(app.settings.get("set_here") or [])
            for key, tag in self.tags.items():
                if key in here:
                    tag.pack(side=tk.LEFT, padx=(px(self.app, 6), 0))
                else:
                    tag.pack_forget()
        else:
            self.prov_var.set("Enter your site and equipment once. SHOBS-P, if you add it later, can share these.")
            self.prov.configure(style="Card.TLabel")
            for tag in self.tags.values():
                tag.pack_forget()

    def _units_changed(self):
        if getattr(self, "_loading", False):
            return
        v = self.vars
        lu, tu = v["site.length_unit"].get() or "m", v["site.temp_unit"].get() or "C"
        if lu != getattr(self, "_shown_len", lu):
            try:
                x = float(v["site.elevation_m"].get())
                v["site.elevation_m"].set(fmt_num(x / 0.3048 if lu == "ft" else x * 0.3048, 1))
            except ValueError:
                pass
        if tu != getattr(self, "_shown_temp", tu):
            try:
                x = float(v["options.dew_spread_c"].get())
                v["options.dew_spread_c"].set(fmt_num(x * 1.8 if tu == "F" else x / 1.8, 1))
            except ValueError:
                pass
        self._shown_len, self._shown_temp = lu, tu
        self.elev_unit.configure(text="ft" if lu == "ft" else "m")
        self.spread_unit.configure(text="°F" if tu == "F" else "°C")

    def _scale_hint(self):
        try:
            f, p = float(self.vars["optics.focal_mm"].get()), float(self.vars["optics.pixel_um"].get())
            b = int(self.vars["optics.binning"].get() or 1)
            self.scale_hint.configure(text=f"{206.265 * p * b / f:.2f}″ per pixel")
        except (ValueError, ZeroDivisionError, tk.TclError):
            self.scale_hint.configure(text="")

    def import_horizon(self):
        path = filedialog.askopenfilename(parent=self.app, title="Horizon profile (CSV: azimuth, altitude)",
                                          filetypes=[("CSV or text", "*.csv *.txt"), ("All files", "*.*")])
        if not path:
            return
        pts, err = read_horizon_csv(path)
        if err:
            set_msg(self.msg_var, self.msg, f"✖ {err}")
            return
        self.horizon.delete("1.0", tk.END)
        self.horizon.insert("1.0", "\n".join(f"{fmt_num(a, 2)} {fmt_num(h, 2)}" for a, h in pts))
        set_msg(self.msg_var, self.msg, f"✔ Read {len(pts)} horizon points from {os.path.basename(path)}. "
                                        "Press Save to keep them.")

    def collect(self) -> tuple[dict | None, dict | None, dict | None, list[str]]:
        """The form as new site / optics / options dicts, or the reasons it cannot be saved (plain words)."""
        v = self.vars
        errors: list[str] = []

        def num(key, label, lo=None, hi=None, required=True, integer=False, what=""):
            text = str(v[key].get()).strip().replace(",", ".")
            if not text:
                if required:
                    errors.append(f"{label} is empty{(': ' + what) if what else ''}.")
                return None
            try:
                x = float(text)
            except ValueError:
                errors.append(f"{label} must be a number{(' ' + what) if what else ''} (you typed “{text}”).")
                return None
            if (lo is not None and x < lo) or (hi is not None and x > hi):
                errors.append(f"{label} must be between {fmt_num(lo)} and {fmt_num(hi)}{(' ' + what) if what else ''}.")
                return None
            return int(round(x)) if integer else x

        site = dict(self.app.settings.get("site") or {})
        op = dict(self.app.settings.get("optics") or {})
        opts = dict(self.app.options)
        site["name"] = v["site.name"].get().strip()
        site["lat"] = num("site.lat", "Latitude", -90, 90, what="(degrees, north positive)")
        site["lon"] = num("site.lon", "Longitude", -180, 180, what="(degrees, east positive, west negative)")
        lu = v["site.length_unit"].get() or "m"
        el = num("site.elevation_m", "Elevation", -500 / (0.3048 if lu == "ft" else 1),
                 9000 / (0.3048 if lu == "ft" else 1), required=False)
        site["elevation_m"] = (el * 0.3048 if lu == "ft" else el) if el is not None else 0.0
        tz = v["site.tz"].get().strip()
        if not sky.zone_ok(tz):
            errors.append(f"Time zone “{tz}” is not a zone name SHOBS-SCHED knows; pick one from the list "
                          "(e.g. America/New_York).")
        site["tz"] = tz
        site["temp_unit"], site["length_unit"] = (v["site.temp_unit"].get() or "C"), lu
        for k, label in (("aperture_mm", "Aperture"), ("focal_mm", "Focal length"), ("pixel_um", "Pixel size")):
            op[k] = num(f"optics.{k}", label, 0.001, None, required=False)
        op["binning"] = int(v["optics.binning"].get() or 1)
        op["camera"] = v["optics.camera"].get().strip()
        op["telescope"] = v["optics.telescope"].get().strip()
        op["color_camera"] = v["optics.color"].get() != "mono"
        op["gain"] = num("optics.gain", "Gain setting", 0, None, required=False)
        op["e_per_adu"] = num("optics.e_per_adu", "e⁻ per ADU", 0.0001, None, required=False)
        op["full_well_e"] = num("optics.full_well_e", "Full well", 1, None, required=False)
        ll = num("optics.linear_limit_adu", "Linear limit", 100, 70000, required=False, what="(raw ADU)")
        op["linear_limit_adu"] = ll if ll is not None else 32000.0
        op["estimates"] = [k for k, var in self.est.items() if var.get()]
        site["mount"] = v["site.mount"].get() or "GEM"
        pm = num("site.past_meridian_min", "Tracking past the meridian", 0, 120, required=False, what="minutes")
        site["past_meridian_min"] = pm if pm is not None else 0.0
        ma = num("site.min_alt", "Minimum altitude", 0, 89, what="(degrees)")
        site["min_alt"] = ma if ma is not None else 30.0
        mx = num("site.max_airmass", "Maximum airmass", 1.0, 8.0, required=False)
        site["max_airmass"] = mx if mx is not None else 2.0
        site["sun_alt"] = dict(DARKNESS).get(v["site.sun_alt"].get(), -18.0)
        pts, err = parse_horizon(self.horizon.get("1.0", tk.END))
        if err:
            errors.append(err)
        site["horizon"] = pts
        site["horizon_north"] = v["site.horizon_north"].get() or "true"
        dec = num("site.magnetic_declination", "Magnetic declination", -60, 60, required=False)
        site["magnetic_declination"] = dec or 0.0
        opts["weather_source"] = v["options.weather_source"].get() or "Open-Meteo"
        sp = num("options.dew_spread_c", "The dew spread", 0, 20, required=False)
        tu = site["temp_unit"]
        opts["dew_spread_c"] = (sp / 1.8 if tu == "F" else sp) if sp is not None else 2.5
        opts["dew_sensitive_end"] = bool(v["options.dew_sensitive_end"].get())
        if errors:
            return None, None, None, errors
        return site, op, opts, []

    def save(self):
        site, op, opts, errors = self.collect()
        if errors:
            more = f" ({len(errors) - 1} more after you fix this one)" if len(errors) > 1 else ""
            set_msg(self.msg_var, self.msg, f"✖ Not saved: {errors[0]}{more}")
            return False
        s = self.app.settings
        new = {"site": site, "optics": op}
        for dotted, theirs in self.app.p_values.items():
            sec, _, key = dotted.partition(".")
            if sec not in new:
                continue
            mine = new[sec].get(key)
            if isinstance(theirs, (int, float)) and isinstance(mine, (int, float)):
                same = abs(float(theirs) - float(mine)) < 1e-6 * max(1.0, abs(float(theirs)))
            else:
                same = theirs == mine
            if same:      # typed back to SHOBS-P's value: follow SHOBS-P again
                s["set_here"] = [x for x in (s.get("set_here") or []) if x != dotted]
            else:
                settings.mark_set_here(s, dotted)
        # a value typed here while SHOBS-P has none (or isn't installed) is the user's own: a SHOBS-P installed
        # later must not overwrite it (only empty fields get filled from SHOBS-P)
        shared = {f"{sec}.{fld}" for sec, fld in settings.SHOBS_P_FIELDS.values()} | {"site.mount"}
        for dotted in shared:
            if dotted in self.app.p_values:
                continue
            sec, _, key = dotted.partition(".")
            val = new.get(sec, {}).get(key)
            if val not in (None, "") and not (isinstance(val, str) and not val.strip()):
                settings.mark_set_here(s, dotted)
        s["site"], s["optics"] = site, op
        s["options"] = opts
        if not settings.save_settings(s):
            set_msg(self.msg_var, self.msg, "✖ Not saved: the settings file is not writable.")
            return False
        settings.log("settings saved")
        self.tz_hint.configure(text="")
        self._show_provenance()
        self.app.settings_changed()
        self._snapshot = self.form_state()
        set_msg(self.msg_var, self.msg, "✔ Saved.")
        self.app.set_status("✔ Setup saved.")
        return True

    def form_state(self) -> tuple:
        vals = [(k, str(v.get())) for k, v in sorted(self.vars.items())]
        vals += [(k, bool(v.get())) for k, v in sorted(self.est.items())]
        return tuple(vals) + (self.horizon.get("1.0", tk.END).strip(),)

    def dirty_form(self) -> bool:
        snap = getattr(self, "_snapshot", None)
        return snap is not None and self.form_state() != snap

    def leave(self, closing: bool = False) -> bool:
        """Leaving the page (or closing the window) with unsaved changes: save them when they are valid and say
        so; otherwise ask whether to drop them. Returns False to stay."""
        if not self.dirty_form():
            return True
        _s, _o, _p, errors = self.collect()
        if not errors:
            ok = self.save()
            if ok:
                self.app.set_status("✔ Setup saved: you left the page with changes, so they were kept.")
            return ok or closing
        where = "Close SHOBS-SCHED" if closing else "Leave the Setup page"
        if ask_yes_no(self.app, "Setup not saved", f"Your Setup changes cannot be saved: {errors[0]} "
                                                   f"{where} and drop them?", "Drop the changes"):
            self.load_form()
            return True
        set_msg(self.msg_var, self.msg, f"✖ Not saved: {errors[0]}")
        return False


def parse_horizon(text: str) -> tuple[list, str]:
    pts = []
    for n, line in enumerate(str(text).splitlines(), 1):
        line = line.split("#")[0].strip()
        if not line:
            continue
        parts = [p for p in line.replace(",", " ").replace(";", " ").split() if p]
        try:
            az, alt = float(parts[0]), float(parts[1])
        except (ValueError, IndexError):
            return [], f"Horizon line {n} (“{line}”) is not two numbers: azimuth and altitude."
        if not (0 <= az <= 360) or not (-5 <= alt <= 90):
            return [], f"Horizon line {n}: azimuth must be 0–360° and altitude −5–90°."
        pts.append((az % 360.0, alt))
    pts.sort()
    return pts, ""


def read_horizon_csv(path: str) -> tuple[list, str]:
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        return [], f"Could not read {os.path.basename(path)} ({exc.strerror or exc})."
    pts = []
    for row in csv.reader(io.StringIO(text.replace(";", ",").replace("\t", ","))):
        nums = []
        for cell in row:
            try:
                nums.append(float(cell.strip()))
            except ValueError:
                continue
        if len(nums) >= 2 and 0 <= nums[0] <= 360 and -5 <= nums[1] <= 90:
            pts.append((nums[0] % 360.0, nums[1]))
    if not pts:
        return [], f"{os.path.basename(path)} has no azimuth, altitude pairs."
    return sorted(pts), ""


# ---- 2 Targets ------------------------------------------------------------------------------------------------------

class TargetsPage:
    title = "Targets"
    subtitle = ("What you want to observe. Click ☐ to plan a target (☑ = planned), click its Priority to change it, "
                "select a row for its details, and double-click it to edit. The square beside a name is its SHOBS-P "
                "mode color.")
    COLS = (("plan", "Plan"), ("vmag", "V"), ("radec", "RA / Dec (J2000)"), ("priority", "Priority"),
            ("next", "Next useful event"), ("source", "Source"))
    FIT = {"#0": 360, "next": 420, "source": 320}

    def __init__(self, app: App, parent):
        self.app = app
        self.dirty = True
        self.next_text: dict[str, str] = {}
        self._next_key = None
        self._tc_cache: dict = {}
        self._badges = {m: style.badge_image(app, m) for m in ("V", "T", "D")}
        self.frame = ttk.Frame(parent, style="Card.TFrame")
        top = ttk.Frame(self.frame, style="Card.TFrame")
        top.pack(fill=tk.X, pady=(0, px(app, 6)))
        ttk.Label(top, text="Add a target", style="Bold.TLabel").pack(side=tk.LEFT)
        self.add_var = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.add_var, width=28)
        e.pack(side=tk.LEFT, padx=px(app, 8))
        e.bind("<Return>", lambda _e: self.add())
        style.green_button(top, "Add", self.add).pack(side=tk.LEFT)
        self.add_ind = JobIndicator(top)
        self.add_ind.pack(side=tk.LEFT, padx=px(app, 10))
        ttk.Label(top, text="a planet (WASP-12 b), a variable (DY Peg) or any star", style="Hint.TLabel").pack(
            side=tk.LEFT)

        bottom = ttk.Frame(self.frame, style="Card.TFrame")
        bottom.pack(side=tk.BOTTOM, fill=tk.X, pady=(px(app, 6), 0))
        ttk.Button(bottom, text="Edit…", command=self.edit_selected).pack(side=tk.LEFT)
        ttk.Button(bottom, text="Remove", command=self.remove_selected).pack(side=tk.LEFT, padx=px(app, 6))
        ttk.Button(bottom, text="Keep (move to Transits)", command=self.keep_selected).pack(side=tk.LEFT)
        self.find_ind = JobIndicator(bottom)
        self.find_ind.pack(side=tk.RIGHT, padx=(px(app, 10), 0))
        style.green_button(bottom, "Find suggested transits", self.find_suggested).pack(side=tk.RIGHT)
        self.msg_var = tk.StringVar()
        self.msg = ttk.Label(self.frame, textvariable=self.msg_var, style="Card.TLabel", justify=tk.LEFT)
        self.msg.pack(side=tk.BOTTOM, fill=tk.X, anchor="w")
        follow_wrap(self.msg, self.frame, px(app, 20))
        self.detail_var = tk.StringVar()
        self.detail = ttk.Label(self.frame, textvariable=self.detail_var, style="Card.TLabel", justify=tk.LEFT)
        self.detail.pack(side=tk.BOTTOM, fill=tk.X, anchor="w", pady=(px(app, 6), 0))
        follow_wrap(self.detail, self.frame, px(app, 20))

        tf = ttk.Frame(self.frame, style="Card.TFrame")
        tf.pack(fill=tk.BOTH, expand=True)
        cols = [c for c, _h in self.COLS]
        self.tree = ttk.Treeview(tf, columns=cols, show="tree headings", selectmode="extended")
        self.headings = {"#0": "Name", **dict(self.COLS)}
        self.tree.heading("#0", text="Name", anchor="w")
        for c, h in self.COLS:
            self.tree.heading(c, text=h, anchor="w" if c not in ("plan", "vmag") else "center")
            self.tree.column(c, anchor="center" if c in ("plan", "vmag") else "w")
        vb = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=self.tree.yview)
        hb = ttk.Scrollbar(tf, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vb.set, xscrollcommand=hb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vb.grid(row=0, column=1, sticky="ns")
        hb.grid(row=1, column=0, sticky="ew")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        self.tree.tag_configure("group", font=style.font(10, "bold"), background="#eef3f7", foreground=style.SIDE)
        self.tree.tag_configure("off", foreground=style.HINT)
        self.tree.tag_configure("suggested", foreground="#3c4a52")
        self.tree.bind("<Button-1>", self._click, add="+")
        self.tree.bind("<Double-1>", self._double)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.show_detail())
        self._editor = None

    # ---- table ----
    def refresh(self):
        self.fill()
        self._start_next_events()

    def _open_state(self):
        st = {}
        for key, _n in GROUPS:
            iid = f"grp:{key}"
            if self.tree.exists(iid):
                st[key] = bool(self.tree.item(iid, "open"))
        return st

    def fill(self):
        tree, app = self.tree, self.app
        opened = self._open_state()
        sel = [i for i in tree.selection()]
        tree.delete(*tree.get_children())
        when = sky.now_jd()
        today = app.tonight()
        by_group: dict[str, list[Target]] = {k: [] for k, _n in GROUPS}
        for t in app.targets:
            by_group[group_of(t)].append(t)
        for key, name in GROUPS:
            rows = by_group[key]
            planned = sum(1 for t in rows if t.enabled)
            label = f"{name}  ({len(rows)}{f', {planned} planned' if rows else ''})"
            hint = "" if rows else {"suggested": "none yet: press Find suggested transits",
                                    "campaign": "none: a campaign is a star or field you visit on many nights",
                                    }.get(key, "none")
            vals = ["", "", "", "", hint, ""]       # the hint sits in the wide "Next useful event" column
            tree.insert("", tk.END, iid=f"grp:{key}", text=label, values=vals, open=opened.get(key, True),
                        tags=("group",))
            for t in sorted(rows, key=lambda x: x.name.lower()):      # rows stay put when ticked
                tags = []
                if not t.enabled:
                    tags.append("off")
                tree.insert(f"grp:{key}", tk.END, iid=t.id, text=" " + t.name, image=self._badges.get(t.mode, ""),
                            values=self.row_values(t, when, today), tags=tuple(tags))
        keep = [i for i in sel if tree.exists(i)]
        if keep:
            tree.selection_set(keep)
        fit_columns(tree, self.headings, cap=300, stretch="next", extra=self.FIT)
        self.show_detail()

    def row_values(self, t: Target, when: float, today: date) -> tuple:
        src = f"✔ {t.source}" if t.source else "typed"
        pl = t.planet_obj()
        if pl and t.kind == "transit":
            tc = self.tc_text(t, pl, when)
            if tc.startswith("⚠"):
                src += f"  ·  ⚠ Tc {tc[2:]}"
        nxt = self.next_text.get(t.id, "…" if self.app.site.complete() else "set the site first")
        return ("☑" if t.enabled else "☐", f"{t.vmag:.1f}" if t.vmag is not None else "—",
                fmt_radec(t.ra, t.dec), t.priority, nxt, src)

    def detail_text(self, t: Target) -> str:
        """The selected target's extras: transit depth, duration, ephemeris and Tc ±; campaign rules, goal and
        progress; notes."""
        bits = [f"{t.name} ({MODE_NAMES.get(t.mode, t.mode)})"]
        pl = t.planet_obj()
        if pl and t.kind == "transit":
            if pl.depth:
                bits.append(f"depth {pl.depth * 100:.2f} %")
            if pl.t14:
                bits.append(f"duration {pl.t14 * 24:.2f} h")
            if pl.period:
                bits.append(f"period {pl.period:.6f} d")
            bits.append("Tc " + self.tc_text(t, pl, sky.now_jd()))
            if pl.reference or pl.source:
                bits.append(f"ephemeris: {pl.reference or pl.source}")
        if t.kind == "transit" and not pl:
            bits.append("⚠ no ephemeris: Edit… to type one")
        el = t.elements_obj()
        if el and el.vtype:
            bits.append(f"{el.vtype}" + (f", P = {el.period:.5f} d" if el.period else ""))
        rs = rules_summary(t)
        if rs:
            bits.append(rs)
        if t.goal_per_week:
            bits.append(self.progress_text(t, self.app.tonight()))
        bits.append(f"exposure {fmt_num(t.exposure_s)} s" if t.exposure_s else "exposure: SCHED estimates it")
        if t.kind != "transit":
            bits.append(f"block {fmt_num(t.block_min)} min (at least {fmt_num(t.min_block_min)})")
        if t.notes:
            bits.append(f"notes: {t.notes}")
        return "   ·   ".join(bits)

    def show_detail(self):
        ts = self.selected_targets()
        if len(ts) == 1:
            text = self.detail_text(ts[0])
            self.detail_var.set(text)
            self.detail.configure(style="Card.TLabel")
        else:
            self.detail_var.set("Select a target for its details (depth, ephemeris, rules, goal, progress).")
            self.detail.configure(style="Hint.TLabel")

    def tc_text(self, t: Target, pl: Planet, when: float) -> str:
        key = (pl.name, pl.period, pl.t0, pl.period_err, pl.t0_err, int(when))
        if key not in self._tc_cache:
            self._tc_cache[key] = self._tc_sigma(pl, when)
        sig = self._tc_cache[key]
        if sig is None:
            return "—"
        warn = sig > float(self.app.options.get("tc_sigma_warn_min", 10.0))
        return f"{'⚠ ' if warn else ''}± {sig:.1f} min"

    def _tc_sigma(self, pl: Planet, when: float):
        sig = None
        tg = mod("targets")
        if tg is not None and hasattr(tg, "tc_sigma_minutes"):
            try:
                sig = tg.tc_sigma_minutes(pl, when)
            except Exception:
                sig = None
        if sig is None and pl.period and pl.t0 and (pl.t0_err or pl.period_err):
            n = abs(round((when - pl.t0) / pl.period))
            sig = math.hypot(pl.t0_err or 0.0, n * (pl.period_err or 0.0)) * 1440.0
        return sig

    def progress_text(self, t: Target, today: date) -> str:
        tg = mod("targets")
        done = None
        if tg is not None and hasattr(tg, "campaign_progress"):
            try:
                done, goal = tg.campaign_progress(t, self.app.schedule, today)
            except Exception:
                done = None
        return (f"{done} of {t.goal_per_week} this week" if done is not None
                else f"goal {t.goal_per_week} nights a week")

    def _start_next_events(self):
        app = self.app
        site = app.site
        tg = mod("targets")
        if not site.complete() or tg is None or not hasattr(tg, "next_event_text"):
            if tg is None and app.targets:
                for t in app.targets:
                    if self.tree.exists(t.id):
                        self.tree.set(t.id, "next", "—")
            return
        when = sky.now_jd()
        key = (tuple((t.id, t.ra, t.dec, repr(t.planet), repr(t.elements), repr(t.rules)) for t in app.targets),
               (site.lat, site.lon, site.tz, site.min_alt, site.max_airmass, site.sun_alt, repr(site.horizon)),
               app.tonight().isoformat())
        if key == self._next_key:
            return
        self._next_key = key
        targets = [Target.from_dict(t.to_dict()) for t in app.targets]

        def work():
            out = {}
            for t in targets:
                try:
                    out[t.id] = tg.next_event_text(t, site, when) or "—"
                except Exception as exc:
                    settings.log(f"next_event_text {t.name}: {exc!r}")
                    out[t.id] = "—"
            return out

        def done(res, exc):
            if exc is not None or res is None:
                return
            self.next_text.update(res)
            for tid, text in res.items():
                if self.tree.exists(tid):
                    self.tree.set(tid, "next", text)
            fit_columns(self.tree, self.headings, cap=300, stretch="next", extra=self.FIT)
        app.jobs.run(work, done, None, "Next useful events")

    # ---- clicks ----
    def _click(self, event):
        tree = self.tree
        if tree.identify_region(event.x, event.y) != "cell":
            return None
        iid = tree.identify_row(event.y)
        col = tree.identify_column(event.x)
        t = self.app.targets_by_id().get(iid)
        if t is None:
            return None
        name = tree.column(col, "id") if col != "#0" else "#0"
        if name == "plan":
            self.toggle(iid)
            return "break"
        if name == "priority":
            self._edit_priority(iid, col)
            return "break"
        return None

    def _double(self, event):
        iid = self.tree.identify_row(event.y)
        if iid in self.app.targets_by_id() and self.tree.identify_column(event.x) not in (
                self._colref("plan"), self._colref("priority")):
            self.edit(iid)

    def _colref(self, name):
        return f"#{[c for c, _h in self.COLS].index(name) + 1}"

    def toggle(self, tid: str):
        t = self.app.targets_by_id().get(tid)
        if t is None:
            return
        t.enabled = not t.enabled
        self.app.save_targets()
        self.app.targets_changed()
        self.fill()
        self.tree.selection_set(tid)
        set_msg(self.msg_var, self.msg, f"✔ {t.name} will {'be planned' if t.enabled else 'not be planned'}. "
                                        "Build the schedule to apply.")

    def _edit_priority(self, iid, col):
        if self._editor is not None:
            self._editor.destroy()
        bbox = self.tree.bbox(iid, col)
        if not bbox:
            return
        x, y, w, h = bbox
        cb = ttk.Combobox(self.tree, values=PRIORITIES, state="readonly")
        cb.set(self.app.targets_by_id()[iid].priority)
        cb.place(x=x, y=y, width=max(w, px(self.app, 90)), height=h)
        self._editor = cb

        def chosen(_e=None):
            self.set_priority(iid, cb.get())
            close()

        def close(_e=None):
            if self._editor is cb:
                self._editor = None
            cb.destroy()
        cb.bind("<<ComboboxSelected>>", chosen)
        cb.bind("<Escape>", close)
        cb.bind("<FocusOut>", lambda e: cb.after(150, lambda: close() if cb.winfo_exists() and
                                                 str(cb.focus_get()).find("popdown") < 0 else None))
        cb.focus_set()
        cb.after(30, lambda: cb.event_generate("<Button-1>") if cb.winfo_exists() else None)

    def set_priority(self, tid: str, value: str):
        t = self.app.targets_by_id().get(tid)
        if t is None or value not in PRIORITIES:
            return
        t.priority = value
        self.app.save_targets()
        self.app.targets_changed()
        self.fill()
        set_msg(self.msg_var, self.msg, f"✔ {t.name}: priority {value}.")

    def selected_targets(self) -> list[Target]:
        by = self.app.targets_by_id()
        return [by[i] for i in self.tree.selection() if i in by]

    # ---- add ----
    def add(self, name: str | None = None):
        name = (name if name is not None else self.add_var.get()).strip()
        if not name:
            self.add_ind.finish("✖ Type a name first: a planet (WASP-12 b), a variable (DY Peg) or any star.")
            return
        tg = mod("targets")
        if tg is None:
            self.add_ind.finish(missing("targets"))
            return
        self.app.jobs.run(lambda: tg.add_target(name), lambda r, e: self._added(name, r, e), self.add_ind,
                          f"Looking up {name}")

    def _added(self, name, res, exc):
        tg = mod("targets")
        if exc is None and isinstance(res, Target):
            self.accept(res)
            return
        if exc is None:
            self.add_ind.finish(f"✖ The lookup for {name} gave no target.")
            return
        needs = getattr(tg, "NeedsMode", None)
        if needs is not None and isinstance(exc, needs):
            label = getattr(exc, "main_id", "") or name
            self.add_star(name, {"ra": getattr(exc, "ra", None), "dec": getattr(exc, "dec", None),
                                 "vmag": getattr(exc, "vmag", None), "main_id": label},
                          f"{label} was found, but it is not a known transiting planet or VSX variable. "
                          "What is it for you?", [c[0] for c in KIND_CHOICES])
            return
        choices = getattr(exc, "choices", None) or getattr(exc, "planets", None) or getattr(exc, "names", None)
        if choices:
            names = [c if isinstance(c, str) else (c.get("name") if isinstance(c, dict) else getattr(c, "name", str(c)))
                     for c in choices]
            system = getattr(exc, "system", "") or name
            pick = ask_choice(self.app, "Which planet, or the star?",
                              f"{system} has more than one planet. Add one of them as a transit target, or the star "
                              "itself (for example a campaign that keeps clear of the planets' transits)?",
                              names + [STAR_ITSELF], "Add")
            if pick == STAR_ITSELF:
                self.add_star_lookup(system)
            elif pick:
                self.add(pick)
            else:
                self.add_ind.finish("⚠ Not added.")
            return
        self.add_ind.finish(f"✖ {exc}" if str(exc) else f"✖ Could not add {name} ({exc.__class__.__name__}).")

    def add_star_lookup(self, system: str):
        """The host star of a planet system as its own target: its position from SIMBAD (cached), then ask what it
        is (a campaign, a variable or a Discovery field)."""
        tg = mod("targets")
        if tg is None or not hasattr(tg, "resolve_simbad"):
            self.add_ind.finish(missing("targets"))
            return

        def done(res, exc):
            if exc is not None or not isinstance(res, dict) or res.get("ra") is None:
                self.add_ind.finish(f"✖ Could not find {system}'s position: {exc or 'SIMBAD gave none'}.")
                return
            self.add_star(system, res, f"What is {system} for you?", [c[0] for c in KIND_CHOICES[:3]])
        self.app.jobs.run(lambda: tg.resolve_simbad(system), done, self.add_ind, f"Looking up the star {system}")

    def add_star(self, name: str, pos: dict, question: str, choices: list):
        pick = ask_choice(self.app, "What kind of target?", question, choices, "Add")
        km = kind_mode_of(pick) if pick else None
        if not km:
            self.add_ind.finish("⚠ Not added.")
            return
        kind, mode = km
        t = Target(name=name, kind=kind, mode=mode, ra=pos.get("ra"), dec=pos.get("dec"), vmag=pos.get("vmag"),
                   source="SIMBAD")
        if kind == "variable":
            t.elements = {"vtype": "", "any_time": True}
        self.accept(t)
        if kind == "campaign":
            set_msg(self.msg_var, self.msg, f"✔ {name} is a campaign: Edit… sets its goal (nights a week), block "
                                            "length and rules, e.g. no data while its planets transit.")

    def accept(self, t: Target):
        if any(x.name.strip().lower() == t.name.strip().lower() and not x.suggested for x in self.app.targets):
            self.add_ind.finish(f"⚠ {t.name} is already in the list.")
            return
        # a suggested copy becomes the user's own
        self.app.targets = [x for x in self.app.targets
                            if not (x.suggested and x.name.strip().lower() == t.name.strip().lower())]
        if not t.added:
            t.added = self.app.tonight().isoformat()
        t.suggested = False
        self.app.targets.append(t)
        self.app.save_targets()
        self.app.targets_changed()
        settings.log(f"added target {t.name} ({t.kind}, {t.mode}, {t.source})")
        self.add_var.set("")
        self.fill()
        if self.tree.exists(t.id):
            self.tree.selection_set(t.id)
            self.tree.see(t.id)
        self._start_next_events()
        note = str(getattr(t, "lookup_note", "") or "").strip()
        done = f"Added {t.name}" + (f" ({t.source})" if t.source else "") + "."
        self.add_ind.finish(f"⚠ {done} {note[:1].upper() + note[1:]}." .replace("..", ".") if note else "✔ " + done,
                            linger_ms=0 if note else 6000)

    # ---- remove / keep / edit ----
    def remove_selected(self):
        ts = self.selected_targets()
        if not ts:
            set_msg(self.msg_var, self.msg, "⚠ Select one or more targets first.")
            return
        names = ", ".join(t.name for t in ts)
        if not self.app.testing and not messagebox.askyesno(
                "Remove targets", f"Remove {names} from the list? Blocks already planned stay until you rebuild.",
                parent=self.app):
            return
        self.remove_ids({t.id for t in ts})
        set_msg(self.msg_var, self.msg, f"✔ Removed {names}.")

    def remove_ids(self, ids: set):
        self.app.targets = [t for t in self.app.targets if t.id not in ids]
        self.app.save_targets()
        self.app.targets_changed()
        self.fill()

    def keep_selected(self):
        ts = [t for t in self.selected_targets() if t.suggested]
        if not ts:
            set_msg(self.msg_var, self.msg, "⚠ Select a Suggested transit to keep.")
            return
        for t in ts:
            t.suggested = False
        self.app.save_targets()
        self.app.targets_changed()
        self.fill()
        set_msg(self.msg_var, self.msg, "✔ Moved to Transits: " + ", ".join(t.name for t in ts) + ".")

    def edit_selected(self):
        ts = self.selected_targets()
        if len(ts) != 1:
            set_msg(self.msg_var, self.msg, "⚠ Select one target to edit.")
            return
        self.edit(ts[0].id)

    def edit(self, tid: str):
        t = self.app.targets_by_id().get(tid)
        if t is None:
            return
        EditTargetDialog(self.app, t, on_save=self._edited)

    def _edited(self, t: Target):
        self.app.targets = [t if x.id == t.id else x for x in self.app.targets]
        self.app.save_targets()
        self.app.targets_changed()
        self.fill()
        self._start_next_events()
        set_msg(self.msg_var, self.msg, f"✔ Saved {t.name}.")

    # ---- suggested transits ----
    def suggestion_nights(self) -> list[date]:
        nights = [date.fromisoformat(n["date"]) for n in self.app.schedule.nights if n.get("date")]
        tonight = self.app.tonight()
        nights = [d for d in nights if d >= tonight]
        return nights or [tonight + timedelta(days=k) for k in range(7)]

    def find_suggested(self):
        app = self.app
        tg = mod("targets")
        if tg is None:
            self.find_ind.finish(missing("targets"))
            return
        if not app.site.complete():
            self.find_ind.finish("✖ Enter your site on the Setup page first (latitude, longitude, time zone).")
            return
        site, optics, opts = app.site, app.optics, dict(app.options)
        nights = self.suggestion_nights()
        exclude = {t.name for t in app.targets if not t.suggested or t.enabled}
        def work(progress):
            note, kw = "", {}
            if hasattr(tg, "load_transit_catalog"):      # load it here to hear its note (saved copy, age ...)
                catalog, note = tg.load_transit_catalog()
                kw["catalog"] = catalog
            try:
                found = tg.suggest_transits(site, optics, opts, nights, exclude_names=exclude, progress=progress,
                                            **kw)
            except TypeError:
                found = tg.suggest_transits(site, optics, opts, nights, exclude_names=exclude)
            return found, str(note or "")
        app.jobs.run(work, self._found, self.find_ind, "Finding suggested transits", counted=True)

    def _found(self, res, exc):
        if exc is not None:
            self.find_ind.finish(f"✖ No suggestions: {exc}")
            return
        note = ""
        if isinstance(res, tuple):
            res, note = res
        new = [t for t in (res or []) if isinstance(t, Target)]
        keep = [t for t in self.app.targets if not t.suggested or t.enabled]
        names = {t.name.strip().lower() for t in keep}
        added = []
        for t in new:
            if t.name.strip().lower() in names:
                continue
            t.suggested, t.enabled = True, False
            if t.kind != "transit":
                t.kind, t.mode = "transit", "T"
            added.append(t)
            names.add(t.name.strip().lower())
        self.app.targets = keep + added
        self.app.save_targets()
        self.app.targets_changed()
        self.fill()
        if self.tree.exists("grp:suggested"):
            self.tree.item("grp:suggested", open=True)
            self.tree.see("grp:suggested")
        self._start_next_events()
        text = (f"{len(added)} suggested transit{'s' if len(added) != 1 else ''} found."
                if added else "Done: no new full transits fit these nights.")
        if note:
            note = note.strip().rstrip(".")
            self.find_ind.finish(f"⚠ {text} Catalog: {note[:1].upper() + note[1:]}.", linger_ms=0)
        else:
            self.find_ind.finish("✔ " + text)


class EditTargetDialog:
    """Exposure, block length, goal, rules (incl. "no data while a planet transits" with a planet lookup), minimum
    altitude, Moon distance, clock window, notes; for transits also the ephemeris."""

    def __init__(self, app: App, target: Target, on_save=None, modal: bool = True):
        self.app = app
        self.t = Target.from_dict(target.to_dict())
        self.on_save = on_save
        self.win, outer = _dialog(app, f"Edit {target.name}")
        foot = ttk.Frame(outer, style="Card.TFrame")
        foot.pack(side=tk.BOTTOM, fill=tk.X)
        self.sb = ScrollBody(outer)
        self.sb.outer.pack(fill=tk.BOTH, expand=True)
        body = self.sb.inner
        self.vars: dict[str, tk.Variable] = {}
        self.loaded: dict[str, str] = {}       # field text as opened (unchanged text = unchanged value)
        self.no_transit = [r for r in self.t.rules if r.get("kind") == "no_transit"]
        self.widgets: dict = {}
        r = 0

        def row(label, key, value, unit="", width=12, values=None):
            nonlocal r
            ttk.Label(body, text=label, style="Card.TLabel").grid(row=r, column=0, sticky="w", pady=px(app, 2))
            var = tk.StringVar(value="" if value is None else str(value))
            self.vars[key] = var
            self.loaded[key] = var.get().strip()
            f = ttk.Frame(body, style="Card.TFrame")
            f.grid(row=r, column=1, columnspan=2, sticky="w", padx=px(app, 6), pady=px(app, 2))
            if values:
                w = ttk.Combobox(f, textvariable=var, values=values, state="readonly", width=width)
            else:
                w = ttk.Entry(f, textvariable=var, width=width)
            w.pack(side=tk.LEFT)
            if unit:
                ttk.Label(f, text=unit, style="Hint.TLabel").pack(side=tk.LEFT, padx=(px(app, 6), 0))
            self.widgets[key] = w
            r += 1

        def head(text):
            nonlocal r
            ttk.Label(body, text=text, style="Section.TLabel").grid(row=r, column=0, columnspan=3, sticky="w",
                                                                    pady=(px(app, 8), px(app, 2)))
            r += 1

        t = self.t
        head("Target")
        row("Name", "name", t.name, width=24)
        row("SHOBS-P mode", "mode", dict((m, c) for c, m in MODE_CHOICES)[t.mode if t.mode in "VTD" else "V"], "",
            16, [c for c, _m in MODE_CHOICES])
        row("Group", "kind", dict((k, lab) for lab, k in GROUP_CHOICES).get(t.kind, "Variable"),
            "the Targets page group (and how it is planned)", 12, [lab for lab, _k in GROUP_CHOICES])
        row("Priority", "priority", t.priority, "", 10, list(PRIORITIES))
        row("RA (J2000)", "ra", sky.fmt_ra(t.ra, 1) if t.ra is not None else "", "hh:mm:ss", 14)
        row("Dec (J2000)", "dec", sky.fmt_dec(t.dec) if t.dec is not None else "", "±dd:mm:ss", 14)
        row("V magnitude", "vmag", fmt_num(t.vmag, 2), "", 8)
        head("Observing")
        row("Exposure", "exposure_s", fmt_num(t.exposure_s), "s (empty = SCHED estimates one)", 8)
        row("Block length", "block_min", fmt_num(t.block_min), "min (flexible targets)", 8)
        row("Shortest useful block", "min_block_min", fmt_num(t.min_block_min), "min", 8)
        row("Goal", "goal_per_week", str(int(t.goal_per_week or 0)), "nights a week (0 = no goal)", 8)
        rules = {x.get("kind"): x for x in t.rules}
        row("Own minimum altitude", "min_alt", fmt_num((rules.get("min_alt") or {}).get("value")),
            "° (empty = the site's)", 8)
        row("Moon at least", "min_moon_sep", fmt_num((rules.get("min_moon_sep") or {}).get("value")),
            "° away (empty = no limit)", 8)
        row("Only between", "window", (rules.get("window") or {}).get("value") or "",
            "HH:MM-HH:MM site clock (empty = any time)", 12)
        if t.kind == "transit":
            pl = t.planet or {}
            head("Ephemeris")
            row("Period", "period", fmt_num(pl.get("period"), 8), "days", 14)
            row("Tc (T0)", "t0", fmt_num(pl.get("t0"), 6), "BJD_TDB", 14)
            row("Duration", "t14", fmt_num((pl.get("t14") or 0) * 24, 3) if pl.get("t14") else "", "hours", 8)
            row("Depth", "depth", fmt_num((pl.get("depth") or 0) * 100, 3) if pl.get("depth") else "", "%", 8)
            row("Reference", "reference", pl.get("reference") or "", "", 30)
        head("No data while a planet transits")
        self.nt_list = tk.Listbox(body, height=3, width=34, activestyle="none", relief="solid", borderwidth=1,
                                  highlightthickness=0, font=style.font(10))
        self.nt_list.grid(row=r, column=0, columnspan=2, sticky="w", pady=px(app, 2))
        nb = ttk.Frame(body, style="Card.TFrame")
        nb.grid(row=r, column=2, sticky="nw", padx=px(app, 6))
        ttk.Button(nb, text="Remove", command=self._nt_remove).pack(anchor="w")
        r += 1
        lf = ttk.Frame(body, style="Card.TFrame")
        lf.grid(row=r, column=0, columnspan=3, sticky="w", pady=px(app, 2))
        ttk.Label(lf, text="Planet", style="Card.TLabel").pack(side=tk.LEFT)
        self.nt_name = tk.StringVar()
        self.nt_entry = ttk.Entry(lf, textvariable=self.nt_name, width=16)
        self.nt_entry.pack(side=tk.LEFT, padx=px(app, 4))
        ttk.Label(lf, text="margin", style="Card.TLabel").pack(side=tk.LEFT)
        self.nt_pad = tk.StringVar(value="0")
        ttk.Entry(lf, textvariable=self.nt_pad, width=4).pack(side=tk.LEFT, padx=px(app, 4))
        ttk.Label(lf, text="min", style="Hint.TLabel").pack(side=tk.LEFT)
        style.green_button(lf, "Look up and add", self._nt_add).pack(side=tk.LEFT, padx=px(app, 6))
        r += 1
        self.nt_ind = JobIndicator(body)
        self.nt_ind.grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1
        head("Notes")
        self.notes = tk.Text(body, width=50, height=3, wrap="word", font=style.font(10), relief="solid",
                             borderwidth=1, highlightthickness=0)
        self.notes.insert("1.0", t.notes or "")
        self.notes.grid(row=r, column=0, columnspan=3, sticky="ew", pady=px(app, 2))
        r += 1
        ttk.Label(body, text="Write “defocus” in the notes for a defocused campaign (adds a refocus and its flats).",
                  style="Hint.TLabel").grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1
        self.msg_var = tk.StringVar()
        self.msg = ttk.Label(foot, textvariable=self.msg_var, style="Card.TLabel", wraplength=px(app, 460),
                             justify=tk.LEFT)
        self.msg.pack(anchor="w", fill=tk.X, pady=(px(app, 6), 0))
        btns = ttk.Frame(foot, style="Card.TFrame")
        btns.pack(fill=tk.X, pady=(px(app, 10), 0))
        ttk.Button(btns, text="Cancel", command=self.win.destroy).pack(side=tk.RIGHT)
        style.green_button(btns, "Save", self.save).pack(side=tk.RIGHT, padx=(0, px(app, 8)))
        self._fill_nt()
        _keys(self.win, self._return)
        # the scrolling body: as tall as its contents, at most ~70 % of the screen (buttons always on screen)
        self.win.update_idletasks()
        sh = app.winfo_screenheight()
        self.sb.canvas.configure(width=body.winfo_reqwidth(),
                                 height=min(body.winfo_reqheight(), int(sh * 0.70) - foot.winfo_reqheight()))
        first = self.widgets.get("exposure_s")
        if modal and not app.testing:
            _show_modal(app, self.win, first)

    def _return(self):
        """Return saves, except in the notes box (a new line there) and the planet box (looks the planet up)."""
        focus = self.win.focus_get()
        if focus is self.notes:
            return
        if focus is getattr(self, "nt_entry", None):
            self._nt_add()
            return
        self.save()

    def _fill_nt(self):
        self.nt_list.delete(0, tk.END)
        for rule in self.no_transit:
            pl = rule.get("planet") or {}
            pad = float(rule.get("pad_min") or 0)
            self.nt_list.insert(tk.END, f"{pl.get('name') or 'planet'}" + (f"  (± {pad:.0f} min)" if pad else ""))

    def _nt_remove(self):
        sel = list(self.nt_list.curselection())
        for i in reversed(sel):
            del self.no_transit[i]
        self._fill_nt()

    def _nt_add(self):
        name = self.nt_name.get().strip()
        if not name:
            self.nt_ind.finish("✖ Type the planet's name (e.g. HD 219134 c).")
            return
        try:
            pad = float(self.nt_pad.get() or 0)
        except ValueError:
            self.nt_ind.finish("✖ The margin must be a number of minutes.")
            return
        tg = mod("targets")
        if tg is None:
            self.nt_ind.finish(missing("targets"))
            return

        def done(res, exc):
            if exc is not None:
                self.nt_ind.finish(f"✖ {exc}")
                return
            pl = res.planet if isinstance(res, Target) else res
            if not pl:
                self.nt_ind.finish(f"✖ No ephemeris found for {name}.")
                return
            self.add_no_transit(pl, pad)
            self.nt_ind.finish(f"✔ Added {pl.get('name') or name}.")
        self.app.jobs.run(lambda: tg.lookup_planet(name), done, self.nt_ind, f"Looking up {name}")

    def add_no_transit(self, planet: dict, pad: float = 0.0):
        self.no_transit.append({"kind": "no_transit", "planet": dict(planet), "pad_min": float(pad),
                                "note": "No data while a planet transits", "value": None})
        self._fill_nt()

    def build(self) -> tuple[Target | None, str]:
        v = {k: str(var.get()).strip() for k, var in self.vars.items()}
        t = Target.from_dict(self.t.to_dict())

        def num(key, label, lo=None, hi=None):
            if not v.get(key):
                return None
            try:
                x = float(v[key].replace(",", "."))
            except ValueError:
                raise ValueError(f"{label} must be a number (you typed “{v[key]}”).")
            if (lo is not None and x < lo) or (hi is not None and x > hi):
                raise ValueError(f"{label} must be between {fmt_num(lo)} and {fmt_num(hi)}.")
            return x
        try:
            if not v["name"]:
                raise ValueError("The name is empty.")
            t.name = v["name"]
            t.mode = dict(MODE_CHOICES).get(v["mode"], t.mode)
            new_kind = dict(GROUP_CHOICES).get(v.get("kind", ""), t.kind)
            if new_kind != t.kind:
                t.kind = new_kind
                t.suggested = False
                if new_kind == "variable" and not t.elements:
                    t.elements = {"vtype": "", "any_time": True}
            t.priority = v["priority"] if v["priority"] in PRIORITIES else t.priority
            if v["ra"] or v["dec"]:
                try:
                    t.ra, t.dec = sky.parse_ra(v["ra"]), sky.parse_dec(v["dec"])
                except (ValueError, IndexError):
                    raise ValueError("RA and Dec: type them as 23:13:17 and +57:10:06.")
                if not (0 <= t.ra < 360 and -90 <= t.dec <= 90):
                    raise ValueError("RA must be 0–24 h and Dec −90 to +90°.")
            t.vmag = num("vmag", "V magnitude", -2, 25)
            t.exposure_s = num("exposure_s", "Exposure", 0.001, 3600)
            bl = num("block_min", "Block length", 5, 900)
            if bl is None:
                raise ValueError("Block length is empty: type the minutes you want per visit (e.g. 120).")
            mb = num("min_block_min", "Shortest useful block", 5, 900)
            if mb is None:
                raise ValueError("Shortest useful block is empty: type the fewest minutes worth a visit (e.g. 45).")
            if mb > bl:
                raise ValueError("The shortest useful block cannot be longer than the block length.")
            t.block_min, t.min_block_min = bl, mb
            g = num("goal_per_week", "Goal", 0, 7)
            if g is not None and g != int(g):
                raise ValueError("Goal must be a whole number of nights a week (0 to 7).")
            t.goal_per_week = int(g or 0)
            rules = [r for r in t.rules if r.get("kind") not in ("no_transit", "min_alt", "min_moon_sep", "window")]
            ma = num("min_alt", "Own minimum altitude", 0, 89)
            if ma is not None:
                rules.append({"kind": "min_alt", "value": ma})
            ms = num("min_moon_sep", "Moon distance", 0, 180)
            if ms is not None:
                rules.append({"kind": "min_moon_sep", "value": ms})
            if v.get("window"):
                try:
                    a, b = v["window"].replace("–", "-").split("-")
                    parse_hhmm(a)
                    parse_hhmm(b)
                except ValueError:
                    raise ValueError("Only between: type two site clock times, e.g. 21:00-02:30.")
                rules.append({"kind": "window", "value": v["window"].replace("–", "-").replace(" ", "")})
            rules.extend(self.no_transit)
            t.rules = rules
            if t.kind == "transit" and "period" in v:
                pl = dict(t.planet or {})
                before = dict(pl)
                pl["name"] = pl.get("name") or t.name
                pl["period"] = num("period", "Period", 0.01, 10000)
                pl["t0"] = num("t0", "Tc (T0)", 2400000, 2600000)
                hrs = num("t14", "Duration", 0.1, 48)
                pl["t14"] = hrs / 24.0 if hrs else None
                dep = num("depth", "Depth", 0.001, 100)
                pl["depth"] = dep / 100.0 if dep else None
                pl["reference"] = v.get("reference", "")
                for k in ("period", "t0", "t14", "depth"):   # text left as opened keeps the full-precision value
                    if v.get(k, "").strip() == self.loaded.get(k, "") and k in before:
                        pl[k] = before[k]
                changed = any(not _close(before.get(k), pl.get(k)) for k in ("period", "t0", "t14", "depth"))
                if changed:
                    pl["source"] = "typed"
                    if before.get("period") != pl.get("period"):
                        pl["period_err"] = None
                    if before.get("t0") != pl.get("t0"):
                        pl["t0_err"] = None
                t.planet = pl
        except ValueError as exc:
            return None, str(exc)
        t.notes = self.notes.get("1.0", tk.END).strip()
        return t, ""

    def save(self):
        t, err = self.build()
        if err:
            set_msg(self.msg_var, self.msg, f"✖ Not saved: {err}")
            return False
        if self.on_save:
            self.on_save(t)
        self.win.destroy()
        return True


def _close(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    try:
        return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)))
    except (TypeError, ValueError):
        return a == b


# ---- 3 Schedule -----------------------------------------------------------------------------------------------------

class SchedulePage:
    title = "Schedule"
    subtitle = ("One tab per night, on the site's clock. Lock a block to keep it through rebuilds; "
                "mark what happened (Done, Partial, Clouded out) and Re-review to roll the week forward.")
    BUSY = "⚠ Wait for the build to finish."
    STALE = ("⚠ Setup changed since this schedule was built: press Build schedule (or Re-review) to rebuild it "
             "with the new settings.")

    def __init__(self, app: App, parent):
        self.app = app
        self.dirty = True
        self.busy = False
        self.setup_stale = False                  # Setup saved since this schedule was built
        self.frame = ttk.Frame(parent, style="Card.TFrame")
        self.wx_note = ""
        ctl = ttk.Frame(self.frame, style="Card.TFrame")
        ctl.pack(fill=tk.X, pady=(0, px(app, 6)))
        ttk.Label(ctl, text="Nights", style="Card.TLabel").pack(side=tk.LEFT)
        self.n_var = tk.StringVar()
        cb = ttk.Combobox(ctl, textvariable=self.n_var, values=[str(k) for k in range(1, 8)], state="readonly",
                          width=3)
        cb.pack(side=tk.LEFT, padx=(px(app, 4), px(app, 12)))
        cb.bind("<<ComboboxSelected>>", lambda _e: self._nights_changed())
        ttk.Label(ctl, text="first night", style="Card.TLabel").pack(side=tk.LEFT)
        self.start_var = tk.StringVar()
        # any date: pick one of the next nights or type one (2026-11-03, 3 Nov)
        self.start_box = ttk.Combobox(ctl, textvariable=self.start_var, width=20)
        self.start_box.pack(side=tk.LEFT, padx=(px(app, 4), px(app, 12)))
        self.build_btn = style.green_button(ctl, "Build schedule", self.build)
        self.build_btn.pack(side=tk.LEFT)
        self.review_btn = style.green_button(ctl, "Re-review schedule", self.rereview)
        self.review_btn.pack(side=tk.LEFT, padx=px(app, 8))
        self.ind = JobIndicator(ctl)
        self.ind.pack(side=tk.LEFT, padx=px(app, 6))
        self.msg_var = tk.StringVar()
        self.msg = ttk.Label(self.frame, textvariable=self.msg_var, style="Card.TLabel", justify=tk.LEFT)
        self.msg.pack(fill=tk.X, anchor="w")
        follow_wrap(self.msg, self.frame, px(app, 20))
        self.placeholder = ttk.Label(self.frame, style="Hint.TLabel", justify=tk.LEFT,
                                     text="No schedule yet. Choose how many nights and the first night, then press "
                                          "Build schedule.")
        self.nb = ttk.Notebook(self.frame)
        self.nb.bind("<<NotebookTabChanged>>", lambda _e: self._tab_changed())
        self.tabs: list[NightTab] = []
        self._start_dates: list[date] = []

    # ---- controls ----
    def _nights_changed(self):
        try:
            self.app.options["nights"] = int(self.n_var.get())
            settings.save_settings(self.app.settings)
        except ValueError:
            pass

    def _fill_controls(self):
        app = self.app
        n = int(app.options.get("nights", 1) or 1)
        self.n_var.set(str(min(7, max(1, n))))
        tonight = app.tonight()
        self._start_dates = [tonight + timedelta(days=k) for k in range(7)]
        labels = [self._label(d, k == 0) for k, d in enumerate(self._start_dates)]
        self.start_box.configure(values=labels)
        if not self.start_var.get().strip():
            self.start_var.set(labels[0])

    def _label(self, d: date, tonight: bool = False) -> str:
        return f"{night_label(d.isoformat(), self.app.site)} {d.year}" + (" (tonight)" if tonight else "")

    def chosen_start(self) -> date | None:
        """The first night from the box: one of the listed nights, or a typed date (2026-11-03, 3 Nov 2026,
        3 Nov). None when it cannot be read."""
        text = self.start_var.get().strip()
        labels = list(self.start_box.cget("values"))
        if text in labels:
            return self._start_dates[labels.index(text)]
        if not text:
            return self.app.tonight()
        t = text.replace("(tonight)", "").strip()
        for fmt in ("%Y-%m-%d", "%a %d %b %Y", "%d %b %Y", "%d %B %Y", "%a %d %b", "%d %b", "%d %B"):
            try:
                d = datetime.strptime(t, fmt).date()
            except ValueError:
                continue
            if "%Y" not in fmt:
                tonight = self.app.tonight()
                d = d.replace(year=tonight.year)
                if d < tonight - timedelta(days=60):
                    d = d.replace(year=tonight.year + 1)
            return d
        return None

    def refresh(self):
        self._fill_controls()
        app = self.app
        if not app.site.complete():
            set_msg(self.msg_var, self.msg, "✖ Enter your site on the Setup page first (latitude, longitude, "
                                            "time zone); then build.")
        elif self.setup_stale and app.schedule.nights:
            set_msg(self.msg_var, self.msg, self.STALE)
        elif (app.schedule.nights and app.schedule.nights[0].get("date", "") < app.tonight().isoformat()) \
                or not self.msg_var.get() or self.msg_var.get() == self.STALE or \
                self.msg_var.get().startswith("✖ Enter your site"):
            nights = app.schedule.nights
            if nights and nights[0].get("date", "") < app.tonight().isoformat():
                set_msg(self.msg_var, self.msg,
                        f"⚠ The schedule starts on {night_label(nights[0]['date'], app.site)}, before tonight. "
                        "Mark what happened and press Re-review schedule to roll it forward.")
            elif not app.planner_targets():
                set_msg(self.msg_var, self.msg, "⚠ No targets are ticked to plan (☑ on the Targets page).")
            else:
                set_msg(self.msg_var, self.msg, "")
        if self.dirty:
            self.rebuild_tabs()
            self.dirty = False

    def current_index(self) -> int | None:
        try:
            return self.nb.index("current") if self.tabs else None
        except tk.TclError:
            return None

    def _tab_changed(self):
        k = self.current_index()
        if k is not None and k < len(self.tabs):
            self.tabs[k].shown()

    def rebuild_tabs(self, select: int | None = None):
        """One (empty) tab per night; a tab builds its contents the first time it is shown."""
        cur = select if select is not None else (self.current_index() or 0)
        for tab in self.tabs:
            tab.frame.destroy()
        self.tabs = []
        nights = self.app.schedule.nights
        if not nights:
            self.nb.pack_forget()
            self.placeholder.pack(anchor="w", pady=px(self.app, 20))
            return
        self.placeholder.pack_forget()
        if not self.nb.winfo_manager():
            self.nb.pack(fill=tk.BOTH, expand=True, pady=(px(self.app, 4), 0))
        for i, _n in enumerate(nights):
            tab = NightTab(self, i)
            self.nb.add(tab.frame, text=tab.tab_text())
            self.tabs.append(tab)
        try:
            self.nb.select(min(max(0, cur), len(self.tabs) - 1))
        except tk.TclError:
            pass
        self._tab_changed()

    # ---- busy ----
    def set_busy(self, on: bool):
        self.busy = on
        for b in (self.build_btn, self.review_btn):
            try:
                b.state(["disabled"] if on else ["!disabled"])
            except tk.TclError:
                pass

    def refuse_if_busy(self, show=None) -> bool:
        """True (and says so) while a build or rebuild runs: edits then would be lost."""
        if not self.busy:
            return False
        # (the message line, not the indicator: its spinner keeps showing the running job)
        (show or (lambda t: set_msg(self.msg_var, self.msg, t)))(self.BUSY)
        return True

    # ---- building ----
    def _ready(self) -> bool:
        app = self.app
        if self.refuse_if_busy():
            return False
        if not app.site.complete():
            self.ind.finish("✖ Enter your site on the Setup page first (latitude, longitude, time zone).")
            return False
        if mod("planner") is None:
            self.ind.finish(missing("planner"))
            return False
        return True

    def _prefetch_info(self, sch: Schedule):
        for n in sch.nights:
            try:
                self.app.night_info(date.fromisoformat(n["date"]))
            except Exception:
                pass

    def _run(self, work, done, label):
        self.set_busy(True)

        def finish(res, exc):
            self.set_busy(False)
            done(res, exc)
        self.app.jobs.run(work, finish, self.ind, label)

    def build(self, start: date | None = None, n: int | None = None):
        if not self._ready():
            return
        app = self.app
        start = start or self.chosen_start()
        if start is None:
            self.ind.finish("✖ Type the first night as a date, e.g. 2026-11-03 or 3 Nov.")
            return
        n = int(n or self.n_var.get() or 1)
        site, optics, opts = app.site, app.optics, dict(app.options)
        targets = [Target.from_dict(t.to_dict()) for t in app.planner_targets()]
        pl = mod("planner")
        prior = Schedule.from_dict(app.schedule.to_dict())
        if hasattr(pl, "roll_forward") and any(nd.get("date", "") < start.isoformat() for nd in prior.nights):
            prior = pl.roll_forward(prior, start)      # nights before the new start go to the history, marks kept

        def work():
            wx, note = app.fetch_weather(start, n)
            sch = pl.build_schedule(start, n, site, optics, targets, opts, prior=prior, weather_by_date=wx or None)
            self._attach_weather(sch, wx)
            self._prefetch_info(sch)
            return sch, wx, note
        settings.log(f"build schedule {start} × {n}, {len(targets)} targets")
        self._run(work, lambda r, e: self._built(r, e, prior), f"Building {n} night{'s' if n > 1 else ''}")

    def review_start(self) -> date:
        """Re-review rolls the schedule forward to tonight; a schedule that starts later keeps its first night (no
        future night is dropped)."""
        tonight = self.app.tonight()
        nights = self.app.schedule.nights
        first = date.fromisoformat(nights[0]["date"]) if nights else tonight
        return max(tonight, first)

    def rereview(self):
        if not self._ready():
            return
        app = self.app
        pl = mod("planner")
        today = self.review_start()
        past = [b for nd in app.schedule.nights_obj() if nd.date < today.isoformat()
                for b in nd.blocks_obj() if (b.locked or b.set_by_user) and b.status == "planned"]
        if past and not ask_yes_no(app, "Re-review", f"{len(past)} locked block{'s' if len(past) != 1 else ''} on "
                                   "past nights (" + ", ".join(sorted({b.name for b in past})) + ") go to the "
                                   "history as Planned (not Done). Mark them first if you observed them. Continue?"):
            return
        n = max(1, len(app.schedule.nights) or int(self.n_var.get() or 1))
        site, optics, opts = app.site, app.optics, dict(app.options)
        targets = [Target.from_dict(t.to_dict()) for t in app.planner_targets()]
        prior = Schedule.from_dict(app.schedule.to_dict())

        def work():
            wx, note = app.fetch_weather(today, n)
            # rereview moves past nights to the history and rebuilds from today, keeping the schedule's length
            sch = pl.rereview(prior, today, site, optics, targets, opts, weather_by_date=wx or None)
            self._attach_weather(sch, wx)
            self._prefetch_info(sch)
            return sch, wx, note
        settings.log(f"re-review from {today}")
        self._run(work, lambda r, e: self._built(r, e, prior), "Re-reviewing")

    @staticmethod
    def _attach_weather(sch: Schedule, wx: dict):
        for nd in sch.nights:
            if not nd.get("weather") and isinstance(wx, dict) and isinstance(wx.get(nd.get("date")), dict):
                nd["weather"] = wx[nd["date"]]

    def _merge_live(self, sch: Schedule, prior: Schedule):
        """Edits made to the live schedule while the build ran (lock, set times, status) win over the result."""
        def key(b):
            return (b.get("target_id"), round(b.get("start", 0), 6), round(b.get("end", 0), 6),
                    bool(b.get("locked")), bool(b.get("set_by_user")), b.get("status", "planned"))
        before = {n.get("date"): {key(b) for b in n.get("blocks", [])} for n in prior.nights}
        out = {n.get("date"): n for n in sch.nights}
        for live in self.app.schedule.nights:
            d = live.get("date")
            if d not in out:
                continue
            for b in live.get("blocks", []):
                if key(b) in before.get(d, set()):
                    continue                       # unchanged since the build started
                res = out[d].setdefault("blocks", [])
                if b.get("locked") or b.get("set_by_user"):
                    res[:] = [x for x in res if x.get("target_id") != b.get("target_id")] + [dict(b)]
                    res.sort(key=lambda x: x.get("start", 0))
                else:                              # a mark: the same block (target and start) only
                    for x in res:
                        if x.get("target_id") == b.get("target_id") and \
                                abs(x.get("start", 0) - b.get("start", 0)) < 1e-6:
                            x["status"] = b.get("status", "planned")

    def _built(self, res, exc, prior: Schedule | None = None):
        app = self.app
        if exc is not None:
            self.ind.finish(f"✖ The schedule could not be built: {exc}")
            return
        sch, wx, note = res
        if prior is not None:
            self._merge_live(sch, prior)
        # (no re-stamping of marks here: the planner keeps every marked block on its night with its status)
        if not sch.built:
            sch.built = datetime.now(timezone.utc).isoformat(timespec="seconds")
        app.schedule = sch
        app.schedule_changed()
        self.setup_stale = False
        self.wx_note = note
        nights = sch.nights_obj()
        if nights:
            self.n_var.set(str(min(7, len(nights))))
            app.options["nights"] = min(7, len(nights))
            settings.save_settings(app.settings)
            self.start_var.set(self._label(date.fromisoformat(nights[0].date),
                                           nights[0].date == app.tonight().isoformat()))
        self.rebuild_tabs(select=0)
        self.dirty = False
        blocks = sum(len(n.blocks) for n in nights)
        nofit = len({d.get("target_id") or d.get("name") for n in nights for d in n.didnt_fit})
        text = (f"Built {len(nights)} night{'s' if len(nights) != 1 else ''}: {blocks} "
                f"block{'s' if blocks != 1 else ''}")
        if nofit:
            text += f"; {nofit} target{'s' if nofit != 1 else ''} didn't fit (see each night)"
        text += "."
        any_hours = any((n.weather or {}).get("hours") for n in nights)
        if not any_hours:
            text = f"⚠ {text} No forecast" + (f" ({note})" if note else "") + ", so it was built without weather."
        elif note:
            text = f"⚠ {text} Forecast: {note.rstrip('.')}."
        else:
            text = "✔ " + text
        set_msg(self.msg_var, self.msg, text)
        self.ind.finish("✔ Done")
        app.set_status(text)

    # ---- one night ----
    def night(self, idx: int) -> NightPlan:
        return NightPlan.from_dict(self.app.schedule.nights[idx])

    def set_night(self, idx: int, night: NightPlan, redraw: bool = True):
        self.app.schedule.nights[idx] = night.to_dict()
        self.app.schedule_changed()
        self.dirty = False
        if redraw and idx < len(self.tabs):
            self.tabs[idx].fill()
            try:
                self.nb.tab(self.tabs[idx].frame, text=self.tabs[idx].tab_text())
            except tk.TclError:
                pass

    def rebuild_night(self, idx: int, prefer=(), extra_locked: list | None = None, then_msg=None,
                      drop: Block | None = None, on_success=None, show=None) -> bool:
        """Rebuild one night around its locked blocks (+ extra_locked, − drop). Nothing is written unless the
        planner succeeds: then on_success(new) may change other nights, and then_msg(new) (or a string) is shown."""
        show = show or (lambda t: set_msg(self.msg_var, self.msg, t))
        if self.refuse_if_busy(show) or not self._ready():
            return False
        app = self.app
        pl = mod("planner")
        night = self.night(idx)
        d = date.fromisoformat(night.date)

        def same(a: Block, b: Block) -> bool:
            return a.target_id == b.target_id and abs(a.start - b.start) < 1e-6 and abs(a.end - b.end) < 1e-6
        locked = [b for b in night.blocks_obj() if (b.locked or b.set_by_user or b.status != "planned")
                  and not (drop is not None and same(b, drop))] + list(extra_locked or [])
        site, optics, opts = app.site, app.optics, dict(app.options)
        targets = [Target.from_dict(t.to_dict()) for t in app.planner_targets()]
        # the history plus the earlier nights' blocks, exactly as build_schedule passes them (weekly-goal pacing)
        if hasattr(pl, "history_for_night"):
            history = pl.history_for_night(Schedule.from_dict(app.schedule.to_dict()), night.date)
        else:
            history = list(app.schedule.history)
        date_iso = night.date

        def work():
            return pl.build_night(d, site, optics, targets, opts, locked=locked, weather=night.weather,
                                  history=history, prefer=set(prefer))

        def done(new, exc):
            if exc is not None:
                self.ind.finish(f"✖ {night_label(date_iso, site)} could not be rebuilt ({exc}); nothing changed.")
                show(f"✖ {night_label(date_iso, site)} could not be rebuilt ({exc}); nothing changed.")
                return
            if isinstance(new, dict):
                new = NightPlan.from_dict(new)
            if not new.weather and night.weather:
                new.weather = night.weather
            k = next((i for i, n in enumerate(app.schedule.nights) if n.get("date") == date_iso), None)
            if k is None:
                self.ind.finish("⚠ That night is no longer in the schedule.")
                return
            app.schedule.nights[k] = new.to_dict()
            if on_success is not None:
                on_success(new)
            self.set_night(k, new)
            for j, tab in enumerate(self.tabs):
                if j != k:
                    tab.fill()
                    try:
                        self.nb.tab(tab.frame, text=tab.tab_text())
                    except tk.TclError:
                        pass
            self.ind.finish("✔ Done")
            text = then_msg(new) if callable(then_msg) else then_msg
            if text:
                show(text)
        self._run(work, done, f"Rebuilding {night_label(date_iso, site)}")
        return True


class Axis:
    """The shared time axis of the weather strip and the block bar (JD → x)."""

    def __init__(self, j0: float, j1: float, x0: float, x1: float):
        self.j0, self.j1, self.x0, self.x1 = j0, j1, x0, x1

    def x(self, jd: float) -> float:
        return self.x0 + (jd - self.j0) / (self.j1 - self.j0) * (self.x1 - self.x0)

    def clip(self, x: float) -> float:
        return min(self.x1, max(self.x0, x))

    def hour_ticks(self, tz: str) -> list[float]:
        t = sky.to_local(self.j0, tz)
        first = t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        jd = sky.jd_from_datetime(first)
        out = []
        while jd <= self.j1:
            out.append(jd)
            jd += 1.0 / 24.0
        return out


class NightTab:
    """One night: weather strip, block bar with airmass bands, the nightly table, its actions, and below it flats and
    darks, didn't fit (with Plan this instead) and free time.

    Speed: the contents are built the first time the tab is shown; the canvases draw only while they are on screen,
    and a resize redraws once after the window settles (80 ms) and only when the width really changed."""

    PLAN_INSTEAD_REASONS = ("collides with", "no free stretch", "no room")

    def __init__(self, page: SchedulePage, idx: int):
        self.page, self.app, self.idx = page, page.app, idx
        self.frame = ttk.Frame(page.nb, style="Card.TFrame")
        self.built = False
        self.stale = True
        self._drawn_w = {"wx": None, "bar": None}
        self._pending = {"wx": None, "bar": None}
        f = self.app.fonts
        self.small, self.norm, self.bold = f["small"], f["norm"], f["bold"]
        self.lh = self.norm.metrics("linespace")

    # ---- lazy contents ----
    def shown(self):
        """The tab became the visible one: build its contents once, then bring them up to date."""
        if not self.built:
            self._build_ui()
        if self.stale:
            self.fill()
        else:
            self.frame.after_idle(self.redraw)

    def _build_ui(self):
        app = self.app
        self.built = True
        self.body = ScrollBody(self.frame)
        self.body.outer.pack(fill=tk.BOTH, expand=True)
        inner = self.body.inner
        pad = px(app, 10)
        self.head_var = tk.StringVar()
        head = ttk.Label(inner, textvariable=self.head_var, style="Card.TLabel", justify=tk.LEFT)
        head.pack(anchor="w", fill=tk.X, padx=pad, pady=(px(app, 8), px(app, 2)))
        follow_wrap(head, inner, 2 * pad)
        self.notes_box = ttk.Frame(inner, style="Card.TFrame")
        self.notes_box.pack(fill=tk.X, padx=pad)
        self.wx = tk.Canvas(inner, background=style.CARD, highlightthickness=0, height=int(self.lh * 7.4))
        self.wx.pack(fill=tk.X, padx=pad, pady=(px(app, 4), 0))
        self.bar = tk.Canvas(inner, background=style.CARD, highlightthickness=0, height=int(self.lh * 8.0))
        self.bar.pack(fill=tk.X, padx=pad)
        legend = ("Blocks take the color of the SHOBS-P mode they will be reduced in   ·   ▼ the target's meridian   ·   "
                  "yellow line: Moon up   ·   gold outline: locked or set by you   ·   band under a block: airmass, "
                  "light = low (start · best · end)")
        lg = ttk.Label(inner, text=legend, style="Small.TLabel", justify=tk.LEFT)
        lg.pack(anchor="w", fill=tk.X, padx=pad, pady=(px(app, 2), px(app, 8)))
        follow_wrap(lg, inner, 2 * pad)
        self.wx.bind("<Configure>", lambda e: self._configured("wx", e.width))
        self.bar.bind("<Configure>", lambda e: self._configured("bar", e.width))

        # nightly table
        cols = (("time", "Time"), ("target", "Target"), ("radec", "RA / Dec (J2000)"), ("mode", "SHOBS-P mode"),
                ("alt", "Alt"), ("meridian", "Meridian"), ("exposure", "Exposure"), ("frames", "Frames"),
                ("notes", "Notes"))
        self.headings = dict(cols)
        tf = ttk.Frame(inner, style="Card.TFrame")
        tf.pack(fill=tk.X, padx=pad)
        self.table = ttk.Treeview(tf, columns=[c for c, _h in cols], show="headings", selectmode="browse",
                                  height=3)
        self.table_hbar = ttk.Scrollbar(tf, orient=tk.HORIZONTAL, command=self.table.xview)
        self.table.configure(xscrollcommand=self._table_bar)
        for c, h in cols:
            self.table.heading(c, text=h, anchor="w")
            self.table.column(c, anchor="w")
        for m in ("V", "T", "D"):
            self.table.tag_configure(f"mode{m}", background=style.mode_color(m, "tint"))
        self.table.tag_configure("done", foreground="#5d6d76")
        self.table.pack(fill=tk.X)
        self.table.bind("<<TreeviewSelect>>", lambda _e: self._selected())
        self.detail_var = tk.StringVar()
        self.detail = ttk.Label(inner, textvariable=self.detail_var, style="Card.TLabel", justify=tk.LEFT)
        self.detail.pack(anchor="w", fill=tk.X, padx=pad, pady=(px(app, 4), 0))
        follow_wrap(self.detail, inner, 2 * pad)
        act = ttk.Frame(inner, style="Card.TFrame")
        act.pack(fill=tk.X, padx=pad, pady=(px(app, 6), 0))
        self.lock_btn = ttk.Button(act, text="Lock", command=self.lock_selected)
        self.lock_btn.pack(side=tk.LEFT)
        ttk.Button(act, text="Remove", command=self.remove_selected).pack(side=tk.LEFT, padx=px(app, 6))
        self.move_btn = ttk.Button(act, text="Move to night…", command=self.move_selected)
        self.move_btn.pack(side=tk.LEFT)
        ttk.Button(act, text="Edit times…", command=self.edit_times).pack(side=tk.LEFT, padx=px(app, 6))
        ttk.Label(act, text="Status", style="Card.TLabel").pack(side=tk.LEFT, padx=(px(app, 12), px(app, 4)))
        self.status_var = tk.StringVar()
        self.status_box = ttk.Combobox(act, textvariable=self.status_var, values=STATUS_CHOICES, state="readonly",
                                       width=max(len(s) for s in STATUS_CHOICES) + 1)
        self.status_box.pack(side=tk.LEFT)
        self.status_box.bind("<<ComboboxSelected>>", lambda _e: self.set_status(STATUS_KEY.get(self.status_var.get(),
                                                                                                 "planned")))
        # the action's message on its own row, wrapping
        self.act_var = tk.StringVar()
        self.act_msg = ttk.Label(inner, textvariable=self.act_var, style="Card.TLabel", justify=tk.LEFT)
        self.act_msg.pack(anchor="w", fill=tk.X, padx=pad, pady=(px(app, 4), px(app, 4)))
        follow_wrap(self.act_msg, inner, 2 * pad)

        lower = ttk.Frame(inner, style="Card.TFrame")
        lower.pack(fill=tk.X, padx=pad, pady=(px(app, 4), px(app, 12)))
        for k in range(3):
            lower.columnconfigure(k, weight=1, uniform="lower")
        self.flats_box = ttk.LabelFrame(lower, text="Flats and darks to shoot", style="Group.TLabelframe",
                                        padding=px(app, 8))
        self.nofit_box = ttk.LabelFrame(lower, text="Didn't fit, and why", style="Group.TLabelframe",
                                        padding=px(app, 8))
        self.free_box = ttk.LabelFrame(lower, text="Free time", style="Group.TLabelframe", padding=px(app, 8))
        for k, b in enumerate((self.flats_box, self.nofit_box, self.free_box)):
            b.grid(row=0, column=k, sticky="nsew", padx=(0 if k == 0 else px(app, 8), 0))

    def _table_bar(self, first, last):
        try:
            whole = float(first) <= 0.0 and float(last) >= 1.0
        except (TypeError, ValueError):
            whole = True
        if whole:
            self.table_hbar.pack_forget()
        elif not self.table_hbar.winfo_manager():
            self.table_hbar.pack(fill=tk.X)
        self.table_hbar.set(first, last)

    # ---- drawing on demand ----
    def _canvas(self, which):
        return self.wx if which == "wx" else self.bar

    def _configured(self, which: str, width: int):
        if width == self._drawn_w[which]:
            return
        if self._pending[which] is not None:
            try:
                self.frame.after_cancel(self._pending[which])
            except tk.TclError:
                pass
        self._pending[which] = self.frame.after(80, lambda: self._draw(which))

    def _draw(self, which: str, force: bool = False):
        self._pending[which] = None
        if not self.built:
            return
        c = self._canvas(which)
        try:
            if not c.winfo_viewable():
                self._drawn_w[which] = None          # draw when shown
                return
            w = int(c.winfo_width())
        except tk.TclError:
            return
        if not force and w == self._drawn_w[which]:
            return
        self._drawn_w[which] = w
        (self.draw_weather if which == "wx" else self.draw_bar)()

    def redraw(self):
        self._draw("wx", force=True)
        self._draw("bar", force=True)

    # ---- data ----
    def night(self) -> NightPlan:
        return self.page.night(self.idx)

    def tab_text(self) -> str:
        n = self.app.schedule.nights[self.idx]
        warn = (any(b.get("cautions") for b in n.get("blocks", [])) or n.get("didnt_fit")
                or any(str(x).startswith(("✖", "⚠")) for x in n.get("notes", []) or []))
        return ("⚠ " if warn else "") + night_label(n.get("date", ""), self.app.site)

    def blocks(self) -> list[tuple[int, Block]]:
        bl = self.night().blocks_obj()
        return sorted(enumerate(bl), key=lambda p: p[1].start)

    def fill(self):
        if not self.built:
            self.stale = True
            return
        self.stale = False
        app, night, site = self.app, self.night(), self.app.site
        tz = site.tz
        info = app.night_info(date.fromisoformat(night.date)) if night.date else None
        bits = []
        if night.dusk and night.dawn:
            dark = "astronomical" if (site.sun_alt or -18) <= -17.9 else "nautical"
            bits.append(f"Dark {span_text(night.dusk, night.dawn, tz)} ({dark})")
        illum = night.moon_illum if night.moon_illum is not None else (info or {}).get("moon_illum")
        if illum is not None:
            moon = f"Moon {illum * 100:.0f} %"
            mr = night.moonrise if night.moonrise is not None else (info or {}).get("moonrise")
            ms = night.moonset if night.moonset is not None else (info or {}).get("moonset")
            ev = sorted([(j, w) for j, w in ((mr, "rises"), (ms, "sets")) if j], key=lambda p: p[0])
            if ev:
                moon += " (" + ", ".join(f"{w} {sky.hhmm(j, tz)}" for j, w in ev) + ")"
            bits.append(moon)
        wx = night.weather or {}
        if wx.get("summary"):
            bits.append(str(wx["summary"]))
        self.head_var.set(f"{night_label(night.date, site)}   ·   " + "   ·   ".join(bits) if bits else
                          night_label(night.date, site))
        # night-wide lines from the planner (✖ no dark time, ⚠ dew ...), each colored by its icon
        for w in self.notes_box.winfo_children():
            w.destroy()
        lines = [str(x) for x in (night.notes or []) if str(x).strip()]
        if (not night.dusk or not night.dawn) and not any("dark" in x.lower() for x in lines):
            darkest = (info or {}).get("darkest_sun_alt")
            nautical_helps = site.sun_alt <= -17.9 and darkest is not None and darkest < -12
            lines.append("✖ No dark time tonight: the Sun stays above " + f"{site.sun_alt:.0f}°"
                         + (f" (lowest {darkest:.1f}°)" if darkest is not None else "")
                         + (". Choose nautical darkness on the Setup page to plan in what darkness there is."
                            if nautical_helps else "."))
        darkest = (info or {}).get("darkest_sun_alt")
        if (not night.dusk or not night.dawn) and site.sun_alt <= -17.9 and \
                darkest is not None and darkest < -12 and \
                not any("nautical" in x.lower() for x in lines):
            lines.append("⚠ Choose nautical darkness on the Setup page to plan in what darkness there is.")
        for line in lines:
            lab = ttk.Label(self.notes_box, text=line, justify=tk.LEFT,
                            style=style.status_style(style.kind_of(line)) if line[:1] in "✖⚠✔" else "Card.TLabel")
            lab.pack(anchor="w", fill=tk.X, pady=(0, px(app, 2)))
            follow_wrap(lab, self.notes_box, px(app, 4))
        self.fill_table()
        self.fill_lower()
        self.redraw()

    # ---- table ----
    def table_rows(self, night: NightPlan) -> list[tuple[int, Block, dict]]:
        """(index, block, row) per block in time order. The row comes from export.table_rows (the same text the
        plan card uses) where its keys are found, and from the block itself otherwise. Alt is the app's own
        "62° (X 1.13)" at the best airmass."""
        site = self.app.site
        pairs = self.blocks()
        rows = None
        ex = mod("export")
        if ex is not None and hasattr(ex, "table_rows"):
            try:
                ordered = NightPlan.from_dict(night.to_dict())
                ordered.blocks = [b.to_dict() for _i, b in pairs]
                rows = ex.table_rows(ordered, site, targets_by_id=self.app.targets_by_id())
                if not isinstance(rows, list) or len(rows) != len(pairs):
                    rows = None
            except Exception as exc:
                settings.log(f"export.table_rows: {exc!r}")
                rows = None
        out = []
        for k, (i, b) in enumerate(pairs):
            mine = own_row(b, site, self.app.targets_by_id())
            if rows is not None and isinstance(rows[k], dict):
                r = {str(key).lower(): val for key, val in rows[k].items()}
                code = r.get("mode_code")
                if code:
                    name = str(r.get("mode") or "")
                    r["mode"] = f"{code} {MODE_NAMES.get(code, name) if name in ('', code) else name}"
                for col, keys in ROW_KEYS.items():
                    for key in keys:
                        if key in r and r[key] not in (None, ""):
                            mine[col] = str(r[key])
                            break
            out.append((i, b, mine))
        return out

    def fill_table(self):
        night = self.night()
        tbl = self.table
        sel = tbl.selection()
        tbl.delete(*tbl.get_children())
        rows = self.table_rows(night)
        for i, b, r in rows:
            target = r["target"]
            if b.set_by_user:
                target += "  · set by you"
            elif b.locked:
                target += "  · locked"
            if b.status != "planned":
                target += f"  · {STATUS_LABELS.get(b.status, b.status)}"
            tags = [f"mode{b.mode if b.mode in 'VTD' else 'V'}"]
            if b.status in ("done", "clouded"):
                tags.append("done")
            notes = r["notes"]
            if b.cautions and not any(str(c).strip() in notes for c in b.cautions):
                notes = "; ".join(str(c) for c in b.cautions) + ("; " + notes if notes else "")
            tbl.insert("", tk.END, iid=str(i), tags=tuple(tags),
                       values=(r["time"], target, r["radec"], r["mode"], r["alt"], r["meridian"], r["exposure"],
                               r["frames"], notes))
        tbl.configure(height=max(2, len(rows)))
        # notes are capped (the full text is in the line under the table) so the table's natural width stays sane
        fit_columns(tbl, self.headings, cap=220, stretch="notes", extra={"notes": 300, "target": 260})
        keep = [s for s in sel if tbl.exists(s)]
        if keep:
            tbl.selection_set(keep)
        self._selected()

    def selected(self) -> tuple[int, Block] | None:
        if not self.built:
            return None
        sel = self.table.selection()
        if not sel:
            return None
        i = int(sel[0])
        bl = self.night().blocks_obj()
        return (i, bl[i]) if 0 <= i < len(bl) else None

    def _selected(self):
        s = self.selected()
        if s is None:
            self.detail_var.set("Select a row for its notes and actions.")
            self.detail.configure(style="Hint.TLabel")
            self.status_var.set("")
            self.lock_btn.configure(text="Lock")
            return
        _i, b = s
        lines = [f"{b.name}: " + ("; ".join(str(n) for n in b.notes) if b.notes else "no notes")]
        lines += [str(c) if str(c).startswith("⚠") else f"⚠ {c}" for c in b.cautions]
        self.detail_var.set("\n".join(lines))
        self.detail.configure(style="Card.TLabel")
        self.status_var.set(STATUS_LABELS.get(b.status, "Planned"))
        self.lock_btn.configure(text="Unlock" if (b.locked or b.set_by_user) else "Lock")
        try:
            self.move_btn.state(["disabled"] if b.fixed else ["!disabled"])
        except tk.TclError:
            pass

    def _act(self, text: str):
        if not self.built:
            return
        self.act_var.set(text)
        self.act_msg.configure(style=style.status_style(style.kind_of(text)))

    # ---- actions ----
    def _need(self):
        if self.page.refuse_if_busy(self._act):
            return None
        s = self.selected()
        if s is None:
            self._act("⚠ Select a block in the table first.")
        return s

    def select_block(self, i: int):
        if not self.built:
            self.shown()
        if self.table.exists(str(i)):
            self.table.selection_set(str(i))
            self._selected()

    def lock_selected(self):
        s = self._need()
        if s is None:
            return
        i, b = s
        night = self.night()
        bl = night.blocks_obj()
        on = not (b.locked or b.set_by_user)
        bl[i].locked = on
        if not on:
            bl[i].set_by_user = False
        night.blocks = [x.to_dict() for x in bl]
        self.page.set_night(self.idx, night)
        self.select_block(i)
        self._act(f"✔ {b.name} {'locked: it keeps its times when you rebuild' if on else 'unlocked'}.")

    def remove_selected(self):
        s = self._need()
        if s is None:
            return
        i, b = s
        night = self.night()
        bl = night.blocks_obj()
        del bl[i]
        night.blocks = [x.to_dict() for x in bl]
        self.page.set_night(self.idx, night)
        self._act(f"✔ Removed {b.name} from this night (it comes back when you rebuild unless you untick it).")

    def move_selected(self):
        s = self._need()
        if s is None:
            return
        i, b = s
        if b.fixed:
            self._act(f"✖ {b.name} happens at a time set by the sky (a transit or a timed event); it cannot move "
                      "to another night.")
            return
        nights = self.app.schedule.nights
        others = [(k, night_label(n["date"], self.app.site)) for k, n in enumerate(nights) if k != self.idx]
        if not others:
            self._act("⚠ There is no other night in the schedule. Build more nights first.")
            return
        pick = ask_choice(self.app, "Move to night", f"Move {b.name} to which night? It keeps its clock time "
                                                     "(the planner checks it is still up).", [l for _k, l in others],
                          "Move")
        if not pick:
            return
        k = dict((l, kk) for kk, l in others)[pick]
        self.move_block(i, k)

    def move_block(self, i: int, k: int) -> bool:
        """Put block i on night k (same clock time, locked, set by you). The source night changes only after the
        destination night has been rebuilt with it."""
        if self.page.refuse_if_busy(self._act):
            return False
        night = self.night()
        b = night.blocks_obj()[i]
        if b.fixed:
            self._act(f"✖ {b.name} happens at a time set by the sky; it cannot move to another night.")
            return False
        dst = self.page.night(k)
        days = (date.fromisoformat(dst.date) - date.fromisoformat(night.date)).days
        shift = days * (1.0 - 3.93 / 1440.0)       # same sidereal place (a star rises ~4 min earlier each night)
        nb = Block.from_dict(b.to_dict())
        nb.start, nb.end = b.start + shift, b.end + shift
        nb.locked, nb.set_by_user, nb.status = True, True, "planned"
        for key in ("t1", "tc", "t4", "meridian"):
            if getattr(nb, key) is not None:
                setattr(nb, key, None if key != "meridian" else getattr(nb, key) + shift)
        src_date, tz = night.date, self.app.site.tz

        def remove_from_source(_new):
            for nd in self.app.schedule.nights:
                if nd.get("date") == src_date:
                    nd["blocks"] = [x for x in nd.get("blocks", [])
                                    if not (x.get("target_id") == b.target_id and abs(x.get("start", 0) - b.start)
                                            < 1e-6)]

        def message(new: NightPlan) -> str:
            got = next((x for x in new.blocks_obj() if x.target_id == b.target_id), None)
            text = f"Moved {b.name} to {night_label(dst.date, self.app.site)} ({span_text(nb.start, nb.end, tz)})."
            if got is not None and got.cautions:
                return f"⚠ {text} {got.cautions[0].lstrip('⚠ ')}"
            return "✔ " + text
        return self.page.rebuild_night(k, extra_locked=[nb], then_msg=message, on_success=remove_from_source,
                                       show=self._act)

    def edit_times(self):
        s = self._need()
        if s is None:
            return
        i, b = s
        tz = self.app.site.tz
        ans = ask_times(self.app, "Edit times", f"New start and end for {b.name} (site clock). The block is then "
                                                "marked “set by you” and keeps these times through rebuilds.",
                        sky.hhmm(b.start, tz), sky.hhmm(b.end, tz))
        if not ans:
            return
        self.set_times(i, *ans)

    def night_limits(self, night: NightPlan) -> tuple[float, float]:
        """Sunset to sunrise (the times a block may be set to), or noon to noon without a sunset."""
        d = date.fromisoformat(night.date)
        info = self.app.night_info(d) or {}
        a = info.get("sunset") or night.civil_dusk or night.dusk
        b = info.get("sunrise") or night.civil_dawn or night.dawn
        if not a or not b:
            a, b = sky.night_span(d, self.app.site)
        return a, b

    def set_times(self, i: int, start_text: str, end_text: str) -> bool:
        if self.page.refuse_if_busy(self._act):
            return False
        night = self.night()
        tz = self.app.site.tz
        try:
            sh, sm = parse_hhmm(start_text)
            eh, em = parse_hhmm(end_text)
        except ValueError:
            self._act("✖ Type the times as HH:MM on the site clock, e.g. 22:15.")
            return False
        d = date.fromisoformat(night.date)
        s, e = night_clock_jd(d, sh, sm, tz), night_clock_jd(d, eh, em, tz)
        if e <= s:
            self._act("✖ The end must come after the start.")
            return False
        lo, hi = self.night_limits(night)
        if s < lo - 0.5 / 1440 or e > hi + 0.5 / 1440:
            self._act(f"✖ {span_text(s, e, tz)} is outside the night (sunset {sky.hhmm(lo, tz)} to sunrise "
                      f"{sky.hhmm(hi, tz)}).")
            return False
        bl = night.blocks_obj()
        b = bl[i]
        for j, x in enumerate(bl):
            # blocks that stay where they are (fixed by the sky, locked, set by you, or marked) cannot be overlapped;
            # a flexible planned block simply gets re-placed around the new times by the rebuild
            kept = x.fixed or x.locked or x.set_by_user or (x.status or "planned") != "planned"
            if j != i and kept and x.start < e and s < x.end:
                self._act(f"✖ {span_text(s, e, tz)} overlaps {x.name} {span_text(x.start, x.end, tz)}. "
                          "Unlock, remove or move that block first, or pick other times.")
                return False
        nb = Block.from_dict(b.to_dict())
        nb.start, nb.end = s, e
        nb.locked = nb.set_by_user = True

        def message(new: NightPlan) -> str:
            got = next((x for x in new.blocks_obj() if x.target_id == b.target_id and x.set_by_user), None)
            text = f"{b.name} set by you: {span_text(s, e, tz)}."
            if got is not None and got.cautions:
                return f"⚠ {text} {got.cautions[0].lstrip('⚠ ')}"
            return "✔ " + text
        return self.page.rebuild_night(self.idx, extra_locked=[nb], drop=b, then_msg=message, show=self._act)

    def set_status(self, status: str):
        s = self._need()
        if s is None:
            return
        i, b = s
        night = self.night()
        bl = night.blocks_obj()
        bl[i].status = status
        night.blocks = [x.to_dict() for x in bl]
        self.page.set_night(self.idx, night)
        self.select_block(i)
        self._act(f"✔ {b.name}: {STATUS_LABELS.get(status, status)}.")

    # ---- lower part ----
    def plan_instead_ok(self, d: dict) -> bool:
        """Plan this instead helps only when another block took the room (not for "below your limits" etc.)."""
        reason = str(d.get("reason", "")).lower()
        if "your locked block" in reason:
            return False
        return any(k in reason for k in self.PLAN_INSTEAD_REASONS)

    def fill_lower(self):
        night, site = self.night(), self.app.site
        for box in (self.flats_box, self.nofit_box, self.free_box):
            for w in box.winfo_children():
                w.destroy()

        def para(box, text, st="Card.TLabel"):
            lab = ttk.Label(box, text=text, style=st, justify=tk.LEFT)
            lab.pack(anchor="w", fill=tk.X, pady=1)
            follow_wrap(lab, box, px(self.app, 24), least=120)
            return lab

        lines = list(night.flats) + list(night.darks)
        for line in lines or ["Nothing listed for this night."]:
            para(self.flats_box, str(line), "Card.TLabel" if lines else "Hint.TLabel")
        if not night.didnt_fit:
            para(self.nofit_box, "Every ticked target has a block." if night.blocks else "Nothing to report.",
                 "Hint.TLabel")
        for d in night.didnt_fit:
            row = ttk.Frame(self.nofit_box, style="Card.TFrame")
            row.pack(fill=tk.X, pady=(0, px(self.app, 4)))
            nxt = d.get("next_chance") or ""
            text = f"{d.get('name', '?')}: {d.get('reason', '')}"
            if nxt and "next chance" not in text:
                text += f"; next chance {night_label(nxt, site) if len(nxt) == 10 else nxt}"
            lab = ttk.Label(row, text=text, style="Card.TLabel", justify=tk.LEFT)
            lab.pack(side=tk.TOP, anchor="w", fill=tk.X)
            follow_wrap(lab, row, px(self.app, 8), least=120)
            tid = d.get("target_id")
            if tid and tid in self.app.targets_by_id() and self.plan_instead_ok(d):
                style.green_button(row, "Plan this instead",
                                   lambda t=tid, nm=d.get("name", ""): self.plan_instead(t, nm)).pack(
                    side=tk.TOP, anchor="w", pady=(px(self.app, 2), 0))
        if not night.free:
            para(self.free_box, "No free gaps." if night.blocks else "The whole night is free.", "Hint.TLabel")
        for gap in night.free:
            try:
                a, b = gap[0], gap[1]
            except (TypeError, IndexError, KeyError):
                continue
            mins = (b - a) * 1440
            para(self.free_box, f"Free {span_text(a, b, site.tz)}  ({mins:.0f} min)")

    def plan_instead(self, tid: str, name: str = "") -> bool:
        name = name or "that target"

        def message(new: NightPlan) -> str:
            if any(b.target_id == tid for b in new.blocks_obj()):
                return f"✔ Rebuilt with {name} first."
            why = next((d.get("reason", "") for d in new.didnt_fit if d.get("target_id") == tid), "")
            return f"✖ Still didn't fit: {why or 'no room for it tonight'}."
        return self.page.rebuild_night(self.idx, prefer={tid}, then_msg=message, show=self._act)

    # ---- drawing ----
    def _axis_range(self, night: NightPlan) -> tuple[float, float]:
        site = self.app.site
        d = date.fromisoformat(night.date)
        info = self.app.night_info(d) or {}
        a = next((j for j in (info.get("sunset"), night.civil_dusk, info.get("civil_dusk"), night.dusk) if j), None)
        b = next((j for j in (info.get("sunrise"), night.civil_dawn, info.get("civil_dawn"), night.dawn) if j), None)
        if a is None or b is None or b <= a:
            a, b = sky.local_jd(d, 18, 0, site.tz), sky.local_jd(d + timedelta(days=1), 7, 0, site.tz)
        bl = night.blocks_obj()
        if bl:
            a = min(a, min(x.start for x in bl))
            b = max(b, max(x.end for x in bl))
        return a - 12 / 1440.0, b + 12 / 1440.0

    def _gutter(self) -> int:
        labels = ("Cloud", "Temp · dew", "Spread · wind", "Moon 100 %", "Blocks", "Airmass", "Site clock")
        return max([self.norm.measure(s) for s in labels] + [self.small.measure(self.app.site.tz or "")]) + px(
            self.app, 14)

    def axis(self, canvas) -> Axis | None:
        night = self.night()
        if not night.date or not self.app.site.complete():
            return None
        w = int(canvas.winfo_width())
        if w < 50:
            return None
        j0, j1 = self._axis_range(night)
        return Axis(j0, j1, self._gutter(), w - px(self.app, 6))

    def draw_weather(self):
        c = self.wx
        c.delete("all")
        ax = self.axis(c)
        if ax is None:
            return
        night, site, lh = self.night(), self.app.site, self.lh
        H = int(c.cget("height"))
        wx = night.weather or {}
        hours = [h for h in (wx.get("hours") or []) if isinstance(h, dict) and h.get("jd")]
        hours = [h for h in hours if ax.j0 - 1 / 24 <= h["jd"] <= ax.j1 + 1 / 24]
        gl = self.norm
        if not hours:
            note = wx.get("note") or self.page.wx_note
            msg = "No forecast for this night" + (f" ({note})" if note else
                                                  " (none fetched yet, or the night is beyond the forecast)")
            c.create_rectangle(ax.x0, lh * 0.4, ax.x1, H - lh * 0.4, outline=style.LINE, fill="#f7f9fb")
            c.create_text((ax.x0 + ax.x1) / 2, H / 2, text=msg, fill=style.HINT, font=style.font(9))
            c.create_text(4, H / 2, text="Weather", anchor="w", fill=style.HINT, font=style.font(9))
            return
        ya, yb = lh * 1.3, lh * 3.0             # cloud bars (legend row above)
        yc, yd = lh * 3.3, lh * 6.0             # temperature / dew area (numbers above / below the lines)
        ye = lh * 6.75                          # spread · wind text row
        tu, lu = site.temp_unit or "C", site.length_unit or "m"
        c.create_text(4, (ya + yb) / 2, text="Cloud", anchor="w", fill=style.HINT, font=style.font(9))
        c.create_text(4, (yc + yd) / 2, text="Temp · dew", anchor="w", fill=style.HINT, font=style.font(9))
        c.create_text(4, ye, text="Spread · wind", anchor="w", fill=style.HINT, font=style.font(9))
        src = wx.get("source") or ""
        if wx.get("note"):
            src += f" ({wx['note']})"
        c.create_text(ax.x1, lh * 0.6, anchor="e", fill=style.HINT, font=style.font(9),
                      text=f"cloud %  ·  temperature (red) and dew point (blue) °{tu}  ·  spread °{tu} · wind "
                           f"{wind_unit(lu)}  ·  {src}")
        c.create_line(ax.x0, yb, ax.x1, yb, fill=style.LINE)
        pph = ax.x(ax.j0 + 1 / 24) - ax.x(ax.j0)
        step = max(1, math.ceil((gl.measure("100%") + px(self.app, 8)) / max(1.0, pph)))
        for k, h in enumerate(hours):
            xa, xb = ax.clip(ax.x(h["jd"] - 1 / 48)), ax.clip(ax.x(h["jd"] + 1 / 48))
            if xb - xa < 1:
                continue
            cl = float(h.get("cloud") or 0.0)
            top = yb - (yb - ya) * min(100.0, max(0.0, cl)) / 100.0
            if cl > 0:
                c.create_rectangle(xa + 1, top, xb - 1, yb, outline="", fill=style.mix("#dfe6ec", "#6c7a86",
                                                                                         cl / 100))
        def conv(t):
            return None if t is None else (t * 9 / 5 + 32 if tu == "F" else t)
        temps = [(h["jd"], conv(h.get("temp_c")), conv(h.get("dew_c"))) for h in hours]
        vals = [v for _j, a, b in temps for v in (a, b) if v is not None]
        if vals:
            lo, hi = min(vals), max(vals)
            if hi - lo < 4:
                lo, hi = (lo + hi) / 2 - 2, (lo + hi) / 2 + 2
            yv = lambda v: (yd - lh * 0.75) - (v - lo) / (hi - lo) * ((yd - lh * 0.75) - (yc + lh * 0.75))
            for idx, color in ((1, style.TEMP), (2, style.DEW)):
                pts = []
                for row in temps:
                    if row[idx] is not None and ax.j0 <= row[0] <= ax.j1:
                        pts += [ax.x(row[0]), yv(row[idx])]
                if len(pts) >= 4:
                    c.create_line(*pts, fill=color, width=max(1, px(self.app, 2)), smooth=False)
        step = max(1, math.ceil((gl.measure("100% ") + px(self.app, 10)) / max(1.0, pph)))
        for k, h in enumerate(hours):
            if k % step or not (ax.j0 <= h["jd"] <= ax.j1):
                continue
            x = ax.x(h["jd"])
            cl = h.get("cloud")
            if cl is not None:
                f = min(100.0, max(0.0, float(cl))) / 100.0
                top = yb - (yb - ya) * f
                inside = top - 1 < ya + lh * 0.75          # no room above the bar: write inside it
                yl = top + lh * 0.8 if inside else top - 1
                c.create_text(x, yl, text=f"{cl:.0f}%", font=style.font(9), anchor="s",
                              fill=style.text_on(style.mix("#dfe6ec", "#6c7a86", f)) if inside else style.TEXT)
            t, dw = conv(h.get("temp_c")), conv(h.get("dew_c"))
            if t is not None and vals:
                c.create_text(x, yv(t) - 2, text=f"{round(t) + 0:.0f}", fill=style.TEMP, font=style.font(9),
                              anchor="s")
            if dw is not None and vals:
                c.create_text(x, yv(dw) + 2, text=f"{round(dw) + 0:.0f}", fill=style.DEW, font=style.font(9),
                              anchor="n")
            sp = h.get("spread_c")
            if sp is None and h.get("temp_c") is not None and h.get("dew_c") is not None:
                sp = h["temp_c"] - h["dew_c"]
            wv = wind_value(h.get("wind_kmh"), lu)
            txt = (f"{round(sp * 1.8 if tu == 'F' else sp) + 0:.0f}°" if sp is not None else "–") + \
                  (f" · {round(wv) + 0:.0f}" if wv is not None else "")
            dew_lim = float(self.app.options.get("dew_spread_c", 2.5))
            c.create_text(x, ye, text=txt, font=style.font(9),
                          fill=style.WARN_FG if (sp is not None and sp < dew_lim) else style.TEXT)
        onset = wx.get("dew_onset")
        if onset and ax.j0 <= onset <= ax.j1:
            x = ax.x(onset)
            c.create_line(x, ya - lh * 0.2, x, H, fill=style.WARN_FG, dash=(4, 3), width=max(1, px(self.app, 2)))
            label = f"⚠ dew from {sky.hhmm(onset, site.tz)}"
            anchor = "w" if x + gl.measure(label) + 10 < ax.x1 else "e"
            tid = c.create_text(x + (5 if anchor == "w" else -5), lh * 0.6, text=label, anchor=anchor,
                                fill=style.WARN_FG, font=style.font(9, "bold"))
            bb = c.bbox(tid)
            if bb:
                r = c.create_rectangle(bb[0] - 3, bb[1] - 1, bb[2] + 3, bb[3] + 1, fill=style.WARN_BG,
                                       outline=style.WARN_FG)
                c.tag_lower(r, tid)
                # keep the legend line clear of the dew label
                for item in c.find_overlapping(*bb):
                    if item not in (tid, r) and c.type(item) == "text" and c.itemcget(item, "anchor") == "e" and \
                            "cloud %" in c.itemcget(item, "text"):
                        c.delete(item)

    def draw_bar(self):
        c = self.bar
        c.delete("all")
        ax = self.axis(c)
        if ax is None:
            return
        app, night, site, lh = self.app, self.night(), self.app.site, self.lh
        tz = site.tz
        ym0, ym1 = lh * 0.25, lh * 0.75           # Moon line row
        yb0, yb1 = lh * 0.9, lh * 3.0             # sky band with the blocks
        ya0, ya1 = yb1 + lh * 0.25, yb1 + lh * 1.05   # airmass bands
        yt = ya1 + lh * 0.65                      # airmass values
        yh = yt + lh * 1.25                       # hour labels
        illum = night.moon_illum
        c.create_text(4, (ym0 + ym1) / 2, text="Moon" + (f" {illum * 100:.0f} %" if illum is not None else ""),
                      anchor="w", fill=style.HINT, font=style.font(9))
        c.create_text(4, (yb0 + yb1) / 2, text="Blocks", anchor="w", fill=style.HINT, font=style.font(9))
        c.create_text(4, (ya0 + ya1) / 2, text="Airmass", anchor="w", fill=style.HINT, font=style.font(9))
        # sky: day / civil / nautical / astronomical twilight / night, from the Sun's altitude
        n = max(60, int((ax.x1 - ax.x0) / 2))
        jds = np.linspace(ax.j0, ax.j1, n + 1)
        try:
            sun = sky.sun_alt_at(site, jds)
        except Exception:
            sun = np.full(jds.shape, -30.0)

        def level(a):
            return (style.SKY_DAY if a > sky.SUN_RISE_ALT else style.SKY_CIVIL if a > -6 else style.SKY_NAUTICAL
                    if a > -12 else style.SKY_ASTRO if a > -18 else style.SKY_DARK)
        run_start, run_col = 0, level(sun[0])
        for k in range(1, n + 2):
            col = level(sun[k]) if k <= n else None
            if col != run_col:
                c.create_rectangle(ax.x(jds[run_start]), yb0, ax.x(jds[min(k, n)]), yb1, outline="", fill=run_col)
                run_start, run_col = k, col
        c.create_rectangle(ax.x0, yb0, ax.x1, yb1, outline=style.LINE)
        # planning darkness edges (labelled under the hour axis)
        dark_labels = []
        for j, lab, anc in ((night.dusk, "dark " + sky.hhmm(night.dusk, tz) if night.dusk else "", "w"),
                            (night.dawn, sky.hhmm(night.dawn, tz) + " dawn" if night.dawn else "", "e")):
            if j and ax.j0 <= j <= ax.j1:
                x = ax.x(j)
                c.create_line(x, yb0, x, yb1 + lh * 0.25, fill="#8aa0b2", dash=(2, 3))
                dark_labels.append((x, lab, anc))
        # Moon up
        try:
            moon = sky.moon_alt_at(site, jds) > sky.MOON_RISE_ALT
        except Exception:
            moon = np.zeros(jds.shape, dtype=bool)
        k = 0
        first_x = None
        while k <= n:
            if moon[k]:
                k0 = k
                while k <= n and moon[k]:
                    k += 1
                xa, xb = ax.x(jds[k0]), ax.x(jds[min(k, n)])
                c.create_line(xa, (ym0 + ym1) / 2, xb, (ym0 + ym1) / 2, fill=style.MOON, width=max(3, px(app, 4)),
                              capstyle="round")
                if first_x is None:
                    first_x = (xa, xb)
            k += 1
        if not first_x:
            c.create_text(ax.x0 + 4, (ym0 + ym1) / 2, text="Moon down all night", anchor="w", fill=style.HINT,
                          font=style.font(9))
        # blocks
        by_id = app.targets_by_id()
        for i, b in self.blocks():
            xa, xb = ax.clip(ax.x(b.start)), ax.clip(ax.x(b.end))
            if xb - xa < 2:
                continue
            fill, edge = style.mode_color(b.mode, "light"), style.mode_color(b.mode, "dark")
            locked = b.locked or b.set_by_user
            pad = lh * 0.18
            c.create_rectangle(xa, yb0 + pad, xb, yb1 - pad, fill=fill, outline=style.LOCK if locked else edge,
                               width=max(2, px(app, 3)) if locked else 1, tags=(f"blk{i}",))
            name = b.name + {"done": " ✔", "clouded": " ✖", "partial": " ½"}.get(b.status, "")
            txt = self._fit(name, xb - xa - 8, self.bold)
            if txt:
                c.create_text((xa + xb) / 2, (yb0 + yb1) / 2, text=txt, fill="#ffffff", font=style.font(9, "bold"),
                              tags=(f"blk{i}",))
            c.tag_bind(f"blk{i}", "<Button-1>", lambda _e, k=i: self.select_block(k))
            # meridian tick
            if b.meridian and ax.j0 <= b.meridian <= ax.j1:
                x = ax.x(b.meridian)
                inside = b.start <= b.meridian <= b.end
                s = lh * 0.32
                c.create_polygon(x - s, yb0 - s * 0.2, x + s, yb0 - s * 0.2, x, yb0 + s * 1.1,
                                 fill=style.MERIDIAN if inside else "#c9a36b", outline=edge)
                if inside:
                    c.create_line(x, yb0 + pad, x, yb1 - pad, fill=style.MERIDIAN, width=max(1, px(app, 2)),
                                  dash=(3, 2))
            # airmass band
            self._airmass_band(c, ax, b, by_id.get(b.target_id), xa, xb, ya0, ya1, yt)
        # hour axis
        ticks = ax.hour_ticks(tz)
        pph = (ax.x1 - ax.x0) / max(1e-6, (ax.j1 - ax.j0) * 24)
        every = 1 if pph > self.norm.measure("00:00") + px(app, 10) else (2 if pph * 2 > self.norm.measure("00")
                                                                             + px(app, 10) else 3)
        for k, j in enumerate(ticks):
            x = ax.x(j)
            c.create_line(x, yb1, x, yb1 + lh * 0.2, fill=style.HINT)
            lab = sky.hhmm(j, tz)
            if k % every == 0:
                c.create_text(x, yh, text=lab if every == 1 else lab[:2], fill=style.TEXT, font=style.font(9))
        for x, lab, anc in dark_labels:
            c.create_text(x, yh + lh * 0.95, text=lab, anchor=anc, fill=style.SIDE, font=style.font(9, "bold"))
        c.create_text(4, yh, text="Site clock", anchor="w", fill=style.HINT, font=style.font(9))
        c.create_text(4, yh + lh * 0.95, text=tz, anchor="w", fill=style.HINT, font=style.font(9))

    def _fit(self, text: str, width: float, f) -> str:
        if f.measure(text) <= width:
            return text
        while len(text) > 2:
            text = text[:-1]
            if f.measure(text + "…") <= width:
                return text + "…"
        return ""

    def _airmass_band(self, c, ax, b: Block, t: Target | None, xa, xb, y0, y1, yt):
        site = self.app.site
        mx = float(site.max_airmass or 2.0)
        k = max(2, int((xb - xa) / 3))
        js = np.linspace(max(b.start, ax.j0), min(b.end, ax.j1), k + 1)
        xs = None
        ra, dec = (t.ra, t.dec) if t is not None else (getattr(b, "ra", None), getattr(b, "dec", None))
        if ra is not None and dec is not None:
            try:
                xs = np.asarray(sky.target_airmass(ra, dec, site, js), dtype=float)
            except Exception:
                xs = None
        if xs is None:
            a, best, e = b.airmass_start, b.airmass_best, b.airmass_end
            if a is None or e is None:
                xs = np.full(js.shape, np.nan)
            else:
                mid = best if best is not None else min(a, e)
                xs = np.interp(np.linspace(0, 1, k + 1), [0, 0.5, 1], [a, mid, e])
        for q in range(k):
            x0, x1 = ax.x(js[q]), ax.x(js[q + 1])
            v = xs[q] if np.isfinite(xs[q]) else None
            c.create_rectangle(x0, y0, x1 + 0.5, y1, outline="", fill=style.airmass_color(v, mx))
        c.create_rectangle(xa, y0, xb, y1, outline=style.LINE)
        vals = [v for v in (b.airmass_start, b.airmass_best, b.airmass_end) if v is not None]
        high = (max(vals) if vals else 0) > mx + 0.005 or bool(np.nanmax(xs) > mx + 0.005 if np.isfinite(xs).any()
                                                               else False)
        f = self.small
        full = " · ".join(f"{v:.2f}" for v in (b.airmass_start, b.airmass_best, b.airmass_end) if v is not None)
        options = []
        if high:
            options += [f"⚠ high airmass  {full}", "⚠ high airmass", "⚠ high", "⚠"]
        else:
            options += [full, f"X {b.airmass_best:.2f}" if b.airmass_best else ""]
        for text in options:
            if text and f.measure(text) <= xb - xa - 4:
                c.create_text((xa + xb) / 2, yt, text=text, font=style.font(9),
                              fill=style.WARN_FG if high else style.TEXT)
                break


ROW_KEYS = {   # nightly-table column → keys tried in export.table_rows' dicts (lower case)
    "time": ("time",),
    "target": ("target", "name"),
    "radec": ("radec", "ra / dec (j2000)", "ra_dec", "ra / dec"),
    "alt": ("alt_x",),
    "mode": ("shobs-p mode", "mode_name", "mode"),
    "meridian": ("meridian",),
    "exposure": ("exposure",),
    "frames": ("frames",),
    "notes": ("notes",),
}


def own_row(b: Block, site, targets_by_id: dict) -> dict:
    """The nightly-table row from the block itself (also the fallback when export.py is missing)."""
    tz = site.tz
    t = targets_by_id.get(b.target_id)
    ra, dec = (t.ra, t.dec) if t is not None else (getattr(b, "ra", None), getattr(b, "dec", None))
    alt = "—"
    if b.airmass_best:
        best_alt = math.degrees(math.asin(min(1.0, 1.0 / max(1.0, b.airmass_best))))
        alt = f"{best_alt:.0f}° (X {b.airmass_best:.2f})"
    elif b.alt_max is not None:
        alt = f"{b.alt_max:.0f}°"
    mer = sky.hhmm(b.meridian, tz) if b.meridian else "—"
    if b.flip_in_block:
        mer += " (flip)"
    if b.exposure_s:
        e = b.exposure_s
        exp = (f"{e:g} s" if e < 10 else f"{e:.0f} s") + (" (est.)" if b.exposure_is_estimate else "")
    else:
        exp = "—"
    frames = f"~{b.frames}" if b.frames else "—"
    if b.frames_in_transit:
        frames += f" (~{b.frames_in_transit} in transit)"
    return {"time": span_text(b.start, b.end, tz), "target": b.name, "radec": fmt_radec(ra, dec),
            "mode": f"{b.mode} {MODE_NAMES.get(b.mode, '')}".strip(), "alt": alt, "meridian": mer,
            "exposure": exp, "frames": frames, "notes": "; ".join(str(n) for n in b.notes)}


# ---- 4 Export -------------------------------------------------------------------------------------------------------

class ExportPage:
    title = "Export"
    subtitle = ("ASIAIR's Plan imports coordinates only: copy the list and import it there. Exposures, times and "
                "notes are on the printable plan card.")

    def __init__(self, app: App, parent):
        self.app = app
        self.dirty = True
        self.frame = ttk.Frame(parent, style="Card.TFrame")
        top = ttk.Frame(self.frame, style="Card.TFrame")
        top.pack(fill=tk.X)
        ttk.Label(top, text="Nights", style="Bold.TLabel").pack(anchor="w")
        self.checks = ttk.Frame(top, style="Card.TFrame")
        self.checks.pack(fill=tk.X, pady=(px(app, 2), px(app, 8)))
        self.night_vars: dict[str, tk.BooleanVar] = {}
        btns = ttk.Frame(self.frame, style="Card.TFrame")
        btns.pack(fill=tk.X, pady=(0, px(app, 8)))
        style.green_button(btns, "Copy ASIAIR CSV", self.copy_csv).pack(side=tk.LEFT)
        style.green_button(btns, "Save CSV…", self.save_csv).pack(side=tk.LEFT, padx=px(app, 8))
        style.green_button(btns, "Print plan card", self.print_card).pack(side=tk.LEFT)
        self.msg_var = tk.StringVar()
        self.msg = ttk.Label(self.frame, textvariable=self.msg_var, style="Card.TLabel", justify=tk.LEFT)
        self.msg.pack(anchor="w", fill=tk.X, pady=(0, px(app, 8)))
        follow_wrap(self.msg, self.frame, px(app, 20))
        ttk.Label(self.frame, text="Plan card preview", style="Section.TLabel").pack(anchor="w")
        pf = ttk.Frame(self.frame, style="Card.TFrame")
        pf.pack(fill=tk.BOTH, expand=True, pady=(px(app, 2), 0))
        self.preview = tk.Text(pf, wrap="none", font=(style.MONO_FONT, 10), relief="solid", borderwidth=1,
                               highlightthickness=0, background="#fbfcfd", foreground=style.TEXT, padx=px(app, 8),
                               pady=px(app, 6))
        vb = ttk.Scrollbar(pf, orient=tk.VERTICAL, command=self.preview.yview)
        hb = ttk.Scrollbar(pf, orient=tk.HORIZONTAL, command=self.preview.xview)
        self.preview.configure(yscrollcommand=vb.set, xscrollcommand=hb.set)
        self.preview.grid(row=0, column=0, sticky="nsew")
        vb.grid(row=0, column=1, sticky="ns")
        hb.grid(row=1, column=0, sticky="ew")
        pf.rowconfigure(0, weight=1)
        pf.columnconfigure(0, weight=1)

    def refresh(self):
        for w in self.checks.winfo_children():
            w.destroy()
        nights = self.app.schedule.nights
        old = {k: v.get() for k, v in self.night_vars.items()}
        self.night_vars = {}
        if not nights:
            ttk.Label(self.checks, text="No schedule yet: build one on the Schedule page.", style="Hint.TLabel").pack(
                anchor="w")
        tonight = self.app.tonight().isoformat()
        for n in nights:
            iso = n.get("date", "")
            var = tk.BooleanVar(value=old.get(iso, iso >= tonight or len(nights) == 1))
            var.trace_add("write", lambda *_: self.update_preview())
            self.night_vars[iso] = var
            nb = len(n.get("blocks", []))
            ttk.Checkbutton(self.checks, text=f"{night_label(iso, self.app.site)} ({nb} block{'s' if nb != 1 else ''})",
                            variable=var, style="Card.TCheckbutton").pack(side=tk.LEFT, padx=(0, px(self.app, 12)))
        if nights and not any(v.get() for v in self.night_vars.values()):
            for v in self.night_vars.values():
                v.set(True)
        self.update_preview()

    def chosen(self) -> list[NightPlan]:
        return [NightPlan.from_dict(n) for n in self.app.schedule.nights if self.night_vars.get(n.get("date"),
                                                                                                tk.BooleanVar()).get()]

    def _weather_by_date(self, nights):
        return {n.date: n.weather for n in nights if n.weather}

    def _msg(self, text):
        set_msg(self.msg_var, self.msg, text)

    def update_preview(self):
        ex = mod("export")
        nights = self.chosen()
        self.preview.configure(state="normal")
        self.preview.delete("1.0", tk.END)
        if ex is None:
            text = missing("export")
        elif not nights:
            text = "Tick one or more nights above."
        else:
            try:
                text = ex.plan_card_text(nights, self.app.site, self.app.settings, self._weather_by_date(nights),
                                         targets_by_id=self.app.targets_by_id())
            except Exception as exc:
                settings.log(f"plan_card_text: {exc!r}\n{traceback.format_exc()}")
                text = f"✖ The preview could not be made: {exc}"
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")

    def csv_text(self) -> str | None:
        ex = mod("export")
        if ex is None:
            self._msg(missing("export"))
            return None
        nights = self.chosen()
        if not nights:
            self._msg("⚠ Tick one or more nights first.")
            return None
        try:
            return ex.asiair_csv(nights, self.app.targets_by_id(), self.app.site, optics=self.app.optics)
        except Exception as exc:
            settings.log(f"asiair_csv: {exc!r}\n{traceback.format_exc()}")
            self._msg(f"✖ The CSV could not be made: {exc}")
            return None

    def count_targets(self, text: str) -> tuple[int, list]:
        ex = mod("export")
        if ex is not None and hasattr(ex, "asiair_targets"):
            try:
                rows, skipped = ex.asiair_targets(self.chosen(), self.app.targets_by_id(), self.app.site)
                return len(rows), list(skipped)
            except Exception:
                pass
        return self.count_rows(text), []

    @staticmethod
    def count_rows(text: str) -> int:
        lines = [l for l in text.splitlines() if l.strip()]
        return max(0, len(lines) - 1)

    def copy_csv(self):
        text = self.csv_text()
        if text is None:
            return
        try:
            self.app.clipboard_clear()
            self.app.clipboard_append(text)
            self.app.update_idletasks()
        except tk.TclError as exc:
            self._msg(f"✖ The clipboard did not take the list ({exc}).")
            return
        n, skipped = self.count_targets(text)
        msg = f"✔ Copied {n} target{'s' if n != 1 else ''}. In ASIAIR's Plan: Add → Import, and paste."
        if skipped:
            msg = f"⚠ Copied {n} target{'s' if n != 1 else ''}; left out (no coordinates): {', '.join(skipped)}."
        self._msg(msg)
        settings.log(f"ASIAIR CSV copied ({n} rows)")

    def save_csv(self):
        text = self.csv_text()
        if text is None:
            return
        nights = self.chosen()
        name = f"SHOBS-SCHED_{nights[0].date}.csv" if nights else "SHOBS-SCHED.csv"
        path = filedialog.asksaveasfilename(parent=self.app, title="Save the ASIAIR coordinate list",
                                            defaultextension=".csv", initialfile=name,
                                            filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        self.write_csv(path, text)

    def write_csv(self, path: str, text: str) -> bool:
        try:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
        except OSError as exc:
            self._msg(f"✖ Not saved: {exc.strerror or exc}.")
            return False
        self._msg(f"✔ Saved {os.path.basename(path)} ({self.count_rows(text)} targets).")
        return True

    def print_card(self, open_browser: bool = True) -> str | None:
        ex = mod("export")
        if ex is None:
            self._msg(missing("export"))
            return None
        nights = self.chosen()
        if not nights:
            self._msg("⚠ Tick one or more nights first.")
            return None
        try:
            html = ex.plan_card_html(nights, self.app.site, self.app.settings, self._weather_by_date(nights),
                                     targets_by_id=self.app.targets_by_id())
        except Exception as exc:
            settings.log(f"plan_card_html: {exc!r}\n{traceback.format_exc()}")
            self._msg(f"✖ The plan card could not be made: {exc}")
            return None
        path = os.path.join(settings.cache_dir(), "plan_card.html")
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(html)
        except OSError as exc:
            self._msg(f"✖ The plan card could not be written ({exc.strerror or exc}).")
            return None
        if open_browser and not self.app.testing:
            try:
                if sys.platform == "win32":
                    os.startfile(path)          # handles spaces and non-ASCII in the user's folder name
                    opened = True
                else:
                    import pathlib
                    opened = webbrowser.open(pathlib.Path(path).resolve().as_uri())
            except Exception as exc:
                opened, why = False, str(exc)
            else:
                why = "no browser answered"
            if not opened:
                self._msg(f"✖ The browser did not open ({why}). The plan card is saved at {path}: open it "
                          "yourself and print it from there.")
                return path
        self._msg("✔ The plan card is open in your browser; print it from there.")
        return path


# ---- self-test ------------------------------------------------------------------------------------------------------

def selftest_ui() -> int:
    """Open the app on a temporary home with the golden targets, build, draw each page, copy the CSV, close."""
    import json
    import tempfile
    import urllib.request
    tmp = tempfile.mkdtemp(prefix="shobs_sched_ui_")
    os.environ["SHOBS_SCHED_HOME"] = tmp
    os.environ["SHOBS_P_SETTINGS"] = os.path.join(tmp, "no_shobs_p.json")

    def offline(*_a, **_k):
        raise OSError("network is off in the self-test")
    urllib.request.urlopen = offline
    fx = os.path.join(APP_DIR, "tests", "fixtures", "golden_oct19_24.json")
    problems = []
    if os.path.exists(fx):
        with open(fx, encoding="utf-8") as fh:
            g = json.load(fh)
        s = settings.default_settings()
        s["site"].update(g["site"])
        s["optics"].update(g["optics"])
        settings.save_settings(s)
        settings.save_targets([Target.from_dict(t) for t in g["targets"]])
    app = App(testing=True)
    try:
        for i in range(4):
            app.show_page(i)
            app.update()
        if mod("planner") is not None and app.site.complete():
            app.pages[2].build(date(2026, 10, 19), 5)
            app.jobs.wait(300)
            app.show_page(2)
            app.update()
            if not app.schedule.nights:
                problems.append("the schedule did not build: " + app.pages[2].msg_var.get()
                                + " " + app.pages[2].ind.var.get())
        else:
            problems.append("planner.py missing or the site incomplete: build skipped")
        app.show_page(3)
        app.update()
        if mod("export") is not None and app.schedule.nights:
            app.pages[3].copy_csv()
            if not app.pages[3].msg_var.get().startswith("✔"):
                problems.append("CSV: " + app.pages[3].msg_var.get())
    except Exception as exc:
        problems.append(f"{exc!r}\n{traceback.format_exc()}")
    finally:
        app.destroy()
    for p in problems:
        print("UI self-test:", p)
    print("UI self-test " + ("passed." if not problems else "found problems."))
    return 0 if not problems else 1


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--selftest-ui" in argv:
        return selftest_ui()
    style.enable_windows_dpi()
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
