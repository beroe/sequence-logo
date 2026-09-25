"use strict";
// Loads Python (Pyodide) in the page, then draws logos with the unmodified
// seqlogo.py. The alignment is only ever read here, in the browser.

const FONTS = [
  { file: "Oswald-700.ttf", name: "Oswald 700" },
  { file: "SofiaSansCondensed-800.ttf", name: "Sofia Sans Condensed 800" },
  { file: "RobotoCondensed-400.ttf", name: "Roboto Condensed 400" },
  { file: "RobotoCondensed-700.ttf", name: "Roboto Condensed 700" },
  { file: "Inter-900.ttf", name: "Inter 900" },
  { file: "RobotoSlab-800.ttf", name: "Roboto Slab 800" },
  { file: "IBMPlexMono-700.ttf", name: "IBM Plex Mono 700" },
];
const COLOR_LABELS = {
  chem: "chemistry", hydro: "hydrophobicity", charge: "charge",
  rasmol: "RasMol", okabe_ito: "Okabe–Ito (color-blind safe)", mono: "black",
};
const SWATCH_ORDER = "GSTYCMNQKRHDEPAWFLIV";
const PREVIEW_DPI = 110;
const PREVIEW_ROWS = 2;      // unless "Preview the whole alignment" is ticked
const DOWNLOAD_DPI = 300;
const MAX_FILE_BYTES = 500_000;   // same limit as pasted text in logo_web.py
const MIME = { png: "image/png", svg: "image/svg+xml", pdf: "application/pdf" };

const $ = (id) => document.getElementById(id);
// Always check with the server for newer files, so a cached seqlogo.py can't
// fall out of step with logo_web.py after an update.
const fetchFresh = (url) => fetch(url, { cache: "no-cache" });
let makeLogo = null;       // Python function, once loaded
let previewUrl = null;
let pending = null;        // debounce timer
let baseName = "logo";     // for download names

function setStatus(text, isError = false) {
  $("status").textContent = text;
  $("status").classList.toggle("error", isError);
}

function radio(name, value, checked) {
  const input = document.createElement("input");
  input.type = "radio";
  input.name = name;
  input.value = value;
  input.checked = checked;
  return input;
}

function buildFontChoices() {
  FONTS.forEach((f, i) => {
    const label = document.createElement("label");
    label.className = "choice";
    const sample = document.createElement("span");
    sample.className = "sample";
    sample.style.fontFamily = `"${f.name}"`;
    sample.textContent = "KCEHRW";
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = f.name;
    label.append(radio("font", String(i), i === 0), sample, name);
    $("fonts").append(label);
  });
}

function buildColorChoices(sets) {
  Object.entries(sets).forEach(([set, colors], i) => {
    const label = document.createElement("label");
    label.className = "choice";
    const strip = document.createElement("span");
    strip.className = "swatches";
    for (const aa of SWATCH_ORDER) {
      const box = document.createElement("span");
      box.style.background = colors[aa];
      box.textContent = aa;
      strip.append(box);
    }
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = COLOR_LABELS[set] ? `${set} — ${COLOR_LABELS[set]}` : set;
    label.append(radio("colors", set, i === 0), strip, name);
    $("colors").append(label);
  });
}

function choice(name) {
  return document.querySelector(`input[name="${name}"]:checked`).value;
}

function settings(format, dpi, maxColumns = 0) {
  const font = FONTS[Number(choice("font"))];
  return {
    font, format, dpi, maxColumns,
    colors: choice("colors"),
    background: choice("background"),
    units: choice("units"),
    gap: Number(choice("gap")),
    tick: Number(choice("tick")),
    perLine: Number(choice("perline")),
    correction: $("correction").checked,
    showFont: $("showfont").checked,
  };
}

// Returns {data: Uint8Array, notes} or {error}.
function draw(s) {
  const result = makeLogo(
    $("alignment").value, `/fonts/${s.font.name}.ttf`, s.colors, s.format, s.units,
    s.correction, s.gap, s.tick, s.perLine, s.showFont, s.dpi, s.maxColumns, s.background);
  const out = result.toJs({ dict_converter: Object.fromEntries });
  result.destroy();
  return out;
}

function refresh() {
  if (!makeLogo) return;
  const perLine = Number(choice("perline"));
  const limit = $("fullpreview").checked ? 0 : PREVIEW_ROWS * perLine;
  const out = draw(settings("png", PREVIEW_DPI, limit));
  $("notes").hidden = !out.notes;
  $("notes").textContent = out.notes || "";
  if (out.error) {
    setStatus(out.error, true);
    $("logo").hidden = true;
    $("download").disabled = true;
    return;
  }
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = URL.createObjectURL(new Blob([out.data], { type: MIME.png }));
  $("logo").src = previewUrl;
  $("logo").hidden = false;
  $("download").disabled = false;
  setStatus("");
}

