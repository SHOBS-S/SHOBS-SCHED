#!/usr/bin/env python3
"""SHOBS-SCHED — Shiloh Hill Observatory Photometry Scheduler (the app).

Plans each night's targets (transits, variable stars and campaigns), for one night or a rolling seven, and exports
the plan for ASIAIR and NINA. Four pages, as the numbered steps in the blue sidebar:

  1 Setup     site, optics and camera, mount, limits, horizon, weather source
  2 Targets   the target list in three boxes (Campaigns, Transits, Variables), each row with its next event in the
              planned nights; the Plan tick decides what is built
  3 Schedule  one tab per night: night plot (airmass, twilight, Moon, weather), nightly table, didn't fit, free time
  Credits and sources (sidebar button): copyright, license and every source SCHED uses
  4 OBSPlan   the plan card (print it), per-night ASIAIR and NINA files

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
import re
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

import settings  # noqa: E402
import sky  # noqa: E402
import style  # noqa: E402
from models import (PRIORITIES, STATUS_LABELS, Block, NightPlan, Optics, Planet,  # noqa: E402
                    Schedule, Site, Target, TYPE_NAMES, block_type_code, type_name)
from style import px  # noqa: E402

STEPS = ("Setup", "Targets", "Schedule", "OBSPlan")
# the Targets page boxes (1.1.0, John 24 Oct): a target's box is its type (kind); suggested, hand-added and kept
# targets share it, and the Plan tick decides what is built. Group keys are saved nowhere, so old files load unchanged.
GROUPS = (("campaign", "Campaigns"), ("transit", "Transits"), ("variable", "Variables"))
DARKNESS = (("Astronomical (Sun −18°)", -18.0), ("Nautical (Sun −12°)", -12.0))


def darkness_label(sun_alt) -> str:
    """The Setup drop-down's text for a planning Sun altitude: the two named darknesses, or "Sun −15°" for another
    value (SHOBS-P's finder may use one)."""
    try:
        v = float(sun_alt)
    except (TypeError, ValueError):
        return DARKNESS[0][0]
    for label, val in DARKNESS:
        if abs(val - v) < 1e-6:
            return label
    return f"Sun {fmt_num(v, 1).replace('-', '−')}°"


def darkness_value(label: str) -> float:
    if label in dict(DARKNESS):
        return dict(DARKNESS)[label]
    m = re.search(r"[-−]?\d+(?:\.\d+)?", str(label or ""))
    try:
        v = float(m.group(0).replace("−", "-")) if m else -18.0
    except ValueError:
        v = -18.0
    return v if -24.0 <= v <= -6.0 else -18.0
MODE_CHOICES = (("Variables (V)", "V"), ("Transits (T)", "T"), ("Discovery (D)", "D"))
# what a star that is not (only) a known planet or VSX variable becomes: (label, kind, mode)
KIND_CHOICES = (("Campaign (a star you visit on many nights)", "campaign", "V"),
                ("Variable star", "variable", "V"),
                ("Star field (a campaign)", "campaign", "D"),
                ("Transit target (type its ephemeris later)", "transit", "T"))
_KIND_LEGACY = {"Variables (V)": ("campaign", "V"), "Discovery (D)": ("campaign", "D"),
                "Transits (T)": ("transit", "T"),
                # the v1.0 / early 1.1.0 labels (they named SHOBS-P's modes)
                "Campaign, reduced in Variables (V)": ("campaign", "V"), "Variable star (V)": ("variable", "V"),
                "Discovery field (D)": ("campaign", "D"),
                "Transit target, ephemeris typed later (T)": ("transit", "T")}
STAR_ITSELF = "The star itself (a campaign or a variable)"
GROUP_CHOICES = (("Transit", "transit"), ("Variable", "variable"), ("Campaign", "campaign"))


def kind_mode_of(label: str):
    for lab, kind, mode in KIND_CHOICES:
        if lab == label:
            return kind, mode
    return _KIND_LEGACY.get(label)


def planet_pick(name: str) -> str:
    """The "which mode?" choice for one of a star's transiting planets."""
    return f"{name}: transiting planet (a transit target)"


def and_list(names) -> str:
    names = [str(n) for n in names]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1] if names else ""


def no_transit_rule(planet: dict, pad: float = 0.0) -> dict:
    return {"kind": "no_transit", "planet": dict(planet), "pad_min": float(pad),
            "note": "No data while a planet transits", "value": None}


def planet_key(name: str) -> str:
    """"HD219134 b" and "hd 219134-b" compare equal."""
    return re.sub(r"[\s\-_]+", "", str(name or "")).lower()


def bare_letter(text: str) -> str:
    """A bare planet letter ("b", " C ") → "b"; anything else → ""."""
    s = str(text or "").strip()
    return s.lower() if re.fullmatch(r"[b-zB-Z]", s) else ""


def lookup_star_planets(tg, name: str, ra=None, dec=None, vmag=None):
    """(host_planets(...) result, "") or (None, the reason in plain words); (None, "") when this copy of targets.py
    has no planet-host lookup. Never raises (it runs in a background job whose answer is shown, not an error)."""
    fn = getattr(tg, "host_planets", None) if tg is not None else None
    if fn is None:
        return None, ""
    try:
        return fn(name, ra, dec, vmag), ""
    except Exception as exc:              # the Archive did not answer and nothing is cached, or no name/position
        return None, str(exc) or exc.__class__.__name__


# the Status drop-down: a blank entry on top clears a mark (back to planned, unmarked; John 24 Oct), then
# Done / Partial / Clouded out
STATUS_CHOICES = ("",) + tuple(v for k, v in STATUS_LABELS.items() if k != "planned")
STATUS_KEY = {v: k for k, v in STATUS_LABELS.items() if k != "planned"}
STATUS_KEY[""] = "planned"
STATUS_KEY[STATUS_LABELS["planned"]] = "planned"


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
    """The Targets box a target sits in: its type (1.1.0: suggested, hand-added and kept targets share one box; the
    Plan tick decides what is built)."""
    return t.kind if t.kind in ("transit", "variable", "campaign") else "variable"


def suggested_when(t: Target) -> float | None:
    """A suggestion's date (JD): a transit's ingress, a variable's next event or first good night; None unknown."""
    info = t.suggest_info if isinstance(t.suggest_info, dict) else {}
    try:
        w = float(info.get("when"))
    except (TypeError, ValueError):
        return None
    return w if math.isfinite(w) else None


