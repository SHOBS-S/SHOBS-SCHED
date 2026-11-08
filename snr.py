"""SHOBS-SCHED noise model: exposure by S/N and the detectability verdict (1.4.0, GitHub issue #4).

Pure functions only (no files, no network, no Tk): instrument.py gathers the inputs (a SHOBS-FLUX profile, Setup's
own values, or the cold-start constants) and the planner and the Edit dialog call these.

CONVENTIONS (as SHOBS-FLUX's onsky.py and SHOBS-P's photometry_core.py):
  zero point ZP   the magnitude that gives 1 e⁻/s at airmass 1 in the band, counting the electrons of all the band's
                  pixels (a one-shot-color camera's green channel TG has 2 raw pixels in each 2×2 cell, red TR and blue
                  TB one each; a mono camera 4). A star of magnitude m at airmass X gives
                      rate = 10^(−0.4 (m − ZP + k (X − 1)))  e⁻/s.
  sky             mag/arcsec² (as measured, so no extinction is applied again): 10^(−0.4 (S − ZP)) e⁻/s/arcsec².
  scintillation   modified Young approximation (Osborn et al. 2015, MNRAS 452, 1707, eq. 7):
                      σ² = 1e-5 C_Y² D^(−4/3) t^(−1) X³ exp(−2h/H),  D in metres, h the site's elevation, H = 8000 m.
  aperture        radius APERTURE_FWHM × FWHM (the FWHM in focus, or the defocused one); it holds ~99.8 % of a
                  Gaussian star. The pixels inside it carry the sky, dark current and read noise.

ONE FRAME of exposure t (relative variances, i.e. (σ_flux / flux)²):
    photon  = 1 / S                    S = star electrons in the aperture
    sky     = B / S²                   B = sky electrons in the aperture
    read    = N (RN² + D t) / S²       N = band pixels in the aperture, RN read noise, D dark current
    scint   = Osborn's σ² above
A 10-MINUTE BIN holds n = 600 / (t + overhead) frames: σ_bin = sqrt(Σ variances / n). This is the target star alone;
the comparison stars add a little more.

TRANSIT S/N = depth / (σ_bin / sqrt(bins in transit)),  bins in transit = duration / 10 min.
VARIABLE S/N = amplitude (as a flux fraction, 1 − 10^(−0.4 Δmag)) / σ_bin: one 10-minute bin against the range.
Verdict: S/N ≥ 10 Detectable, 5–10 Marginal, under 5 Out of reach; a predicted peak over the linear limit (any way
the exposure was chosen) is "Saturates" whatever the S/N. Inputs that are only the cold-start guess (no measured
zero point) never read as a measurement: the same thresholds, and the word gets a "?" ("Detectable?", "Marginal?",
"Out of reach?", and "Saturates?" because the predicted peak then comes from the same guessed zero point; John,
8 Nov 2026).

THE MOON. The planner's Moon penalty is magnitudes of brightening against Setup's dark sky (21.5, as 1.3.0's
exposure estimate uses it). A profile's measured sky can be much brighter (18.8 at a suburban site): subtracting the
same magnitudes from it would add the Moon several times over. rebase_moon_penalty turns the penalty into the Moon's
own flux and adds that to the measured sky.

PEAK of the brightest raw pixel (saturation guard): Gaussian of the FWHM integrated over the central pixel, times the
band's rate × 4 / (its pixels per cell) (the star's electrons spread over the band's pixels only), plus sky and dark;
in ADU through the gain. With the cold-start inputs it matches planner.predict_peak_adu."""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

BIN_MIN = 10.0                 # precision is given per 10-minute bin
APERTURE_FWHM = 1.5            # photometry aperture radius = this × FWHM
SCALE_HEIGHT_M = 8000.0        # Osborn et al. 2015
DETECTABLE_SNR = 10.0
MARGINAL_SNR = 5.0
NEAR_BEST = 0.02               # Best precision: the shortest exposure within 2 % of the best 10-min precision
SATURATION_ADU = 65535.0       # a 16-bit camera's top (a camera profile's saturation_adu wins)

VERDICTS = ("Detectable", "Marginal", "Out of reach")
SATURATES = "Saturates"        # the predicted peak is over the linear limit (rough: "Saturates?")
ROUGH_MARK = "?"               # a rough estimate's verdict word ends in this


