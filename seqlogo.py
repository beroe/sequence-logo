#!/usr/bin/env python3
"""
seqlogo.py - WebLogo-style protein sequence logos with any typeface.

Computes per-column information content the way WebLogo does (bits, with
the Schneider et al. 1986 small-sample correction), stacks residues by
frequency, colors them by residue group, and draws every glyph as a
vector outline taken from a font you choose -- an installed font, a font
file, or a Google Font (downloaded once and cached).

Examples
--------
  # one logo, installed font
  ./seqlogo.py aln.fasta -o logo.pdf --font "Futura:bold"

  # Google Font, specific weight
  ./seqlogo.py aln.fasta -o logo.svg --font "google:Roboto Slab:800"

  # compare several typefaces on one sheet (repeat --font)
  ./seqlogo.py aln.fasta -o compare.pdf \
      --font "Helvetica:bold" --font "Avenir Next:bold" \
      --font "google:Inter:900" --font ~/fonts/MyFont.otf

  # find installed families
  ./seqlogo.py --list-fonts avenir

Font spec syntax:  "Family[:weight][:italic]"  |  "google:Family[:weight]"  |  path/to/font.ttf|.otf
Weights: 100-900 or thin, light, regular, medium, semibold, bold, heavy, black.

Input: FASTA, Clustal (.aln), or plain text with one aligned sequence per line.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
# Embed axis text as TrueType so it stays editable in Illustrator etc.
matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["ps.fonttype"] = 42
# fontTools warns about harmless quirks in some system fonts when subsetting.
import logging  # noqa: E402
logging.getLogger("fontTools").setLevel(logging.ERROR)
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import PathPatch  # noqa: E402
from matplotlib.textpath import TextPath  # noqa: E402
from matplotlib.transforms import Affine2D  # noqa: E402

AMINO_ACIDS = "ACDEFGHIKLMNPQRSTVWY"
GAP_CHARS = set("-.~")

# Built-in colour schemes in a muted palette. The residue groups of hydro and
# charge follow WebLogo 3's hydrophobicity and charge schemes; chem follows its
# chemistry scheme but gives C and M their own sulfur group; rasmol follows
# RasMol's "amino" groups.
COLOR_SCHEMES = {
    "chem": [("GSTY", "#D19D69"),       # polar
             ("NQ", "#9F92A5"),         # neutral
             ("KRH", "#48869F"),        # basic
             ("DE", "#679E73"),         # acidic
             ("PAWFLIV", "#C06F76"),    # hydrophobic
             ("CM", "#E2CA3D")],        # sulfur
    "hydro": [("RKDENQ", "#48869F"),     # hydrophilic
              ("SGHTAP", "#B8B8C1"),     # neutral
              ("YVMCLFIW", "#C06F76")],  # hydrophobic
    "charge": [("KRH", "#48869F"),              # positive
               ("DE", "#C06F76"),               # negative
               ("GSTYCNQPAWFLIMV", "#B8B8C1")], # uncharged
    "rasmol": [("DE", "#D35353"),    # acidic
               ("KR", "#5680D6"),    # basic
               ("H", "#8282D2"),     # histidine
               ("NQ", "#57BCB9"),    # amide
               ("LVI", "#649B64"),   # aliphatic
               ("FY", "#434389"),    # aromatic
               ("CM", "#E2CA3D"),    # sulfur
               ("ST", "#EAA244"),    # hydroxyl
               ("G", "#B5B5B5"),     # glycine
               ("A", "#A5A5A5"),     # alanine
               ("P", "#DC9682"),     # proline
               ("W", "#B45AB4")],    # tryptophan
    "mono": [],
}

WEIGHT_NAMES = {
    "thin": 100, "hairline": 100, "extralight": 200, "ultralight": 200,
    "light": 300, "regular": 400, "normal": 400, "book": 400, "roman": 400,
    "medium": 500, "semibold": 600, "demibold": 600, "demi": 600,
    "bold": 700, "extrabold": 800, "ultrabold": 800, "heavy": 900,
    "black": 900,
}

DEFAULT_FONT = "google:Oswald:700"
# Tried in order if the default can't be downloaded. The last is bundled with
# matplotlib, so it is always available.
FALLBACK_FONTS = ["Helvetica:bold",          # macOS
                  "Liberation Sans:bold",    # most Linux distributions
                  str(Path(matplotlib.get_data_path()) / "fonts/ttf/DejaVuSans-Bold.ttf")]
FONT_CACHE = Path.home() / ".cache" / "seqlogo" / "fonts"


# --------------------------------------------------------------------------
# Alignment parsing
# --------------------------------------------------------------------------

def read_alignment(path: str) -> list[str]:
    text = sys.stdin.read() if path == "-" else Path(path).read_text()
    lines = text.splitlines()
    first = next((ln for ln in lines if ln.strip()), "")

    if first.startswith(">"):
        seqs, cur = [], []
        for ln in lines:
            if ln.startswith(">"):
                if cur:
                    seqs.append("".join(cur))
                cur = []
            else:
                cur.append(ln.strip())
        if cur:
            seqs.append("".join(cur))
    elif first.upper().startswith("CLUSTAL") or first.upper().startswith("MUSCLE"):
        blocks: dict[str, list[str]] = {}
        for ln in lines[1:]:
            if not ln.strip() or ln[0].isspace():  # blank or conservation line
                continue
            parts = ln.split()
            if len(parts) >= 2:
                blocks.setdefault(parts[0], []).append(parts[1])
        seqs = ["".join(v) for v in blocks.values()]
    else:
        seqs = [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]

    seqs = [s.upper() for s in seqs]
    if not seqs:
        sys.exit("error: no sequences found")
    lengths = {len(s) for s in seqs}
    if len(lengths) != 1:
        sys.exit(f"error: sequences are not aligned (lengths {sorted(lengths)})")
    return seqs


# --------------------------------------------------------------------------
# Information content (WebLogo-style)
# --------------------------------------------------------------------------

def logo_heights(seqs: list[str], units: str, correction: bool,
                 scale_by_occupancy: bool) -> tuple[np.ndarray, np.ndarray]:
    """Return (heights[L, 20], n_residues[L])."""
    nseq, length = len(seqs), len(seqs[0])
    s = len(AMINO_ACIDS)
    counts = np.zeros((length, s))
    ignored = Counter()
    for j in range(length):
        col = Counter(seq[j] for seq in seqs)
        for ch, n in col.items():
            if ch in GAP_CHARS:
                continue
            idx = AMINO_ACIDS.find(ch)
            if idx >= 0:
                counts[j, idx] = n
            else:
                ignored[ch] += n
    if ignored:
        print(f"note: ignored non-standard residues {dict(ignored)}", file=sys.stderr)

    n = counts.sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        freqs = np.where(n[:, None] > 0, counts / n[:, None], 0.0)
        plogp = np.where(freqs > 0, freqs * np.log2(freqs), 0.0)
    entropy = -plogp.sum(axis=1)

    if units == "probability":
        heights = freqs.copy()
    else:
        max_bits = math.log2(s)
        e_n = np.where(n > 0, (s - 1) / (2 * math.log(2) * np.maximum(n, 1)), 0.0) \
            if correction else 0.0
        info = np.clip(max_bits - (entropy + e_n), 0.0, None)
        info[n == 0] = 0.0
        heights = freqs * info[:, None]

    if scale_by_occupancy:
        heights *= (n / nseq)[:, None]
    return heights, n


# --------------------------------------------------------------------------
# Font resolution
# --------------------------------------------------------------------------

def parse_weight(w: str | None) -> int:
    if not w:
        return 700
    w = w.strip().lower().replace("-", "").replace(" ", "")
    if w.isdigit():
        return int(w)
    if w in WEIGHT_NAMES:
        return WEIGHT_NAMES[w]
    sys.exit(f"error: unknown weight '{w}'")


FONT_INDEX = FONT_CACHE.parent / "font_index.json"


def _scan_face(font, path: str, index: int) -> dict | None:
    """Describe one face: names it answers to, weight, italic, variable range."""
    try:
        name = font["name"]
        names = {n for n in (name.getDebugName(16), name.getDebugName(1),
                             name.getBestFamilyName()) if n}
        style = name.getDebugName(17) or name.getDebugName(2) or ""
        os2 = font["OS/2"] if "OS/2" in font else None
        weight = os2.usWeightClass if os2 else 400
        italic = bool(os2.fsSelection & 1) if os2 else "italic" in style.lower()
        wrange = None
        if "fvar" in font:
            for ax in font["fvar"].axes:
                if ax.axisTag == "wght":
                    wrange = [int(ax.minValue), int(ax.maxValue)]
        return {"names": sorted(names), "style": style, "weight": weight,
                "italic": italic, "wrange": wrange, "path": path, "index": index}
    except Exception:
        return None


FONT_DIRS = ["/System/Library/Fonts", "/Library/Fonts", "~/Library/Fonts",
             "/Network/Library/Fonts", "/usr/X11/lib/X11/fonts",
             "/usr/share/fonts", "/usr/local/share/fonts", "~/.local/share/fonts",
             "~/.fonts"]
# macOS on-demand fonts (installed through Font Book) live in asset bundles.
FONT_ASSET_GLOBS = ["/System/Library/AssetsV2/*/*/com_apple_MobileAsset_Font*/*/AssetData",
                    "/System/Library/AssetsV2/com_apple_MobileAsset_Font*/*/AssetData"]
FONT_EXTS = {".ttf", ".otf", ".ttc", ".otc"}


def _font_files() -> list[str]:
    # Faster than matplotlib's findSystemFonts(), which shells out on macOS.
    import glob
    roots = [Path(d).expanduser() for d in FONT_DIRS]
    roots += [Path(d) for g in FONT_ASSET_GLOBS for d in glob.glob(g)]
    files = set()
    for root in roots:
        if root.is_dir():
            files.update(str(p) for p in root.rglob("*")
                         if p.suffix.lower() in FONT_EXTS)
    return sorted(files)


def font_index() -> list[dict]:
    """Every face of every installed font (TTC collections expanded), cached."""
    import json
    from fontTools.ttLib import TTCollection, TTFont

    files = _font_files()
    stamp = {f: Path(f).stat().st_mtime for f in files if Path(f).exists()}
    if FONT_INDEX.exists():
        cached = json.loads(FONT_INDEX.read_text())
        if cached.get("stamp") == stamp:
            return cached["faces"]
    print("note: indexing installed fonts (first run only)...", file=sys.stderr)
    faces = []
    for f in stamp:
        try:
            if f.lower().endswith((".ttc", ".otc")):
                coll = TTCollection(f, lazy=True)
                fonts = list(enumerate(coll.fonts))
            else:
                fonts = [(0, TTFont(f, lazy=True))]
        except Exception:
            continue
        for i, font in fonts:
            face = _scan_face(font, f, i)
            if face:
                faces.append(face)
    FONT_INDEX.parent.mkdir(parents=True, exist_ok=True)
    FONT_INDEX.write_text(json.dumps({"stamp": stamp, "faces": faces}))
    return faces


def _materialise(face: dict, weight: int) -> Path:
    """Return a single-face font file matplotlib can read, at the given weight."""
    from fontTools.ttLib import TTFont

    variable = face["wrange"] is not None
    if face["index"] == 0 and not variable:
        return Path(face["path"])
    stem = Path(face["path"]).stem.replace(" ", "_")
    out = FONT_CACHE / f"{stem}-{face['index']}{f'-w{weight}' if variable else ''}.ttf"
    if not out.exists():
        FONT_CACHE.mkdir(parents=True, exist_ok=True)
        font = TTFont(face["path"], fontNumber=face["index"])
        if variable:
            from fontTools.varLib.instancer import instantiateVariableFont
            font = instantiateVariableFont(font, {"wght": weight})
        font.save(out)
    return out


def _weight_distance(face: dict, weight: int) -> int:
    if face["wrange"]:
        lo, hi = face["wrange"]
        return 0 if lo <= weight <= hi else min(abs(weight - lo), abs(weight - hi))
    return abs(face["weight"] - weight)


class FontNotFound(Exception):
    pass


def find_installed_font(family: str, weight: int,
                        italic: bool = False) -> tuple[Path, int]:
    """Return (font file, weight actually used) for an installed family."""
    fam = family.lower()
    faces = font_index()
    matches = [f for f in faces if fam in (n.lower() for n in f["names"])]
    if not matches:
        close = sorted({n for f in faces for n in f["names"] if fam in n.lower()})
        hint = f" Similar: {', '.join(close[:10])}" if close else \
            " Try --list-fonts, a file path, or google:<Family>."
        raise FontNotFound(f"font family '{family}' not installed.{hint}")
    matches.sort(key=lambda f: (f["italic"] != italic, _weight_distance(f, weight)))
    best = matches[0]
    if best["wrange"]:
        lo, hi = best["wrange"]
        use = min(max(weight, lo), hi)
    else:
        use = best["weight"]
    if use != weight:
        print(f"note: '{family}' has no weight {weight}; using {use} "
              f"({best['style']})", file=sys.stderr)
    return _materialise(best, use), use


def _urlopen(url: str, timeout: int):
    import ssl
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    # A non-browser User-Agent makes the Google Fonts CSS API return TrueType URLs.
    req = urllib.request.Request(url, headers={"User-Agent": "seqlogo/1.0"})
    return urllib.request.urlopen(req, timeout=timeout, context=ctx)


class GoogleFontError(Exception):
    pass


def fetch_google_font(family: str, weight: int) -> Path:
    FONT_CACHE.mkdir(parents=True, exist_ok=True)
    dest = FONT_CACHE / f"google-{family.replace(' ', '_')}-{weight}.ttf"
    if dest.exists():
        return dest
    url = ("https://fonts.googleapis.com/css2?family="
           f"{urllib.parse.quote_plus(family)}:wght@{weight}")
    try:
        css = _urlopen(url, 20).read().decode()
    except urllib.error.HTTPError as e:
        raise GoogleFontError(f"Google Fonts has no '{family}' at weight {weight} "
                              f"(HTTP {e.code}); check the name and weights at "
                              "fonts.google.com")
    except Exception as e:
        raise GoogleFontError(f"could not reach Google Fonts: {e}")
    m = re.search(r"url\((https://[^)]+)\)", css)
    if not m:
        raise GoogleFontError(f"no font file URL for Google Font '{family}'")
    try:
        data = _urlopen(m.group(1), 30).read()
    except Exception as e:
        raise GoogleFontError(f"could not download Google Font '{family}': {e}")
    dest.write_bytes(data)
    print(f"note: downloaded {family} {weight} -> {dest}", file=sys.stderr)
    return dest


def resolve_font(spec: str) -> tuple[Path, str]:
    """Return (font file, display label) for a font spec."""
    p = Path(spec).expanduser()
    if p.suffix.lower() in {".ttf", ".otf", ".ttc"} and p.exists():
        return p, p.stem
    if spec.lower().startswith("google:"):
        _, family, *rest = spec.split(":")
        weight = parse_weight(rest[0] if rest else None)
        return fetch_google_font(family.strip(), weight), f"{family.strip()} {weight} (Google)"
    family, *rest = [part.strip() for part in spec.split(":")]
    italic = "italic" in (r.lower() for r in rest)
    rest = [r for r in rest if r.lower() != "italic"]
    weight = parse_weight(rest[0] if rest else None)
    path, used = find_installed_font(family, weight, italic)
    return path, f"{family} {used}{' italic' if italic else ''}"


# --------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------

class GlyphSet:
    """Caches glyph outlines for one font, normalised to their ink bounds."""

    def __init__(self, font_file: Path):
        self.prop = font_manager.FontProperties(fname=str(font_file))
        self._cache = {}

    def path(self, ch: str):
        if ch not in self._cache:
            tp = TextPath((0, 0), ch, size=1, prop=self.prop)
            self._cache[ch] = (tp, tp.get_extents())
        return self._cache[ch]


STYLES_FILE = Path(__file__).resolve().parent / "seqlogo_styles.yaml"
DEFAULT_COLORS = "chem"
# Column order for swatch sheets: chem's residue groups kept together.
SWATCH_ORDER = "GSTYCMNQKRHDEPAWFLIV"


def load_styles(path: Path) -> dict[str, dict[str, str]]:
    """Read named colour sets from a YAML file, e.g.

        okabe_ito:
          GSTYC: "#009E73"
          DE: red

    Returns {name: {residue: colour}}; residues a set doesn't list are absent.
    A missing file just means no saved sets.
    """
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        sys.exit("error: saved colour sets need PyYAML (pip install pyyaml)")
    from matplotlib.colors import is_color_like

    # YAML 1.1 reads bare Y and N (yes/no) as booleans; keep them as letters.
    class StyleLoader(yaml.SafeLoader):
        pass
    StyleLoader.yaml_implicit_resolvers = {
        ch: [(tag, rx) for tag, rx in rs if tag != "tag:yaml.org,2002:bool"]
        for ch, rs in yaml.SafeLoader.yaml_implicit_resolvers.items()}

    def bad(msg):
        sys.exit(f"error: {path}: {msg}")

    try:
        data = yaml.load(path.read_text(), Loader=StyleLoader)
    except OSError as e:
        bad(e.strerror)
    except yaml.YAMLError as e:
        bad(f"invalid YAML: {e}")
    if data is None:
        return {}
    if not isinstance(data, dict):
        bad("expected named colour sets at the top level")

    styles = {}
    for name, groups in data.items():
        if not isinstance(groups, dict):
            bad(f"set '{name}' should map residue groups to colours")
        table, source = {}, {}
        for group, color in groups.items():
            where = f"set '{name}', '{group}'"
            if color is None:
                bad(f"{where}: no colour (quote hex colours: \"#1b9e77\")")
            if not is_color_like(color):
                bad(f"{where}: '{color}' is not a colour name or hex code")
            for aa in str(group).strip().upper():
                if aa not in AMINO_ACIDS:
                    bad(f"{where}: '{aa}' is not a standard amino acid")
                if aa in table:
                    bad(f"set '{name}': '{aa}' is in both '{source[aa]}' and '{group}'")
                table[aa], source[aa] = color, group
        styles[str(name)] = table
    return styles


def all_color_sets(styles_file: Path) -> dict[str, dict[str, str]]:
    """Built-in schemes plus saved sets (saved sets win on a name clash)."""
    sets = {name: {aa: c for letters, c in groups for aa in letters}
            for name, groups in COLOR_SCHEMES.items()}
    sets.update(load_styles(styles_file))
    return sets


def pick_colors(name: str, styles_file: Path) -> dict[str, str]:
    sets = all_color_sets(styles_file)
    if name not in sets:
        sys.exit(f"error: no colour set '{name}'. Available: {', '.join(sets)}")
    table = sets[name]
    missing = "".join(aa for aa in AMINO_ACIDS if aa not in table)
    if missing and name not in COLOR_SCHEMES:
        print(f"note: {missing} not in set '{name}'; drawn in black", file=sys.stderr)
    return {aa: table.get(aa, "black") for aa in AMINO_ACIDS}


def list_styles(styles_file: Path):
    saved = load_styles(styles_file)
    for name in all_color_sets(styles_file):
        where = "saved" if name in saved else "built-in"
        print(f"{name:24s} {where}")
    sys.stdout.flush()
    print(f"\n(saved sets are read from {styles_file})", file=sys.stderr)


def draw_swatches(styles_file: Path, output: str, dpi: int):
    """One sheet: a row of residue swatches for every distinct colour set.
    A set with exactly the same colours as an earlier one is not drawn again;
    its name is listed on the earlier set's row instead."""
    from matplotlib.colors import to_hex, to_rgb
    from matplotlib.patches import Rectangle

    saved = load_styles(styles_file)
    sets, aliases, seen = {}, {}, {}
    for name, table in all_color_sets(styles_file).items():
        key = tuple(to_hex(table.get(aa, "black")) for aa in AMINO_ACIDS)
        if key in seen:
            aliases[seen[key]].append(name)
            continue
        seen[key] = name
        sets[name], aliases[name] = table, []
    cell = 0.38
    fig = plt.figure(figsize=((len(SWATCH_ORDER) + 3.45) * cell, (len(sets) + 0.9) * cell))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(-3.4, len(SWATCH_ORDER) + 0.05)
    ax.set_ylim(len(sets) + 0.05, -0.85)
    ax.axis("off")
    for j, aa in enumerate(SWATCH_ORDER):
        ax.text(j + 0.5, -0.4, aa, ha="center", va="center", fontsize=9,
                fontweight="bold", color="0.3")
    for i, (name, table) in enumerate(sets.items()):
        label = name if name in saved else f"{name} (built-in)"
        if aliases[name]:
            label += f" (= {', '.join(aliases[name])})"
        ax.text(-0.3, i + 0.5, label, ha="right", va="center", fontsize=9)
        for j, aa in enumerate(SWATCH_ORDER):
            color = table.get(aa, "black")
            ax.add_patch(Rectangle((j + 0.06, i + 0.06), 0.88, 0.88,
                                   facecolor=color, edgecolor="0.85", lw=0.5))
            r, g, b = to_rgb(color)
            ink = "black" if 0.299 * r + 0.587 * g + 0.114 * b > 0.6 else "white"
            ax.text(j + 0.5, i + 0.5, aa, ha="center", va="center",
                    fontsize=8, fontweight="bold", color=ink)
    fig.savefig(output, dpi=dpi, bbox_inches="tight", pad_inches=0.15)
    print(f"wrote {output}", file=sys.stderr)


