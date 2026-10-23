"""Read the camera and optics values from a FITS frame's header (the Setup page's "Read from a FITS frame…").

Pure Python, no astropy: only the primary header is read, in 2880-byte blocks of 80-character cards up to END, so a
60 MB frame costs a few kilobytes and a file cut off after its header (no image data) reads the same.

ASIAIR (ZWO) frames carry: INSTRUME (camera model), BAYERPAT (one-shot-color only), GAIN, EGAIN (e⁻/ADU), XPIXSZ
(pixel size WITH binning), XBINNING, FOCALLEN (mm) and ROTATOR (camera angle, degrees). TELESCOP is ignored:
ASIAIR writes the mount driver there ("EQMod Mount"), not the telescope."""
from __future__ import annotations

import math

BLOCK = 2880
CARD = 80
MAX_BLOCKS = 200            # a header longer than ~7000 cards is not a camera frame
NOT_BAYER = frozenset({"NONE", "MONO", "N/A", "NA", "-"})     # BAYERPAT values some software writes for mono

# what the Setup page fills, in its order: (Setup field, label, FITS keyword(s) it comes from)
FIELDS = (
    ("optics.camera", "Camera", "INSTRUME"),
    ("optics.color_camera", "Sensor", "BAYERPAT"),
    ("optics.gain", "Gain setting", "GAIN"),
    ("optics.e_per_adu", "e⁻ per ADU", "EGAIN"),
    ("optics.pixel_um", "Pixel size", "XPIXSZ"),
    ("optics.binning", "Binning", "XBINNING"),
    ("optics.focal_mm", "Focal length", "FOCALLEN"),
    ("optics.camera_angle_deg", "Usual camera angle", "ROTATOR"),
)


class FitsError(Exception):
    """Not a FITS file, or its header cannot be read (the message is a plain sentence)."""


def _value(text: str):
    """A card's value field → str (quoted), bool (T/F), int, float, or None (empty / unparsable)."""
    s = text.strip()
    if s.startswith("'"):
        out, i = [], 1
        while i < len(s):
            ch = s[i]
            if ch == "'":
                if i + 1 < len(s) and s[i + 1] == "'":     # '' is an escaped quote
                    out.append("'")
                    i += 2
                    continue
                break
            out.append(ch)
            i += 1
        return "".join(out).rstrip()
    s = s.split("/", 1)[0].strip()
    if not s:
        return None
    if s in ("T", "F"):
        return s == "T"
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s.replace("D", "E").replace("d", "e"))
    except ValueError:
        return None


def parse_header(data: bytes) -> dict:
    """Header bytes → {KEYWORD: value} (first occurrence wins; COMMENT/HISTORY/blank cards skipped).
    FitsError when the bytes do not start like a FITS file."""
    if not data[:9].startswith(b"SIMPLE  =") and not data[:9].startswith(b"SIMPLE ="):
        raise FitsError("it does not start like a FITS file (no SIMPLE card)")
    out: dict = {}
    long_key = None                       # a string value ending in "&" continues on CONTINUE cards
    for i in range(0, len(data) - CARD + 1, CARD):
        card = data[i:i + CARD].decode("ascii", "replace")
        key = card[:8].strip().upper()
        if key == "END":
            break
        if key == "CONTINUE":
            more = _value(card[8:])
            if long_key and isinstance(more, str):
                out[long_key] = out[long_key][:-1] + more
                if not more.endswith("&"):
                    long_key = None
            continue
        long_key = None
        if not key or key in ("COMMENT", "HISTORY") or card[8:10] != "= ":
            continue
        if key not in out:
            out[key] = _value(card[10:])
            if isinstance(out[key], str) and out[key].endswith("&"):
                long_key = key
    for k, v in out.items():             # an unfinished continuation keeps no dangling "&"
        if isinstance(v, str) and v.endswith("&"):
            out[k] = v[:-1]
    return out


