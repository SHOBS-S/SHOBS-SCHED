"""SHOBS-SCHED data model: site, optics, targets, blocks and night plans.

Every class round-trips through plain JSON (to_dict / from_dict) so the settings, target list and saved schedules
are readable text files. Times are Julian Dates in UTC ("jd") everywhere inside the program; only the user interface
and the exports turn them into the site's local clock time (see sky.py)."""
from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass, field
from typing import Any

MODES = ("V", "T", "D")                       # SHOBS-P mode a block will be reduced in
MODE_NAMES = {"V": "Variables", "T": "Transits", "D": "Discovery"}
KINDS = ("transit", "variable", "campaign")   # Targets-page groups (Suggested = transit with suggested=True)
PRIORITIES = ("High", "Normal", "Low")
PRIORITY_RANK = {"High": 0, "Normal": 1, "Low": 2}
BLOCK_STATUS = ("planned", "done", "partial", "clouded")
STATUS_LABELS = {"planned": "Planned", "done": "Done", "partial": "Partial", "clouded": "Clouded out"}


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _from_dict(cls, data: dict | None):
    """Build a dataclass from a dict, ignoring unknown keys (files written by a newer version still load) and
    keeping defaults for missing ones (files written by an older version still load)."""
    data = data or {}
    names = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class Site:
    """Where the telescope is. lon is east-positive (degrees); tz is an IANA zone name ("America/New_York").
    horizon = [(azimuth deg, altitude deg), ...] from the site survey, azimuth from TRUE north, sorted by azimuth;
    empty = flat horizon. The minimum altitude applies on top of the horizon."""
    name: str = ""
    lat: float | None = None
    lon: float | None = None
    elevation_m: float = 0.0
    tz: str = ""
    horizon: list = field(default_factory=list)
    horizon_north: str = "true"               # "true" or "magnetic" (as surveyed)
    magnetic_declination: float = 0.0         # degrees east; used only when horizon_north == "magnetic"
    min_alt: float = 30.0
    max_airmass: float = 2.0
    sun_alt: float = -18.0                    # darkness used for planning (astronomical by default)
    mount: str = "GEM"                        # "GEM" (meridian flips) or "altaz"
    past_meridian_min: float = 5.0            # how long the mount may track past the meridian before it must flip
    temp_unit: str = "C"                      # "C" or "F" (display only)
    length_unit: str = "m"                    # "m" or "ft" (display only)
    date_format: str = "%a %d %b"             # strftime format for night labels

    def complete(self) -> bool:
        return self.lat is not None and self.lon is not None and bool(self.tz)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data):
        s = _from_dict(cls, data)
        s.horizon = [tuple(map(float, p)) for p in (s.horizon or [])]
        return s


@dataclass
class Optics:
    """Telescope and camera. Values typed from a spec sheet carry their key in `estimates` (shown with ⚠)."""
    aperture_mm: float | None = None
    focal_mm: float | None = None
    pixel_um: float | None = None
    binning: int = 1
    telescope: str = ""                       # its name ("14in RC"); SHOBS-P's telescope_name lands here
    camera: str = ""
    color_camera: bool = True                 # one-shot-color (Bayer) or mono
    gain: float | None = None                 # camera gain setting (as in the FITS GAIN keyword)
    e_per_adu: float | None = None
    full_well_e: float | None = None
    linear_limit_adu: float = 32000.0         # raw ADU a star's peak should stay under
    qe: float = 0.8                           # typical peak QE, used only for the cold-start exposure estimate
    estimates: list = field(default_factory=list)

    def pixel_scale(self) -> float | None:
        """Arcseconds per (binned) pixel, or None."""
        if self.focal_mm and self.pixel_um:
            return 206.265 * self.pixel_um * max(1, int(self.binning or 1)) / self.focal_mm
        return None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data):
        return _from_dict(cls, data)


@dataclass
class Planet:
    """A transit ephemeris. t0 in BJD_TDB, period and t14 in days, depth as a fraction (0.015 = 1.5 %)."""
    name: str = ""
    period: float | None = None
    period_err: float | None = None
    t0: float | None = None
    t0_err: float | None = None
    t14: float | None = None
    depth: float | None = None
    reference: str = ""                       # where the ephemeris came from ("Kabáth et al. 2022", "your Tc", ...)
    source: str = ""                          # "NEA", "NEA (most precise)", "ExoFOP", "typed"

    def ok(self) -> bool:
        return bool(self.period and self.t0 and self.t14)

    @classmethod
    def from_dict(cls, data):
        return _from_dict(cls, data)


@dataclass
class VarElements:
    """Light elements of a variable: epoch (HJD or BJD; the planner treats them alike, within ~8 min at worst — the
    HJD/BJD difference is under a second) of a minimum or maximum, period in days. any_time = a pulsator with no
    timed event worth catching (any part of the cycle is useful)."""
    vtype: str = ""
    period: float | None = None
    epoch: float | None = None
    epoch_kind: str = "max"                   # "max" (pulsators) or "min" (eclipsing binaries)
    any_time: bool = True
    event_halfwidth_min: float = 60.0         # minutes either side of the event to cover when timed
    mag_max: float | None = None              # brightest
    mag_min: float | None = None              # faintest

    @classmethod
    def from_dict(cls, data):
        return _from_dict(cls, data)


@dataclass
class Rule:
    """A campaign rule. kind:
      "no_transit": no data while a planet transits; planet = Planet fields as a dict; pad_min = margin either side.
      "min_alt": the campaign's own minimum altitude (value).
      "min_moon_sep": the campaign's own minimum Moon distance in degrees (value).
      "window": only between local clock times value = "HH:MM-HH:MM"."""
    kind: str = ""
    value: Any = None
    planet: dict | None = None
    pad_min: float = 0.0
    note: str = ""

    @classmethod
    def from_dict(cls, data):
        return _from_dict(cls, data)