TEXT_FONTS = ["Helvetica", "Arial"]  # first installed one is used for all text


def use_text_font():
    """Make the first installed TEXT_FONTS family matplotlib's default for axis
    labels, panel labels, titles and swatch sheets (else keep DejaVu Sans).
    Regular and Bold are registered explicitly because matplotlib only reads
    the first face of .ttc collections such as Helvetica.ttc."""
    faces = font_index()
    for family in TEXT_FONTS:
        found = {}
        for f in faces:
            if (family.lower() in (n.lower() for n in f["names"])
                    and not f["italic"] and not f["wrange"]
                    and f["weight"] in (400, 700)):
                found.setdefault(f["weight"], f)
        if 400 not in found:
            continue
        for weight, face in found.items():
            font_manager.fontManager.addfont(str(_materialise(face, weight)))
        matplotlib.rcParams["font.family"] = family
        return


def draw_glyph(ax, glyphs: GlyphSet, ch, x, y, w, h, color):
    if h <= 0:
        return
    path, bb = glyphs.path(ch)
    if bb.width == 0 or bb.height == 0:
        return
    t = (Affine2D().translate(-bb.x0, -bb.y0)
         .scale(w / bb.width, h / bb.height).translate(x, y))
    ax.add_patch(PathPatch(t.transform_path(path), facecolor=color,
                           edgecolor="none", lw=0))