@dataclass
class Inputs:
    """Everything the noise model needs for one band (see the module text for the conventions)."""
    zp: float                          # mag for 1 e⁻/s at airmass 1
    k: float = 0.20                    # extinction, mag per airmass
    sky_mag: float = 21.5              # dark sky, mag/arcsec²
    fwhm: float = 3.0                  # arcsec: the star size used (in focus, or defocused)
    scale: float = 0.27                # arcsec per (binned) raw pixel
    aperture_m: float = 0.356          # telescope aperture (scintillation)
    gain: float = 1.0                  # e⁻ per ADU
    linear_adu: float = 32000.0        # the camera's linear limit, raw ADU
    sat_adu: float = SATURATION_ADU
    read_noise_e: float = 2.0
    dark_e_s: float = 0.01             # e⁻ per pixel per second
    cy: float = 1.5                    # Young's C_Y
    overhead_s: float = 3.0            # per frame on top of the exposure
    elevation_m: float = 0.0
    pix_per_cell: int = 2              # raw band pixels per 2×2 cell: TG 2, TR / TB 1, mono 4
                                       # (a one-shot-color camera binned 2×2 in hardware still keeps its Bayer
                                       # pattern on most CMOS sensors, so the same counts are used; a camera that
                                       # merges colours when binned would want 4 — not modelled)
    bias_adu: float = 0.0              # the camera's bias level (raw ADU), added to the peak (linear limits are raw)
    rough: bool = False                # the zero point is only the cold-start guess
    sources: dict = field(default_factory=dict)   # key → "from FLUX" / "set here" / "estimate" (for the readout)

    def band_share(self) -> float:
        """4 / pixels per cell: how many times one band pixel's light the band's per-cell count is."""
        n = int(self.pix_per_cell or 4)
        return 4.0 / (n if n in (1, 2, 4) else 4)


# ---- rates -----------------------------------------------------------------------------------------------------------

def star_rate(inp: Inputs, mag: float, airmass: float = 1.0) -> float:
    """Star electrons per second in the band (all its pixels)."""
    x = max(1.0, float(airmass or 1.0))
    return 10.0 ** (-0.4 * (float(mag) - inp.zp + inp.k * (x - 1.0)))


def flux_fraction(delta_mag: float) -> float:
    """A range of Δmag as a flux fraction of the bright state: 1 − 10^(−0.4 Δmag)."""
    return 1.0 - 10.0 ** (-0.4 * abs(float(delta_mag)))


def rebase_moon_penalty(moon_penalty: float, dark_v: float, sky_mag: float) -> float:
    """The Moon's brightening (mag) of a sky of sky_mag, from its brightening of a dark_v sky: the Moon's own flux
    10^(−0.4 (dark_v − pen)) − 10^(−0.4 dark_v) added to the sky_mag sky."""
    pen = max(0.0, float(moon_penalty or 0.0))
    if pen <= 0:
        return 0.0
    moon = 10.0 ** (-0.4 * (float(dark_v) - pen)) - 10.0 ** (-0.4 * float(dark_v))
    base = 10.0 ** (-0.4 * float(sky_mag))
    return 2.5 * math.log10(1.0 + moon / base)


def sky_rate(inp: Inputs, moon_penalty: float = 0.0) -> float:
    """Sky electrons per second per arcsec² in the band (moon_penalty magnitudes brighter than the dark sky)."""
    return 10.0 ** (-0.4 * (inp.sky_mag - max(0.0, float(moon_penalty or 0.0)) - inp.zp))


def central_fraction(fwhm_arcsec: float, scale: float) -> float:
    """The share of a Gaussian star that lands on its central pixel."""
    sigma_px = max(1e-6, fwhm_arcsec / 2.3548 / max(1e-6, scale))
    return math.erf(0.5 / (math.sqrt(2.0) * sigma_px)) ** 2


def peak_adu(inp: Inputs, mag: float, t: float, airmass: float = 1.0, moon_penalty: float = 0.0) -> float:
    """Predicted raw peak (ADU, the bias included when known) of the star's brightest pixel."""
    share = inp.band_share()
    star = star_rate(inp, mag, airmass) * central_fraction(inp.fwhm, inp.scale) * share
    sky = sky_rate(inp, moon_penalty) * inp.scale ** 2 * share
    return (star + sky + inp.dark_e_s) * float(t) / max(1e-9, inp.gain) + float(inp.bias_adu or 0.0)


def scint_variance(inp: Inputs, t: float, airmass: float) -> float:
    """Osborn et al. 2015 eq. 7, relative flux variance for one exposure."""
    d = max(0.01, float(inp.aperture_m))
    x = max(1.0, float(airmass or 1.0))
    return (1e-5 * inp.cy ** 2 * d ** (-4.0 / 3.0) / max(1e-6, float(t)) * x ** 3
            * math.exp(-2.0 * float(inp.elevation_m or 0.0) / SCALE_HEIGHT_M))


# ---- one frame, one bin ----------------------------------------------------------------------------------------------

def frame_variances(inp: Inputs, mag: float, t: float, airmass: float = 1.0, moon_penalty: float = 0.0) -> dict:
    """{photon, sky, read, scint}: relative variances of one frame (read includes the dark current)."""
    t = float(t)
    r = APERTURE_FWHM * inp.fwhm
    area = math.pi * r * r                                      # arcsec²
    sigma = inp.fwhm / 2.3548
    s = star_rate(inp, mag, airmass) * t * (1.0 - math.exp(-r * r / (2.0 * sigma * sigma)))
    if s <= 0:
        return {"photon": math.inf, "sky": 0.0, "read": 0.0, "scint": 0.0}
    b = sky_rate(inp, moon_penalty) * area * t
    npix = area / max(1e-9, inp.scale ** 2) / inp.band_share()
    rd = npix * (inp.read_noise_e ** 2 + inp.dark_e_s * t)
    return {"photon": 1.0 / s, "sky": b / s ** 2, "read": rd / s ** 2, "scint": scint_variance(inp, t, airmass)}


