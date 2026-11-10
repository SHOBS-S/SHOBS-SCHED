"""SHOBS-SCHED's reader of the shared SHOBS instrument profiles (1.4.0): which SHOBS-FLUX system profile belongs to
Setup's telescope and camera, and the inputs of the noise model (snr.py) for one band, each with where it came from.

Read only: SCHED never writes FLUX's files (shobs_profile is called with make=False throughout, so not even the
~/.shobs folder is created). The data folder is shobs_profile's: ~/.shobs, or $SHOBS_DATA_HOME (tests).

WHICH PROFILE. Setup's choice (options["snr_profile"]): "auto" (the default) matches as SHOBS-FLUX 0.5.3's resolver
does: the system profile whose camera profile id starts with Setup's camera (slug, e.g. zwo-asi2600mc-air), at
Setup's gain and binning when the profile says, whose telescope name is Setup's (when one is typed), with the focal
length within 3 % (ASIAIR's plate-solved FOCALLEN moves a few mm night to night; a profile with no focal length only
matches a Setup with none); the nearest focal length wins. "none" uses no profile; any other value is a profile id.

WHERE EACH INPUT COMES FROM, in this order (the tag shown beside it):
  1. the profile, when its record is measured, typed in FLUX, from a header, worked out or a spec   "from FLUX"
  2. the value typed in Setup's "Photometry noise" box                                                "set here"
  3. the profile's own estimate (FLUX's cold start), else SCHED's cold-start constant                 "estimate"
Zero point, extinction, sky, seeing in focus and defocused, scintillation C_Y: the profile's band. Gain (per Bayer
channel when the bench measured one), read noise, dark current, linear limit and saturation: its camera profile
(gain and linear limit fall back to Setup's camera fields, not to a guess). Overhead per frame: the profile's
cadence, else Setup's per-frame overhead, else 3 s (a target's own "seconds between frames" still wins; planner).
Aperture and pixel scale: Setup's telescope and camera.
The cold-start zero point is worked out from the same constants as planner.predict_peak_adu (so a guess predicts
the same peak as 1.3.0's exposure estimate) and is offered for Setup's main band only: the green channel TG of a
one-shot-color camera, clear (CV) for a mono one."""
from __future__ import annotations

import math
import os

import shobs_profile as sp

BUILT_AGAINST = "1.2.0"            # shobs_profile.SHOBS_PROFILE_VERSION this copy of SCHED was built and tested with
FOCAL_TOL = 0.03
STRONG = ("measured", "user", "header", "derived", "spec")

# the catalog band each observing band is calibrated against (as SHOBS-FLUX's onsky.BAND_CATALOG)
BAND_CATALOG = {"TG": "V", "V": "V", "CV": "V", "TB": "B", "B": "B", "TR": "R", "R": "R", "I": "I",
                "SG": "SG", "SR": "SR", "SI": "SI", "SZ": "SZ", "SU": "SU"}
BAND_WORDS = {"TG": "your green channel (TG)", "TR": "your red channel (TR)", "TB": "your blue channel (TB)",
              "CV": "your clear band (CV)", "V": "your V filter", "B": "your B filter", "R": "your R filter",
              "I": "your I filter", "SG": "your g′ filter", "SR": "your r′ filter", "SI": "your i′ filter",
              "SZ": "your z′ filter", "SU": "your u′ filter"}
BAND_ORDER = ("TG", "TR", "TB", "CV", "V", "B", "R", "I", "SG", "SR", "SI", "SZ", "SU")
BAND_PIXELS = {"TG": 2, "TR": 1, "TB": 1}                       # raw pixels per 2×2 cell (others: 4)
BAND_CHANNEL = {"TG": "G", "TR": "R", "TB": "B"}                # the camera profile's per-channel values
CATALOG_WORDS = {"V": "V", "B": "B", "R": "R", "I": "I", "SG": "g′", "SR": "r′", "SI": "i′", "SZ": "z′", "SU": "u′"}
# a Sun-like star's colour against V (Willmer 2018, ApJS 236, 47: the Sun's absolute magnitudes), used to carry the
# only magnitude SCHED knows (V) into another catalog band. A real star's colour differs: the readout says so.
SUN_COLOUR = {"V": 0.0, "B": 0.63, "R": -0.39, "I": -0.73, "SG": 0.30, "SR": -0.16, "SI": -0.28, "SZ": -0.31,
              "SU": 1.58}