// The small-sample correction only changes heights in bits.
function updateCorrection() {
  const bits = choice("units") === "bits";
  $("correction").disabled = !bits;
  $("correction-label").classList.toggle("disabled", !bits);
  $("correction-label").title = bits ? "" : "Only applies to heights in bits";
}

function applyTheme() {
  document.documentElement.classList.toggle("dark", choice("background") === "black");
}

function scheduleRefresh() {
  clearTimeout(pending);
  setStatus("Drawing…");
  pending = setTimeout(refresh, 350);
}

async function download() {
  const format = choice("format");
  const s = settings(format, DOWNLOAD_DPI);
  setStatus("Preparing download…");
  $("download").disabled = true;
  await new Promise((r) => setTimeout(r, 30));   // let the message appear first
  const out = draw(s);
  $("download").disabled = false;
  if (out.error) {
    setStatus(out.error, true);
    return;
  }
  setStatus("");
  const slug = s.font.name.toLowerCase().replace(/[^a-z0-9]/g, "");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([out.data], { type: MIME[format] }));
  const bg = s.background === "black" ? "-black" : "";
  a.download = `${baseName}-${slug}-${s.colors}${bg}.${format}`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

async function loadSample() {
  const response = await fetchFresh("../examples/zinc_finger.fasta");
  $("alignment").value = await response.text();
  baseName = "zinc_finger";
  scheduleRefresh();
}

// A chosen or dropped file is read here in the browser, like pasted text.
async function loadFile(file) {
  if (!file) return;
  if (file.size > MAX_FILE_BYTES) {
    setStatus(`That file is too large (${Math.round(file.size / 1000).toLocaleString()} KB; ` +
              `the limit is ${MAX_FILE_BYTES / 1000} KB).`, true);
    return;
  }
  $("alignment").value = await file.text();
  baseName = file.name.replace(/\.[^.]*$/, "").toLowerCase().replace(/[^a-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "") || "logo";
  scheduleRefresh();
}

async function start() {
  buildFontChoices();
  const pyodide = await loadPyodide();
  setStatus("Loading numpy and matplotlib…");
  await pyodide.loadPackage(["numpy", "matplotlib"]);

  setStatus("Loading fonts…");
  pyodide.FS.mkdirTree("/app");
  pyodide.FS.mkdirTree("/fonts");
  for (const [src, dest] of [["../seqlogo.py", "/app/seqlogo.py"],
                             ["logo_web.py", "/app/logo_web.py"]]) {
    pyodide.FS.writeFile(dest, await (await fetchFresh(src)).text());
  }
  for (const f of FONTS) {
    const bytes = new Uint8Array(await (await fetchFresh(`fonts/${f.file}`)).arrayBuffer());
    pyodide.FS.writeFile(`/fonts/${f.name}.ttf`, bytes);
  }
  pyodide.runPython("import sys; sys.path.insert(0, '/app')");
  const web = pyodide.pyimport("logo_web");
  makeLogo = web.make_logo;
  const sets = web.color_sets().toJs({ dict_converter: Object.fromEntries });
  buildColorChoices(sets);

  // The preview is always a PNG; the download format only matters for Download.
  document.querySelectorAll('.options input:not([name="format"]), input[name="background"], #fullpreview')
    .forEach((el) => el.addEventListener("change", scheduleRefresh));
  $("alignment").addEventListener("input", scheduleRefresh);
  $("sample").addEventListener("click", loadSample);
  $("clear").addEventListener("click", () => {
    $("alignment").value = "";
    baseName = "logo";
    scheduleRefresh();
  });
  $("open").addEventListener("click", () => $("file").click());
  $("file").addEventListener("change", (e) => { loadFile(e.target.files[0]); e.target.value = ""; });
  const box = $("alignment");
  box.addEventListener("dragover", (e) => { e.preventDefault(); box.classList.add("dragging"); });
  box.addEventListener("dragleave", () => box.classList.remove("dragging"));
  box.addEventListener("drop", (e) => {
    e.preventDefault();
    box.classList.remove("dragging");
    loadFile(e.dataTransfer.files[0]);
  });
  $("download").addEventListener("click", download);
  document.querySelectorAll('input[name="background"]').forEach((el) =>
    el.addEventListener("change", applyTheme));
  document.querySelectorAll('input[name="units"]').forEach((el) =>
    el.addEventListener("change", updateCorrection));
  applyTheme();   // a reload can keep these choices
  updateCorrection();
  await loadSample();
}

start().catch((e) => setStatus(`Could not start: ${e.message}`, true));
