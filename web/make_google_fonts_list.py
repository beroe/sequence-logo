"""Write web/google_fonts.json, the Google Fonts families and weights the web
page accepts in its Google font box. Run now and then to pick up new fonts:

    python3 web/make_google_fonts_list.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import seqlogo  # noqa: E402

catalogue = seqlogo.google_catalogue()
families = {name: info["weights"] for name, info in sorted(catalogue.items())
            if info["weights"]}
(HERE / "google_fonts.json").write_text(json.dumps(families, separators=(",", ":")))
print(f"wrote {len(families)} families to {HERE / 'google_fonts.json'}")