# a dark sky's colour against V (mag/arcsec² in the band minus V's; Walker 1987 / Leinert et al. 1998, a typical dark
# site): a band with no measured sky of its own takes the main band's sky plus this, tagged "estimate"
TYPICAL_SKY_COLOUR = {"V": 0.0, "B": 0.9, "R": -0.9, "I": -1.9, "SU": 0.8, "SG": 0.5, "SR": -0.4, "SI": -1.3,
                      "SZ": -2.0}
# typical extinction (mag/airmass) at a low site until one is measured (as SHOBS-FLUX's TYPICAL_K)
TYPICAL_K = {"B": 0.30, "V": 0.20, "R": 0.13, "I": 0.08, "SU": 0.55, "SG": 0.20, "SR": 0.12, "SI": 0.08, "SZ": 0.06}

# cold-start constants (labelled "estimate")
COLD_READ_NOISE_E = 2.0            # e⁻: a typical cooled CMOS at unity-ish gain
COLD_DARK_E_S = 0.01               # e⁻/px/s: a cooled CMOS near −10 °C
COLD_CY = 1.5                      # Osborn et al. 2015's typical-site C_Y (SHOBS-P's default)
COLD_OVERHEAD_S = 3.0

# Setup's "Photometry noise" fields: options key → (label, unit, lowest, highest)
SETUP_FIELDS = {
    "snr_zp": ("Zero point", "mag for 1 e⁻/s (main band)", 10.0, 30.0),
    "snr_k": ("Extinction", "mag per airmass", 0.0, 1.5),
    "snr_sky": ("Dark-sky brightness", "mag/arcsec²", 15.0, 23.0),
    "snr_fwhm": ("Seeing (FWHM, in focus)", "arcsec", 0.3, 20.0),
    "snr_read_noise": ("Read noise", "e⁻", 0.0, 50.0),
    "snr_dark": ("Dark current", "e⁻/px/s", 0.0, 10.0),
    "snr_cy": ("Scintillation C_Y", "(Young; 1.5 typical)", 0.1, 10.0),
}


def _num(v):
    f = sp._finite(v)
    return f


def _text(v) -> str:
    return str(v or "").strip()


# ---- finding the profile ----------------------------------------------------------------------------------------------

def data_dir() -> str:
    return sp.data_dir(make=False)


def _signature() -> tuple:
    """The profiles on disk now (names and times), so a FLUX save is seen without restarting SCHED."""
    out = []
    for kind in ("systems", "cameras"):
        d = sp.sub_dir(kind, make=False)
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for n in names:
            if n.endswith(".json"):
                try:
                    out.append((kind, n, os.path.getmtime(os.path.join(d, n))))
                except OSError:
                    pass
    return (data_dir(),) + tuple(out)


_CACHE: dict = {}


def newer_files() -> list[str]:
    """Profile files a NEWER SHOBS app wrote (this copy cannot read them: update SHOBS-SCHED)."""
    _load_all()
    hit = _CACHE.get("all")
    return list(hit[3]) if hit else []


def _load_all() -> tuple[list[dict], dict]:
    """(system profiles, {camera id: camera profile}) that load; cached until a file changes. Files written by a
    newer format are listed apart (newer_files)."""
    sig = _signature()
    hit = _CACHE.get("all")
    if hit and hit[0] == sig:
        return hit[1], hit[2]
    systems, cams, newer = [], {}, []
    for path in sp.list_profiles("systems"):
        try:
            doc = sp.load(path)
        except sp.NewerFormatError:
            newer.append(os.path.basename(path))
            continue
        except sp.ProfileError:
            continue
        if doc.get("format") == sp.SYSTEM_FORMAT and doc.get("profile_id"):
            systems.append(doc)
    for path in sp.list_profiles("cameras"):
        try:
            doc = sp.load(path)
        except sp.NewerFormatError:
            newer.append(os.path.basename(path))
            continue
        except sp.ProfileError:
            continue
        if doc.get("format") == sp.CAMERA_FORMAT and doc.get("profile_id"):
            cams[doc["profile_id"]] = doc
    _CACHE["all"] = (sig, systems, cams, newer)
    return systems, cams