def draw_logo_rows(fig, gs_rows, heights, glyphs, colors, args, ymax, label=None):
    length = heights.shape[0]
    start = args.first_index
    per_line = args.per_line
    n_rows = math.ceil(length / per_line)
    axes = []
    for r in range(n_rows):
        ax = fig.add_subplot(gs_rows[r])
        lo, hi = r * per_line, min(length, (r + 1) * per_line)
        for j in range(lo, hi):
            order = np.argsort(heights[j])  # smallest at the bottom
            y = 0.0
            for k in order:
                h = heights[j, k]
                if h <= args.min_height:
                    continue
                aa = AMINO_ACIDS[k]
                draw_glyph(ax, glyphs, aa, j + (1 - args.glyph_width) / 2, y,
                           args.glyph_width, h, colors[aa])
                y += h
        ax.set_xlim(lo, lo + min(per_line, length))
        ax.set_ylim(0, ymax)
        cols = [j for j in range(lo, hi) if (j + start) % args.tick_every == 0]
        ax.set_xticks([j + 0.5 for j in cols], [str(j + start) for j in cols],
                      fontsize=7, rotation=90 if args.tick_every == 1 else 0)
        ax.set_ylabel("bits" if args.units == "bits" else "probability", fontsize=8)
        ax.tick_params(axis="y", labelsize=7)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        axes.append(ax)
    if label:
        axes[0].set_title(label, loc="left", fontsize=9, color="0.3")
    return axes