def frames_per_bin(inp: Inputs, t: float, bin_min: float = BIN_MIN) -> float:
    return bin_min * 60.0 / (float(t) + max(0.0, float(inp.overhead_s or 0.0)))


def bin_precision(inp: Inputs, mag: float, t: float, airmass: float = 1.0, moon_penalty: float = 0.0,
                  bin_min: float = BIN_MIN) -> dict:
    """{sigma (relative flux per bin), parts {photon, sky, read, scint} (shares of the bin's variance, sum 1),
    frames (per bin)}."""
    v = frame_variances(inp, mag, t, airmass, moon_penalty)
    n = max(1e-9, frames_per_bin(inp, t, bin_min))
    tot = sum(v.values())
    if not math.isfinite(tot) or tot <= 0:
        return {"sigma": math.inf, "parts": {k: 0.0 for k in v}, "frames": n}
    return {"sigma": math.sqrt(tot / n), "parts": {k: x / tot for k, x in v.items()}, "frames": n}


def curve(inp: Inputs, mag: float, exposures, airmass: float = 1.0, moon_penalty: float = 0.0,
          overhead=None) -> list[dict]:
    """[{t, sigma, peak (ADU), peak_frac (of the linear limit)}] for each exposure. overhead(t) → seconds per frame
    on top of t, when it depends on the exposure (a target's own seconds between frames)."""
    out = []
    for t in exposures:
        use = inp if overhead is None else replace(inp, overhead_s=max(0.0, float(overhead(t))))
        p = peak_adu(use, mag, t, airmass, moon_penalty)
        out.append({"t": float(t), "sigma": bin_precision(use, mag, t, airmass, moon_penalty)["sigma"],
                    "peak": p, "peak_frac": p / max(1.0, use.linear_adu)})
    return out


def best_exposure(points: list[dict], aim_frac: float = 0.8) -> tuple[dict | None, bool]:
    """Best precision: of the exposures whose peak stays under aim_frac × the linear limit, the shortest whose
    10-minute precision is within NEAR_BEST of the best one (a curve that has flattened gains nothing from longer
    frames, and shorter frames time ingress and egress better). (point, True) — or (the shortest point, False) when
    even that is over the aim (too bright: defocus)."""
    ok = [p for p in points if p["peak_frac"] <= aim_frac and math.isfinite(p["sigma"])]
    if not ok:
        return (min(points, key=lambda p: p["t"]) if points else None), False
    best = min(p["sigma"] for p in ok)
    near = [p for p in ok if p["sigma"] <= best * (1.0 + NEAR_BEST)]
    return min(near, key=lambda p: p["t"]), True


# ---- S/N and the verdict ---------------------------------------------------------------------------------------------

def bins_in(duration_days: float, bin_min: float = BIN_MIN) -> float:
    return max(0.0, float(duration_days or 0.0) * 1440.0 / bin_min)


def transit_snr(depth: float, sigma_bin: float, duration_days: float, bin_min: float = BIN_MIN) -> float | None:
    """depth / (σ_bin / sqrt(bins in transit)); None without a depth, a duration or a finite precision."""
    n = bins_in(duration_days, bin_min)
    if not depth or n <= 0 or not sigma_bin or not math.isfinite(sigma_bin):
        return None
    return float(depth) / (float(sigma_bin) / math.sqrt(n))


def amplitude_snr(delta_mag: float, sigma_bin: float) -> float | None:
    """A variable's range (Δmag as a flux fraction, flux_fraction) against one 10-minute bin's scatter."""
    if not delta_mag or not sigma_bin or not math.isfinite(sigma_bin):
        return None
    return flux_fraction(delta_mag) / float(sigma_bin)


def verdict(snr: float | None, rough: bool = False) -> str:
    """"Detectable" (S/N ≥ 10), "Marginal" (5–10), "Out of reach" (under 5); from a rough estimate the same word
    with a "?" ("Detectable?"), never a bare word. "" when there is no S/N."""
    if snr is None or not math.isfinite(snr):
        return ""
    word = "Out of reach" if snr < MARGINAL_SNR else ("Detectable" if snr >= DETECTABLE_SNR else "Marginal")
    return word + ROUGH_MARK if rough else word


def saturates(rough: bool = False) -> str:
    return SATURATES + (ROUGH_MARK if rough else "")


def verdict_kind(word: str) -> str:
    """The colour key of a verdict word: "good" (green), "marginal" (yellow), "" (plain)."""
    w = str(word or "")
    if w.startswith("Detectable"):
        return "good"
    if w.startswith("Marginal"):
        return "marginal"
    return ""


def ppt(sigma: float) -> str:
    """0.0018 → '1.8 ppt'."""
    if sigma is None or not math.isfinite(sigma):
        return "—"
    x = sigma * 1000.0
    return f"{x:.2f} ppt" if x < 1 else (f"{x:.1f} ppt" if x < 100 else f"{x:.0f} ppt")
