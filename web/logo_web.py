"""Browser glue for seqlogo.py, run by Pyodide from web/app.js.

The pasted alignment is checked here, then drawn by the unmodified
command-line code (seqlogo.main) inside Pyodide's in-memory file system.
Nothing leaves the browser.
"""
import contextlib
import io
import math
import re
from pathlib import Path

import matplotlib.pyplot as plt

import seqlogo

# There are no installed fonts to search in the browser; keep matplotlib's
# DejaVu Sans for axis text.
seqlogo.use_text_font = lambda: None

MAX_CHARS = 500_000     # pasted text
MAX_SEQS = 5_000
MAX_COLUMNS = 1_000
ALLOWED = re.compile(r"[A-Z\-.~*]*")   # residues, gaps, stop

FORMATS = {"png", "svg", "pdf"}
WORK = Path("/tmp/seqlogo")


class InputError(Exception):
    pass


def color_sets() -> dict:
    """Built-in color sets as {name: {residue: color}} for the swatches."""
    return {name: seqlogo.pick_colors(name, Path("/nonexistent.yaml"))
            for name in seqlogo.COLOR_SCHEMES}


GOOGLE_DIR = Path("/fonts/google")
FAMILY_NAME = re.compile(r"[A-Za-z0-9 ]{1,60}")


def install_google_font(data, family: str, weight: int) -> str:
    """Save a Google Fonts download (woff2, maybe variable) as a static TTF
    matplotlib can read, and return its path."""
    from fontTools.ttLib import TTFont
    if not FAMILY_NAME.fullmatch(family) or not 1 <= int(weight) <= 1000:
        raise ValueError(f"bad font name {family!r} {weight!r}")
    font = TTFont(io.BytesIO(bytes(data)))
    font.flavor = None                      # woff2 -> plain TrueType/CFF
    if "fvar" in font:
        from fontTools.varLib import instancer
        axes = {a.axisTag: a for a in font["fvar"].axes}
        location = {tag: a.defaultValue for tag, a in axes.items()}
        if "wght" in axes:
            location["wght"] = min(max(weight, axes["wght"].minValue), axes["wght"].maxValue)
        font = instancer.instantiateVariableFont(font, location)
    GOOGLE_DIR.mkdir(parents=True, exist_ok=True)
    path = GOOGLE_DIR / f"{family} {weight}.ttf"
    font.save(str(path))
    return str(path)


def check_alignment(text: str) -> list[str]:
    if not text.strip():
        raise InputError("Paste an alignment first.")
    if len(text) > MAX_CHARS:
        raise InputError(f"The alignment is too long ({len(text):,} characters; "
                         f"the limit is {MAX_CHARS:,}).")
    WORK.mkdir(parents=True, exist_ok=True)
    path = WORK / "alignment.txt"
    path.write_text(text)
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            seqs = seqlogo.read_alignment(str(path))
    except SystemExit as e:
        raise InputError(str(e).removeprefix("error: ").capitalize())
    if len(seqs) > MAX_SEQS:
        raise InputError(f"Too many sequences ({len(seqs):,}; the limit is {MAX_SEQS:,}).")
    if len(seqs[0]) > MAX_COLUMNS:
        raise InputError(f"The alignment is too wide ({len(seqs[0]):,} columns; "
                         f"the limit is {MAX_COLUMNS:,}).")
    for i, s in enumerate(seqs, 1):
        if not ALLOWED.fullmatch(s):
            bad = sorted(set(re.sub(r"[A-Z\-.~*]", "", s)))[:5]
            raise InputError(f"Sequence {i} contains characters that aren't residues "
                             f"or gaps: {' '.join(repr(c) for c in bad)}")
    return seqs


def make_logo(text, font_file, colors, fmt, units, correction, gap,
              tick_every, per_line, show_font, dpi, max_columns=0, background="white"):
    """Return {"data": bytes, "notes": str} or {"error": str}.
    max_columns > 0 draws only the first that many positions (for previews)."""
    try:
        if fmt not in FORMATS:
            raise InputError(f"Unknown format {fmt!r}.")
        if background not in ("white", "black"):
            raise InputError(f"Unknown background {background!r}.")
        if colors not in seqlogo.COLOR_SCHEMES:
            raise InputError(f"Unknown color set {colors!r}.")
        seqs = check_alignment(text)
        warnings = []
        n_aa = len(seqlogo.AMINO_ACIDS)
        if units == "bits" and correction and \
                (n_aa - 1) / (2 * math.log(2) * len(seqs)) >= math.log2(n_aa):
            warnings.append(f"With only {len(seqs)} sequences, the small-sample correction "
                            "removes all the information, so the stacks are empty. Turn "
                            "off the correction or choose Probability.")
        out = WORK / f"logo.{fmt}"
        argv = [str(WORK / "alignment.txt"), "-o", str(out), "-f", font_file,
                "-c", colors, "-U", units, "--gap", str(gap),
                "--tick-every", str(tick_every), "--per-line", str(per_line),
                "--dpi", str(dpi), "--background", background]
        if not correction:
            argv.append("--no-correction")
        length = len(seqs[0])
        if 0 < max_columns < length:
            argv += ["--end", str(max_columns)]
            warnings.append(f"Preview shows positions 1–{max_columns} of {length:,}; "
                            "the download includes all of them.")
        if show_font:
            argv.append("--show-font")
        notes = io.StringIO()
        try:
            with contextlib.redirect_stderr(notes):
                seqlogo.main(argv)
        except SystemExit as e:
            raise InputError(str(e).split("\n")[0].removeprefix("error: "))
        finally:
            plt.close("all")
        kept = [ln.removeprefix("note: ") for ln in notes.getvalue().splitlines()
                if ln.startswith("note:")]
        return {"data": out.read_bytes(), "notes": "\n".join(warnings + kept)}
    except InputError as e:
        return {"error": str(e)}