def _jd(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def row_order(group: str, now: float | None = None, events_by_id: dict | None = None):
    """Sort key of a Targets box (John, 23–24 Oct): Transits soonest transit first (by ingress); Variables night by
    night, each night's timed minima / maxima first (by time), then its any-time stars; rows with no event in the
    planned nights last, by name; Campaigns by name (rows stay put when ticked). events_by_id = {target id:
    targets.next_events(...)}; a row whose event is not known yet falls back on its suggestion's own date."""
    evs = events_by_id or {}

    def fallback(t):
        w = suggested_when(t)
        if w is None or (now is not None and w < now):
            return None
        info = t.suggest_info if isinstance(t.suggest_info, dict) else {}
        return {"night": str(info.get("night") or ""), "t": w, "t1": w,
                "any": bool(info.get("kind") == "any" or info.get("time_uncertain"))}

    def event(t):
        ev = evs.get(t.id)
        if not isinstance(ev, dict) or "none" in ev or "text" in ev:
            return fallback(t) if not isinstance(ev, dict) else None
        return ev

    def night_iso(ev):
        d = ev.get("night")
        return d.isoformat() if isinstance(d, date) else str(d or "")
    if group == "variable":
        def key(t):
            ev = event(t)
            w = _jd(ev.get("t")) if ev else None
            if ev is None or w is None:
                return (1, "", 0, 0.0, t.name.lower())
            return (0, night_iso(ev), 1 if ev.get("any") else 0, w, t.name.lower())
        return key
    if group == "transit":
        def key(t):
            ev = event(t)
            w = _jd(ev.get("t1")) if ev else None
            return (0, w, t.name.lower()) if w is not None else (1, 0.0, t.name.lower())
        return key
    return lambda t: (0, 0.0, t.name.lower())


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

def busy_bar_of(widget):
    """The busy bar of the window (or dialog) a widget is in, or None (House Style: one per window, lower left)."""
    try:
        return getattr(widget.winfo_toplevel(), "busy_bar", None)
    except (tk.TclError, AttributeError):
        return None


class Jobs:
    """Run work in a thread; its result comes back to the main thread through a queue (Tk is not thread-safe).
    Every job shows on the busy bar of the window it was started from (the indicator's window, else the main one)
    with its words; the indicator beside the button only shows the ✔ / ⚠ / ✖ result."""

    def __init__(self, root):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.active = 0
        self._poll()

    def run(self, work, done=None, indicator=None, label: str = "Working", counted: bool = False,
            steps: bool = False):
        """work() runs in a thread; work(progress) when counted (progress(i, n) puts "i of n" in the busy bar's
        words, the bar stays back-and-forth), work(step) when steps (step("Getting the forecast") changes the words).
        Then done(result, exc) runs here, after the job has left the busy bar."""
        self.active += 1
        bar = busy_bar_of(indicator) if indicator is not None else None
        if bar is None:
            bar = getattr(self.root, "busy_bar", None)
        tok = bar.begin(label + "…") if bar is not None else None
        if indicator is not None:
            indicator.start(label)

        def progress(i, n):
            self.q.put(("words", (bar, tok), f"{label}… {i} of {n}"))

        def step(text):
            self.q.put(("words", (bar, tok), str(text).rstrip(".…") + "…"))

        def target():
            try:
                res = work(progress) if counted else (work(step) if steps else work())
                self.q.put(("done", (done, indicator, label, bar, tok), (res, None)))
            except Exception as exc:
                settings.log(f"{label} failed: {exc!r}\n{traceback.format_exc()}")
                self.q.put(("done", (done, indicator, label, bar, tok), (None, exc)))

        threading.Thread(target=target, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, a, b = self.q.get_nowait()
                if kind == "words":
                    bar, tok = a
                    if bar is not None:
                        bar.words(tok, b)
                    continue
                done, ind, label, bar, tok = a
                res, exc = b
                self.active -= 1
                if bar is not None:
                    try:
                        bar.end(tok)
                    except tk.TclError:
                        pass
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
    """The result line beside a job's button: empty while the job runs (the window's busy bar, lower left, shows
    what it is doing), then ✔ Done / ⚠ / ✖ reason. Not a delay signal itself (House Style, 26 Oct 2026)."""

    def __init__(self, parent):
        super().__init__(parent, style="Card.TFrame")
        self.var = tk.StringVar()
        self.label = ttk.Label(self, textvariable=self.var, style="Card.TLabel")
        self.label.pack(side=tk.LEFT)
        self._clear = None

    def start(self, text: str = ""):
        if self._clear:
            self.after_cancel(self._clear)
            self._clear = None
        self.var.set("")
        self.label.configure(style="Card.TLabel")

    def finish(self, msg: str = "✔ Done", linger_ms: int = 6000):
        try:
            if self._clear:
                self.after_cancel(self._clear)
                self._clear = None
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


def _clamp_to_screen(app, x: int, y: int, w: int, h: int, sw: int, sh: int) -> tuple[int, int]:
    """Keep a dialog placed over the main window on the screen. Tk only knows the PRIMARY screen's size: when the
    main window is on another monitor (its corner outside the primary screen) the position is left as it is."""
    try:
        ax, ay = app.winfo_rootx(), app.winfo_rooty()
    except tk.TclError:
        ax, ay = 0, 0
    if not (0 <= ax < sw and 0 <= ay < sh):
        return x, y
    return min(max(0, x), max(0, sw - w)), min(max(0, y), max(0, sh - h - 40))


def _show_modal(app, win, focus=None):
    win.update_idletasks()
    try:
        sw, sh = app.winfo_screenwidth(), app.winfo_screenheight()
        w, h = min(win.winfo_reqwidth(), int(sw * 0.9)), min(win.winfo_reqheight(), int(sh * 0.85))
        x = app.winfo_rootx() + max(0, (app.winfo_width() - w) // 2)
        y = app.winfo_rooty() + max(0, (app.winfo_height() - h) // 3)
        x, y = _clamp_to_screen(app, x, y, w, h, sw, sh)
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
        self.show_page(0)                # always opens on Setup (John 24 Oct)
        if self.site_name_note:          # said once at start too
            self.set_status(f"⚠ Site name cleared: it was SHOBS-P's telescope name (“{self.site_name_note}”); type "
                            "your observatory's name on the Setup page.")
        if not testing:                  # kept transits still on the composite ephemeris: upgrade them (background)
            self.after(800, lambda: self.pages[1].refine_ephemerides(quiet=True))

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
        self.site_name_note = str(s.pop("site_name_cleared", "") or "")    # said once on Setup, never saved
        self.settings = s
        self.p_taken = list(taken)
        self.p_values = self._shobs_p_values()
        self.targets: list[Target] = settings.load_targets()
        self.schedule: Schedule = settings.load_schedule()
        settings.log(f"opened; {len(self.targets)} targets, {len(self.schedule.nights)} nights"
                     + (f"; SHOBS-P settings read ({', '.join(taken)})" if taken else ""))

    def _shobs_p_values(self) -> dict:
        """SHOBS-P's own value of each field it can share ("site.lat" → 35.303, "site.min_alt" → 30 from its
        transit finder), to tell an override from a value typed back to SHOBS-P's."""
        return settings.shobs_p_values(self.shobs_p)

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

    def plan_nights(self) -> list[date]:
        """The nights the Targets page searches and shows events for (John 24 Oct): first night plan_start (never
        before tonight; None = tonight) for options["nights"] nights (1–7). The Schedule page's Nights and first
        night share it."""
        tonight = self.tonight()
        start = getattr(self, "plan_start", None)
        if not isinstance(start, date) or start < tonight:
            start = tonight
        try:
            n = min(7, max(1, int(self.options.get("nights", 7) or 7)))
        except (TypeError, ValueError):
            n = 7
        return [start + timedelta(days=k) for k in range(n)]

    def set_plan_span(self, start: date | None = None, n: int | None = None, source=None):
        """Change the shared first night and/or number of nights (saved: the number; the first night lasts the
        session) and bring the other page's controls in line."""
        if start is not None:
            self.plan_start = start
        if n is not None:
            self.options["nights"] = min(7, max(1, int(n)))
            settings.save_settings(self.settings)
        for p in getattr(self, "pages", []):
            if p is not source and hasattr(p, "span_changed"):
                try:
                    p.span_changed()
                except Exception as exc:
                    settings.log(f"span_changed {getattr(p, 'title', p)}: {exc!r}")

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
        # the busy bar at the left end of the status strip (House Style: the one delay signal), then the status line
        row = ttk.Frame(status, style="Status.TFrame")
        row.pack(fill=tk.X, padx=px(self, 12), pady=(px(self, 4), 0))
        self.busy_bar = style.BusyBar(row, label_style="Status.TLabel", frame_style="Status.TFrame")
        self.busy_bar.pack(side=tk.LEFT, padx=(0, px(self, 12)))
        self.status_label = ttk.Label(row, textvariable=self.status_var, style="Status.TLabel")
        self.status_label.pack(side=tk.LEFT, anchor="w")
        cr = mod("credits")
        # the copyright and sources line under the status line (as SHOBS-P's; the full list: Credits and sources)
        self.credits_line = ttk.Label(status, text=cr.STATUS_LINE if cr else "© Shiloh Hill Observatory",
                                      style="Status.TLabel", foreground=style.SIDE_HINT)
        self.credits_line.pack(anchor="w", fill=tk.X, padx=px(self, 12), pady=(0, px(self, 4)))
        follow_wrap(self.credits_line, status, px(self, 24))

        outer = ttk.Frame(self)
        outer.pack(fill=tk.BOTH, expand=True)
        side = ttk.Frame(outer, style="Side.TFrame")
        side.pack(side=tk.LEFT, fill=tk.Y)
        self.side = side
        ttk.Label(side, text="Shiloh Hill\nObservatory\nPhotometry\nScheduler", style="SideTitle.TLabel", justify=tk.LEFT).pack(
            anchor="w", padx=px(self, 16), pady=(px(self, 18), px(self, 2)))
        ttk.Label(side, text="(SHOBS-SCHED)", style="SideHint.TLabel").pack(anchor="w", padx=px(self, 16))
        ttk.Label(side, text="Plans each night's\nphotometry: transits,\nvariable stars and\ncampaigns", style="SideHint.TLabel",
                  justify=tk.LEFT).pack(anchor="w", padx=px(self, 16), pady=(px(self, 8), px(self, 12)))
        ttk.Label(side, text="STEPS", style="SideHead.TLabel").pack(anchor="w", padx=px(self, 16))
        self.step_buttons = []
        for i, name in enumerate(STEPS):
            b = ttk.Button(side, text=f"{i + 1}   {name}", style="Step.TButton", command=lambda n=i: self.show_page(n))
            b.pack(fill=tk.X, padx=px(self, 10), pady=px(self, 2))
            self.step_buttons.append(b)
        ttk.Label(side, text=f"Version {settings.APP_VERSION}", style="SideHint.TLabel").pack(
            side=tk.BOTTOM, anchor="w", padx=px(self, 16), pady=px(self, 10))
        self.credits_button = ttk.Button(side, text="Credits and sources", style="Step.TButton",
                                         command=self.show_credits)
        self.credits_button.pack(side=tk.BOTTOM, fill=tk.X, padx=px(self, 10), pady=(0, px(self, 4)))
        # (no color legend in the sidebar: John 24 Oct; the Schedule page's plot legend says what the colors mean)

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
        self.credits_page = CreditsPage(self, holder)
        self.credits_page.frame.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.page_index = None
        self.on_credits = False

    def _place_window(self):
        try:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            w, h = int(sw * 0.8), int(sh * 0.82)
            self.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 3)}")
            self.minsize(min(w, px(self, 900)), min(h, px(self, 560)))
        except tk.TclError:
            pass

    def show_page(self, i: int):
        if (getattr(self, "page_index", None) == 0 and i != 0 and hasattr(self, "pages")
                and not getattr(self, "on_credits", False)):           # (left already on the way to Credits)
            if not self.pages[0].leave():
                return
        self.on_credits = False
        if hasattr(self, "credits_button"):
            self.credits_button.configure(style="Step.TButton")
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

    def show_credits(self):
        """The Credits and sources page (not a step: Back returns to the page you came from)."""
        if getattr(self, "page_index", None) == 0 and not getattr(self, "on_credits", False):
            if not self.pages[0].leave():
                return
        self.on_credits = True
        page = self.credits_page
        self.title_var.set(page.title)
        self.subtitle_var.set(page.subtitle)
        for b in self.step_buttons:
            b.configure(style="Step.TButton")
        self.credits_button.configure(style="StepOn.TButton")
        page.frame.tkraise()

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

    def targets_changed(self, planned: bool = True):
        """The target list changed. planned=False: nothing the planner uses changed (only unticked suggestions came
        or went, or a suggestion was kept as it was): the pages refresh, the plan is not marked out of date."""
        if planned and self.schedule.nights:      # the saved schedule was built from the old target list
            self.pages[2].setup_stale = True
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

    def _tag(self, f, key):
        """The small gray source tag beside a field ("from SHOBS-P", "from FITS", "set here"; none = default)."""
        self.tags[key] = ttk.Label(f, text="", style="Tag.TLabel")
        self.vars[key].trace_add("write", lambda *_a: self._tags_later())

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
            self._tag(f, key)
        return e, u

    def _radios(self, box, row, label, key, choices, tag=False):
        ttk.Label(box, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", pady=px(self.app, 2))
        var = tk.StringVar()
        self.vars[key] = var
        f = ttk.Frame(box, style="Card.TFrame")
        f.grid(row=row, column=1, sticky="w", padx=px(self.app, 6))
        for text, value in choices:
            ttk.Radiobutton(f, text=text, value=value, variable=var, style="Card.TRadiobutton").pack(
                side=tk.LEFT, padx=(0, px(self.app, 10)))
        if tag:
            self._tag(f, key)
        return var

    def _hint(self, box, row, text):
        """A gray explanation under a field, wrapping with the box."""
        h = ttk.Label(box, text=text, style="Hint.TLabel", justify=tk.LEFT, wraplength=px(self.app, 420))
        h.grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, px(self.app, 4)))
        return h

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
        self._tag(f, "site.tz")
        self.tz_hint = ttk.Label(box, text="", style="Hint.TLabel", justify=tk.LEFT, wraplength=px(self.app, 320))
        self.tz_hint.grid(row=5, column=1, sticky="w", padx=px(self.app, 6))
        self.vars["site.tz"].trace_add("write", lambda *_a: self._tz_hint())
        self._radios(box, 6, "Temperatures", "site.temp_unit", (("°C", "C"), ("°F", "F")))
        self._radios(box, 7, "Lengths", "site.length_unit", (("m", "m"), ("ft", "ft")))
        self.vars["site.temp_unit"].trace_add("write", lambda *_: self._units_changed())
        self.vars["site.length_unit"].trace_add("write", lambda *_: self._units_changed())
        return box

    def _optics_group(self, g):
        box = self._box(g, "Telescope and camera")
        self._entry(box, 0, "Telescope", "optics.telescope", width=20)
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
        self._tag(f, "optics.binning")
        for k in ("optics.focal_mm", "optics.pixel_um", "optics.binning"):
            self.vars[k].trace_add("write", lambda *_: self._scale_hint())
        self._entry(box, 5, "Camera", "optics.camera", width=20)
        self._radios(box, 6, "Sensor", "optics.color", (("Color (one-shot)", "color"), ("Mono", "mono")),
                     tag=True)
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
        self._entry(box, 13, "Per-frame overhead\n(download/save, all targets)", "optics.frame_overhead_s",
                    "s; empty = 3 s", width=6)
        self._hint(box, 14, "Added to EVERY target's exposure: the camera's download and save time only (about 3 s on "
                            "an ASIAIR). A target's own “Seconds between frames” (Edit on the Targets page) is its "
                            "whole frame-to-frame interval and wins for that target.")
        self._entry(box, 15, "Usual camera angle (rotator)", "optics.camera_angle_deg", "°; empty = 0", width=6)
        self._entry(box, 16, "Defocus widens stars ×", "options.defocus_fwhm_factor",
                    "(“defocus” targets)", width=6)
        f = ttk.Frame(box, style="Card.TFrame")
        f.grid(row=17, column=0, columnspan=2, sticky="w", pady=(px(self.app, 6), px(self.app, 2)))
        ttk.Button(f, text="Read from a FITS frame…", command=self.read_fits).pack(side=tk.LEFT)
        ttk.Label(f, text="fills the camera fields from its header", style="Hint.TLabel").pack(
            side=tk.LEFT, padx=(px(self.app, 8), 0))
        self.fits_var = tk.StringVar()
        self.fits_msg = ttk.Label(box, textvariable=self.fits_var, style="Card.TLabel", justify=tk.LEFT,
                                  wraplength=px(self.app, 440))
        self.fits_msg.grid(row=18, column=0, columnspan=2, sticky="w")
        self.fits_msg.grid_remove()
        return box

    def _est_tag(self, var, warn):
        if var.get():
            warn.pack(side=tk.LEFT, padx=px(self.app, 8))
        else:
            warn.pack_forget()

    def _mount_group(self, g):
        box = self._box(g, "Mount and limits")
        self._radios(box, 0, "Mount", "site.mount", (("Equatorial (flips)", "GEM"), ("Alt-az", "altaz")),
                     tag=True)
        self._entry(box, 1, "Tracks past the meridian", "site.past_meridian_min", "min before it must flip")
        self._entry(box, 2, "Minimum altitude", "site.min_alt", "°")
        self._entry(box, 3, "Maximum airmass", "site.max_airmass", "")
        ttk.Label(box, text="Darkness to plan in", style="Card.TLabel").grid(row=4, column=0, sticky="w",
                                                                             pady=px(self.app, 2))
        self.vars["site.sun_alt"] = tk.StringVar()
        f = ttk.Frame(box, style="Card.TFrame")
        f.grid(row=4, column=1, sticky="w", padx=px(self.app, 6), pady=px(self.app, 2))
        self.dark_box = ttk.Combobox(f, textvariable=self.vars["site.sun_alt"], values=[d for d, _v in DARKNESS],
                                     state="readonly", width=24)
        self.dark_box.pack(side=tk.LEFT)
        self._tag(f, "site.sun_alt")
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
        self._tz_guessed = not tz
        if not tz:
            tz = settings.guess_tz()
        v["site.tz"].set(tz)
        self._tz_hint()
        for k in ("aperture_mm", "focal_mm", "pixel_um", "gain", "e_per_adu", "full_well_e", "linear_limit_adu"):
            v[f"optics.{k}"].set(fmt_num(op.get(k)))
        v["optics.binning"].set(str(int(op.get("binning") or 1)))
        v["optics.camera"].set(op.get("camera") or "")
        v["optics.telescope"].set(op.get("telescope") or "")
        v["optics.color"].set("color" if op.get("color_camera", True) else "mono")
        v["optics.frame_overhead_s"].set(fmt_num(op.get("frame_overhead_s")))
        v["optics.camera_angle_deg"].set(fmt_num(op.get("camera_angle_deg")))
        v["options.defocus_fwhm_factor"].set(fmt_num(opts.get("defocus_fwhm_factor", 1.5)))
        for key, var in self.est.items():
            var.set(key in (op.get("estimates") or []))
        v["site.mount"].set("altaz" if site.get("mount") == "altaz" else "GEM")
        v["site.past_meridian_min"].set(fmt_num(site.get("past_meridian_min", 5.0)))
        v["site.min_alt"].set(fmt_num(site.get("min_alt", 30.0)))
        v["site.max_airmass"].set(fmt_num(site.get("max_airmass", 2.0)))
        sa = darkness_label(site.get("sun_alt", -18.0))
        self.dark_box.configure(values=[d for d, _v in DARKNESS] + ([sa] if sa not in dict(DARKNESS) else []))
        v["site.sun_alt"].set(sa)
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
        self.fits_pending = {}
        self._units_changed()
        self._scale_hint()
        self._loaded_text = {k: str(var.get()).strip() for k, var in self.vars.items()}
        self._show_provenance()
        self._snapshot = self.form_state()

    # ---- where each value came from ----
    FORM_TO_SETTING = {"optics.color": "optics.color_camera"}
    SETTING_TO_FORM = {"optics.color_camera": "optics.color"}

    def _text_of(self, key: str, value) -> str:
        """The text the form shows for a value of this field (to tell a FITS value from one typed over it)."""
        if key == "optics.color":
            return "color" if value else "mono"
        if key == "optics.binning":
            return str(int(value or 1))
        if key == "site.sun_alt":
            return darkness_label(value)
        return fmt_num(value) if not isinstance(value, str) else value.strip()

    def source_of(self, key: str) -> str:
        """Where the value now in this Setup field came from: "from FITS", "set here", "from SHOBS-P" or "" (default,
        or nothing yet). A value typed over another becomes "set here" at once."""
        dotted = self.FORM_TO_SETTING.get(key, key)
        cur = str(self.vars[key].get()).strip()
        fits = dict(settings.from_fits(self.app.settings))
        fits.update(getattr(self, "fits_pending", {}) or {})
        if dotted in fits and cur == self._text_of(key, fits[dotted]):
            return "from FITS"
        loaded = getattr(self, "_loaded_text", {}).get(key)
        if loaded is not None and cur != loaded:
            return "set here"
        if dotted in set(self.app.settings.get("set_here") or []):
            return "set here"
        if dotted in settings.FINDER_DOTTED:
            # limits the finder seeded (seeded once: SHOBS-P's finder today may say something else)
            return "from SHOBS-P" if dotted in set(self.app.p_taken or []) else ""
        theirs = (self.app.p_values or {}).get(dotted)
        if theirs not in (None, "") and (cur == self._text_of(key, theirs)
                                         or (key == "site.elevation_m" and dotted in set(self.app.p_taken or []))):
            return "from SHOBS-P"                        # SHOBS-P's live value (e.g. typed back to it)
        return ""

    def _tags_later(self):
        if getattr(self, "_loading", False) or getattr(self, "_tags_pending", False):
            return
        self._tags_pending = True
        try:
            self.frame.after_idle(self._show_tags)
        except tk.TclError:
            self._tags_pending = False

    def _show_tags(self):
        self._tags_pending = False
        for key, tag in self.tags.items():
            try:
                text = self.source_of(key)
                if text:
                    tag.configure(text=text)
                    if not tag.winfo_manager():
                        tag.pack(side=tk.LEFT, padx=(px(self.app, 6), 0))
                else:
                    tag.pack_forget()
            except tk.TclError:
                pass

    def _show_provenance(self):
        app = self.app
        # worded from where the values on the page actually came from (the tags)
        src = {self.FORM_TO_SETTING.get(k, k): self.source_of(k) for k in self.tags}
        from_p = [w for w, pre in (("site", "site."), ("telescope", "optics."))
                  if any(v == "from SHOBS-P" and d.startswith(pre) and d not in settings.FINDER_DOTTED
                         for d, v in src.items())]
        finder = [d for d, v in src.items() if v == "from SHOBS-P" and d in settings.FINDER_DOTTED]
        fits = [d for d, v in src.items() if v == "from FITS"]
        bits = []
        if app.shobs_p is not None and from_p:
            when = app.settings.get("shobs_p_read") or "?"
            what = and_list(from_p)
            bits.append(f"{what[:1].upper() + what[1:]} read from SHOBS-P (settings of {when}). Change them there, "
                        "or override here.")
        if fits:
            bits.append("Camera values read from a FITS frame.")
        if finder:
            bits.append("The minimum altitude, darkness and the Suggested-transits filter started from SHOBS-P's "
                        "Tonight's-transits finder (once; later changes there do not move them).")
        if bits:
            text = "✔ " + " ".join(bits) + " Tags: from SHOBS-P, from FITS, set here."
        else:
            text = ("Enter your site and equipment once (or read the camera from a FITS frame). SHOBS-P, if you add "
                    "it later, can share these.")
        cleared = getattr(app, "site_name_note", "")
        if cleared:
            text = (f"⚠ Site name cleared: it was SHOBS-P's telescope name (“{cleared}”); type your observatory's "
                    f"name. " + text.lstrip("✔ "))
        if text.startswith(("✔", "⚠")):
            set_msg(self.prov_var, self.prov, text)
        else:
            self.prov_var.set(text)
            self.prov.configure(style="Card.TLabel")
        self._show_tags()

    def _fits_shown(self, key: str, value) -> str:
        """A FITS value in the summary's words ("2901 mm", "Color")."""
        if key == "optics.color":
            return "Color" if value in (True, "color") else "Mono"
        text = self._text_of(key, value) if not isinstance(value, str) else value
        if not text:
            return "empty"
        unit = {"optics.pixel_um": " µm", "optics.focal_mm": " mm", "optics.camera_angle_deg": "°",
                "optics.e_per_adu": " e⁻/ADU"}.get(key, "")
        return text + unit

    def read_fits(self, path: str | None = None):
        """Fill the camera and optics fields from a FITS frame's header (only the header is read). The values are
        tagged "from FITS" and win over SHOBS-P's once saved; the summary lists what changed (old → new)."""
        fh = mod("fits_header")
        if fh is None:
            set_msg(self.fits_var, self.fits_msg, missing("fits_header"))
            self.fits_msg.grid()
            return None
        if path is None:
            path = filedialog.askopenfilename(
                parent=self.app, title="A FITS frame from this camera and telescope",
                filetypes=[("FITS frames", "*.fits *.fit *.fts *.FITS *.FIT *.FTS"), ("All files", "*.*")])
        if not path:
            return None
        name = os.path.basename(path)
        try:
            vals, notes, absent = fh.read_camera_values(path)
        except fh.FitsError as exc:
            set_msg(self.fits_var, self.fits_msg, f"✖ {name} could not be read: {exc}. Nothing changed.")
            self.fits_msg.grid()
            return None
        changed, same = [], []
        self.fits_pending = dict(getattr(self, "fits_pending", {}) or {})
        for dotted, label, _kw in fh.FIELDS:
            if dotted not in vals:
                continue
            key = self.SETTING_TO_FORM.get(dotted, dotted)
            new = self._text_of(key, vals[dotted])
            old = str(self.vars[key].get()).strip()
            if old != new:
                old_shown = self._fits_shown(key, old) if old else "empty"
                changed.append(f"{label}: {old_shown} → {self._fits_shown(key, vals[dotted])}")
                self.vars[key].set(new)
            else:
                same.append(label)
            self.fits_pending[dotted] = vals[dotted]
        gone = [f"{label} ({kw})" for _d, label, kw in fh.FIELDS if kw in absent]
        cam = vals.get("optics.camera")
        lines = [f"Read the header of {name}" + (f" ({cam})" if cam else "") + ". Press Save to keep these values."]
        if changed:
            lines.append("Changed:")
            lines += ["    " + c for c in changed]
        if same:
            lines.append("Already the same: " + ", ".join(same) + ".")
        lines += [n + "." for n in notes]
        if gone:
            lines.append("Not in this frame: " + ", ".join(gone) + ".")
        lines[0] = ("⚠ " if gone else "✔ ") + lines[0]
        self._fits_lines = lines
        set_msg(self.fits_var, self.fits_msg, "\n".join(lines))
        self.fits_msg.grid()
        settings.log(f"FITS header read from {name}: " + (", ".join(changed) or "nothing changed")
                     + (f"; not in the frame: {', '.join(absent)}" if absent else ""))
        self._show_provenance()
        return vals

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

    def _tz_hint(self):
        """Under the time zone: what every time in SHOBS-SCHED will read as ("Local time (UTC−4) now"), and whether
        the zone is only a guess."""
        tz = str(self.vars["site.tz"].get()).strip()
        if not sky.zone_ok(tz):
            text = "Not a zone name: pick one from the list."
        else:
            text = f"Times show as {sky.local_label(tz)} now"
            text += ("; guessed from this computer's clock: check it and Save." if getattr(self, "_tz_guessed", False)
                     else ".")
        try:
            self.tz_hint.configure(text=text)
        except tk.TclError:
            pass

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
        op["frame_overhead_s"] = num("optics.frame_overhead_s", "Per-frame overhead", 0, 600, required=False,
                                     what="seconds")
        op["camera_angle_deg"] = num("optics.camera_angle_deg", "Usual camera angle", 0, 360, required=False,
                                     what="degrees")
        dfw = num("options.defocus_fwhm_factor", "Defocus widens stars", 1, 20, required=False, what="times")
        opts["defocus_fwhm_factor"] = dfw if dfw is not None else 1.5
        site["mount"] = v["site.mount"].get() or "GEM"
        pm = num("site.past_meridian_min", "Tracking past the meridian", 0, 120, required=False, what="minutes")
        site["past_meridian_min"] = pm if pm is not None else 0.0
        ma = num("site.min_alt", "Minimum altitude", 0, 89, what="(degrees)")
        site["min_alt"] = ma if ma is not None else 30.0
        mx = num("site.max_airmass", "Maximum airmass", 1.0, 8.0, required=False)
        site["max_airmass"] = mx if mx is not None else 2.0
        site["sun_alt"] = darkness_value(v["site.sun_alt"].get())
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
        new = {"site": site, "optics": op, "options": opts}

        def unmark(dotted):
            s["set_here"] = [x for x in (s.get("set_here") or []) if x != dotted]

        # values read from a FITS frame stay "from FITS" (and win over SHOBS-P) while the field still holds the
        # frame's value; a value typed over one is the user's own ("set here")
        fits = settings.from_fits(s)
        fits.update(getattr(self, "fits_pending", {}) or {})
        for dotted, val in list(fits.items()):
            sec, _, key = dotted.partition(".")
            if _same(val, new.get(sec, {}).get(key)):
                unmark(dotted)
            else:
                del fits[dotted]
                settings.mark_set_here(s, dotted)
        s["from_fits"] = fits
        self.fits_pending = {}
        # only a field whose text was changed since it was shown changes its source (an untouched form saves with
        # the same tags it opened with)
        loaded = getattr(self, "_loaded_text", {}) or {}
        seeded = settings.from_finder(s)
        defaults = {"site": Site().to_dict(), "optics": Optics().to_dict(), "options": dict(settings.DEFAULT_OPTIONS)}
        for key in self.tags:
            dotted = self.FORM_TO_SETTING.get(key, key)
            sec, _, fld = dotted.partition(".")
            if sec not in new or dotted in fits or key not in loaded:
                continue
            if str(self.vars[key].get()).strip() == loaded[key]:
                continue
            val = new[sec].get(fld)
            if dotted in settings.FINDER_DOTTED:
                # a limit the finder seeded (or SCHED's own) typed over: the user's own, never seeded again
                seeded.pop(dotted, None)
                settings.mark_set_here(s, dotted)
            elif dotted in self.app.p_values:
                if _same(self.app.p_values[dotted], val):   # typed back to SHOBS-P's value: follow SHOBS-P again
                    unmark(dotted)
                else:
                    settings.mark_set_here(s, dotted)
            elif val in (None, "") or (isinstance(val, str) and not val.strip()) or \
                    _same(val, defaults[sec].get(fld)):
                unmark(dotted)                            # back to empty / the default
            else:
                # the user's own value: a SHOBS-P installed later must not overwrite it
                settings.mark_set_here(s, dotted)
        s["from_finder"] = seeded
        # only a real change of site / optics / options makes the schedule stale (a no-edit Save, or one after a
        # FITS frame that changed nothing, does not)
        changed = any(_differs(s.get(sec) or {}, val) for sec, val in (("site", site), ("optics", op),
                                                                         ("options", opts)))
        s["site"], s["optics"] = site, op
        s["options"] = opts
        if not settings.save_settings(s):
            set_msg(self.msg_var, self.msg, "✖ Not saved: the settings file is not writable.")
            return False
        settings.log("settings saved")
        self.app.site_name_note = ""                      # said; the user has seen the page and saved
        self._tz_guessed = False
        self._tz_hint()
        self._loaded_text = {k: str(var.get()).strip() for k, var in self.vars.items()}
        self._show_provenance()
        if getattr(self, "_fits_lines", None) and self.fits_var.get():
            lines = list(self._fits_lines)
            lines[0] = lines[0].replace("Press Save to keep these values.", "Saved.")
            self._fits_lines = lines
            set_msg(self.fits_var, self.fits_msg, "\n".join(lines))
        if changed:
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
        if getattr(self, "fits_pending", None):          # a FITS frame read, even one that changed no value
            return True
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

class TargetTable:
    """One box of the Targets page (Campaigns, Transits or Variables): a table with its own scroll bars and mouse
    wheel (1.1.0, John 24 Oct: the one shared list's scroll bars did not scroll). The Plan tick is the first column
    (no mode square)."""

    def __init__(self, page: "TargetsPage", parent, group: str, cols: tuple, height: int):
        self.page, self.group = page, group
        app = page.app
        self.cols = cols
        self.headings = dict(cols)
        self.box = ttk.LabelFrame(parent, text="", style="Group.TLabelframe", padding=(px(app, 6), px(app, 2)))
        tf = ttk.Frame(self.box, style="Card.TFrame")
        tf.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(tf, columns=[c for c, _h in cols], show="headings", selectmode="extended",
                                 height=height)
        for c, h in cols:
            centre = c in ("plan", "vmag")
            self.tree.heading(c, text=h, anchor="center" if centre else "w")
            self.tree.column(c, anchor="center" if centre else "w")
        self.vbar = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=self.tree.yview)
        self.hbar = ttk.Scrollbar(tf, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=self.vbar.set, xscrollcommand=self.hbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.hbar.grid(row=1, column=0, sticky="ew")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        self.hint = ttk.Label(tf, text="", style="Hint.TLabel")
        self.tree.tag_configure("off", foreground=style.HINT)
        self.tree.bind("<Button-1>", lambda e: page._click(self, e), add="+")
        self.tree.bind("<Double-1>", lambda e: page._double(self, e))
        self.tree.bind("<<TreeviewSelect>>", lambda _e: page._selected(self))
        # the wheel scrolls THIS list (Windows / macOS <MouseWheel>, X11 buttons 4 and 5)
        self.tree.bind("<MouseWheel>", self._wheel)
        self.tree.bind("<Button-4>", lambda e: self._wheel(e, -1))
        self.tree.bind("<Button-5>", lambda e: self._wheel(e, 1))

    def _wheel(self, event, units=None):
        """Scroll the list under the pointer (whichever Tk delivered the event to: some Windows Tk builds send the
        wheel to the focused list)."""
        tree = self.tree
        try:
            under = self.tree.winfo_containing(event.x_root, event.y_root)
            for tbl in self.page.tables.values():
                if under is tbl.tree:
                    tree = tbl.tree
                    break
        except (tk.TclError, AttributeError, KeyError, TypeError):
            pass
        try:
            if units is None:
                d = int(getattr(event, "delta", 0))
                if d == 0:
                    return "break"
                units = -1 if d > 0 else 1
                if sys.platform == "win32" and abs(d) >= 120:
                    units = -int(d / 120) * 2
            tree.yview_scroll(units, "units")
        except tk.TclError:
            pass
        return "break"

    def colref(self, name: str) -> str:
        return f"#{[c for c, _h in self.cols].index(name) + 1}"

    def show_hint(self, text: str):
        """The empty box's hint over the table area ("none yet: press Find suggested transits")."""
        if text:
            self.hint.configure(text=text)
            self.hint.place(relx=0.0, rely=0.0, x=px(self.page.app, 8), y=px(self.page.app, 30))
        else:
            self.hint.place_forget()


class TargetsPage:
    title = "Targets"
    subtitle = ("What you want to measure. Tick Plan (☑) to plan a target, click its Priority to change it, select a "
                "row for its details, and double-click it to edit. Each list shows the next event in the planned "
                "nights; drag the bar between Transits and Variables to share the room.")
    # (key, heading) per box; Plan always first (John 24 Oct)
    COLS = {
        "campaign": (("plan", "Plan"), ("name", "Name"), ("vmag", "V"), ("next", "Next useful event"),
                     ("week", "This week"), ("priority", "Priority"), ("radec", "RA / Dec (J2000)"),
                     ("source", "Source")),
        "transit": (("plan", "Plan"), ("name", "Name"), ("vmag", "V"), ("period", "Period"), ("night", "Night"),
                    ("ingress", "Ingress"), ("mid", "Mid"), ("egress", "Egress"), ("priority", "Priority"),
                    ("radec", "RA / Dec (J2000)"), ("source", "Source")),
        "variable": (("plan", "Plan"), ("name", "Name"), ("vmag", "V"), ("vtype", "Type"), ("period", "Period"),
                     ("night", "Night"), ("event", "Next min / max"), ("priority", "Priority"),
                     ("radec", "RA / Dec (J2000)"), ("source", "Source")),
    }
    STRETCH = {"campaign": "next", "transit": "source", "variable": "event"}
    FIT = {"name": 260, "next": 420, "source": 320, "event": 300, "night": 260}
    EMPTY = {"transit": "none yet: Add a planet, or press Find suggested transits",
             "variable": "none yet: Add a variable star, or press Find suggested variables",
             "campaign": "none: a campaign is a star or field you visit on many nights"}

    def __init__(self, app: App, parent):
        self.app = app
        self.dirty = True
        self.events: dict[str, dict] = {}          # target id → targets.next_events(...) for the planned nights
        self._events_key = None
        self._tc_cache: dict = {}
        self._selecting = False
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
        # the nights the Find buttons search and the lists' next events cover (shared with the Schedule page)
        span = ttk.Frame(self.frame, style="Card.TFrame")
        span.pack(fill=tk.X, pady=(0, px(app, 6)))
        ttk.Label(span, text="Nights", style="Bold.TLabel").pack(side=tk.LEFT)
        ttk.Label(span, text="from", style="Card.TLabel").pack(side=tk.LEFT, padx=(px(app, 8), px(app, 4)))
        self.span_start = tk.StringVar()
        self.span_start_box = ttk.Combobox(span, textvariable=self.span_start, state="readonly", width=22)
        self.span_start_box.pack(side=tk.LEFT)
        self.span_start_box.bind("<<ComboboxSelected>>", lambda _e: self._span_picked())
        ttk.Label(span, text="for", style="Card.TLabel").pack(side=tk.LEFT, padx=(px(app, 8), px(app, 4)))
        self.span_n = tk.StringVar()
        nb = ttk.Combobox(span, textvariable=self.span_n, values=[str(k) for k in range(1, 8)], state="readonly",
                          width=3)
        nb.pack(side=tk.LEFT)
        nb.bind("<<ComboboxSelected>>", lambda _e: self._span_picked())
        self.span_hint = ttk.Label(span, text="", style="Hint.TLabel")
        self.span_hint.pack(side=tk.LEFT, padx=(px(app, 8), 0))
        self._span_dates: list[date] = []

        bottom = ttk.Frame(self.frame, style="Card.TFrame")
        bottom.pack(side=tk.BOTTOM, fill=tk.X, pady=(px(app, 6), 0))
        ttk.Button(bottom, text="Edit…", command=self.edit_selected).pack(side=tk.LEFT)
        ttk.Button(bottom, text="Remove", command=self.remove_selected).pack(side=tk.LEFT, padx=px(app, 6))
        ttk.Button(bottom, text="Keep (a suggestion stays when you Find again)", command=self.keep_selected).pack(
            side=tk.LEFT)
        style.green_button(bottom, "Find suggested transits…", self.ask_transit_search).pack(side=tk.RIGHT)
        style.green_button(bottom, "Find suggested variables…", self.ask_variable_search).pack(
            side=tk.RIGHT, padx=(0, px(app, 8)))
        # the searches' spinner and result on a line of their own above the buttons (a long message never squeezes
        # the buttons)
        find_row = ttk.Frame(self.frame, style="Card.TFrame")
        find_row.pack(side=tk.BOTTOM, fill=tk.X, pady=(px(app, 4), 0))
        self.find_ind = JobIndicator(find_row)
        self.find_ind.pack(side=tk.RIGHT)
        self.msg_var = tk.StringVar()
        self.msg = ttk.Label(self.frame, textvariable=self.msg_var, style="Card.TLabel", justify=tk.LEFT)
        self.msg.pack(side=tk.BOTTOM, fill=tk.X, anchor="w")
        follow_wrap(self.msg, self.frame, px(app, 20))
        self.detail_var = tk.StringVar()
        self.detail = ttk.Label(self.frame, textvariable=self.detail_var, style="Card.TLabel", justify=tk.LEFT)
        self.detail.pack(side=tk.BOTTOM, fill=tk.X, anchor="w", pady=(px(app, 6), 0))
        follow_wrap(self.detail, self.frame, px(app, 20))

        # Campaigns: a short box on top; Transits and Variables share the rest, with a bar to drag between them
        self.tables: dict[str, TargetTable] = {}
        self.tables["campaign"] = TargetTable(self, self.frame, "campaign", self.COLS["campaign"], height=3)
        self.tables["campaign"].box.pack(fill=tk.X, pady=(0, px(app, 4)))
        self.paned = ttk.PanedWindow(self.frame, orient=tk.VERTICAL)
        self.paned.pack(fill=tk.BOTH, expand=True)
        for key in ("transit", "variable"):
            tbl = TargetTable(self, self.paned, key, self.COLS[key], height=6)
            self.tables[key] = tbl
            self.paned.add(tbl.box, weight=1)
        self._editor = None

    @property
    def tree(self):
        """The table holding the current selection (else Transits): old callers' single table."""
        for tbl in self.tables.values():
            if tbl.tree.selection():
                return tbl.tree
        return self.tables["transit"].tree

    def table_of(self, tid: str) -> "TargetTable | None":
        for tbl in self.tables.values():
            if tbl.tree.exists(tid):
                return tbl
        return None

    def select_target(self, tid: str):
        """Select one target's row (its box scrolled to it); the other boxes' selections are cleared."""
        tbl = self.table_of(tid)
        if tbl is None:
            return
        self._selecting = True
        try:
            for other in self.tables.values():
                if other is not tbl and other.tree.selection():
                    other.tree.selection_remove(*other.tree.selection())
            tbl.tree.selection_set(tid)
            tbl.tree.see(tid)
        finally:
            self._selecting = False
        self.show_detail()

    def _selected(self, tbl: "TargetTable"):
        """A selection in one box clears the others (one target's details at a time; Edit/Remove/Keep act on what is
        selected)."""
        if not self._selecting and tbl.tree.selection():
            self._selecting = True
            try:
                for other in self.tables.values():
                    if other is not tbl and other.tree.selection():
                        other.tree.selection_remove(*other.tree.selection())
            finally:
                self._selecting = False
        self.show_detail()

    # ---- table ----
    def refresh(self):
        self._fill_span()
        self.fill()
        self._start_next_events()

    def nights(self) -> list[date]:
        return self.suggestion_nights()

    def fill(self):
        app = self.app
        when = sky.now_jd()
        today = app.tonight()
        by_group: dict[str, list[Target]] = {k: [] for k, _n in GROUPS}
        for t in app.targets:
            by_group[group_of(t)].append(t)
        clock = ""
        site = app.site
        nights = self.nights()
        if site.complete() and nights:
            d0, d1 = nights[0], nights[-1]
            a = sky.night_window(d0, site)
            b = sky.night_window(d1, site)
            if a and b:
                clock = sky.local_label(site.tz, a[0], b[1])
        span = (f"{len(nights)} planned night{'s' if len(nights) != 1 else ''}" if nights else "")
        for key, name in GROUPS:
            tbl = self.tables[key]
            tree = tbl.tree
            sel = list(tree.selection())
            top = tree.yview()[0] if tree.get_children() else 0.0
            tree.delete(*tree.get_children())
            rows = by_group[key]
            planned = sum(1 for t in rows if t.enabled)
            label = f"{name}  ({len(rows)}{f', {planned} planned' if rows else ''})"
            if key != "campaign" and span:
                label += f"   ·   next event in the {span}" + (f", {clock}" if clock else "")
            elif key == "campaign" and clock:
                label += f"   ·   {clock}"
            tbl.box.configure(text=label)
            tbl.show_hint("" if rows else self.EMPTY[key])
            for t in sorted(rows, key=row_order(key, when, self.events)):   # rows stay put when ticked
                tree.insert("", tk.END, iid=t.id, values=self.row_values(t, when, today),
                            tags=() if t.enabled else ("off",))
            keep = [i for i in sel if tree.exists(i)]
            if keep:
                self._selecting = True
                try:
                    tree.selection_set(keep)
                finally:
                    self._selecting = False
            if top:
                tree.yview_moveto(top)
            fit_columns(tree, tbl.headings, cap=300, stretch=self.STRETCH[key], extra=self.FIT)
        self.show_detail()

    def _source_text(self, t: Target, when: float) -> str:
        src = f"✔ {t.source}" if t.source else "typed"
        if t.suggested:
            src = f"suggested · {t.source or 'SCHED'}"
        pl = t.planet_obj()
        if pl and t.kind == "transit":
            tc = self.tc_text(t, pl, when)
            if tc.startswith("⚠"):
                src += f"  ·  ⚠ Tc {tc[2:]}"
        return src

    def _night_text(self, d) -> str:
        if isinstance(d, date):
            return night_label(d.isoformat(), self.app.site)
        return str(d or "")

    def event_cells(self, t: Target) -> dict:
        """The event columns of a row from self.events: transits night / ingress / mid / egress; variables night /
        event; campaigns next. "…" while the planned nights are still being searched."""
        site = self.app.site
        if not site.complete():
            wait = "set the site first"
        else:
            wait = "…"
        ev = self.events.get(t.id)
        tz = site.tz
        if t.kind == "campaign":
            return {"next": (ev or {}).get("text") or (ev or {}).get("none") or wait}
        if isinstance(ev, dict) and "none" not in ev and not (
                ("t1" in ev and "tc" in ev and "t4" in ev) if t.kind == "transit" else ("t" in ev)):
            ev = None                        # computed for the target's old type (it was just moved): wait
        if not isinstance(ev, dict) and t.kind == "variable" and t.suggested and site.complete():
            # not searched yet: a suggested variable shows the event its search found
            info = t.suggest_info if isinstance(t.suggest_info, dict) else {}
            w = _jd(info.get("when"))
            if w is not None and w >= sky.now_jd():
                try:
                    nd = date.fromisoformat(str(info.get("night") or ""))
                except ValueError:
                    nd = sky.night_date_of(w, tz)
                ev = {"night": nd, "t": w, "event": str(info.get("event") or "max"),
                      "any": info.get("kind") in ("any", "cycle") or bool(info.get("time_uncertain"))}
        if not isinstance(ev, dict):
            return {"night": wait, "ingress": "", "mid": "", "egress": "", "event": ""}
        if "none" in ev:
            return {"night": ev["none"], "ingress": "", "mid": "", "egress": "", "event": ""}
        night = self._night_text(ev.get("night"))
        if t.kind == "transit":
            if not ev.get("full"):
                night += " (partial)"
            return {"night": night, "ingress": sky.hhmm(ev["t1"], tz), "mid": sky.hhmm(ev["tc"], tz),
                    "egress": sky.hhmm(ev["t4"], tz)}
        # the search's verdict on timing stays with the star after Keep (review round 4): an imprecise VSX
        # minimum is never shown as a confident time, even when the elements themselves are "timed"
        info = t.suggest_info if isinstance(t.suggest_info, dict) else {}
        if ev.get("any") or info.get("time_uncertain"):
            event = "any time"
            sig = info.get("timing_sigma_min")
            if info.get("time_uncertain"):
                if isinstance(sig, (int, float)) and math.isfinite(sig):
                    event += (f"  ⚠ time uncertain ±{sig / 60:.0f} h" if sig >= 90
                              else f"  ⚠ time uncertain ±{sig:.0f} min")
                else:
                    event += "  ⚠ no usable timing"
            elif info.get("kind") == "cycle":
                event = "any time (a whole cycle)"
        else:
            event = f"{ev.get('event', 'max')} {sky.hhmm(ev['t'], tz)}"
        return {"night": night, "event": event}

    def row_values(self, t: Target, when: float, today: date) -> tuple:
        cells = self.event_cells(t)
        base = {"plan": "☑" if t.enabled else "☐", "name": t.name,
                "vmag": f"{t.vmag:.1f}" if t.vmag is not None else "—",
                "priority": t.priority, "radec": fmt_radec(t.ra, t.dec), "source": self._source_text(t, when)}
        tg = mod("targets")
        ptext = getattr(tg, "period_text", None) if tg is not None else None
        if t.kind == "transit":
            pl = t.planet_obj()
            base["period"] = ptext(pl.period) if (ptext and pl) else (f"{pl.period:.5f} d" if pl and pl.period
                                                                      else "—")
        elif t.kind == "variable":
            el = t.elements_obj()
            base["period"] = ptext(el.period) if (ptext and el) else "—"
            base["vtype"] = (el.vtype if el and el.vtype else "—")
        else:
            base["week"] = self.progress_text(t, today) if t.goal_per_week else "—"
        base.update(cells)
        return tuple(base.get(c, "") for c, _h in self.COLS[group_of(t)])

    def detail_text(self, t: Target) -> str:
        """The selected target's extras: transit depth, duration, ephemeris and Tc ±; campaign rules, goal and
        progress; notes."""
        bits = [f"{t.name} ({type_name(t.kind)}{', suggested' if t.suggested else ''})"]
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
        info = t.suggest_info if (t.suggested and isinstance(t.suggest_info, dict)) else {}
        if info.get("score") is not None:
            what = "transit" if t.kind == "transit" else "variable"
            extra = f", S/N {float(info['snr']):.1f}" if info.get("snr") is not None else ""
            if info.get("good_nights") is not None and info.get("nights"):
                extra += f", good on {info['good_nights']} of {info['nights']} nights"
            bits.append(f"suggested {what}: score {float(info['score']):.2f}{extra}")
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
        """Each target's next event in a dark window of the planned nights (targets.next_events), in the background;
        the rows are refilled (and re-sorted) when it is done."""
        app = self.app
        site = app.site
        tg = mod("targets")
        if not site.complete() or tg is None or not hasattr(tg, "next_events"):
            return
        nights = self.nights()
        when = sky.now_jd()
        key = (tuple((t.id, t.kind, t.ra, t.dec, repr(t.planet), repr(t.elements), repr(t.rules))
                     for t in app.targets),
               (site.lat, site.lon, site.tz, site.min_alt, site.max_airmass, site.sun_alt, repr(site.horizon)),
               tuple(d.isoformat() for d in nights), int(when * 96))          # refreshed every 15 min at most
        if key == self._events_key:
            return
        self._events_key = key
        self._events_gen = getattr(self, "_events_gen", 0) + 1
        gen = self._events_gen                # a slower earlier search never overwrites a newer one
        targets = [Target.from_dict(t.to_dict()) for t in app.targets]

        def work():
            out = {}
            for t in targets:
                try:
                    out[t.id] = tg.next_events(t, site, nights, when)
                except Exception as exc:
                    settings.log(f"next_events {t.name}: {exc!r}")
                    out[t.id] = {"none": "—"}
            return out

        def done(res, exc):
            if exc is not None or res is None or gen != self._events_gen:
                return
            self.events = res
            self.fill()
        app.jobs.run(work, done, None, "Working out each target's next event")

    # ---- clicks ----
    def _click(self, tbl: "TargetTable", event):
        tree = tbl.tree
        if tree.identify_region(event.x, event.y) != "cell":
            return None
        iid = tree.identify_row(event.y)
        col = tree.identify_column(event.x)
        t = self.app.targets_by_id().get(iid)
        if t is None:
            return None
        name = tree.column(col, "id")
        if name == "plan":
            self.toggle(iid)
            return "break"
        if name == "priority":
            self._edit_priority(tbl, iid, col)
            return "break"
        return None

    def _double(self, tbl: "TargetTable", event):
        iid = tbl.tree.identify_row(event.y)
        if iid in self.app.targets_by_id() and tbl.tree.identify_column(event.x) not in (
                tbl.colref("plan"), tbl.colref("priority")):
            self.edit(iid)

    def toggle(self, tid: str):
        t = self.app.targets_by_id().get(tid)
        if t is None:
            return
        t.enabled = not t.enabled
        self.app.save_targets()
        self.app.targets_changed()
        self.fill()
        self.select_target(tid)
        set_msg(self.msg_var, self.msg, f"✔ {t.name} will {'be planned' if t.enabled else 'not be planned'}. "
                                        "Build the schedule to apply.")
        if t.enabled:
            self.refine_ephemerides([tid])

    def refine_ephemerides(self, ids=None, quiet: bool = False):
        """Transit targets still carrying the Archive composite table's ephemeris (from Suggested) get the most
        precise published one, in the background (TOI-4329 b, 22 Oct: composite ±190 min vs ExoFOP ±2 min)."""
        tg = mod("targets")
        if tg is None or not hasattr(tg, "refine_transit_target"):
            return
        comp = getattr(tg, "COMPOSITE_REF", "Archive composite (planet list)")
        todo = [Target.from_dict(t.to_dict()) for t in self.app.targets
                if (ids is None or t.id in ids) and t.kind == "transit" and t.planet
                and (t.planet.get("reference") or "") == comp]
        if not todo:
            return
        when = sky.now_jd()

        def work():
            out = []
            for t in todo:
                before = dict(t.planet)
                if tg.refine_transit_target(t, when):
                    out.append((t.id, t.planet, before))
            return out

        def done(res, exc):
            if exc is not None or not res:
                return
            names = []
            by_id = self.app.targets_by_id()
            for tid, planet, before in res:
                t = by_id.get(tid)
                if t is None or t.planet != before:
                    continue                          # edited meanwhile: the user's version wins
                t.planet = planet
                names.append(t.name)
                settings.log(f"ephemeris for {t.name}: composite → {planet.get('reference')}")
            if not names:
                return
            self.app.save_targets()
            self.app.targets_changed()
            self.fill()
            self._start_next_events()
            set_msg(self.msg_var, self.msg, f"✔ Most precise ephemeris now used for {and_list(names)} (was the "
                                            "Archive's planet-list value). Build the schedule to apply.")
        self.app.jobs.run(work, done, None, "Checking transit ephemerides at the NASA Exoplanet Archive")

    def _edit_priority(self, tbl: "TargetTable", iid, col):
        if self._editor is not None:
            self._editor.destroy()
        bbox = tbl.tree.bbox(iid, col)
        if not bbox:
            return
        x, y, w, h = bbox
        cb = ttk.Combobox(tbl.tree, values=PRIORITIES, state="readonly")
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
        return [by[i] for tbl in self.tables.values() for i in tbl.tree.selection() if i in by]

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
            pos = {"ra": getattr(exc, "ra", None), "dec": getattr(exc, "dec", None),
                   "vmag": getattr(exc, "vmag", None), "main_id": label}
            if "exoplanet" in str(getattr(exc, "source", "")).lower():
                # a planet that does not transit (or has no transit ephemeris): never an empty transit target —
                # offer its star instead (same position), with the star's transiting planets if it has any
                host = re.sub(r"\s+[b-z]$", "", label) or label
                why = str(getattr(exc, "otype", "") or "a planet that does not transit")
                self.star_lookup(host, dict(pos, main_id=host),
                                 f"{label} is {why}, so it cannot be a transit target.", typed_transit=False)
                return
            self.star_lookup(name, pos, f"{label} was found, but it is not a known transiting planet or VSX variable.")
            return
        choices = getattr(exc, "choices", None) or getattr(exc, "planets", None) or getattr(exc, "names", None)
        if choices:
            names = [c if isinstance(c, str) else (c.get("name") if isinstance(c, dict) else getattr(c, "name", str(c)))
                     for c in choices]
            system = getattr(exc, "system", "") or name
            transiting = set(getattr(exc, "transiting", None) or ())
            others = []
            if transiting:                      # only transiting planets can be Transits targets
                others = [n for n in names if n not in transiting]
                names = [n for n in names if n in transiting]
            text = (f"{system} has more than one planet. Add one of them as a transit target, or the star itself "
                    "(for example a campaign that keeps clear of the planets' transits)?")
            if others:
                text += f" ({', '.join(others)} {'does' if len(others) == 1 else 'do'} not transit.)"
            pick = ask_choice(self.app, "Which planet, or the star?", text, names + [STAR_ITSELF], "Add")
            if pick == STAR_ITSELF:
                self.add_star_lookup(system)
            elif pick:
                self.add(pick)
            else:
                self.add_ind.finish("⚠ Not added.")
            return
        self.add_ind.finish(f"✖ {exc}" if str(exc) else f"✖ Could not add {name} ({exc.__class__.__name__}).")

    def add_star_lookup(self, system: str):
        """The host star of a planet system as its own target: its position from SIMBAD (cached) and its planets
        from the Archive, then ask what it is (a campaign, a variable or a Discovery field)."""
        tg = mod("targets")
        if tg is None or not hasattr(tg, "resolve_simbad"):
            self.add_ind.finish(missing("targets"))
            return
        self.star_lookup(system, None, "", typed_transit=False)

    def star_lookup(self, name: str, pos: dict | None, why: str, typed_transit: bool = True):
        """In the background: the star's position from SIMBAD when pos is None, and its known planets from the NASA
        Exoplanet Archive (by name, else by position); then the "which mode?" question."""
        tg = mod("targets")
        if tg is None:
            self.add_ind.finish(missing("targets"))
            return

        def work():
            p = pos if pos is not None else tg.resolve_simbad(name)
            if not isinstance(p, dict):
                return None
            info, problem = lookup_star_planets(tg, p.get("main_id") or name, p.get("ra"), p.get("dec"),
                                                p.get("vmag"))
            return p, info, problem

        def done(res, exc):
            if exc is not None or not res or res[0].get("ra") is None:
                self.add_ind.finish(f"✖ Could not find {name}'s position: {exc or 'SIMBAD gave none'}.")
                return
            p, info, problem = res
            self.ask_star_mode(name, p, why, info, problem, typed_transit)
        self.app.jobs.run(work, done, self.add_ind, f"Looking up the star {name}")

    def ask_star_mode(self, name: str, pos: dict, why: str, info: dict | None, problem: str = "",
                      typed_transit: bool = True):
        """Which mode? A star with transiting planets is offered those planets (as Transits targets) and itself as a
        campaign / variable / Discovery field; a star with known planets is never offered as an empty transit target
        (it has no ephemeris of its own)."""
        label = pos.get("main_id") or name
        planets = list((info or {}).get("planets") or [])
        usable = [p for p in planets if p.get("usable")]
        star_choices = [c[0] for c in KIND_CHOICES[:3]]
        lead = f"{why} " if why else ""
        if usable and not typed_transit:          # the star itself was chosen already (not one of its planets)
            names = [p["name"] for p in usable]
            question = (f"{lead}What is {label} for you? (No data while {and_list(names)} "
                        f"transit{'s' if len(names) == 1 else ''}.)")
            choices = star_choices
        elif usable:
            names = [p["name"] for p in usable]
            question = (f"{lead}{label} has known planets: {and_list(names)} transit{'s' if len(names) == 1 else ''}. "
                        "Add a transiting planet as a transit target, or the star itself as a campaign, variable or "
                        "star field (with no data while its planets transit)?")
            choices = [planet_pick(n) for n in names] + star_choices
        elif planets:
            none = ("none of its transiting planets has a transit ephemeris at the Archive"
                    if any(p.get("transits") for p in planets) else "none of them transits")
            question = (f"{lead}{label} has known planets, but {none}, so it cannot be a transit target. "
                        f"What is {label} for you?")
            choices = star_choices
        else:
            question = f"{lead}What is {label} for you?"
            if problem:
                question += f" (Its planets could not be checked: {problem.rstrip('.')}.)"
            choices = [c[0] for c in KIND_CHOICES] if typed_transit else star_choices
        pick = ask_choice(self.app, "What kind of target?", question, choices, "Add")
        for p in usable:
            if pick == planet_pick(p["name"]):
                self.add(p["name"])
                return
        self.add_star(name, pos, question, choices, pick=pick or "", planets=usable)

    def add_star(self, name: str, pos: dict, question: str, choices: list, pick: str | None = None,
                 planets: list | None = None):
        """The star itself as a target. planets = its transiting planets (host_planets entries): a campaign or
        variable gets "no data while … transits" for each of them straight away."""
        if pick is None:
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
        windows = []
        if kind != "transit":
            for p in planets or []:
                if p.get("usable") and p.get("planet"):
                    t.rules.append(no_transit_rule(p["planet"]))
                    windows.append(p["name"])
            t.planets_checked = bool(planets)
        self.accept(t)
        if not any(x is t for x in self.app.targets):
            return                                   # not added (already in the list)
        what = {"campaign": "a star-field campaign" if mode == "D" else "a campaign", "variable": "a variable star"}
        if windows:
            set_msg(self.msg_var, self.msg,
                    f"✔ {name} is {what.get(kind, 'added')}, with no data while {and_list(windows)} "
                    f"transit{'s' if len(windows) == 1 else ''} (Edit… to change).")
        elif kind == "campaign":
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
        self.select_target(t.id)
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
            set_msg(self.msg_var, self.msg, "⚠ Select a suggested transit or variable to keep.")
            return
        for t in ts:
            t.suggested = False
        self.app.save_targets()
        self.app.targets_changed(planned=False)          # the planner never looks at "suggested"
        self.fill()
        set_msg(self.msg_var, self.msg, f"✔ Kept {and_list([t.name for t in ts])}: a new Find no longer replaces "
                                        f"{'it' if len(ts) == 1 else 'them'}.")
        transits = [t.id for t in ts if t.kind == "transit"]
        if transits:
            self.refine_ephemerides(transits)

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
        """The nights chosen on this page (Nights from … for …), shared with the Schedule page."""
        return self.app.plan_nights()

    def _fill_span(self):
        app = self.app
        nights = app.plan_nights()
        tonight = app.tonight()
        self._span_dates = [tonight + timedelta(days=k) for k in range(14)]
        labels = [night_label(d.isoformat(), app.site) + (" (tonight)" if k == 0 else "")
                  for k, d in enumerate(self._span_dates)]
        self.span_start_box.configure(values=labels)
        if nights[0] in self._span_dates:
            self.span_start.set(labels[self._span_dates.index(nights[0])])
        self.span_n.set(str(len(nights)))
        self.span_hint.configure(text=f"night{'s' if len(nights) != 1 else ''}: searched by Find, shown in the "
                                      "lists below, and built on the Schedule page")

    def _span_picked(self):
        labels = list(self.span_start_box.cget("values"))
        text = self.span_start.get()
        start = self._span_dates[labels.index(text)] if text in labels else None
        try:
            n = int(self.span_n.get())
        except ValueError:
            n = None
        self.app.set_plan_span(start, n, source=self)
        self.span_changed()

    def span_changed(self):
        """The shared nights changed (here or on the Schedule page): new event columns."""
        self._fill_span()
        self._events_key = None
        self.events = {}
        self.fill()
        self._start_next_events()

    def ask_transit_search(self):
        """The Find suggested transits pop-up (how many to show, the nights); its Search runs find_suggested."""
        if mod("targets") is None:
            self.find_ind.finish(missing("targets"))
            return None
        if not self.app.site.complete():
            self.find_ind.finish("✖ Enter your site on the Setup page first (latitude, longitude, time zone).")
            return None
        return TransitSearchDialog(self.app, self, modal=not self.app.testing)

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
                                            refine=True, **kw)
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
        # unticked Suggested Transits are replaced; the user's own targets, ticked suggestions and the Suggested
        # Variables stay
        keep = [t for t in self.app.targets if not t.suggested or t.enabled or t.kind == "variable"]
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
        self.app.targets_changed(planned=False)          # only unticked suggestions came and went
        self.fill()
        self._start_next_events()
        n = len(self.suggestion_nights())
        nights = "tonight" if n == 1 else f"the next {n} nights"
        text = (f"{len(added)} suggested transit{'s' if len(added) != 1 else ''} found, soonest first."
                if added else f"Done: no new full transits fit {nights} (planets already listed are not "
                              "suggested again).")
        note = str(note or "").strip().rstrip(".")
        # the planet list is refreshed weekly (the timings are not: each suggestion gets the most precise published
        # ephemeris, fetched now); only an Archive that did not answer is a caution (John 24 Oct: "stale?")
        if "did not answer" in note:
            self.find_ind.finish(f"⚠ {text} Planet list: {note[:1].upper() + note[1:]}.", linger_ms=0)
        else:
            extra = (f" (planet list: {note}; refreshed weekly)" if note.startswith("saved copy") else "")
            self.find_ind.finish("✔ " + text + extra, linger_ms=0 if extra else 6000)

    # ---- suggested variables ----
    def ask_variable_search(self):
        """The search parameters pop-up; its Search runs find_variables."""
        tg = mod("targets")
        if tg is None or not hasattr(tg, "suggest_variables"):
            self.find_ind.finish(missing("targets"))
            return None
        if not self.app.site.complete():
            self.find_ind.finish("✖ Enter your site on the Setup page first (latitude, longitude, time zone).")
            return None
        return VarSearchDialog(self.app, self, modal=not self.app.testing)

    def find_variables(self, params: dict):
        app = self.app
        tg = mod("targets")
        if tg is None or not hasattr(tg, "suggest_variables"):
            self.find_ind.finish(missing("targets"))
            return
        if not app.site.complete():
            self.find_ind.finish("✖ Enter your site on the Setup page first (latitude, longitude, time zone).")
            return
        site, opts = app.site, dict(app.options)
        nights = self.suggestion_nights()
        exclude = {t.name for t in app.targets if not t.suggested or t.enabled}

        def work(progress):
            return tg.suggest_variables(site, opts, nights, dict(params), exclude_names=exclude, progress=progress)
        app.jobs.run(work, self._found_variables, self.find_ind,
                     "Finding suggested variables (VSX at VizieR; up to a minute or two)", counted=True)

    def _found_variables(self, res, exc):
        if exc is not None:
            self.find_ind.finish(f"✖ No suggested variables: {exc}", linger_ms=0)
            return
        res = tuple(res) if isinstance(res, (tuple, list)) else (res, "", "")
        found, note, info = (list(res) + ["", ""])[:3]
        new = [t for t in (found or []) if isinstance(t, Target)]
        # unticked Suggested Variables are replaced; everything else stays
        keep = [t for t in self.app.targets if not (t.suggested and t.kind == "variable" and not t.enabled)]
        tg = mod("targets")
        key = getattr(tg, "variable_name_key", None) or (lambda n: str(n).strip().lower())
        names = {key(t.name) for t in keep}               # "V523 Cas" already listed = VSX's "V0523 Cas"
        added = []
        for t in new:
            if key(t.name) in names:
                continue
            t.suggested, t.enabled, t.kind, t.mode = True, False, "variable", "V"
            added.append(t)
            names.add(key(t.name))
        self.app.targets = keep + added
        self.app.save_targets()
        self.app.targets_changed(planned=False)          # only unticked suggestions came and went
        self.fill()
        self._start_next_events()
        text = (f"{len(added)} suggested variable{'s' if len(added) != 1 else ''} found, soonest first."
                if added else "Done: no new variables fit these nights and settings.")
        info = str(info or "").strip().rstrip(".")
        set_msg(self.msg_var, self.msg, f"Suggested variables: {info}." if info else "")
        note = str(note or "").strip().rstrip(".")
        if note:
            self.find_ind.finish(f"⚠ {text} VSX: {note[:1].upper() + note[1:]}.", linger_ms=0)
        else:
            self.find_ind.finish("✔ " + text, linger_ms=0)


