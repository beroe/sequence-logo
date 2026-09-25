#!/usr/bin/env bash
# Regenerate every example image in examples/png and examples/pdf from the
# sample alignment. Run from anywhere:  examples/make_examples.sh
set -euo pipefail
cd "$(dirname "$0")/.."

L=./seqlogo.py
A=examples/zinc_finger.fasta
P=examples/png
D=examples/pdf
mkdir -p "$P" "$D"

slug() { echo "$1" | sed 's/^google://; s/:.*//; s/ /_/g' | tr 'A-Z' 'a-z'; }

# One logo per typeface, default colours
for f in "Helvetica Neue:bold" "Futura:bold" "Avenir Next Condensed:heavy" \
         "Gill Sans:bold" "DIN Condensed:bold" "Impact:regular" "Optima:black" \
         "Rockwell:bold" "Georgia:bold" "Baskerville:bold" \
         "American Typewriter:bold" "Menlo:bold" "google:Inter:900" \
         "google:Oswald:700" "google:Roboto Slab:800" "google:IBM Plex Mono:700" \
         "google:Space Grotesk:700"; do
  $L $A -o "$P/zf_$(slug "$f").png" --tick-every 1 --dpi 200 --show-font -f "$f"
done

# Probability units (README example)
$L $A -o "$P/zf_prob_roboto_condensed.png" --tick-every 1 --dpi 200 --show-font \
  -U prob -f "google:Roboto Condensed:400"

# Monaspace families in SemiWide and Wide, Bold and ExtraBold
for fam in Neon Argon Xenon Radon Krypton; do
  for w in SemiWide Wide; do
    base="$P/zf_monaspace_$(echo $fam | tr 'A-Z' 'a-z')_$(echo $w | tr 'A-Z' 'a-z')"
    $L $A -o "${base}_700.png" --tick-every 1 --dpi 200 --show-font -f "Monaspace $fam $w:700"
    $L $A -o "${base}_800.png" --tick-every 1 --dpi 200 --show-font -f "Monaspace $fam $w ExtraBold:800"
  done
done

# Typeface comparison sheets
$L $A -o $P/compare_sans.png --tick-every 1 --dpi 150 \
  -f "Helvetica Neue:bold" -f "Futura:bold" -f "Avenir Next Condensed:heavy" \
  -f "Gill Sans:bold" -f "DIN Condensed:bold" -f "google:Inter:900" \
  -f "google:Oswald:700" -f "google:Space Grotesk:700"
$L $A -o $P/compare_serif_mono.png --tick-every 1 --dpi 150 \
  -f "Georgia:bold" -f "Baskerville:bold" -f "Rockwell:bold" \
  -f "google:Roboto Slab:800" -f "American Typewriter:bold" -f "Menlo:bold" \
  -f "google:IBM Plex Mono:700"
monaspace=()
for fam in Neon Argon Xenon Radon Krypton; do
  monaspace+=(-f "Monaspace $fam:700" -f "Monaspace $fam:800")
done
$L $A -o $P/compare_monaspace.png --tick-every 1 --dpi 150 "${monaspace[@]}"
sofia=()
for w in 100 200 300 400 500 600 700 800; do
  sofia+=(-f "google:Sofia Sans Condensed:$w" -f "google:Sofia Sans Extra Condensed:$w")
done
$L $A -o $P/compare_sofia.png --tick-every 1 --dpi 150 --columns 2 "${sofia[@]}"

# Colour sets
$L $A -o $P/zf_okabe_ito.png --tick-every 1 --dpi 200 -c okabe_ito
$L $A -o $P/compare_colors_mine.png --tick-every 1 --dpi 150 \
  -c chem -c hydro -c charge
$L $A -o $P/compare_colors.png --tick-every 1 --dpi 150 --columns 2 \
  -c chem -c hydro -c charge -c rasmol
$L --swatches $P/swatches.png --dpi 200

# Vector versions for editing in Illustrator
$L $A -o $D/zf_chem.pdf --tick-every 1 -c chem
$L $A -o $D/zf_rasmol.pdf --tick-every 1 -c rasmol
$L $A -o $D/compare_colors.pdf --tick-every 1 --columns 2 \
  -c chem -c hydro -c charge -c okabe_ito -c rasmol