def read_header(path: str) -> dict:
    """The primary header of a FITS file as {KEYWORD: value}. Reads block by block until the END card (or the end of
    a truncated file). FitsError with a plain reason when the file cannot be read or is not FITS."""
    try:
        with open(path, "rb") as fh:
            buf = b""
            for _n in range(MAX_BLOCKS):
                block = fh.read(BLOCK)
                if not block:
                    break
                buf += block
                if any(block[i:i + 8] == b"END     " for i in range(0, len(block) - 7, CARD)):
                    break
    except OSError as exc:
        raise FitsError(f"it could not be opened ({exc.strerror or exc})") from None
    if not buf:
        raise FitsError("the file is empty")
    return parse_header(buf)


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None           # NaN and ±inf are not values


def camera_values(hdr: dict) -> tuple[dict, list[str], list[str]]:
    """The Setup values a header gives: ({Setup field: value}, notes, missing keywords).
    pixel_um = XPIXSZ / XBINNING (XPIXSZ is the binned pixel); camera angle = ROTATOR modulo 180 (210° and 30° are
    the same framing on the other pier side; the note says so). Sensor = color when BAYERPAT names a pattern
    ("RGGB"), mono when it says NONE / MONO / N/A; no BAYERPAT keyword leaves the sensor as it is (listed missing).
    Values are rounded the way the Setup page shows them."""
    vals: dict = {}
    notes: list[str] = []
    missing: list[str] = []
    cam = hdr.get("INSTRUME")
    if isinstance(cam, str) and cam.strip():
        vals["optics.camera"] = cam.strip()
    else:
        missing.append("INSTRUME")
    bayer = hdr.get("BAYERPAT")
    if isinstance(bayer, str) and bayer.strip():
        vals["optics.color_camera"] = bayer.strip().upper() not in NOT_BAYER
    else:                               # no Bayer keyword at all: some software never writes one, so say nothing
        missing.append("BAYERPAT")
    gain = _num(hdr.get("GAIN"))
    if gain is not None and gain >= 0:
        vals["optics.gain"] = round(gain, 6)
    else:
        missing.append("GAIN")
    eg = _num(hdr.get("EGAIN"))
    if eg is not None and eg > 0:
        vals["optics.e_per_adu"] = round(eg, 6)
    else:
        missing.append("EGAIN")
    xb = _num(hdr.get("XBINNING"))
    binning = int(round(xb)) if xb is not None and 1 <= xb <= 16 else None
    if binning is not None:
        vals["optics.binning"] = binning
    else:
        missing.append("XBINNING")
    xp = _num(hdr.get("XPIXSZ"))
    if xp is not None and xp > 0:
        vals["optics.pixel_um"] = round(xp / (binning or 1), 4)
        if binning and binning > 1:
            notes.append(f"XPIXSZ {xp:g} µm is the {binning}×{binning} binned pixel: pixel size "
                         f"{vals['optics.pixel_um']:g} µm")
    else:
        missing.append("XPIXSZ")
    fl = _num(hdr.get("FOCALLEN"))
    if fl is not None and fl > 0:
        vals["optics.focal_mm"] = round(fl, 3)
    else:
        missing.append("FOCALLEN")
    rot = _num(hdr.get("ROTATOR"))
    if rot is not None:
        r = round(rot, 3)                                # rounded first: 179.99999 → 180 → 0, never 180
        ang = round(r % 180.0, 3)
        vals["optics.camera_angle_deg"] = ang
        if abs((r % 360.0) - ang) > 1e-6:
            notes.append(f"ROTATOR {rot:g}° = {ang:g}° (the other pier side)")
    else:
        missing.append("ROTATOR")
    return vals, notes, missing


def read_camera_values(path: str) -> tuple[dict, list[str], list[str]]:
    """read_header + camera_values in one call. FitsError when the file is not a readable FITS file."""
    return camera_values(read_header(path))