class TransitSearchDialog:
    """"Find suggested transits": how many to show (default 25, remembered as options["suggest_max"]) and the nights
    searched; the filter itself (V range, depth, baseline, Moon) is Setup's."""

    def __init__(self, app: App, page: TargetsPage, modal: bool = True):
        self.app, self.page = app, page
        o = app.options
        self.win, body = _dialog(app, "Find suggested transits")
        ttk.Label(body, text="Transiting planets with a full transit (and its baselines) in the dark on the nights "
                             "below, from your site, filtered by Setup's Suggested-transits settings (V range, "
                             "depth, Moon). They land unticked in Transits, soonest first.",
                  style="Hint.TLabel", wraplength=px(app, 460), justify=tk.LEFT).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, px(app, 6)))
        ttk.Label(body, text="Show at most", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=px(app, 3))
        f = ttk.Frame(body, style="Card.TFrame")
        f.grid(row=1, column=1, sticky="w", padx=px(app, 8), pady=px(app, 3))
        self.max_var = tk.StringVar(value=fmt_num(o.get("suggest_max", 25)))
        ttk.Entry(f, textvariable=self.max_var, width=8).pack(side=tk.LEFT)
        ttk.Label(f, text="transits", style="Hint.TLabel").pack(side=tk.LEFT, padx=(px(app, 6), 0))
        nights = page.suggestion_nights()
        site = app.site
        span = (night_label(nights[0].isoformat(), site) + (f" – {night_label(nights[-1].isoformat(), site)}"
                                                            if len(nights) > 1 else "")) if nights else "—"
        ttk.Label(body, text="Nights", style="Card.TLabel").grid(row=2, column=0, sticky="nw", pady=px(app, 3))
        ttk.Label(body, text=f"{span} ({len(nights)} night{'s' if len(nights) != 1 else ''}: the Targets page's "
                             "Nights row)", style="Card.TLabel", wraplength=px(app, 380),
                  justify=tk.LEFT).grid(row=2, column=1, sticky="w", padx=px(app, 8), pady=px(app, 3))
        self.msg_var = tk.StringVar()
        self.msg = ttk.Label(body, textvariable=self.msg_var, style="Card.TLabel", wraplength=px(app, 460),
                             justify=tk.LEFT)
        self.msg.grid(row=3, column=0, columnspan=2, sticky="w", pady=(px(app, 6), 0))
        btns = ttk.Frame(body, style="Card.TFrame")
        btns.grid(row=4, column=0, columnspan=2, sticky="e", pady=(px(app, 10), 0))
        ttk.Button(btns, text="Cancel", command=self.win.destroy).pack(side=tk.RIGHT)
        style.green_button(btns, "Search", self.search).pack(side=tk.RIGHT, padx=(0, px(app, 8)))
        _keys(self.win, self.search)
        if modal:
            _show_modal(app, self.win)

    def search(self):
        text = str(self.max_var.get()).strip().replace(",", ".")
        try:
            n = float(text)
            if not (1 <= n <= 100):
                raise ValueError
        except ValueError:
            set_msg(self.msg_var, self.msg, "✖ Show at most must be a number from 1 to 100.")
            return False
        self.app.options["suggest_max"] = int(round(n))      # remembered for next time
        if not settings.save_settings(self.app.settings):
            self.app.set_status("⚠ The search settings could not be saved (the settings file is not writable).")
        self.win.destroy()
        self.page.find_suggested()
        return True