@dataclass
class Target:
    """One row of the Targets page. kind = transit | variable | campaign; mode = the SHOBS-P mode (V / T / D).
    Discovery fields are campaigns with mode "D". suggested = found by SCHED, not chosen by the user."""
    id: str = field(default_factory=_new_id)
    name: str = ""
    kind: str = "variable"
    mode: str = "V"
    ra: float | None = None                   # degrees, J2000
    dec: float | None = None
    vmag: float | None = None
    priority: str = "Normal"
    enabled: bool = True
    suggested: bool = False
    source: str = ""                          # "NEA", "VSX", "VizieR VSX", "SIMBAD", "typed"
    planet: dict | None = None                # Planet fields (kind == "transit")
    elements: dict | None = None              # VarElements fields (kind == "variable")
    rules: list = field(default_factory=list) # Rule dicts (campaigns; any kind may carry rules)
    block_min: float = 120.0                  # wanted block length for flexible targets (minutes)
    min_block_min: float = 45.0               # shortest block worth scheduling
    goal_per_week: int = 0                    # campaigns: wanted nights per week (0 = no goal)
    exposure_s: float | None = None           # user's exposure (None = SCHED estimates one)
    cadence_overhead_s: float = 3.0           # per-frame overhead (download etc.)
    notes: str = ""
    added: str = ""                           # ISO date added

    def planet_obj(self) -> Planet | None:
        return Planet.from_dict(self.planet) if self.planet else None

    def elements_obj(self) -> VarElements | None:
        return VarElements.from_dict(self.elements) if self.elements else None

    def rules_obj(self) -> list[Rule]:
        return [Rule.from_dict(r) for r in (self.rules or [])]

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data):
        return _from_dict(cls, data)


@dataclass
class Block:
    """A scheduled stretch of one target on one night. start/end in JD (UTC).
    For a transit: t1/tc/t4 are ingress/mid/egress. fixed = time set by the sky (transit, timed minimum);
    locked = set by the user (survives rebuilds; times kept). status follows the Done/Partial/Clouded marks."""
    target_id: str = ""
    name: str = ""
    mode: str = "V"
    kind: str = "variable"
    ra: float | None = None                   # target's J2000 position (copied for the table and exports)
    dec: float | None = None
    start: float = 0.0
    end: float = 0.0
    fixed: bool = False
    locked: bool = False
    set_by_user: bool = False
    t1: float | None = None
    tc: float | None = None
    t4: float | None = None
    tc_sigma_min: float | None = None
    meridian: float | None = None             # JD of the target's meridian crossing within/near the block
    flip_in_block: bool = False
    alt_start: float | None = None
    alt_end: float | None = None
    alt_min: float | None = None
    alt_max: float | None = None
    airmass_start: float | None = None
    airmass_best: float | None = None
    airmass_end: float | None = None
    moon_sep: float | None = None
    exposure_s: float | None = None
    exposure_is_estimate: bool = True
    frames: int | None = None
    frames_in_transit: int | None = None
    peak_adu: float | None = None
    notes: list = field(default_factory=list)       # plain sentences for the Notes column
    cautions: list = field(default_factory=list)    # "⚠ ..." lines
    status: str = "planned"
    rotator_side: str = ""                          # "", "east", "west" (pier side after any flip)

    def minutes(self) -> float:
        return (self.end - self.start) * 1440.0

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data):
        return _from_dict(cls, data)


@dataclass
class NightPlan:
    """One night: date = the local calendar date the night STARTS on (ISO "2026-10-22").
    dusk/dawn = the planning darkness (site.sun_alt); civil/nautical are for the block bar shading."""
    date: str = ""
    dusk: float | None = None
    dawn: float | None = None
    civil_dusk: float | None = None
    civil_dawn: float | None = None
    nautical_dusk: float | None = None
    nautical_dawn: float | None = None
    moon_illum: float | None = None
    moonrise: float | None = None
    moonset: float | None = None
    blocks: list = field(default_factory=list)       # Block dicts, sorted by start
    didnt_fit: list = field(default_factory=list)    # {"target_id","name","reason","next_chance"(ISO date or "")}
    free: list = field(default_factory=list)         # [(start, end), ...] gaps over 15 min
    flats: list = field(default_factory=list)        # sentences ("Flats at rotator 210° (HD 219134, NGC 7789)")
    darks: list = field(default_factory=list)        # sentences ("Darks to shoot: 45 s × 20")
    weather: dict | None = None                      # weather.py's night dict (hours, dew_onset, summary, ...)
    built: str = ""                                  # ISO timestamp of the build
    notes: list = field(default_factory=list)        # night-wide messages ("✖ No dark time tonight: ...")

    def blocks_obj(self) -> list[Block]:
        return [Block.from_dict(b) for b in self.blocks]

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data):
        return _from_dict(cls, data)


@dataclass
class Schedule:
    """The rolling schedule: up to 7 NightPlans plus the history of marks (kept after a night rolls off, so
    campaign progress and Re-review still know what was done)."""
    nights: list = field(default_factory=list)       # NightPlan dicts, by date
    history: list = field(default_factory=list)      # {"date","target_id","name","status","minutes"}
    built: str = ""

    def nights_obj(self) -> list[NightPlan]:
        return [NightPlan.from_dict(n) for n in self.nights]

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data):
        return _from_dict(cls, data)