def list_systems() -> list[tuple[str, str]]:
    """[(profile id, a label)] of every system profile that loads, for Setup's drop-down."""
    systems, _c = _load_all()
    out = []
    for d in systems:
        tel = d.get("telescope") or {}
        f = _num(tel.get("focal_mm"))
        out.append((d["profile_id"], f"{_text(tel.get('name')) or 'telescope'}"
                                     f"{f' {f:.0f} mm' if f else ''} + {d.get('camera_profile') or 'camera'}"))
    return sorted(out)


def same_focal(a, b) -> bool:
    """Two focal lengths are one setup within 3 % (both unknown counts as the same; as SHOBS-FLUX's same_focal)."""
    a, b = _num(a), _num(b)
    if not a or not b:
        return not a and not b
    return abs(a - b) <= FOCAL_TOL * max(a, b)


def matches(doc: dict, optics, cams: dict | None = None) -> float | None:
    """How far this system profile is from Setup's telescope and camera (|Δ focal| in mm), or None when it is not
    theirs."""
    cam = sp.slug(getattr(optics, "camera", "") or "")
    if cam == "unknown":
        return None
    cid = _text(doc.get("camera_profile"))
    if not (cid == cam or cid.startswith(cam + "__")):
        return None
    cdoc = (cams or {}).get(cid) or {}
    mode = cdoc.get("mode") or {}
    g, mg = _num(getattr(optics, "gain", None)), _num(mode.get("gain_setting"))
    if g is not None and mg is not None and abs(g - mg) > 1e-6:
        return None
    b, mb = _num(getattr(optics, "binning", None) or 1), _num(mode.get("binning"))
    if mb is not None and b is not None and int(round(mb)) != int(round(b)):
        return None
    tel = doc.get("telescope") or {}
    want = sp.slug(getattr(optics, "telescope", "") or "")
    if want != "unknown" and sp.slug(tel.get("name") or "") != want:
        return None
    f, sf = _num(tel.get("focal_mm")), _num(getattr(optics, "focal_mm", None))
    if not same_focal(f, sf):
        return None
    return abs(f - sf) if f and sf else 0.0


def resolve(optics, options: dict | None) -> dict | None:
    """{"id", "system", "camera" (or None), "how" ("matched" / "chosen")} for Setup's choice, or None (no profile,
    "none" chosen, or the chosen one is gone)."""
    choice = _text((options or {}).get("snr_profile")) or "auto"
    if choice == "none":
        return None
    systems, cams = _load_all()
    if choice != "auto":
        doc = next((d for d in systems if d["profile_id"] == choice), None)
        if doc is None:
            return None
        return {"id": choice, "system": doc, "camera": cams.get(_text(doc.get("camera_profile"))), "how": "chosen"}
    found = [(dist, d) for d in systems for dist in (matches(d, optics, cams),) if dist is not None]
    found = _one_camera(found, optics, cams)
    if not found:
        return None
    doc = min(found, key=lambda x: x[0])[1]
    return {"id": doc["profile_id"], "system": doc, "camera": cams.get(_text(doc.get("camera_profile"))),
            "how": "matched"}


def _gain_of(cams: dict, cid: str):
    v, _r = sp.camera_value(cams.get(cid), "gain_e_per_adu")
    return _num(v)


def _one_camera(found: list, optics, cams: dict) -> list:
    """With no gain setting in Setup, the matches may hold several camera profiles (one per gain): keep the one
    whose e⁻/ADU is nearest Setup's e⁻ per ADU; [] when that cannot decide (Setup then asks for a choice)."""
    ids = {_text(d.get("camera_profile")) for _dist, d in found}
    if len(ids) <= 1 or _num(getattr(optics, "gain", None)) is not None:
        return found
    want = _num(getattr(optics, "e_per_adu", None))
    if want is None:
        return []
    gaps = {cid: abs(g - want) for cid in ids for g in (_gain_of(cams, cid),) if g is not None}
    if not gaps:
        return []
    low = min(gaps.values())
    near = [cid for cid, gap in gaps.items() if gap - low <= 1e-6]
    if len(near) != 1:
        return []
    return [(dist, d) for dist, d in found if _text(d.get("camera_profile")) == near[0]]


def ambiguous(optics, options: dict | None) -> list[str]:
    """The camera profiles that fit Setup equally well when Automatic cannot choose among them ([] otherwise)."""
    if (_text((options or {}).get("snr_profile")) or "auto") != "auto":
        return []
    systems, cams = _load_all()
    found = [(dist, d) for d in systems for dist in (matches(d, optics, cams),) if dist is not None]
    if not found or _one_camera(found, optics, cams):
        return []
    return sorted({_text(d.get("camera_profile")) for _dist, d in found})