class VarSearchDialog:
    """"Find suggested variables": the search parameters (type families, period, V range, amplitude, how many),
    remembered in the settings (options["var_search"]); Search starts the search on the Targets page."""

    def __init__(self, app: App, page: TargetsPage, modal: bool = True):
        self.app, self.page = app, page
        tg = mod("targets")
        p = tg.var_search_params(app.options)
        self.win, body = _dialog(app, "Find suggested variables")
        r = 0

        def label(text, row, **kw):
            ttk.Label(body, text=text, style=kw.pop("style", "Card.TLabel")).grid(row=row, column=0, sticky="nw",
                                                                                 pady=px(app, 3), **kw)
        ttk.Label(body, text="Variable stars from AAVSO VSX (VizieR's copy) that are up in the dark on the nights "
                             "below, from your site; they land unticked in Variables, soonest first.",
                  style="Hint.TLabel", wraplength=px(app, 460), justify=tk.LEFT).grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(0, px(app, 6)))
        r += 1
        label("Types", r)
        box = ttk.Frame(body, style="Card.TFrame")
        box.grid(row=r, column=1, sticky="w", padx=px(app, 8))
        self.fam_vars = {}
        for key, text, _codes in tg.VAR_FAMILIES:
            var = tk.BooleanVar(value=key in p["families"])
            ttk.Checkbutton(box, text=text, variable=var, style="Card.TCheckbutton").pack(anchor="w")
            self.fam_vars[key] = var
        self.unsure = tk.BooleanVar(value=bool(p.get("uncertain")))
        ttk.Checkbutton(box, text="Include uncertain types (marked “:” in VSX)", variable=self.unsure,
                        style="Card.TCheckbutton").pack(anchor="w", pady=(px(app, 4), 0))
        r += 1
        self.vars: dict[str, tk.StringVar] = {}

        def pair(text, k1, k2, unit, row):
            label(text, row)
            f = ttk.Frame(body, style="Card.TFrame")
            f.grid(row=row, column=1, sticky="w", padx=px(app, 8), pady=px(app, 3))
            for i, k in enumerate((k1, k2)):
                self.vars[k] = tk.StringVar(value=fmt_num(p.get(k)))
                ttk.Entry(f, textvariable=self.vars[k], width=8).pack(side=tk.LEFT)
                if i == 0:
                    ttk.Label(f, text="to", style="Card.TLabel").pack(side=tk.LEFT, padx=px(app, 6))
            return f
        f = pair("Period", "pmin", "pmax", "", r)
        self.punit = tk.StringVar(value=p.get("punit") or "d")
        for text, val in (("days", "d"), ("hours", "h")):
            ttk.Radiobutton(f, text=text, value=val, variable=self.punit, style="Card.TRadiobutton").pack(
                side=tk.LEFT, padx=(px(app, 8), 0))
        ttk.Label(f, text="(empty = any)", style="Hint.TLabel").pack(side=tk.LEFT, padx=(px(app, 6), 0))
        r += 1
        f = pair("Brightness (V at maximum)", "vmin", "vmax", "", r)
        ttk.Label(f, text="mag", style="Hint.TLabel").pack(side=tk.LEFT, padx=(px(app, 6), 0))
        r += 1

        def single(text, k, unit, row):
            label(text, row)
            f = ttk.Frame(body, style="Card.TFrame")
            f.grid(row=row, column=1, sticky="w", padx=px(app, 8), pady=px(app, 3))
            self.vars[k] = tk.StringVar(value=fmt_num(p.get(k)))
            ttk.Entry(f, textvariable=self.vars[k], width=8).pack(side=tk.LEFT)
            ttk.Label(f, text=unit, style="Hint.TLabel").pack(side=tk.LEFT, padx=(px(app, 6), 0))
        single("Amplitude at least", "min_amp", "mag", r)
        r += 1
        single("Show at most", "max_results", "stars", r)
        r += 1
        nights = page.suggestion_nights()
        site = app.site
        span = (night_label(nights[0].isoformat(), site) + (f" – {night_label(nights[-1].isoformat(), site)}"
                                                            if len(nights) > 1 else "")) if nights else "—"
        label("Nights", r)
        ttk.Label(body, text=f"{span} ({len(nights)} night{'s' if len(nights) != 1 else ''}: the Targets page's "
                             "Nights row); your minimum altitude, horizon, darkness and Moon limit from Setup",
                  style="Card.TLabel", wraplength=px(app, 380), justify=tk.LEFT).grid(
            row=r, column=1, sticky="w", padx=px(app, 8), pady=px(app, 3))
        r += 1
        self.msg_var = tk.StringVar()
        self.msg = ttk.Label(body, textvariable=self.msg_var, style="Card.TLabel", wraplength=px(app, 460),
                             justify=tk.LEFT)
        self.msg.grid(row=r, column=0, columnspan=2, sticky="w", pady=(px(app, 6), 0))
        r += 1
        btns = ttk.Frame(body, style="Card.TFrame")
        btns.grid(row=r, column=0, columnspan=2, sticky="e", pady=(px(app, 10), 0))
        ttk.Button(btns, text="Cancel", command=self.win.destroy).pack(side=tk.RIGHT)
        style.green_button(btns, "Search", self.search).pack(side=tk.RIGHT, padx=(0, px(app, 8)))
        _keys(self.win, self.search)
        if modal:
            _show_modal(app, self.win)

    def params(self) -> tuple[dict | None, str]:
        """The form as search parameters, or (None, the reason in plain words)."""
        v = {k: str(var.get()).strip().replace(",", ".") for k, var in self.vars.items()}

        def num(k, text, lo, hi, empty_ok=False):
            if not v.get(k):
                if empty_ok:
                    return None
                raise ValueError(f"{text} is empty.")
            try:
                x = float(v[k])
            except ValueError:
                raise ValueError(f"{text} must be a number (you typed “{v[k]}”).") from None
            if not (lo <= x <= hi):
                raise ValueError(f"{text} must be between {fmt_num(lo)} and {fmt_num(hi)}.")
            return x
        try:
            fams = [k for k, var in self.fam_vars.items() if var.get()]
            if not fams:
                raise ValueError("Tick at least one type.")
            pmin = num("pmin", "The shortest period", 0, 100000, True)
            pmax = num("pmax", "The longest period", 0, 100000, True)
            if pmin is not None and pmax is not None and pmin > pmax:
                raise ValueError("The shortest period is longer than the longest.")
            vmin = num("vmin", "The brightest V", -2, 25)
            vmax = num("vmax", "The faintest V", -2, 25)
            if vmin > vmax:
                raise ValueError("The V range runs the wrong way (brightest first, e.g. 9 to 13).")
            amp = num("min_amp", "The amplitude", 0, 20)
            n = num("max_results", "Show at most", 1, 100)
        except ValueError as exc:
            return None, str(exc)
        return {"families": fams, "uncertain": bool(self.unsure.get()), "pmin": pmin, "pmax": pmax,
                "punit": self.punit.get() or "d", "vmin": vmin, "vmax": vmax, "min_amp": amp,
                "max_results": int(round(n))}, ""

    def search(self):
        p, err = self.params()
        if p is None:
            set_msg(self.msg_var, self.msg, f"✖ {err}")
            return False
        self.app.options["var_search"] = p               # remembered for next time
        if not settings.save_settings(self.app.settings):
            self.app.set_status("⚠ The search settings could not be saved (the settings file is not writable).")
        settings.log(f"variable search: {p}")
        self.win.destroy()
        self.page.find_variables(p)
        return True