def resolve_fonts(font_specs: list[str] | None) -> list[tuple[Path, str]]:
    """Resolve -f specs, or the default font with an installed fallback."""
    if not font_specs:
        try:
            return [resolve_font(DEFAULT_FONT)]
        except GoogleFontError as e:
            print(f"note: {e}", file=sys.stderr)
        for spec in FALLBACK_FONTS:
            try:
                font = resolve_font(spec)
            except FontNotFound:
                continue
            print(f"note: using fallback font {font[1]}", file=sys.stderr)
            return [font]
        sys.exit("error: no fallback font found")
    try:
        return [resolve_font(s) for s in font_specs]
    except (GoogleFontError, FontNotFound) as e:
        sys.exit(f"error: {e}")


def auto_output_name(alignment: str, fonts, color_names) -> str:
    """Default output name: <alignment>-<fonts>-<colour sets>.png, e.g.
    zinc_finger-oswald700-chem.png; several fonts or sets are joined with +."""
    def slug(label: str) -> str:
        label = label.lower().replace(" (google)", "")
        return re.sub(r"[^a-z0-9-]+", "", label)
    seq = "stdin" if alignment == "-" else Path(alignment).stem
    font_part = "+".join(slug(label) for _, label in fonts)
    return f"{seq}-{font_part}-{'+'.join(color_names)}.png"