def resolve_words(optics, options: dict | None) -> str:
    """One line for Setup: which profile is used, or why none."""
    choice = _text((options or {}).get("snr_profile")) or "auto"
    if choice == "none":
        return "No SHOBS-FLUX profile (you chose none): the values below, or rough estimates, are used."
    res = resolve(optics, options)
    if res is None:
        many = ambiguous(optics, options)
        if many:
            return (f"⚠ Several SHOBS-FLUX camera profiles fit ({', '.join(many)}) and Setup has no gain setting to "
                    "tell them apart: choose the profile above (or type the gain setting under Telescope and "
                    "camera). Until then the values below, or rough estimates, are used.")
        newer = newer_files()
        if newer:
            return (f"⚠ A newer SHOBS-FLUX wrote {len(newer)} profile{'s' if len(newer) != 1 else ''} here "
                    f"({', '.join(newer[:3])}{'…' if len(newer) > 3 else ''}): update SHOBS-SCHED to read "
                    f"{'them' if len(newer) != 1 else 'it'}. Until then the values below, or rough estimates, are "
                    "used.")
        if choice != "auto":
            return (f"⚠ The chosen profile {choice} is not in {data_dir()} any more: choose another, or Automatic. "
                    "Until then the values below, or rough estimates, are used.")
        return ("No SHOBS-FLUX profile matches the telescope and camera above (SCHED works without one): the values "
                "below, or rough estimates, are used.")
    doc = res["system"]
    rev = doc.get("revision")
    when = _text(doc.get("updated"))[:10]
    how = "matches the telescope and camera above" if res["how"] == "matched" else "chosen here"
    return (f"✔ SHOBS-FLUX profile {res['id']} ({how}; revision {rev}, {when}). Its values are shown greyed below "
            "(from FLUX); empty fields use rough estimates. SCHED only reads it.")


# ---- bands -----------------------------------------------------------------------------------------------------------

def main_band(optics) -> str:
    """Setup's main band: the green channel of a one-shot-color camera, clear for a mono one."""
    return "TG" if getattr(optics, "color_camera", True) else "CV"


def _band_doc(res: dict | None, band: str) -> dict:
    return (((res or {}).get("system") or {}).get("bands") or {}).get(band) or {}


def bands(optics, options: dict | None) -> list[str]:
    """The bands there is a zero point for: the profile's bands with one, and Setup's main band (typed zero point
    or the cold-start estimate)."""
    res = resolve(optics, options)
    have = [b for b, d in (((res or {}).get("system") or {}).get("bands") or {}).items()
            if isinstance(d, dict) and sp.value_of(d.get("zero_point")) is not None]
    m = main_band(optics)
    if m not in have:
        have.append(m)
    return sorted(set(have), key=lambda b: (BAND_ORDER.index(b) if b in BAND_ORDER else 99, b))


def band_words(band: str) -> str:
    return BAND_WORDS.get(band, f"your {band} band")


def catalog_mag(vmag, band: str) -> tuple[float | None, str]:
    """(the target's magnitude in the band's catalog band, a line saying which magnitude and how). SCHED knows the
    V magnitude only; another catalog band gets a Sun-like colour."""
    if vmag is None:
        return None, "no V magnitude: type one under Target"
    v = float(vmag)
    cat = BAND_CATALOG.get(band, "V")
    words = band_words(band)
    if cat == "V":
        how = "taken as V" if band != "V" else "V itself"
        return v, f"V {v:.2f} → {words} ({how})"
    off = SUN_COLOUR.get(cat, 0.0)
    cw = CATALOG_WORDS.get(cat, cat)
    sign = "+" if off >= 0 else "−"
    return v + off, (f"V {v:.2f} → {words} ({cw} ≈ V {sign} {abs(off):.2f} for a Sun-like star; SCHED knows only "
                     "V, a redder or bluer star differs)")


# ---- the inputs ------------------------------------------------------------------------------------------------------

def _rec(rec):
    """(value, source) of a value record, or (None, None)."""
    if not sp.is_record(rec):
        return None, None
    v = sp.value_of(rec)
    if v is None or isinstance(v, (list, dict)):
        return None, None
    return float(v), rec.get("source")