class EditTargetDialog:
    """Exposure, block length, goal, rules (incl. "no data while a planet transits" with a planet lookup), minimum
    altitude, Moon distance, clock window, notes; for transits also the ephemeris."""

    def __init__(self, app: App, target: Target, on_save=None, modal: bool = True):
        self.app = app
        self.t = Target.from_dict(target.to_dict())
        if (self.t.kind == "transit") != (self.t.mode == "T"):
            # an older target file can pair a group with the wrong mode (a variable in T): the group (the Targets
            # page row, and how it is planned) decides, so an unchanged Save is never refused
            self.t.mode = "T" if self.t.kind == "transit" else ("V" if self.t.mode == "T" else self.t.mode)
        self.on_save = on_save
        self.win, outer = _dialog(app, f"Edit {target.name}")
        self.foot = foot = ttk.Frame(outer, style="Card.TFrame")
        foot.pack(side=tk.BOTTOM, fill=tk.X)
        self.sb = ScrollBody(outer)
        self.sb.outer.pack(fill=tk.BOTH, expand=True)
        body = self.sb.inner
        self.vars: dict[str, tk.Variable] = {}
        self.loaded: dict[str, str] = {}       # field text as opened (unchanged text = unchanged value)
        self.no_transit = [r for r in self.t.rules if r.get("kind") == "no_transit"]
        self._nt_opened = [dict(r) for r in self.no_transit]   # as opened: the user's own changes are never undone
        self.widgets: dict = {}
        self.host_info: dict | None = None          # the star's planets at the Archive (looked up on opening)
        self.pl_vars: dict[str, tk.BooleanVar] = {}
        self._syncing = False
        self._looked = False
        r = 0

        def row(label, key, value, unit="", width=12, values=None):
            nonlocal r
            lab = ttk.Label(body, text=label, style="Card.TLabel")
            lab.grid(row=r, column=0, sticky="w", pady=px(app, 2))
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
            return [lab, f]

        def head(text):
            nonlocal r
            lab = ttk.Label(body, text=text, style="Section.TLabel")
            lab.grid(row=r, column=0, columnspan=3, sticky="w", pady=(px(app, 8), px(app, 2)))
            r += 1
            return [lab]

        t = self.t
        head("Target")
        row("Name", "name", t.name, width=24)
        # the internal mode code (V / T / D) follows the group and is not shown (no photometry-program modes; 1.1.0)
        self.vars["mode"] = tk.StringVar(value=dict((m, c) for c, m in MODE_CHOICES)[
            t.mode if t.mode in ("V", "T", "D") else "V"])
        self.loaded["mode"] = self.vars["mode"].get()
        row("Group", "kind", dict((k, lab) for lab, k in GROUP_CHOICES).get(t.kind, "Variable"),
            "the Targets page group (and how it is planned)", 12, [lab for lab, _k in GROUP_CHOICES])
        row("Priority", "priority", t.priority, "", 10, list(PRIORITIES))
        row("RA (J2000)", "ra", sky.fmt_ra(t.ra, 1) if t.ra is not None else "", "hh:mm:ss", 14)
        row("Dec (J2000)", "dec", sky.fmt_dec(t.dec) if t.dec is not None else "", "±dd:mm:ss", 14)
        row("V magnitude", "vmag", fmt_num(t.vmag, 2), "", 8)
        head("Observing")
        row("Exposure", "exposure_s", fmt_num(t.exposure_s), "s (empty = SCHED estimates one)", 8)
        row("Seconds between frames\n(full interval, this target)", "cycle_s", fmt_num(t.cycle_s),
            "s (empty = exposure + Setup's per-frame overhead)", 8)
        ttk.Label(body, text="The whole frame-to-frame interval (exposure + download/save) for this target only; it "
                             "wins over Setup's per-frame overhead, which is added to every other target's exposure.",
                  style="Hint.TLabel", justify=tk.LEFT, wraplength=px(app, 460)).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(0, px(app, 4)))
        r += 1
        row("Block length", "block_min", fmt_num(t.block_min), "min (flexible targets)", 8)
        row("Shortest useful block", "min_block_min", fmt_num(t.min_block_min), "min", 8)
        row("Goal", "goal_per_week", str(int(t.goal_per_week or 0)), "nights a week (0 = no goal)", 8)
        rules = {x.get("kind"): x for x in t.rules}
        row("Own minimum altitude", "min_alt", fmt_num((rules.get("min_alt") or {}).get("value")),
            "° (empty = the site's)", 8)
        row("Moon at least", "min_moon_sep", fmt_num((rules.get("min_moon_sep") or {}).get("value")),
            "° away (empty = no limit)", 8)
        tz = app.site.tz
        row("Only between", "window", (rules.get("window") or {}).get("value") or "",
            f"HH:MM-HH:MM, {sky.local_label(tz)}; empty = any time", 12)
        # the ephemeris (always built; shown while the group is Transit, so Edit can move a star into Transits)
        pl = (t.planet or {}) if t.kind == "transit" else {}
        self.eph_widgets = head("Ephemeris")
        self.eph_widgets += row("Period", "period", fmt_num(pl.get("period"), 8), "days", 14)
        self.eph_widgets += row("Tc (T0)", "t0", fmt_num(pl.get("t0"), 6), "BJD_TDB", 14)
        self.eph_widgets += row("Duration", "t14", fmt_num((pl.get("t14") or 0) * 24, 3) if pl.get("t14") else "",
                                "hours", 8)
        self.eph_widgets += row("Depth", "depth", fmt_num((pl.get("depth") or 0) * 100, 3) if pl.get("depth") else "",
                                "%", 8)
        self.eph_widgets += row("Reference", "reference", pl.get("reference") or "", "", 30)
        if t.kind != "transit":
            for w in self.eph_widgets:
                w.grid_remove()
        head("No data while a planet transits")
        # the star's planets at the NASA Exoplanet Archive (looked up when the dialog opens): tick = no data then
        self.pl_box = ttk.Frame(body, style="Card.TFrame")
        self.pl_box.grid(row=r, column=0, columnspan=3, sticky="w")
        self.pl_ind = JobIndicator(self.pl_box)
        self.pl_ind.label.configure(wraplength=px(app, 440), justify=tk.LEFT)
        follow_wrap(self.pl_ind.label, body, pad=px(app, 40), least=px(app, 440))   # use the dialog's full width
        self.pl_ind.pack(anchor="w")
        self.pl_checks = ttk.Frame(self.pl_box, style="Card.TFrame")
        self.pl_checks.pack(anchor="w", padx=(px(app, 12), 0))
        r += 1
        self.nt_list = tk.Listbox(body, height=3, width=34, activestyle="none", relief="solid", borderwidth=1,
                                  highlightthickness=0, font=style.font(10))
        self.nt_list.grid(row=r, column=0, columnspan=2, sticky="w", pady=px(app, 2))
        nb = ttk.Frame(body, style="Card.TFrame")
        nb.grid(row=r, column=2, sticky="nw", padx=px(app, 6))
        ttk.Button(nb, text="Remove", command=self._nt_remove).pack(anchor="w")
        r += 1
        lf = ttk.Frame(body, style="Card.TFrame")
        lf.grid(row=r, column=0, columnspan=3, sticky="w", pady=px(app, 2))
        ttk.Label(lf, text="Planet (or just b, c …)", style="Card.TLabel").pack(side=tk.LEFT)
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
        # the dialog's own busy bar, lower left (its lookups show here, not in the main window behind it)
        self.win.busy_bar = self.busy_bar = style.BusyBar(btns)
        self.busy_bar.pack(side=tk.LEFT)
        self._fill_nt()
        _keys(self.win, self._return)
        self.vars["mode"].trace_add("write", self._mode_changed)
        self.vars["kind"].trace_add("write", self._kind_changed)
        self._fit()
        if t.kind != "transit":
            self.lookup_planets()
        first = self.widgets.get("exposure_s")
        if modal and not app.testing:
            _show_modal(app, self.win, first)

    # ---- size ----
    def _fit(self):
        """Size the dialog to its contents, measured after layout (so it is right at 125–150 % Windows scaling
        too): the scrolling body as wide as its widest row plus room for the scrollbar, as tall as its rows up to
        ~70 % of the screen (the buttons always on screen); never below that width, free to be made larger. Called
        again when contents change (planets listed, ephemeris shown); it only ever grows a window on screen."""
        try:
            win, sb, body = self.win, self.sb, self.sb.inner
            win.update_idletasks()
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            bw, bh = body.winfo_reqwidth(), body.winfo_reqheight()
            ch = min(bh, max(px(self.app, 160), int(sh * 0.70) - self.foot.winfo_reqheight()))
            bar = sb.vbar.winfo_reqwidth() if bh > ch else 0
            sb.canvas.configure(width=bw + bar, height=ch)
            win.update_idletasks()
            pad = 2 * px(self.app, 16)
            w = min(max(win.winfo_reqwidth(), bw + bar * 2 + pad), int(sw * 0.95))
            h = min(max(win.winfo_reqheight(), ch + self.foot.winfo_reqheight() + pad), int(sh * 0.90))
            win.minsize(w, min(h, px(self.app, 320)))
            if win.winfo_ismapped():
                w, h = max(w, win.winfo_width()), max(h, win.winfo_height())
                if (w, h) == (win.winfo_width(), win.winfo_height()):
                    return                       # no growth: leave it where the user put it
            # (re-)centred over the main window, so a dialog that grew stays in view
            app = self.app
            x = app.winfo_rootx() + max(0, (app.winfo_width() - w) // 2)
            y = app.winfo_rooty() + max(0, (app.winfo_height() - h) // 3)
            x, y = _clamp_to_screen(app, x, y, w, h, sw, sh)
            win.geometry(f"{w}x{h}+{x}+{y}")
        except tk.TclError:
            pass

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
        self._sync_ticks()

    def _nt_names(self) -> set:
        return {planet_key((r.get("planet") or {}).get("name")) for r in self.no_transit}

    def _sync_ticks(self):
        have = self._nt_names()
        for name, var in self.pl_vars.items():
            var.set(planet_key(name) in have)

    def _alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def star_name(self) -> str:
        """The star this target is (or orbits): the Archive's host name once looked up, else the target's name
        without a planet letter."""
        host = (self.host_info or {}).get("host")
        if host:
            return host
        name = (self.vars["name"].get() if "name" in self.vars else self.t.name).strip() or self.t.name
        return re.sub(r"\s+[b-z]$", "", name) if self.t.kind == "transit" else name

    # ---- the star's planets at the Archive ----
    def lookup_planets(self, refresh: bool = False):
        """In the background (the dialog stays usable): the star's known planets, by host name and by position."""
        tg = mod("targets")
        if tg is None or not hasattr(tg, "host_planets"):
            return
        t, name = self.t, self.star_name()
        self._looked = True

        def work():
            if refresh:
                try:
                    return tg.host_planets(name, t.ra, t.dec, t.vmag, refresh=True), ""
                except Exception as exc:
                    return None, str(exc) or exc.__class__.__name__
            return lookup_star_planets(tg, name, t.ra, t.dec, t.vmag)

        def done(res, exc):
            if not self._alive():
                return
            info, problem = res if isinstance(res, tuple) else (None, str(exc or ""))
            self.show_planets(info, problem)
        self.app.jobs.run(work, done, self.pl_ind, f"Looking up {name}'s planets at the NASA Exoplanet Archive")

    def show_planets(self, info: dict | None, problem: str = ""):
        """List the looked-up planets: transiting ones as ticks (ticked = no data while it transits; pre-ticked when
        this target has no window for any of them yet), the others greyed out with the reason."""
        for w in self.pl_checks.winfo_children():
            w.destroy()
        self.pl_vars = {}
        if info is None:
            self.host_info = None
            why = (problem or "no answer").strip()
            if re.search(r"[.!?]\s", why) or why[:1].isupper():     # a whole sentence of its own: not in brackets
                why = why if why.endswith((".", "!", "?")) else why + "."
                text = f"✖ Couldn't reach the NASA Exoplanet Archive to list this star's planets. {why}"
            else:
                text = f"✖ Couldn't reach the NASA Exoplanet Archive to list this star's planets ({why.rstrip('.')})."
            self.pl_ind.finish(text + " Type a planet below instead.", linger_ms=0)
            self._fit()
            return
        self.host_info = info
        planets = list(info.get("planets") or [])
        if not planets:
            self.pl_ind.finish("✔ No known planets at the NASA Exoplanet Archive.", linger_ms=0)
            self._fit()
            return
        have = self._nt_names()
        # pre-tick only the first time this target's planets are offered, and only if the user has not changed the
        # list while the lookup ran (a campaign whose windows were removed on purpose keeps it that way)
        fresh = (not self.t.planets_checked and self.no_transit == self._nt_opened
                 and not any(planet_key(p["name"]) in have for p in planets))
        added = []
        for p in planets:
            on = bool(p.get("usable")) and (fresh or planet_key(p["name"]) in have)
            var = tk.BooleanVar(value=on)
            if p.get("usable"):
                text = f"{p['name']}  transits"
            else:
                text = f"{p['name']}  ({p.get('why') or 'no transit'}: no window needed)"
            cb = ttk.Checkbutton(self.pl_checks, text=text, variable=var, style="Card.TCheckbutton",
                                 command=lambda p=p, v=var: self._planet_ticked(p, v))
            if not p.get("usable"):
                cb.state(["disabled"])
            cb.pack(anchor="w")
            self.pl_vars[p["name"]] = var
            if on and planet_key(p["name"]) not in have:
                self.add_no_transit(p["planet"], self._pad())
                added.append(p["name"])
        n = len(planets)
        by = f" (found by position as {info.get('host')})" if info.get("matched") == "position" else ""
        text = f"{n} known planet{'s' if n != 1 else ''} at the NASA Exoplanet Archive{by}; ticked = no data while it transits."
        if added:
            text += f" Ticked {and_list(added)}: Save keeps {'it' if len(added) == 1 else 'them'}."
        note = str(info.get("note") or "").strip().rstrip(".")
        self.pl_ind.finish(f"⚠ {text} {note[:1].upper() + note[1:]}." if note else "✔ " + text, linger_ms=0)
        self._fit()

    def _pad(self) -> float:
        try:
            return max(0.0, float(self.nt_pad.get() or 0))
        except ValueError:
            return 0.0

    def _planet_ticked(self, p: dict, var):
        key = planet_key(p["name"])
        if var.get():
            if key not in self._nt_names() and p.get("planet"):
                self.add_no_transit(p["planet"], self._pad())
        else:
            self.no_transit = [r for r in self.no_transit if planet_key((r.get("planet") or {}).get("name")) != key]
            self._fill_nt()

    def _looked_up(self, name: str) -> dict | None:
        """The looked-up planet called name (any spacing), or None."""
        key = planet_key(name)
        for p in (self.host_info or {}).get("planets") or []:
            if planet_key(p["name"]) == key:
                return p
        return None

    def _nt_add(self):
        name = self.nt_name.get().strip()
        if not name:
            self.nt_ind.finish("✖ Type the planet's name (e.g. HD 219134 c), or just its letter (c).")
            return
        try:
            pad = float(self.nt_pad.get() or 0)
        except ValueError:
            self.nt_ind.finish("✖ The margin must be a number of minutes.")
            return
        letter = bare_letter(name)
        if letter:                                  # "b" = this star's planet b
            star = self.star_name()
            hits = [p for p in (self.host_info or {}).get("planets") or []
                    if re.search(rf"[\s\-]{letter}$", p["name"])]
            name = hits[0]["name"] if hits else f"{star} {letter}"
        if planet_key(name) in self._nt_names():
            self.nt_ind.finish(f"⚠ {name} is already in the list.")
            return
        p = self._looked_up(name)
        if p is not None:                           # already looked up with the star: no second lookup
            if not p.get("usable"):
                self.nt_ind.finish(f"✖ {p['name']}: {p.get('why') or 'no transit'}, so it needs no window.")
                return
            self.add_no_transit(p["planet"], pad)
            self._sync_ticks()
            self.nt_name.set("")
            self.nt_ind.finish(f"✔ Added {p['name']}.")
            return
        tg = mod("targets")
        if tg is None:
            self.nt_ind.finish(missing("targets"))
            return

        def done(res, exc):
            if not self._alive():
                return
            if exc is not None:
                needs = getattr(tg, "NeedsMode", None)
                choice = getattr(tg, "PlanetChoice", None)
                if needs is not None and isinstance(exc, needs):
                    why = str(getattr(exc, "otype", "") or "a planet without a transit ephemeris")
                    self.nt_ind.finish(f"✖ {getattr(exc, 'main_id', '') or name} is {why}, so it needs no window.")
                elif choice is not None and isinstance(exc, choice):
                    some = list(getattr(exc, "transiting", None) or getattr(exc, "planets", None) or [])
                    self.nt_ind.finish(f"✖ {name} is a star with several planets: type one of them"
                                       + (f" (e.g. {sorted(some)[0]})." if some else "."))
                else:
                    self.nt_ind.finish(f"✖ {exc}")
                return
            pl = res.planet if isinstance(res, Target) else res
            if not pl:
                self.nt_ind.finish(f"✖ No ephemeris found for {name}.")
                return
            if planet_key(pl.get("name") or name) in self._nt_names():
                self.nt_ind.finish(f"⚠ {pl.get('name') or name} is already in the list.")
                return
            self.add_no_transit(pl, pad)
            self._sync_ticks()
            self.nt_name.set("")
            self.nt_ind.finish(f"✔ Added {pl.get('name') or name}.")
        self.app.jobs.run(lambda: tg.lookup_planet(name), done, self.nt_ind, f"Looking up {name}")

    def add_no_transit(self, planet: dict, pad: float = 0.0):
        self.no_transit.append(no_transit_rule(planet, pad))
        self._fill_nt()

    # ---- moving between groups ----
    def _group(self) -> tuple[str | None, str | None]:
        return dict(MODE_CHOICES).get(self.vars["mode"].get()), dict(GROUP_CHOICES).get(self.vars["kind"].get())

    def _mode_changed(self, *_a):
        """Transits (T) goes with the Transit group, Variables / Discovery with Variable or Campaign."""
        if self._syncing:
            return
        mode, kind = self._group()
        self._syncing = True
        try:
            if mode == "T" and kind != "transit":
                self.vars["kind"].set(dict((k, lab) for lab, k in GROUP_CHOICES)["transit"])
            elif mode != "T" and kind == "transit":
                back = self.t.kind if self.t.kind in ("variable", "campaign") else "campaign"
                self.vars["kind"].set(dict((k, lab) for lab, k in GROUP_CHOICES)[back])
        finally:
            self._syncing = False
        self._group_shown()

    def _kind_changed(self, *_a):
        if self._syncing:
            return
        mode, kind = self._group()
        self._syncing = True
        try:
            if kind == "transit" and mode != "T":
                self.vars["mode"].set(dict((m, c) for c, m in MODE_CHOICES)["T"])
            elif kind != "transit" and mode == "T":
                back = self.t.mode if self.t.mode in ("V", "D") else "V"
                self.vars["mode"].set(dict((m, c) for c, m in MODE_CHOICES)[back])
        finally:
            self._syncing = False
        self._group_shown()

    def _transiting_names(self) -> list[str]:
        return [p["name"] for p in (self.host_info or {}).get("planets") or [] if p.get("usable")]

    def _group_shown(self):
        """The ephemeris rows while the group is Transit, and a word on what moving the target does."""
        _mode, kind = self._group()
        transit = kind == "transit"
        for w in self.eph_widgets:
            if transit:
                w.grid()
            else:
                w.grid_remove()
        msg = ""
        if transit and self.t.kind != "transit":
            names = self._transiting_names()
            msg = "⚠ A transit target needs its ephemeris: type the period and Tc below."
            if names:
                msg = (f"⚠ {self.star_name()} is a star: to plan a transit, add its planet {and_list(names)} on the "
                       "Targets page (type the name in Add) and keep the star a campaign. As a transit target the "
                       "star itself needs a typed period and Tc below.")
        elif not transit and self.t.kind == "transit":
            msg = f"⚠ Saving moves {self.t.name} out of Transits; its ephemeris is dropped."
            if not self._looked:
                self.lookup_planets()             # a star now: offer its planets' no-transit windows
        set_msg(self.msg_var, self.msg, msg)
        self._fit()

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
            # the group and the SHOBS-P mode move together: Transits (T) ⟺ the Transit group
            new_mode = dict(MODE_CHOICES).get(v["mode"], t.mode)
            new_kind = dict(GROUP_CHOICES).get(v.get("kind", ""), t.kind)
            if new_kind != t.kind and new_mode == t.mode:
                new_mode = "T" if new_kind == "transit" else ("V" if new_mode == "T" else new_mode)
            elif new_mode != t.mode and new_kind == t.kind:
                if new_mode == "T":
                    new_kind = "transit"
                elif new_kind == "transit":
                    new_kind = "campaign"
            elif (new_kind == "transit") != (new_mode == "T"):
                raise ValueError("Transits (T) goes with the Transit group: choose both, or neither.")
            was = t.kind
            t.mode = new_mode
            if new_kind != was:
                t.kind = new_kind
                t.suggested = False
                if new_kind == "variable" and not t.elements:
                    t.elements = {"vtype": "", "any_time": True}
                if was == "transit":
                    host = (t.planet or {}).get("host") or ""
                    t.planet = None                  # a star now: its ephemeris is the planet's, not its own
                    if re.search(r"[\s-][b-z]$", t.name) and v["name"] == self.loaded.get("name", v["name"]):
                        # "HD 219134 b" moved out of Transits is the star: drop the planet letter (unless renamed)
                        t.name = host or re.sub(r"[\s-]+[b-z]$", "", t.name)
                if new_kind == "transit":
                    t.elements = None
                    t.planet = None                  # filled from the ephemeris rows below
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
            t.cycle_s = num("cycle_s", "Seconds between frames", 0.001, 7200)
            if t.cycle_s is not None and t.exposure_s and t.cycle_s < t.exposure_s:
                raise ValueError("Seconds between frames cannot be shorter than the exposure.")
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
                    raise ValueError(f"Only between: type two clock times in {sky.local_label(self.app.site.tz)}, "
                                     "e.g. 21:00-02:30.")
                rules.append({"kind": "window", "value": v["window"].replace("–", "-").replace(" ", "")})
            rules.extend(self.no_transit)
            t.rules = rules
            if self.host_info and (self.host_info.get("planets") or []):
                t.planets_checked = True
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
                if was != "transit" and not (pl.get("period") and pl.get("t0")):
                    names = self._transiting_names()
                    raise ValueError("A transit target needs its period and Tc (type them under Ephemeris)"
                                     + (f"; to plan {and_list(names)}, add the planet on the Targets page and keep "
                                        f"{self.star_name()} a campaign" if names else ", or keep it a campaign")
                                     + ".")
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


def _same(a, b) -> bool:
    """Two settings values alike: numbers within a part in a million (a value shown rounded and typed back is the
    same value), anything else equal."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) \
            and not isinstance(b, bool):
        return abs(float(a) - float(b)) < 1e-6 * max(1.0, abs(float(a)))
    return a == b


_SHOWN_TO_TENTHS = frozenset({"elevation_m", "dew_spread_c"})


def _differs(a, b) -> bool:
    """Two settings values (dicts / lists / numbers / text) differ beyond the rounding of a value shown and saved
    back; lists and tuples alike (a horizon read from JSON is lists, one from the form tuples)."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in set(a) | set(b):
            x, y = a.get(k), b.get(k)
            if k in _SHOWN_TO_TENTHS and isinstance(x, (int, float)) and isinstance(y, (int, float)):
                if abs(float(x) - float(y)) > 0.06:      # shown to 0.1 (and maybe in ft / °F): rounding is no change
                    return True
            elif _differs(x, y):
                return True
        return False
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) != len(b) or any(_differs(x, y) for x, y in zip(a, b))
    return not _same(a, b)


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
    STALE = ("⚠ Setup or targets changed since this schedule was built: press Build schedule (or Re-review) to "
             "rebuild it with the changes.")

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
        self.start_box.bind("<<ComboboxSelected>>", lambda _e: self._start_changed())
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
        self._past_key = None                     # which nights were past when the tabs were made

    # ---- past nights ----
    def past_flags(self) -> list[bool]:
        """Per saved night: True when it is before tonight's night (site zone)."""
        tonight = self.app.tonight().isoformat()
        return [str(n.get("date", "")) < tonight for n in self.app.schedule.nights]

    def all_past(self) -> bool:
        flags = self.past_flags()
        return bool(flags) and all(flags)

    def past_note(self) -> str:
        """'⚠ Last built for Thu 22 Oct (past): press Build schedule.' when every night has passed, else ''."""
        if not self.all_past():
            return ""
        last = self.app.schedule.nights[-1].get("date", "")
        return f"⚠ Last built for {night_label(last, self.app.site)} (past): press Build schedule."

    # ---- controls ----
    def _nights_changed(self):
        try:
            self.app.set_plan_span(n=int(self.n_var.get()), source=self)
        except ValueError:
            pass

    def _start_changed(self):
        d = self.chosen_start()
        if d is not None:
            self.app.set_plan_span(start=d, source=self)

    def span_changed(self):
        self._fill_controls(follow=True)

    def _fill_controls(self, follow: bool = False):
        app = self.app
        nights = app.plan_nights()
        self.n_var.set(str(len(nights)))
        tonight = app.tonight()
        self._start_dates = [tonight + timedelta(days=k) for k in range(14)]
        labels = [self._label(d, k == 0) for k, d in enumerate(self._start_dates)]
        self.start_box.configure(values=labels)
        if follow or not self.start_var.get().strip():
            if nights[0] in self._start_dates:
                self.start_var.set(labels[self._start_dates.index(nights[0])])

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
        elif self.all_past():
            set_msg(self.msg_var, self.msg, self.past_note())
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
        key = tuple(self.past_flags())
        if self.dirty or key != self._past_key:           # a night passed since the tabs were made
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
        self._past_key = tuple(self.past_flags())
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

    def _run(self, work, done, label, steps: bool = False):
        self.set_busy(True)

        def finish(res, exc):
            self.set_busy(False)
            done(res, exc)
        self.app.jobs.run(work, finish, self.ind, label, steps=steps)

    def build(self, start: date | None = None, n: int | None = None):
        if not self._ready():
            return
        app = self.app
        start = start or self.chosen_start()
        if start is None:
            self.ind.finish("✖ Type the first night as a date, e.g. 2026-11-03 or 3 Nov.")
            return
        n = int(n or self.n_var.get() or 1)
        if start >= app.tonight():
            app.plan_start = start                      # the Targets page follows the built nights
        site, optics, opts = app.site, app.optics, dict(app.options)
        targets = [Target.from_dict(t.to_dict()) for t in app.planner_targets()]
        pl = mod("planner")
        prior = Schedule.from_dict(app.schedule.to_dict())
        if hasattr(pl, "roll_forward") and any(nd.get("date", "") < start.isoformat() for nd in prior.nights):
            prior = pl.roll_forward(prior, start)      # nights before the new start go to the history, marks kept

        nn = f"{n} night{'s' if n > 1 else ''}"

        def work(step):
            step(f"Getting the forecast from {opts.get('weather_source') or 'Open-Meteo'}")
            wx, note = app.fetch_weather(start, n)
            step(f"Building {nn}")
            sch = pl.build_schedule(start, n, site, optics, targets, opts, prior=prior, weather_by_date=wx or None)
            self._attach_weather(sch, wx)
            step("Working out twilight and Moon times")
            self._prefetch_info(sch)
            return sch, wx, note
        settings.log(f"build schedule {start} × {n}, {len(targets)} targets")
        self._run(work, lambda r, e: self._built(r, e, prior), f"Building {nn}", steps=True)

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

        def work(step):
            step(f"Getting the forecast from {opts.get('weather_source') or 'Open-Meteo'}")
            wx, note = app.fetch_weather(today, n)
            step(f"Re-reviewing {n} night{'s' if n > 1 else ''}")
            # rereview moves past nights to the history and rebuilds from today, keeping the schedule's length
            sch = pl.rereview(prior, today, site, optics, targets, opts, weather_by_date=wx or None)
            self._attach_weather(sch, wx)
            step("Working out twilight and Moon times")
            self._prefetch_info(sch)
            return sch, wx, note
        settings.log(f"re-review from {today}")
        self._run(work, lambda r, e: self._built(r, e, prior), "Re-reviewing", steps=True)

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


class NightTab:
    """One night: the night plot (nightplot.py: TAPIR-style airmass graph with the weather strip above it), the
    nightly table, its actions, and below it flats and darks, didn't fit (with Plan this instead) and free time.

    Speed: the contents are built the first time the tab is shown; the plot is built only while the tab is on screen
    and kept until the night (or the site, targets, dew setting) changes; a row click only restyles it; a resize
    re-renders it once after the window settles (80 ms) and only when the size really changed.

    Size: the plot has a minimum height (in text lines, so it follows the fonts and the display scaling); when the
    window is taller than the tab's contents it takes the spare height, up to a maximum, so the graph grows with the
    window instead of leaving empty space below.

    Selection: a table row and the plot's block go together both ways (click either); the selected target is drawn
    bright and thick, every other target fades."""

    PLAN_INSTEAD_REASONS = ("collides with", "no free stretch", "no room")

    def __init__(self, page: SchedulePage, idx: int):
        self.page, self.app, self.idx = page, page.app, idx
        self.frame = ttk.Frame(page.nb, style="Card.TFrame")
        self.built = False
        self.stale = True
        self.mpl_canvas = None                     # FigureCanvasTkAgg (None without matplotlib)
        self.plot = None                           # nightplot.NightPlot drawn on it
        self._plot_key_now = None                  # what the drawn plot shows (rebuild when it changes)
        self._plot_size = None                     # (width, height) last rendered
        self._plot_event = None
        self._pending = None
        self._syncing = False                      # a plot click is selecting the table row
        self.stale_plot = True
        self._fit_job = None
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

    def _mapped(self):
        """The plot came on screen: render it at its current size and build it if it was left stale."""
        if self.mpl_canvas is None:
            return
        try:
            w = self.plot_widget()
            ev = type("Size", (), {"width": int(w.winfo_width()), "height": int(w.winfo_height())})()
        except tk.TclError:
            return
        if (ev.width, ev.height) != self._plot_size and ev.width > 1 and ev.height > 1:
            self._plot_event = ev
            self._plot_resize()
        if self.stale_plot:
            self.redraw()

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
        # a past night: a ⚠ line above the plot
        self.past_lbl = ttk.Label(inner, text="", style=style.status_style("warn"), justify=tk.LEFT)
        follow_wrap(self.past_lbl, inner, 2 * pad)
        # the night plot (matplotlib; a ⚠ line instead when matplotlib is missing)
        self.plot_box = ttk.Frame(inner, style="Card.TFrame")
        self.plot_box.pack(fill=tk.X, padx=pad, pady=(px(app, 4), 0))
        np_mod = mod("nightplot")
        self.mpl_canvas = np_mod.tk_canvas(self.plot_box) if np_mod is not None else None
        if self.mpl_canvas is not None:
            w = self.mpl_canvas.get_tk_widget()
            w.configure(height=self.graph_heights(0, 0), background=style.CARD)
            w.pack(fill=tk.X)
            w.bind("<Configure>", self._plot_configured)            # debounced (replaces matplotlib's own)
            w.bind("<Map>", lambda _e: self.frame.after(30, self._mapped), add="+")
            self.mpl_canvas.mpl_connect("resize_event", self._resized)
            self.mpl_canvas.mpl_connect("motion_notify_event", self._plot_motion)
            self.mpl_canvas.mpl_connect("button_press_event", self._plot_click)
            self.mpl_canvas.mpl_connect("figure_leave_event", lambda _e: self.hover_var.set(""))
        else:
            text = np_mod.missing_text() if np_mod is not None else \
                "⚠ Night plot needs matplotlib — run Create shortcuts.bat again"
            self.plot_missing = ttk.Label(self.plot_box, text=text, style=style.status_style("warn"),
                                          justify=tk.LEFT)
            self.plot_missing.pack(anchor="w", fill=tk.X, pady=px(app, 4))
        self.hover_var = tk.StringVar()
        self.hover_lbl = ttk.Label(inner, textvariable=self.hover_var, style="Card.TLabel")
        self.hover_lbl.pack(anchor="w", fill=tk.X, padx=pad, pady=(px(app, 2), 0))
        legend = ("Faint line: the target all night  ·  bold: its block, in the color of its type (green Transit, navy "
                  "Variable, plum Campaign), with the airmass at the start and the end  ·  ▼ flip inside the block; | meridian, no flip "
                  "needed  ·  "
                  "dashed gold: the Moon (thicker = fuller)  ·  green band: the transit, lighter: its baselines  ·  "
                  "hatched: a no-transit window that changed the plan  ·  gold box edge: locked or set by you  ·  "
                  "click a block or a row to pick it out; point at the plot for values")
        lg = ttk.Label(inner, text=legend, style="Small.TLabel", justify=tk.LEFT)
        lg.pack(anchor="w", fill=tk.X, padx=pad, pady=(px(app, 2), px(app, 8)))
        follow_wrap(lg, inner, 2 * pad)
        # the plot grows with the window: refit when the visible area or the contents change size
        self.body.canvas.bind("<Configure>", lambda _e: self._fit_later(), add="+")
        inner.bind("<Configure>", lambda _e: self._fit_later(), add="+")


        # nightly table
        cols = (("time", "Time"), ("target", "Target"), ("radec", "RA / Dec (J2000)"),
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
                                       width=max(len(s) for s in STATUS_CHOICES) + 2)
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

    # ---- plot height ----
    GRAPH_LINES = (32.0, 64.0)          # min, max height of the night plot in text lines
    GRAPH_SHARE = 1.0                   # share of the spare window height the plot takes

    def graph_heights(self, view_h: int, other_h: int) -> int:
        """Height of the night plot for a visible area `view_h` px tall whose other contents take `other_h` px:
        the minimum, plus the spare height (so the plot fills the window instead of leaving space below), up to the
        maximum."""
        lh = self.lh
        lo, hi = lh * self.GRAPH_LINES[0], lh * self.GRAPH_LINES[1]
        spare = max(0.0, view_h - other_h - lo - px(self.app, 4))
        return int(min(hi, lo + spare * self.GRAPH_SHARE))

    def _fit_later(self):
        if self._fit_job is None:
            self._fit_job = self.frame.after(30, self.fit_height)

    def plot_widget(self):
        return self.mpl_canvas.get_tk_widget() if self.mpl_canvas is not None else None

    def fit_height(self) -> bool:
        """Size the plot for the visible area; True when its height changed."""
        self._fit_job = None
        w = self.plot_widget() if self.built else None
        if w is None:
            return False
        try:
            view_h = int(self.body.canvas.winfo_height())
            if view_h < 20:
                return False
            h = int(w.cget("height"))
            other = int(self.body.inner.winfo_reqheight()) - h
            nh = self.graph_heights(view_h, other)
            if abs(nh - h) < 3:
                return False
            w.configure(height=nh)
        except tk.TclError:
            return False
        return True

    def redraw(self):
        """Draw the plot now if this tab is on screen (else when it is shown)."""
        if not self.built or self.mpl_canvas is None:
            return
        try:
            if not self.plot_widget().winfo_viewable():
                self.stale_plot = True
                return
        except tk.TclError:
            return
        self.stale_plot = False
        self.draw_plot()


    # ---- data ----
    def night(self) -> NightPlan:
        return self.page.night(self.idx)

    def tab_text(self) -> str:
        n = self.app.schedule.nights[self.idx]
        warn = (any(b.get("cautions") for b in n.get("blocks", [])) or n.get("didnt_fit")
                or any(str(x).startswith(("✖", "⚠")) for x in n.get("notes", []) or []))
        past = " (past)" if str(n.get("date", "")) < self.app.tonight().isoformat() else ""
        return ("⚠ " if warn else "") + night_label(n.get("date", ""), self.app.site) + past

    def blocks(self) -> list[tuple[int, Block]]:
        bl = self.night().blocks_obj()
        return sorted(enumerate(bl), key=lambda p: p[1].start)

    def show_past(self):
        """A night before tonight: a ⚠ line above the plot (the plot stays: it is the record of that night). When
        every night of the schedule has passed, the line says to build a new schedule."""
        app, pad = self.app, px(self.app, 10)
        night = self.app.schedule.nights[self.idx]
        past = str(night.get("date", "")) < app.tonight().isoformat()
        allpast = self.page.all_past()
        if past:
            text = self.page.past_note() if allpast else (
                f"⚠ {night_label(night.get('date', ''), app.site)} has passed: this tab is the record of that night "
                "(mark what happened, then Re-review schedule).")
            self.past_lbl.configure(text=text)
            if not self.past_lbl.winfo_manager():
                self.past_lbl.pack(anchor="w", fill=tk.X, padx=pad, pady=(px(app, 4), 0), before=self.plot_box)
        else:
            self.past_lbl.pack_forget()
        return past

    def fill(self):
        if not self.built:
            self.stale = True
            return
        self.stale = False
        self.show_past()
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
            tags = [f"mode{block_type_code(b)}"]          # tinted by the target's type
            if b.status in ("done", "clouded"):
                tags.append("done")
            notes = r["notes"]
            if b.cautions and not any(str(c).strip() in notes for c in b.cautions):
                notes = "; ".join(str(c) for c in b.cautions) + ("; " + notes if notes else "")
            tbl.insert("", tk.END, iid=str(i), tags=tuple(tags),
                       values=(r["time"], target, r["radec"], r["alt"], r["meridian"], r["exposure"],
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
        self._highlight_from_table()
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
        self.status_var.set("" if b.status in ("", "planned", None) else STATUS_LABELS.get(b.status, ""))
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
        if status == "planned":
            self._act(f"✔ {b.name}: mark cleared (planned, not marked).")
        else:
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

    # ---- the night plot ----
    def plot_inputs(self) -> tuple:
        """What nightplot draws: (night, site, targets_by_id, weather, options)."""
        night = self.night()
        return night, self.app.site, self.app.targets_by_id(), night.weather or {}, self.app.options

    def _plot_key(self, night: NightPlan) -> str:
        """Changes when anything the plot shows changes (the night, the site, the planned targets' positions and
        rules, the dew-risk spread): the figure is rebuilt only then."""
        by_id = self.app.targets_by_id()
        tids = sorted({b.get("target_id", "") for b in night.blocks} |
                      {d.get("target_id", "") for d in night.didnt_fit})
        tg = [(t, by_id[t].ra, by_id[t].dec, repr(by_id[t].rules), by_id[t].name) for t in tids if t in by_id]
        return repr((night.to_dict(), self.app.site.to_dict(), tg, self.app.options.get("dew_spread_c")))

    def draw_plot(self):
        """(Re)build the night plot's figure for this night; the table's selected row stays highlighted."""
        if self.mpl_canvas is None:
            return
        night, site, by_id, wx, opts = self.plot_inputs()
        key = self._plot_key(night)
        if self.plot is not None and key == self._plot_key_now:
            self._highlight_from_table()           # the same night: nothing to rebuild
            return
        np_mod = mod("nightplot")
        fig = self.mpl_canvas.figure
        if not night.date or not site.complete():
            fig.clear()
            self.plot = None
            self._plot_key_now = None
            self.mpl_canvas.draw_idle()
            return
        try:
            info = self.app.night_info(date.fromisoformat(night.date))
            self.plot = np_mod.NightPlot(night, site, by_id, wx, opts, figure=fig, info=info).draw()
            self._plot_key_now = key
        except Exception as exc:
            settings.log(f"night plot: {exc!r}\n{traceback.format_exc()}")
            fig.clear()
            fig.text(0.02, 0.5, f"✖ The night plot could not be drawn ({exc}).", color=style.BAD)
            self.plot = None
            self._plot_key_now = None
        self._highlight_from_table()
        self._render()

    def _render(self):
        """Render once at the widget's final size: fit the height first, and size the figure before drawing it
        (rendering at the default size and again after the fit cost 2–4 renders on the first show)."""
        w = self.plot_widget()
        try:
            if self.fit_height():
                return                             # its <Configure> at the new height renders it
            size = (int(w.winfo_width()), int(w.winfo_height()))
        except tk.TclError:
            return
        if size[0] < 20 or size[1] < 20:
            return                                 # not laid out yet: its <Configure> renders it
        if size != self._plot_size:
            self._plot_event = type("Size", (), {"width": size[0], "height": size[1]})()
            self._plot_resize()                    # sets the figure size and draws
        else:
            self.mpl_canvas.draw_idle()

    def _plot_configured(self, event):
        """The plot widget changed size: redraw once the window settles (80 ms), and only when the size really
        changed (matplotlib's own handler would re-render on every step of a drag)."""
        size = (int(event.width), int(event.height))
        if size == self._plot_size:
            return
        self._plot_event = event
        if self._pending is not None:
            try:
                self.frame.after_cancel(self._pending)
            except tk.TclError:
                pass
        self._pending = self.frame.after(80, self._plot_resize)

    def _plot_resize(self):
        self._pending = None
        ev = self._plot_event
        if ev is None or self.mpl_canvas is None:
            return
        try:
            if not self.mpl_canvas.get_tk_widget().winfo_viewable():
                self._plot_size = None             # resize and draw when shown
                return
        except tk.TclError:
            return
        if self.fit_height():
            return                                 # the height changes again: its <Configure> comes next
        self._plot_size = (int(ev.width), int(ev.height))
        self.mpl_canvas.resize(ev)                 # sets the figure size and draws (layout() via resize_event)
        if self.stale_plot:
            self.redraw()

    def _resized(self, _event=None):
        if self.plot is not None:
            self.plot.layout()

    def _plot_xy(self, event):
        """(x, airmass y or None, on the main plot) for a matplotlib mouse event, or None outside the plot."""
        p = self.plot
        if p is None or event.x is None:
            return None
        for ax, main in ((p.ax, True), (p.axw, False)):
            if ax is not None and ax.bbox.contains(event.x, event.y):
                x, y = ax.transData.inverted().transform((event.x, event.y))
                return float(x), (float(y) if main else None), main
        return None

    def _plot_motion(self, event):
        at = self._plot_xy(event)
        text = self.plot.hover_text(*at) if at else ""
        if text != self.hover_var.get():
            self.hover_var.set(text)

    def _plot_click(self, event):
        """A click on a block selects its table row (and so highlights it); a click beside the blocks clears it."""
        if getattr(event, "button", 1) not in (1, None):
            return
        at = self._plot_xy(event)
        if at is None:
            return
        self.pick_block(self.plot.block_at(at[0]))

    def pick_block(self, i: int | None):
        """Select block i's table row (None: no row) from the plot. Guarded: the table's <<TreeviewSelect>> comes
        back to _selected, which only highlights."""
        if self._syncing:
            return
        self._syncing = True
        try:
            if i is not None and self.table.exists(str(i)):
                self.table.selection_set(str(i))
                self.table.see(str(i))
            else:
                self.table.selection_remove(*self.table.selection())
            self._selected()
        finally:
            self._syncing = False

    def _highlight_from_table(self):
        """The selected row's target bright and thick, every other target faded; no row: all normal."""
        if self.plot is None:
            return
        s = self.selected()
        tid = None if s is None else (s[1].target_id or f"#{s[0]}")
        if self.plot.set_highlight(tid) and self.mpl_canvas is not None:
            self.mpl_canvas.draw_idle()


ROW_KEYS = {   # nightly-table column → keys tried in export.table_rows' dicts (lower case)
    "time": ("time",),
    "target": ("target", "name"),
    "radec": ("radec", "ra / dec (j2000)", "ra_dec", "ra / dec"),
    "alt": ("alt_x",),
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
            "mode": TYPE_NAMES[block_type_code(b)], "mode_code": block_type_code(b), "alt": alt, "meridian": mer,
            "exposure": exp, "frames": frames, "notes": "; ".join(str(n) for n in b.notes)}


# ---- 4 OBSPlan ------------------------------------------------------------------------------------------------------

class ExportPage:
    """4 OBSPlan (John's name for the printed plan; the class keeps its v1.0 name): the plan card itself, on screen
    (one scrolling page for the ticked nights, laid out like the printed card), Print (the HTML card in the browser), and per night Export to ASIAIR / Export to NINA (a CSV file each;
    both programs plan one night at a time)."""
    title = "OBSPlan"
    subtitle = ("The plan card for the ticked nights. Print it, or send one night at a time to ASIAIR or NINA: "
                "their imports take the targets' positions only; times, exposures and notes stay on the card.")
    PROGRAMS = (("asiair", "ASIAIR"), ("nina", "NINA"))
    HOW = {"asiair": "In ASIAIR's Plan: Add → Import, and paste the file's text.",
           "nina": "In NINA: Sequencer → Import targets, and pick this file. It REPLACES the targets now in the "
                   "sequence; each target gets your default sequence template."}
    COLUMNS = ("Time", "Target", "RA / Dec (J2000)", "Alt", "Meridian", "Exposure", "Frames", "Notes")

    def __init__(self, app: App, parent):
        self.app = app
        self.dirty = True
        self.frame = ttk.Frame(parent, style="Card.TFrame")
        # ⚠ bar: plan out of date (Setup or targets changed since the build) and / or every night past
        self.warn_var = tk.StringVar()
        self.warn_lbl = ttk.Label(self.frame, textvariable=self.warn_var, style=style.status_style("warn"),
                                  justify=tk.LEFT, padding=(px(app, 8), px(app, 4)))
        follow_wrap(self.warn_lbl, self.frame, px(app, 30))
        top = ttk.Frame(self.frame, style="Card.TFrame")
        top.pack(fill=tk.X)
        self._top = top
        ttk.Label(top, text="Nights", style="Bold.TLabel").pack(anchor="w")
        self.checks = ttk.Frame(top, style="Card.TFrame")
        self.checks.pack(fill=tk.X, pady=(px(app, 2), px(app, 6)))
        self.checks.bind("<Configure>", lambda e: self._flow_checks(e.width), add="+")
        self.check_widgets: list = []
        self.night_vars: dict[str, tk.BooleanVar] = {}
        bar = ttk.Frame(self.frame, style="Card.TFrame")
        bar.pack(fill=tk.X, pady=(0, px(app, 4)))
        style.green_button(bar, "Print plan card", self.print_card).pack(side=tk.LEFT)
        self.plots_var = tk.BooleanVar(value=bool(app.settings.get("card_plots", True)))
        ttk.Checkbutton(bar, text="Include night plots", variable=self.plots_var, style="Card.TCheckbutton",
                        command=self._plots_changed).pack(side=tk.LEFT, padx=(px(app, 12), 0))
        self.angle_var = tk.StringVar()
        angle = ttk.Label(self.frame, textvariable=self.angle_var, style="Hint.TLabel", justify=tk.LEFT)
        angle.pack(anchor="w", fill=tk.X)
        follow_wrap(angle, self.frame, px(app, 20))
        # where Export to ASIAIR writes: e.g. an iCloud / OneDrive folder that syncs to the phone running ASIAIR
        fold = ttk.Frame(self.frame, style="Card.TFrame")
        fold.pack(fill=tk.X, pady=(px(app, 4), px(app, 4)))
        ttk.Label(fold, text="ASIAIR export folder:", style="Bold.TLabel").pack(side=tk.LEFT)
        ttk.Button(fold, text="Choose folder…", command=self.choose_folder).pack(side=tk.LEFT, padx=(px(app, 8), 0))
        self.downloads_btn = ttk.Button(fold, text="Use Downloads", command=self.use_downloads)
        self.folder_var = tk.StringVar()
        self.folder_label = ttk.Label(fold, textvariable=self.folder_var, style="Card.TLabel")
        self.folder_label.pack(side=tk.LEFT, padx=(px(app, 8), 0))
        self._fold = fold
        hint = ttk.Label(self.frame, text="A folder that syncs to the phone or tablet running ASIAIR (iCloud Drive, "
                                          "OneDrive) puts each night's file there. NINA files go to the Downloads "
                                          "folder.", style="Hint.TLabel", justify=tk.LEFT)
        hint.pack(anchor="w", fill=tk.X)
        follow_wrap(hint, self.frame, px(app, 20))
        self.msg_var = tk.StringVar()            # the last message of any action on this page
        self.page_msg_var = tk.StringVar()       # shown here: Print and folder messages (night ones go by the night)
        self.page_msg = ttk.Label(self.frame, textvariable=self.page_msg_var, style="Card.TLabel", justify=tk.LEFT)
        self.page_msg.pack(anchor="w", fill=tk.X, pady=(px(app, 2), px(app, 4)))
        follow_wrap(self.page_msg, self.frame, px(app, 20))
        self.body = ScrollBody(self.frame)
        self.body.outer.pack(fill=tk.BOTH, expand=True)
        self.card = self.body.inner
        self.nights_ui: dict[str, dict] = {}
        self.results: dict[tuple, dict] = {}     # (night ISO, "asiair" / "nina" / "copy") -> {msg, path, text, shown}

    # ---- nights ----
    def refresh(self):
        for w in self.checks.winfo_children():
            w.destroy()
        self.check_widgets = []
        nights = self.app.schedule.nights
        old = {k: v.get() for k, v in self.night_vars.items()}
        self.night_vars = {}
        if not nights:
            self.check_widgets.append(ttk.Label(self.checks, text="No schedule yet: build one on the Schedule page.",
                                                style="Hint.TLabel"))
        tonight = self.app.tonight().isoformat()
        for n in nights:
            iso = n.get("date", "")
            var = tk.BooleanVar(value=old.get(iso, iso >= tonight or len(nights) == 1))
            var.trace_add("write", lambda *_: self.update_preview())
            self.night_vars[iso] = var
            nb = len(n.get("blocks", []))
            self.check_widgets.append(ttk.Checkbutton(
                self.checks, text=f"{night_label(iso, self.app.site)} ({nb} block{'s' if nb != 1 else ''})",
                variable=var, style="Card.TCheckbutton"))
        self._flow_checks(self.checks.winfo_width())
        if nights and not any(v.get() for v in self.night_vars.values()):
            for v in self.night_vars.values():
                v.set(True)
        # a result belongs to the night as it was when the file was written: a rebuilt night drops it
        self.results = {k: v for k, v in self.results.items() if v.get("fp") == self.fingerprint(k[0])}
        self.update_preview()

    def _flow_checks(self, width: int):
        """Lay the night ticks out left to right in as many rows as the page width needs."""
        width = int(width or 0)
        if width <= 1:
            width = max(1, self.frame.winfo_width())
        key = (width, tuple(str(w) for w in self.check_widgets))
        if key == getattr(self, "_flow_key", None):
            return                                  # our own re-layout changed the height only
        self._flow_key = key
        for f in getattr(self, "_check_rows_frames", []):
            try:
                f.destroy()
            except tk.TclError:
                pass
        self._check_rows_frames = []
        gap = px(self.app, 12)
        x, row = 0, None
        for w in self.check_widgets:
            try:
                need = w.winfo_reqwidth() + gap
                if row is None or (x and x + need > width):
                    row = ttk.Frame(self.checks, style="Card.TFrame")
                    row.pack(fill=tk.X, anchor="w")
                    self._check_rows_frames.append(row)
                    x = 0
                w.pack(in_=row, side=tk.LEFT, padx=(0, gap))
                w.lift(row)
                x += need
            except tk.TclError:
                pass

    def fingerprint(self, iso: str) -> str | None:
        """What the night's exports were made from (its blocks); None when the night is gone."""
        import hashlib
        import json as _json
        for n in self.app.schedule.nights:
            if n.get("date") == iso:
                data = _json.dumps(n.get("blocks", []), sort_keys=True, default=str)
                return hashlib.sha1(data.encode("utf-8")).hexdigest()
        return None

    def chosen(self) -> list[NightPlan]:
        return [NightPlan.from_dict(n) for n in self.app.schedule.nights if self.night_vars.get(n.get("date"),
                                                                                                tk.BooleanVar()).get()]

    def night_of(self, iso: str) -> NightPlan | None:
        for n in self.app.schedule.nights:
            if n.get("date") == iso:
                return NightPlan.from_dict(n)
        return None

    def _weather_by_date(self, nights):
        return {n.date: n.weather for n in nights if n.weather}

    def _msg(self, text):
        """A page-level message (Print, folder): shown under the buttons."""
        self.msg_var.set(text or "")
        set_msg(self.page_msg_var, self.page_msg, text)

    def angle_text(self) -> str:
        """What the ASIAIR and NINA lists write as the position angle (Setup's usual camera angle)."""
        ex = mod("export")
        if ex is None or not hasattr(ex, "asiair_angle"):
            return ""
        a = ex.asiair_angle(self.app.optics)
        if getattr(self.app.optics, "camera_angle_deg", None) is not None:
            why = "Setup's usual camera angle"
        else:
            why = "set your usual camera angle in Setup"
        return f"ASIAIR and NINA lists: position angle {a:.2f}° ({why})."

    # ---- the ASIAIR folder ----
    def asiair_folder(self) -> str:
        return str(self.app.settings.get("asiair_dir") or "")

    def folder_text(self) -> str:
        f = self.asiair_folder()
        if not f:
            return f"Downloads ({settings.downloads_dir()})"
        if not os.path.isdir(f):
            return f"{f}   ⚠ not found: files go to Downloads"
        return f

    def _show_folder(self):
        text = self.folder_text()
        self.folder_var.set(text)
        self.folder_label.configure(style="Warn.TLabel" if "⚠" in text else "Card.TLabel")
        if self.asiair_folder():
            if not self.downloads_btn.winfo_manager():
                self.downloads_btn.pack(side=tk.LEFT, padx=(px(self.app, 6), 0), before=self.folder_label)
        else:
            self.downloads_btn.pack_forget()

    def choose_folder(self) -> str | None:
        start = self.asiair_folder() if os.path.isdir(self.asiair_folder()) else settings.downloads_dir()
        path = filedialog.askdirectory(parent=self.app, title="ASIAIR export folder", initialdir=start,
                                       mustexist=True)
        if not path:
            return None
        return self.set_folder(path)

    def set_folder(self, path: str) -> str:
        path = os.path.abspath(path) if path else ""
        self.app.settings["asiair_dir"] = path
        settings.save_settings(self.app.settings)
        self._show_folder()
        self._msg(f"✔ Export to ASIAIR now writes to {path}." if path else
                  f"✔ Export to ASIAIR now writes to the Downloads folder ({settings.downloads_dir()}).")
        return path

    def use_downloads(self):
        self.set_folder("")

    # ---- the on-screen plan card ----
    STALE = ("⚠ Plan out of date: Setup or targets changed since it was built. Press Build schedule on the "
             "Schedule page before printing or exporting.")

    def stale(self) -> bool:
        """The schedule was built before Setup or the targets last changed (frames and exposures are stored at
        build time)."""
        return bool(self.app.schedule.nights) and bool(getattr(self.app.pages[2], "setup_stale", False))

    def plan_warning(self) -> str:
        lines = []
        past = getattr(self.app.pages[2], "past_note", lambda: "")()
        if past:
            lines.append(past)
        if self.stale():
            lines.append(self.STALE)
        return "\n".join(lines)

    def _show_warn(self):
        text = self.plan_warning()
        self.warn_var.set(text)
        if text and not self.warn_lbl.winfo_manager():
            self.warn_lbl.pack(fill=tk.X, anchor="w", pady=(0, px(self.app, 6)), before=self._top)
        elif not text:
            self.warn_lbl.pack_forget()

    def _stale_mark(self, msg: str) -> str:
        """Print / Export status while the plan is out of date: the action still runs, the line says ⚠."""
        if not self.stale() or not msg:
            return msg
        rest = msg[2:] if msg[:2] in ("✔ ", "⚠ ") else msg
        return f"⚠ Plan out of date (Setup or targets changed since the build). {rest}"

    def update_preview(self):
        """Draw the plan card for the ticked nights (kept under this name: the night ticks call it)."""
        self._show_warn()
        self.angle_var.set(self.angle_text())
        self._show_folder()
        for w in self.card.winfo_children():
            w.destroy()
        self.nights_ui = {}
        ex = mod("export")
        nights = self.chosen()
        if ex is None:
            self._note(missing("export"), "Bad.TLabel")
            return
        if not nights:
            self._note("Tick one or more nights above." if self.app.schedule.nights else
                       "No schedule yet: build one on the Schedule page.", "Hint.TLabel")
            return
        wbd, tbi = self._weather_by_date(nights), self.app.targets_by_id()
        for n in nights:
            try:
                self._night_section(ex, n, wbd, tbi)
            except Exception as exc:
                settings.log(f"export card {n.date}: {exc!r}\n{traceback.format_exc()}")
                self._note(f"✖ {night_label(n.date, self.app.site)}: this night could not be shown ({exc}).",
                           "Bad.TLabel")
        if hasattr(ex, "nights_clock_label"):
            foot = f"All times {ex.nights_clock_label(nights, self.app.site)} ({self.app.site.tz or 'UTC'})"
            ttk.Label(self.card, text=foot, style="Small.TLabel").pack(anchor="w", pady=(px(self.app, 4), 0))

    def card_text(self) -> str:
        """Every text on the on-screen card, in order, one per line (self-test and tests)."""
        out = []

        def walk(w):
            for c in w.winfo_children():
                try:
                    t = c.cget("text")
                except tk.TclError:
                    t = ""
                if t:
                    out.append(str(t))
                walk(c)
        walk(self.card)
        return "\n".join(out)

    def _note(self, text, st):
        holder = ttk.Frame(self.card, style="Card.TFrame")      # destroyed with the label (no handler pile-up)
        holder.pack(fill=tk.X, pady=px(self.app, 6))
        lbl = ttk.Label(holder, text=text, style=st, justify=tk.LEFT)
        lbl.pack(anchor="w", fill=tk.X)
        follow_wrap(lbl, holder, px(self.app, 20))

    def _sections(self, ex, n, wbd, tbi) -> dict:
        if hasattr(ex, "card_sections"):
            return ex.card_sections(n, self.app.site, self.app.settings, wbd, tbi)
        return {"date": night_label(n.date, self.app.site), "iso": n.date, "dark": "", "moon": "", "weather": "",
                "clock": sky.night_local_label(date.fromisoformat(n.date), self.app.site.tz),
                "rows": ex.table_rows(n, self.app.site, tbi), "lists": []}

    def _night_section(self, ex, n: NightPlan, wbd, tbi):
        app = self.app
        sec = self._sections(ex, n, wbd, tbi)
        iso = n.date
        box = ttk.Frame(self.card, style="Card.TFrame")
        box.pack(fill=tk.X, pady=(px(app, 4), px(app, 10)))
        ttk.Separator(box, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, px(app, 6)))
        head = ttk.Frame(box, style="Card.TFrame")
        head.pack(fill=tk.X)
        ttk.Label(head, text=sec["date"], style="Title.TLabel").pack(side=tk.LEFT)
        ui = {"box": box, "buttons": {}}
        for k, (prog, name) in enumerate(self.PROGRAMS):
            b = style.green_button(head, f"Export to {name}", lambda p=prog, i=iso: self.export_night(i, p))
            b.pack(side=tk.LEFT, padx=(px(app, 16) if k == 0 else px(app, 6), 0))
            ui["buttons"][prog] = b
        ui["buttons"]["copy"] = ttk.Button(head, text="Copy ASIAIR list", command=lambda i=iso: self.copy_csv(i))
        ui["buttons"]["copy"].pack(side=tk.LEFT, padx=(px(app, 6), 0))
        facts = ttk.Frame(box, style="Card.TFrame")
        facts.pack(fill=tk.X, pady=(px(app, 2), 0))
        parts = [(sec.get("dark"), "Bold.TLabel"), (sec.get("moon"), "Card.TLabel"),
                 (f"Times: {sec['clock']}" if sec.get("clock") else "", "Card.TLabel")]
        first = True
        for text, st in parts:
            if not text:
                continue
            if not first:
                ttk.Label(facts, text="·", style="Hint.TLabel").pack(side=tk.LEFT, padx=px(app, 6))
            if text.startswith("✖"):
                st = "Bad.TLabel"
            ttk.Label(facts, text=text, style=st).pack(side=tk.LEFT)
            first = False
        if sec.get("weather"):
            wl = ttk.Label(box, text=sec["weather"], style="Card.TLabel", justify=tk.LEFT)
            wl.pack(anchor="w", fill=tk.X)
            follow_wrap(wl, box, px(app, 10))
        ui["status"] = ttk.Frame(box, style="Card.TFrame")
        ui["status"].pack(fill=tk.X)
        self.nights_ui[iso] = ui
        self._draw_status(iso)
        self._table(box, sec.get("rows") or [])
        lists = [(h, items) for h, items in (sec.get("lists") or []) if items]
        if lists:
            lf = ttk.Frame(box, style="Card.TFrame")
            lf.pack(fill=tk.X)
            body = tkfont.Font(app, font=style.font(10))
            items_lbls = []
            gap = px(app, 18)
            for col, (h, items) in enumerate(lists):
                c = ttk.Frame(lf, style="Card.TFrame")
                c.grid(row=0, column=col, sticky="nw", padx=(0, gap))
                lf.columnconfigure(col, weight=1, uniform="lists")
                ttk.Label(c, text=h, style="Section.TLabel").pack(anchor="w")
                for x in items:
                    lbl = ttk.Label(c, text=f"•  {x}", style="Card.TLabel", justify=tk.LEFT,
                                    wraplength=body.measure("0") * 24)
                    lbl.pack(anchor="w")
                    items_lbls.append(lbl)

            def wrap_lists(e, labels=items_lbls, k=len(lists)):
                w = max(body.measure("0") * 16, int(e.width / k) - gap - px(app, 4))
                for lb in labels:
                    try:
                        lb.configure(wraplength=w)
                    except tk.TclError:
                        pass
            lf.bind("<Configure>", wrap_lists, add="+")

    def _table(self, parent, rows: list[dict]):
        """The nightly table as a grid of labels (rows grow to fit wrapped notes; widths from the content)."""
        app = self.app
        if not rows:
            ttk.Label(parent, text="No blocks planned.", style="Hint.TLabel").pack(anchor="w", pady=px(app, 4))
            return
        grid = tk.Frame(parent, background=style.LINE, padx=0, pady=0)
        grid.pack(fill=tk.X, pady=(px(app, 6), px(app, 6)))
        ch = tkfont.Font(app, font=style.font(10)).measure("0")
        pad = (px(app, 5), px(app, 3))

        def cell(r, c, text, font=None, bg="#ffffff", fg=style.TEXT, wrap=0):
            lbl = tk.Label(grid, text=text, font=font or style.font(10), background=bg, foreground=fg, anchor="nw",
                           justify=tk.LEFT, padx=pad[0], pady=pad[1], wraplength=wrap)
            lbl.grid(row=r, column=c, sticky="nsew", padx=(1, 1 if c == len(self.COLUMNS) - 1 else 0),
                     pady=(1, 1 if r == len(rows) else 0))
            return lbl

        notes, heads_notes = [], []
        for c, h in enumerate(self.COLUMNS):
            lb = cell(0, c, h, style.font(9, "bold"), bg="#eef3f6", fg=style.SIDE)
            if c == len(self.COLUMNS) - 1:
                heads_notes.append(lb)
        grid.columnconfigure(len(self.COLUMNS) - 1, weight=1)
        for i, r in enumerate(rows, 1):
            lock = " (locked)" if r.get("locked") else (" (set by you)" if r.get("set_by_user") else "")
            status = "" if r.get("status", "planned") == "planned" else f"\n[{r.get('status_label', r['status'])}]"
            exp = str(r.get("exposure", "—"))
            if r.get("exposure_is_estimate") and exp != "—":
                exp += " est."
            code = str(r.get("mode_code") or "")
            cell(i, 0, r.get("time", ""), style.font(12, "bold"))
            # the target's name on its type's tint (Transit / Variable / Campaign); no mode column (1.1.0)
            cell(i, 1, f"{r.get('target', '')}{lock}{status}", style.font(11, "bold"), wrap=ch * 18,
                 bg=style.mode_color(code, "tint") if code else "#ffffff")
            cell(i, 2, "\n".join(str(r.get("radec", "—")).split(" ", 1)))
            cell(i, 3, f"{r.get('alt', '—')}\n{r.get('alt_x', '—')}")
            cell(i, 4, r.get("meridian", "—"), style.font(11, "bold"))
            cell(i, 5, exp)
            cell(i, 6, r.get("frames", "—"), wrap=ch * 14)
            notes.append(cell(i, 7, r.get("notes", ""), wrap=ch * 46))

        def fit_notes(e=None, notes=notes):
            """Notes wrap to the room the other columns leave (ch × 46 at most, ch × 16 at least)."""
            try:
                width = int(e.width) if e is not None else grid.winfo_width()
                if width <= 1:
                    return
                used = 0
                for c in range(len(self.COLUMNS) - 1):
                    used += max((w.winfo_reqwidth() for w in grid.grid_slaves(column=c)), default=0) + 1
                room = width - used - 2 - 2 * pad[0] - 2
                wrap = int(min(ch * 46, max(ch * 16, room)))
                for lb in notes + heads_notes:
                    if int(float(lb.cget("wraplength") or 0)) != wrap:
                        lb.configure(wraplength=wrap)
            except tk.TclError:
                pass
        grid.bind("<Configure>", fit_notes, add="+")

    # ---- per-night results (status line, Open folder, Open in Notepad, Show file contents) ----
    def _draw_status(self, iso: str):
        ui = self.nights_ui.get(iso)
        if not ui:
            return
        st = ui["status"]
        for w in st.winfo_children():
            w.destroy()
        app = self.app
        for prog in ("asiair", "nina", "copy"):
            r = self.results.get((iso, prog))
            if not r:
                continue
            row = ttk.Frame(st, style="Card.TFrame")
            row.pack(fill=tk.X, pady=(px(app, 4), 0))
            lbl = ttk.Label(row, text=r["msg"], style=style.status_style(style.kind_of(r["msg"])), justify=tk.LEFT)
            lbl.pack(anchor="w", fill=tk.X)
            follow_wrap(lbl, row, px(app, 10))
            ui[prog] = {"label": lbl, "contents": None}
            path = r.get("path")
            if not path:
                continue
            btns = ttk.Frame(row, style="Card.TFrame")
            btns.pack(anchor="w", pady=(px(app, 2), 0))
            ttk.Button(btns, text="Open folder", command=lambda p=path: self.open_folder(p)).pack(side=tk.LEFT)
            ttk.Button(btns, text="Open in Notepad", command=lambda p=path: self.open_in_notepad(p)).pack(
                side=tk.LEFT, padx=(px(app, 6), 0))
            ttk.Button(btns, text="Hide file contents ▾" if r.get("shown") else "Show file contents ▸",
                       command=lambda pr=prog: self.toggle_contents(iso, pr)).pack(side=tk.LEFT, padx=(px(app, 6), 0))
            if r.get("shown"):
                text = (r.get("text") or "").replace("\r\n", "\n").rstrip("\n")
                lines = text.splitlines() or [""]
                t = tk.Text(row, wrap="none", font=(style.MONO_FONT, 9), relief="solid", borderwidth=1,
                            highlightthickness=0, background="#fbfcfd", foreground=style.TEXT,
                            height=min(len(lines), 14), width=min(140, max(len(x) for x in lines) + 2),
                            padx=px(app, 6), pady=px(app, 4))
                t.insert("1.0", text)
                t.configure(state="disabled")
                t.pack(anchor="w", pady=(px(app, 4), 0))
                ui[prog]["contents"] = t

    def toggle_contents(self, iso: str, prog: str) -> bool:
        r = self.results.get((iso, prog))
        if not r:
            return False
        r["shown"] = not r.get("shown")
        self._draw_status(iso)
        return r["shown"]

    def _result(self, iso: str, prog: str, msg: str, path: str = "", text: str = ""):
        self.results[(iso, prog)] = {"msg": msg, "path": path, "text": text, "shown": False,
                                     "fp": self.fingerprint(iso)}
        self.msg_var.set(msg)
        self._draw_status(iso)

    # ---- Export to ASIAIR / NINA, Copy ----
    def _targets_of(self, ex, night) -> tuple[int, list]:
        if hasattr(ex, "asiair_targets"):
            try:
                rows, skipped = ex.asiair_targets([night], self.app.targets_by_id(), self.app.site)
                return len(rows), list(skipped)
            except Exception:
                pass
        return 0, []

    def export_night(self, iso: str, program: str) -> str | None:
        """Write ONE night's file for ASIAIR (to the ASIAIR export folder, else Downloads) or NINA (to the
        Downloads folder); the full path goes in the night's ✔ status line."""
        app = self.app
        name = dict(self.PROGRAMS).get(program, program)
        ex = mod("export")
        if ex is None or not hasattr(ex, "night_file_text"):
            self._result(iso, program, missing("export"))
            return None
        night = self.night_of(iso)
        if night is None:
            self._msg("⚠ That night is no longer in the schedule.")
            return None
        when = night_label(iso, app.site)
        try:
            text = ex.night_file_text(program, night, app.targets_by_id(), app.site, optics=app.optics)
        except Exception as exc:
            settings.log(f"{name} file: {exc!r}\n{traceback.format_exc()}")
            self._result(iso, program, f"✖ {name}: the list for {when} could not be made: {exc}")
            return None
        n, skipped = self._targets_of(ex, night)
        if n == 0:
            self._result(iso, program, f"⚠ {name}: nothing to export for {when}: no target that night has "
                                       "coordinates.")
            return None
        folder = self.asiair_folder() if program == "asiair" else settings.downloads_dir()
        try:
            path, note = ex.write_night_file(text, ex.night_file_name(program, iso), folder, settings.downloads_dir())
        except OSError as exc:
            self._result(iso, program, f"✖ {name}: not saved: {exc.strerror or exc}.")
            return None
        msg = f"{name}: saved {n} target{'s' if n != 1 else ''} for {when} to {path}. {self.HOW[program]}"
        warn = []
        if note:
            warn.append(note.lstrip("⚠ "))
        if skipped:
            warn.append(f"Left out (no coordinates): {', '.join(skipped)}.")
        ang = self.angle_text().replace("ASIAIR and NINA lists: position", "Position")
        if ang:
            msg += " " + ang
        msg = self._stale_mark((f"⚠ {msg} " + " ".join(warn)) if warn else f"✔ {msg}")
        self._result(iso, program, msg, path, text)
        settings.log(f"{name} file for {iso} saved to {path} ({n} rows)")
        return path

    def csv_text(self, iso: str | None = None) -> str | None:
        """The ASIAIR list of one night (the first ticked night when none is given)."""
        ex = mod("export")
        if ex is None:
            self._msg(missing("export"))
            return None
        nights = [self.night_of(iso)] if iso else self.chosen()[:1]
        nights = [n for n in nights if n is not None]
        if not nights:
            self._msg("⚠ Tick one or more nights first.")
            return None
        try:
            return ex.asiair_csv(nights, self.app.targets_by_id(), self.app.site, optics=self.app.optics)
        except Exception as exc:
            settings.log(f"asiair_csv: {exc!r}\n{traceback.format_exc()}")
            self._msg(f"✖ The CSV could not be made: {exc}")
            return None

    def copy_csv(self, iso: str | None = None):
        """Copy one night's ASIAIR list to the clipboard (the secondary way: Export to ASIAIR writes the file)."""
        if iso is None:
            ch = self.chosen()
            iso = ch[0].date if ch else None
        text = self.csv_text(iso)
        if text is None or iso is None:
            return
        try:
            self.app.clipboard_clear()
            self.app.clipboard_append(text)
            self.app.update_idletasks()
        except tk.TclError as exc:
            self._result(iso, "copy", f"✖ The clipboard did not take the list ({exc}).")
            return
        ex = mod("export")
        n, skipped = self._targets_of(ex, self.night_of(iso)) if ex is not None else (0, [])
        if not n:
            n = max(0, len([l for l in text.splitlines() if l.strip()]) - 1)
        when = night_label(iso, self.app.site)
        msg = f"✔ Copied {n} target{'s' if n != 1 else ''} for {when}. In ASIAIR's Plan: Add → Import, and paste."
        if skipped:
            msg = (f"⚠ Copied {n} target{'s' if n != 1 else ''} for {when}; left out (no coordinates): "
                   f"{', '.join(skipped)}.")
        ang = self.angle_text().replace("ASIAIR and NINA lists: position", "Position")
        if ang:
            msg += " " + ang
        self._result(iso, "copy", self._stale_mark(msg))
        settings.log(f"ASIAIR CSV copied for {iso} ({n} rows)")

    # ---- opening files and folders (never in tests) ----
    def _launch(self, args_win, args_other) -> bool:
        if self.app.testing:
            return True
        try:
            if sys.platform == "win32":
                if args_win[0] == "startfile":
                    os.startfile(args_win[1])          # noqa: S606 - the user's own folder
                else:
                    import subprocess
                    subprocess.Popen(args_win)
            else:
                import subprocess
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open"] + args_other)
        except Exception as exc:
            self._msg(f"✖ It did not open ({exc}).")
            return False
        return True

    def open_folder(self, path: str) -> str | None:
        """Show the folder a file went to in Explorer (Windows) or the file manager."""
        folder = os.path.dirname(path) if path else ""
        if not folder or not os.path.isdir(folder):
            self._msg(f"⚠ The folder {folder or '?'} is not there any more.")
            return None
        return folder if self._launch(["startfile", folder], [folder]) else None

    def open_in_notepad(self, path: str) -> str | None:
        """Open the file in Notepad (Windows), else the system's text editor."""
        if not path or not os.path.isfile(path):
            self._msg(f"⚠ The file {path or '?'} is not there any more.")
            return None
        return path if self._launch(["notepad.exe", path], [path]) else None

    # ---- Print ----
    def _plots_changed(self):
        self.app.settings["card_plots"] = bool(self.plots_var.get())
        settings.save_settings(self.app.settings)

    def card_plots(self, nights, ctx=None, progress=None) -> dict | None:
        """Night plots for the printed card ({night ISO: PNG bytes}, white print style); None when matplotlib is
        missing or no night could be drawn (the card then prints exactly as before). Runs in a background job:
        ctx = (targets_by_id, site, options) taken on the main thread; progress(i, n) after each night."""
        np_mod = mod("nightplot")
        if np_mod is None:
            return None
        by_id, site, opts = ctx if ctx else (self.app.targets_by_id(), self.app.site, dict(self.app.options))
        plots = {}
        for i, n in enumerate(nights, 1):
            try:
                png = np_mod.png_bytes(n, site, by_id, n.weather or {}, opts, print_style=True)
            except Exception as exc:                 # never let a plot stop the card
                settings.log(f"card_plots {n.date}: {exc!r}")
                png = None
            if png:
                plots[n.date] = png
            if progress is not None and len(nights) > 1:
                progress(i, len(nights))
        return plots or None

    def print_card(self, open_browser: bool = True) -> str | None:
        """Make the plan card and open it. With night plots the drawing runs in the background (busy bar: "Drawing
        the night plots… 2 of 7") and the card opens when it is done: then this returns None and the path is in
        self.last_card_path afterwards. Without plots it is made at once and the path returned."""
        ex = mod("export")
        if ex is None:
            self._msg(missing("export"))
            return None
        nights = self.chosen()
        if not nights:
            self._msg("⚠ Tick one or more nights first.")
            return None
        self.last_card_path = None
        if not self.plots_var.get():
            return self._make_card(ex, nights, None, open_browser)
        if getattr(self, "_drawing", False):
            self._msg("⚠ Still drawing the night plots for the plan card: it opens when they are done.")
            return None
        self._drawing = True
        self._msg("")
        ctx = (self.app.targets_by_id(), self.app.site, dict(self.app.options))

        def work(progress):
            return self.card_plots(nights, ctx=ctx, progress=progress)

        def done(plots, exc):
            self._drawing = False
            if exc is not None:
                settings.log(f"card_plots: {exc!r}")
                plots = None                         # the card still prints, without plots
            self._make_card(ex, nights, plots, open_browser)
        self.app.jobs.run(work, done, None, "Drawing the night plots for the plan card", counted=True)
        return None

    def _make_card(self, ex, nights, plots, open_browser: bool) -> str | None:
        try:
            kw = {"targets_by_id": self.app.targets_by_id()}
            if plots:
                kw["plots"] = plots
            html = ex.plan_card_html(nights, self.app.site, self.app.settings, self._weather_by_date(nights), **kw)
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
        self.last_card_path = path
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
        self._msg(self._stale_mark("✔ The plan card is open in your browser; print it from there."))
        return path


# ---- self-test ------------------------------------------------------------------------------------------------------

# ---- Credits and sources (not a step) --------------------------------------------------------------------------------

class CreditsPage:
    """Credits and sources (the sidebar button below the steps, as in SHOBS-P): the copyright and license, how to cite
    SHOBS-SCHED (once it has a DOI or ASCL entry), and every catalog, service, design and library it uses with the
    acknowledgement each asks for. Back returns to the page you came from."""
    title = "Credits and sources"
    subtitle = ("Every catalog, service, design and library this app uses. Quoted sentences are the acknowledgements "
                "each source asks for; please include them when you publish work planned with it.")

    def __init__(self, app: App, parent):
        self.app = app
        cr = mod("credits")
        self.frame = ttk.Frame(parent, style="Card.TFrame")
        nav = ttk.Frame(self.frame, style="Card.TFrame")
        nav.pack(side=tk.BOTTOM, fill=tk.X, pady=(px(app, 8), 0))
        ttk.Button(nav, text="Save credits as text…", command=self.save_text).pack(side=tk.LEFT)
        ttk.Button(nav, text="Back", command=self.back).pack(side=tk.RIGHT)
        self.msg_var = tk.StringVar()
        self.msg = ttk.Label(nav, textvariable=self.msg_var, style="Card.TLabel")
        self.msg.pack(side=tk.LEFT, padx=px(app, 10))
        head = ttk.Frame(self.frame, style="Card.TFrame")
        head.pack(fill=tk.X, pady=(0, px(app, 8)))
        copy = (f"SHOBS-SCHED {settings.APP_VERSION}   {cr.COPYRIGHT if cr else '© Shiloh Hill Observatory'}")
        ttk.Label(head, text=copy, style="Bold.TLabel").pack(anchor="w")
        if cr:
            lic = ttk.Label(head, text=cr.LICENSE_LINE, style="Card.TLabel", justify=tk.LEFT)
            lic.pack(anchor="w", fill=tk.X)
            follow_wrap(lic, head, px(app, 10))
        cite = cr.citation_text(settings.APP_VERSION) if cr else ""
        if cite:
            ttk.Label(head, text="How to cite SHOBS-SCHED", style="Section.TLabel").pack(anchor="w",
                                                                                     pady=(px(app, 6), 0))
            row = ttk.Frame(head, style="Card.TFrame")
            row.pack(fill=tk.X)
            ttk.Button(row, text="Copy", command=lambda: self.copy(cite)).pack(side=tk.RIGHT, anchor="n")
            cl = ttk.Label(row, text=cite, style="Card.TLabel", justify=tk.LEFT)
            cl.pack(side=tk.LEFT, fill=tk.X, expand=True)
            follow_wrap(cl, row, px(app, 90))
        tf = ttk.Frame(self.frame, style="Card.TFrame")
        tf.pack(fill=tk.BOTH, expand=True)
        self.text = tk.Text(tf, wrap="word", font=style.font(10), background="#ffffff", foreground=style.TEXT,
                            relief="flat", padx=px(app, 8), pady=px(app, 8), height=10)
        vb = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=self.text.yview)
        self.text.configure(yscrollcommand=vb.set)
        vb.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.text.insert("1.0", cr.credits_text() if cr else missing("credits"))
        self.text.configure(state="disabled")

    def refresh(self):
        pass

    def back(self):
        self.app.show_page(self.app.page_index if self.app.page_index is not None else 0)

    def copy(self, text: str):
        try:
            self.app.clipboard_clear()
            self.app.clipboard_append(text)
            self.app.update_idletasks()
        except tk.TclError as exc:
            set_msg(self.msg_var, self.msg, f"✖ Could not copy to the clipboard ({exc}).")
            return
        set_msg(self.msg_var, self.msg, "✔ Citation copied to the clipboard.")

    def whole_text(self) -> str:
        cr = mod("credits")
        if cr is None:
            return missing("credits")
        cite = cr.citation_text(settings.APP_VERSION)
        return (f"SHOBS-SCHED {settings.APP_VERSION}\n{cr.COPYRIGHT}\n{cr.LICENSE_LINE}\n\n"
                + (f"How to cite SHOBS-SCHED:\n  {cite}\n\n" if cite else "") + cr.credits_text())

    def save_text(self, path: str | None = None) -> str | None:
        if path is None:
            if self.app.testing:
                return None
            path = filedialog.asksaveasfilename(parent=self.app, title="Save credits",
                                                initialdir=settings.downloads_dir(),
                                                initialfile="SHOBS-SCHED_credits.txt", defaultextension=".txt",
                                                filetypes=[("Text", "*.txt")])
            if not path:
                return None
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.whole_text())
        except OSError as exc:
            set_msg(self.msg_var, self.msg, f"✖ Could not save: {exc}")
            return None
        set_msg(self.msg_var, self.msg, f"✔ Saved {path}")
        return path


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
