"""Draw the web page wordmarks: each word as a probability-style sequence logo,
with letters in brackets stacked in one column.  Run: python3 web/make_wordmarks.py"""
import re, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import matplotlib.pyplot as plt
import seqlogo as s

FONT = "builtin:Antonio"
SUFFIX = "-antonio"
path, label = s.resolve_font(FONT)
glyphs = s.GlyphSet(path)
colors = dict(s.all_color_sets(s.STYLES_FILE)["rasmol"])
OTHER = "#BEA06E"            # RasMol's "other residue" tan, for O and U
GAP = 0.006 * 1.0            # same default gap as the logos, on a 0-1 axis
COL_W, H = 0.34, 0.75        # inches per column, inches tall
SPACE = 0.3                  # width of a space, in columns
KERN = {("l", "o"): -0.08}     # extra space between neighbouring columns, in columns

def wordmark(word, out_stem):
    # "[ce]" stacks letters in one column; a space is a narrow gap.
    cols = [list(m[1]) if m[1] else ([] if m[2] == " " else [m[2]])
            for m in re.finditer(r"\[(\w+)\]|(\w| )", word)]
    widths = [SPACE if not c else 1 for c in cols]
    x0, x = [], 0.0
    for i, c in enumerate(cols):
        if i and c and cols[i - 1]:
            x += KERN.get((cols[i - 1][-1].lower(), c[0].lower()), 0)
        x0.append(x)
        x += widths[i]
    fig = plt.figure(figsize=(x * COL_W, H))
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_axis_off()
    ax.set_xlim(0, x); ax.set_ylim(0, 1)
    gw = 0.9
    for x, stack in zip(x0, cols):
        if not stack:
            continue
        h = 1 / len(stack)
        for k, ch in enumerate(reversed(stack)):      # first letter on top
            top_gap = GAP if k < len(stack) - 1 else 0
            s.draw_glyph(ax, glyphs, ch.upper(), x + (1 - gw) / 2, k * h, gw, h - top_gap,
                         colors.get(ch.upper(), OTHER) if ch.upper() in s.AMINO_ACIDS else OTHER)
    for ext in ("png", "svg", "pdf"):
        fig.savefig(f"{out_stem}.{ext}", dpi=300, transparent=True)
    plt.close(fig)
    print("wrote", out_stem)

wordmark("s[eq]logo", f"{HERE}/img/seqlogo-wordmark{SUFFIX}")
wordmark("s[eq]uen[ce] logo", f"{HERE}/img/sequencelogo-wordmark{SUFFIX}")
wordmark("s[eq]uen[ce] lo[go]", f"{HERE}/img/sequencelogo-wordmark{SUFFIX}-go")