def _pick(rec, options: dict, key: str | None, cold):
    """(value, tag): the profile's strong value, else Setup's typed one, else the profile's estimate, else cold."""
    v, src = _rec(rec)
    if v is not None and src in STRONG:
        return v, "from FLUX"
    if key:
        mine = _num((options or {}).get(key))
        if mine is not None:
            return mine, "set here"
    if v is not None:
        return v, "estimate"
    return (None, "") if cold is None else (float(cold), "estimate")


def cold_zero_point(optics, band: str, options: dict | None = None) -> float | None:
    """The cold-start zero point of Setup's main band, from planner's 1.3.0 exposure-model constants (V0 photon
    rate, obstruction, throughput, QE; a mono camera's unfiltered gain): the same peak as predict_peak_adu."""
    import planner
    if band != main_band(optics) or not getattr(optics, "aperture_mm", None):
        return None
    d_cm = float(optics.aperture_mm) / 10.0
    area = math.pi * (d_cm / 2.0) ** 2 * (1.0 - planner.OBSTRUCTION_AREA)
    color = bool(getattr(optics, "color_camera", True))
    band_f = 1.0 if color else planner.MONO_BAND_FACTOR
    n = BAND_PIXELS.get(band, 4)
    rate = planner.V0_PHOTONS * area * planner.OPTICS_THROUGHPUT * float(optics.qe or 0.8) * band_f * n / 4.0
    import sky
    return 2.5 * math.log10(rate) - sky.EXTINCTION_K


def _defocus_record(bd: dict, notes: str):
    """The profile's defocused FWHM record for this target: the key named in its notes ("+125 steps"), else the
    one with the most nights."""
    fd = bd.get("fwhm_defocus") if isinstance(bd.get("fwhm_defocus"), dict) else {}
    low = str(notes or "").lower()
    named = [r for k, r in fd.items() if str(k).lower() in low and _rec(r)[0] is not None]
    if named:
        return named[0]
    valued = [r for r in fd.values() if _rec(r)[0] is not None]
    return max(valued, key=lambda r: _num(r.get("n")) or 0) if valued else None