def render(seqs, font_specs, args):
    if args.start or args.end:
        s0 = (args.start or args.first_index) - args.first_index
        s1 = (args.end or len(seqs[0]) + args.first_index - 1) - args.first_index + 1
        seqs = [s[s0:s1] for s in seqs]
        args.first_index += s0

    heights, _ = logo_heights(seqs, args.units, not args.no_correction,
                              args.scale_by_occupancy)
    ymax = args.ymax or (math.log2(len(AMINO_ACIDS)) if args.units == "bits" else 1.0)
    color_names = args.colors or [DEFAULT_COLORS]
    color_sets = [(name, pick_colors(name, Path(args.styles))) for name in color_names]
    fonts = resolve_fonts(font_specs)
    # One panel per font x colour set; label whichever of the two varies.
    panels = []
    for font_file, font_label in fonts:
        for color_name, colors in color_sets:
            parts = ([font_label] if len(fonts) > 1 else []) + \
                    ([color_name] if len(color_sets) > 1 else [])
            panels.append((font_file, colors, "  ·  ".join(parts) or font_label))

    length = heights.shape[0]
    n_rows = math.ceil(length / args.per_line)
    cols = min(length, args.per_line)
    row_h = args.height
    grid_cols = max(1, min(args.columns, len(panels)))
    grid_rows = math.ceil(len(panels) / grid_cols)
    width = max(4.0, cols * args.column_width + 1.0) * grid_cols
    comparing = len(panels) > 1
    title_pad = 0.3 if comparing else 0.0
    total_rows = n_rows * grid_rows
    fig_h = total_rows * (row_h + 0.45) + grid_rows * title_pad + (0.4 if args.title else 0)
    fig = plt.figure(figsize=(width, fig_h))
    gs = fig.add_gridspec(total_rows, grid_cols, hspace=0.55 + (0.35 if comparing else 0),
                          wspace=0.25)

    glyph_sets = {}
    for i, (font_file, colors, label) in enumerate(panels):
        glyphs = glyph_sets.setdefault(font_file, GlyphSet(font_file))
        row0, col = (i // grid_cols) * n_rows, i % grid_cols
        rows = [gs[row0 + r, col] for r in range(n_rows)]
        draw_logo_rows(fig, rows, heights, glyphs, colors, args,
                       ymax, label if comparing or args.show_font else None)
    if args.title:
        fig.suptitle(args.title, fontsize=11)
    if not args.output:
        args.output = auto_output_name(args.alignment, fonts, color_names)
    fig.savefig(args.output, dpi=args.dpi, bbox_inches="tight")
    print(f"wrote {args.output}", file=sys.stderr)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def list_fonts(filt: str | None):
    fams: dict[str, set[str]] = {}
    for f in font_index():
        for name in f["names"]:
            if filt and filt.lower() not in name.lower():
                continue
            w = f"{f['wrange'][0]}-{f['wrange'][1]} (variable)" if f["wrange"] \
                else str(f["weight"])
            fams.setdefault(name, set()).add(w + ("i" if f["italic"] else ""))
    key = lambda w: int(w.split("-")[0].rstrip("i").split()[0])
    for name in sorted(fams, key=str.lower):
        print(f"{name:40s} {', '.join(sorted(fams[name], key=key))}")
    print("\n(weights ending in 'i' are italic)", file=sys.stderr)


def example_usage() -> str:
    """A runnable example command, pointing at the bundled sample alignment."""
    import os
    script = sys.argv[0] if sys.argv[0].endswith(".py") else "seqlogo.py"
    sample = Path(__file__).resolve().parent / "examples" / "zinc_finger.fasta"
    rel = os.path.relpath(sample)
    if not rel.startswith(".."):  # short path only when it's under this folder
        sample = rel
    return (f"\nTry the sample alignment:\n"
            f"  {script} {sample}\n"
            f"which writes zinc_finger-oswald700-chem.png here. With your own file:\n"
            f"  {script} my_alignment.fasta -f \"Futura:bold\" -c rasmol\n"
            f"All options: {script} --help")


class HelpfulParser(argparse.ArgumentParser):
    """Command-line errors print a runnable example instead of a usage dump."""

    def error(self, message):
        sys.exit(f"error: {message}\n{example_usage()}")


def main(argv=None):
    ap = HelpfulParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("alignment", nargs="?", help="FASTA / Clustal / plain alignment ('-' = stdin)")
    ap.add_argument("-o", "--output",
                    help="output file; format from extension (pdf, svg, png, eps). "
                         "Default: <alignment>-<font>-<colours>.png, "
                         "e.g. zinc_finger-oswald700-chem.png")
    ap.add_argument("-f", "--font", action="append", dest="fonts",
                    help="font spec; repeat to compare typefaces (default google:Oswald:700, "
                         "or a local fallback if it can't be downloaded)")
    ap.add_argument("--list-fonts", nargs="?", const="", metavar="FILTER",
                    help="list installed font families (optionally filtered) and exit")
    ap.add_argument("-c", "--colors", action="append", metavar="NAME",
                    help="colour set: built-in (chem, hydro, charge, rasmol, mono) "
                         "or one saved in the styles file (default chem); "
                         "repeat to compare")
    ap.add_argument("-s", "--styles", default=str(STYLES_FILE), metavar="FILE",
                    help="YAML file of saved colour sets "
                         "(default seqlogo_styles.yaml next to this script)")
    ap.add_argument("--list-styles", action="store_true",
                    help="list available colour sets and exit")
    ap.add_argument("--swatches", metavar="OUT",
                    help="write a sheet comparing every colour set's swatches and exit")
    ap.add_argument("-U", "--units", choices=["bits", "probability"], default="bits")
    ap.add_argument("--no-correction", action="store_true",
                    help="disable small-sample correction")
    ap.add_argument("--scale-by-occupancy", action="store_true",
                    help="scale stacks by fraction of non-gap residues")
    ap.add_argument("--start", type=int, help="first position to show")
    ap.add_argument("--end", type=int, help="last position to show")
    ap.add_argument("--first-index", type=int, default=1, help="number of first column")
    ap.add_argument("--per-line", type=int, default=40, help="stacks per line")
    ap.add_argument("--tick-every", type=int, default=5, help="x-axis label spacing")
    ap.add_argument("--column-width", type=float, default=0.28, help="inches per stack")
    ap.add_argument("--height", type=float, default=1.8, help="inches per logo line")
    ap.add_argument("--glyph-width", type=float, default=0.9,
                    help="fraction of column filled by a glyph")
    ap.add_argument("--min-height", type=float, default=0.0,
                    help="skip glyphs shorter than this (in y units)")
    ap.add_argument("--ymax", type=float, help="y-axis max (default log2(20) bits)")
    ap.add_argument("--columns", type=int, default=1,
                    help="arrange comparison panels in this many columns")
    ap.add_argument("--title")
    ap.add_argument("--show-font", action="store_true", help="label logo with font name")
    ap.add_argument("--dpi", type=int, default=300)
    args = ap.parse_args(argv)

    if args.list_fonts is not None:
        list_fonts(args.list_fonts or None)
        return
    if args.list_styles:
        list_styles(Path(args.styles))
        return
    use_text_font()
    if args.swatches:
        draw_swatches(Path(args.styles), args.swatches, args.dpi)
        return
    if not args.alignment:
        ap.error("no alignment file given")
    if args.alignment != "-":
        if not Path(args.alignment).exists():
            ap.error(f"alignment file '{args.alignment}' not found")
        if Path(args.alignment).is_dir():
            ap.error(f"'{args.alignment}' is a folder, not an alignment file")
    render(read_alignment(args.alignment), args.fonts, args)


if __name__ == "__main__":
    main()