def inputs(optics, options: dict | None, elevation_m: float = 0.0, band: str | None = None,
           defocused: bool = False, notes: str = ""):
    """snr.Inputs for one band with .sources (key → tag), or None when the telescope and camera are not set up (no
    aperture or pixel scale) or the band has no zero point."""
    import snr
    o = options or {}
    if not optics or not getattr(optics, "aperture_mm", None):
        return None
    scale = optics.pixel_scale() if hasattr(optics, "pixel_scale") else None
    if not scale:
        return None
    res = resolve(optics, o)
    band = band or main_band(optics)
    bd = _band_doc(res, band)
    cam = (res or {}).get("camera") or {}
    main = band == main_band(optics)
    src: dict = {}
    zp, src["zp"] = _pick(bd.get("zero_point"), o, "snr_zp" if main else None,
                          cold_zero_point(optics, band, o) if main else None)
    if zp is None:
        return None
    cat = BAND_CATALOG.get(band, "V")
    import sky
    k, src["k"] = _pick(bd.get("extinction_k"), o, "snr_k", TYPICAL_K.get(cat, sky.EXTINCTION_K))
    dark_v = _num(o.get("dark_sky_v"))
    dark_v = dark_v if dark_v is not None else sky.DARK_SKY_V
    own_v, own_s = _rec(bd.get("sky_brightness"))
    if main or (own_v is not None and own_s in STRONG):
        sky_mag, src["sky"] = _pick(bd.get("sky_brightness"), o, "snr_sky", dark_v)
    else:
        # another band without a measured sky: the main band's sky (measured, typed or 21.5) plus a dark sky's
        # typical colour (never the V sky as it is)
        base, _t = _pick(_band_doc(res, main_band(optics)).get("sky_brightness"), o, "snr_sky", dark_v)
        sky_mag, src["sky"] = base + TYPICAL_SKY_COLOUR.get(cat, 0.0), "estimate"
    f0 = _num(o.get("fwhm_arcsec"))
    fwhm, src["fwhm"] = _pick(bd.get("fwhm_in_focus"), o, "snr_fwhm", f0 if f0 else 3.0)
    if defocused:
        rec = _defocus_record(bd, notes)
        v, s = _rec(rec)
        if v is not None:
            fwhm, src["fwhm"] = v, ("from FLUX" if s in STRONG else "estimate")
        else:
            fac = _num(o.get("defocus_fwhm_factor")) or 1.5
            fwhm = fwhm * max(1.0, fac)
            src["fwhm"] = src["fwhm"] + f" × {max(1.0, fac):g} defocus"
    ch = BAND_CHANNEL.get(band)
    g, grec = sp.camera_value(cam, "gain_e_per_adu", ch)
    if g is not None and _num(g):
        gain, src["gain"] = float(g), "from FLUX"
    elif _num(getattr(optics, "e_per_adu", None)):
        gain, src["gain"] = float(optics.e_per_adu), "Setup"
    elif _num(getattr(optics, "full_well_e", None)):
        gain, src["gain"] = float(optics.full_well_e) / 65535.0, "estimate"
    else:
        gain, src["gain"] = 1.0, "estimate"
    ll, _r = sp.camera_value(cam, "linear_limit_adu", ch)
    if _num(ll):
        linear, src["linear"] = float(ll), "from FLUX"
    else:
        linear, src["linear"] = float(getattr(optics, "linear_limit_adu", None) or 32000.0), "Setup"
    sat, _r = sp.camera_value(cam, "saturation_adu")
    sat = float(sat) if _num(sat) else snr.SATURATION_ADU
    bias, _r = sp.camera_value(cam, "bias_level_adu", ch)
    bias = float(bias) if _num(bias) is not None else 0.0
    rn_rec = (cam.get("values") or {}).get("read_noise_e") if cam else None
    chv = ((cam.get("per_channel") or {}).get(ch) or {}).get("read_noise_e") if (cam and ch) else None
    rn, src["read_noise"] = _pick(chv if _rec(chv)[0] is not None else rn_rec, o, "snr_read_noise",
                                  COLD_READ_NOISE_E)
    dk, src["dark"] = _pick((cam.get("values") or {}).get("dark_current_e_per_px_s") if cam else None, o,
                            "snr_dark", COLD_DARK_E_S)
    cy, src["cy"] = _pick(bd.get("scintillation_cy"), o, "snr_cy", COLD_CY)
    oh_rec = (((res or {}).get("system") or {}).get("cadence") or {}).get("overhead_s")
    ov, s = _rec(oh_rec)
    if ov is not None and s in STRONG:
        overhead, src["overhead"] = ov, "from FLUX"
    elif getattr(optics, "frame_overhead_s", None) is not None:
        overhead, src["overhead"] = float(optics.frame_overhead_s), "Setup"
    else:
        overhead, src["overhead"] = COLD_OVERHEAD_S, "estimate"
    rough = src["zp"] == "estimate"
    if rough:
        src["all"] = "estimate (no FLUX profile)" if res is None else "estimate (FLUX has no measured zero point yet)"
    else:
        src["all"] = src["zp"]
    src["profile"] = (res or {}).get("id") or ""
    return snr.Inputs(zp=zp, k=k, sky_mag=sky_mag, fwhm=fwhm, scale=float(scale),
                      aperture_m=float(optics.aperture_mm) / 1000.0, gain=gain, linear_adu=linear, sat_adu=sat,
                      read_noise_e=rn, dark_e_s=dk, cy=cy, overhead_s=overhead,
                      elevation_m=float(elevation_m or 0.0), pix_per_cell=BAND_PIXELS.get(band, 4), rough=rough,
                      bias_adu=bias, sources=src, peak_cal=src["zp"] == "from FLUX")


def setup_value(optics, options: dict | None, key: str) -> tuple[float | None, str]:
    """For Setup's Photometry noise box: (the profile's value, "from FLUX") when the profile has a strong value for
    this field (it then wins over a typed one), else (None, "")."""
    res = resolve(optics, options)
    if res is None:
        return None, ""
    bd = _band_doc(res, main_band(optics))
    cam = res.get("camera") or {}
    rec = {"snr_zp": bd.get("zero_point"), "snr_k": bd.get("extinction_k"), "snr_sky": bd.get("sky_brightness"),
           "snr_fwhm": bd.get("fwhm_in_focus"), "snr_cy": bd.get("scintillation_cy"),
           "snr_read_noise": (cam.get("values") or {}).get("read_noise_e"),
           "snr_dark": (cam.get("values") or {}).get("dark_current_e_per_px_s")}.get(key)
    v, s = _rec(rec)
    return (v, "from FLUX") if v is not None and s in STRONG else (None, "")
