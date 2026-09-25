"use strict";
// Loads Python (Pyodide) in the page, then draws logos with the unmodified
// seqlogo.py. The alignment is only ever read here, in the browser.

const FONTS = [
  { file: "Oswald-700.ttf", name: "Oswald 700" },
  { file: "Antonio-700.ttf", name: "Antonio 700" },
  { file: "SofiaSansCondensed-800.ttf", name: "Sofia Sans Condensed 800" },
  { file: "RobotoCondensed-400.ttf", name: "Roboto Condensed 400" },
  { file: "Inter-900.ttf", name: "Inter 900" },
  { file: "RobotoSlab-800.ttf", name: "Roboto Slab 800" },
  { file: "BebasNeue-400.ttf", name: "Bebas Neue 400" },
];
const COLOR_LABELS = {
  chem: "Chemistry", hydro: "Hydrophobicity", charge: "Charge",
  rasmol: "RasMol", okabe_ito: "Okabe–Ito (color-blind safe)", mono: "Black/white",
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
let pyodide = null;
let web = null;            // the logo_web Python module
let googleFonts = {};      // family -> weights, from google_fonts.json
let googleFont = null;     // {name, path} of the loaded Google font, if any
const googleCache = {};    // "Family 700" -> path in Pyodide's file system
let googleRequest = 0;     // ignores answers to superseded requests
let brotliLoaded = false;
const fontReady = {};      // preset name -> Promise, resolved once it's in Pyodide
let googleListReady = null;
let drawing = 0;           // ignores superseded preview requests

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
    $("font-presets").append(label);
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
    name.textContent = COLOR_LABELS[set] || set;
    label.append(radio("colors", set, i === 0), strip, name);
    $("colors").append(label);
  });
}

function choice(name) {
  return document.querySelector(`input[name="${name}"]:checked`).value;
}

function settings(format, dpi, maxColumns = 0) {
  const which = choice("font");
  const font = which === "google" ? googleFont
    : { ...FONTS[Number(which)], path: `/fonts/${FONTS[Number(which)].name}.ttf` };
  return {
    font, format, dpi, maxColumns,
    colors: choice("colors"),
    background: choice("background"),
    units: choice("units"),
    gap: Number(choice("gap")),
    letterHeight: Number(choice("letterheight")),
    tick: Number(choice("tick")),
    perLine: Number(choice("perline")),
    correction: $("correction").checked,
    showFont: $("showfont").checked,
  };
}

// Returns {data: Uint8Array, notes} or {error}.
function draw(s) {
  const result = makeLogo(
    $("alignment").value, s.font.path, s.colors, s.format, s.units,
    s.correction, s.gap, s.tick, s.perLine, s.showFont, s.dpi, s.maxColumns, s.background, s.letterHeight);
  const out = result.toJs({ dict_converter: Object.fromEntries });
  result.destroy();
  return out;
}

// Wait for a preset font that is still downloading in the background.
async function fontLoaded(s) {
  const ready = fontReady[s.font.name];
  if (ready) {
    setStatus(`Loading ${s.font.name}…`);
    await ready;
  }
}

async function refresh() {
  if (!makeLogo) return;
  if (choice("font") === "google" && !googleFont) {
    setStatus("Type a Google Fonts family name in the Typeface box.");
    return;
  }
  const request = ++drawing;
  const perLine = Number(choice("perline"));
  const limit = $("fullpreview").checked ? 0 : PREVIEW_ROWS * perLine;
  const s = settings("png", PREVIEW_DPI, limit);
  await fontLoaded(s);
  if (request !== drawing) return;
  const out = draw(s);
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
  pending = setTimeout(() => refresh().catch((e) => setStatus(e.message, true)), 350);
}

async function download() {
  if (choice("font") === "google" && !googleFont) return;
  const format = choice("format");
  const s = settings(format, DOWNLOAD_DPI);
  await fontLoaded(s);
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

// ---- Google Fonts box -------------------------------------------------------

const WEIGHT_NAMES = {
  thin: 100, extralight: 200, ultralight: 200, light: 300, regular: 400, normal: 400,
  book: 400, medium: 500, semibold: 600, demibold: 600, bold: 700, extrabold: 800,
  ultrabold: 800, black: 900, heavy: 900,
};
const lowerNames = {};     // "lobster" -> "Lobster"

function nearest(weights, target) {
  return weights.reduce((a, b) => (Math.abs(b - target) < Math.abs(a - target) ? b : a));
}

// "Oswald", "oswald:600" or "Oswald:semibold" -> {family, weight or null}
function parseGoogleSpec(text) {
  const [namePart, weightPart] = text.split(":");
  const family = lowerNames[namePart.trim().toLowerCase().replace(/\s+/g, " ")];
  let weight = null;
  if (weightPart !== undefined) {
    const w = weightPart.trim().toLowerCase().replace(/[\s-]/g, "");
    weight = /^\d+$/.test(w) ? Number(w) : WEIGHT_NAMES[w] ?? null;
  }
  return { family, weight, typed: namePart.trim() };
}

function editDistance(a, b) {
  let prev = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    const row = [i];
    for (let j = 1; j <= b.length; j++) {
      row[j] = Math.min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    }
    prev = row;
  }
  return prev[b.length];
}

// Names starting with or containing what was typed, else the closest spellings.
function suggestions(typed) {
  const t = typed.toLowerCase();
  const names = Object.keys(googleFonts);
  const hits = names.filter((n) => n.toLowerCase().startsWith(t))
    .concat(names.filter((n) => n.toLowerCase().includes(t) && !n.toLowerCase().startsWith(t)));
  if (hits.length) return hits.slice(0, 3);
  const limit = Math.max(1, Math.floor(t.length / 3));
  return names.map((n) => [editDistance(t, n.toLowerCase()), n])
    .filter(([d]) => d <= limit).sort((a, b) => a[0] - b[0]).slice(0, 3).map(([, n]) => n);
}

// While typing, act only on a complete family name (as picked from the
// drop-down list); complain about unknown names once the box is left or
// Enter is pressed, so a half-typed name isn't flagged.
async function onGoogleTyping() {
  await googleListReady;
  const { family } = parseGoogleSpec($("gfont").value.trim());
  if (family || !$("gfont").value.trim()) onGoogleInput();
  else if ($("status").classList.contains("error")) setStatus("");
}

async function onGoogleInput() {
  await googleListReady;
  $("fonts").querySelector('input[value="google"]').checked = true;
  const text = $("gfont").value.trim();
  const menu = $("gweight");
  if (!text) {
    menu.replaceChildren();
    menu.disabled = true;
    googleFont = null;
    $("gsample").textContent = "";
    scheduleRefresh();
    return;
  }
  const { family, weight, typed } = parseGoogleSpec(text);
  if (!family) {
    menu.replaceChildren();
    menu.disabled = true;
    googleFont = null;
    $("gsample").textContent = "";
    const close = suggestions(typed);
    setStatus(`No Google font named "${typed}".` +
              (close.length ? ` Did you mean ${close.join(", ")}?` : ""), true);
    return;
  }
  const weights = googleFonts[family];
  menu.replaceChildren(...weights.map((w) => {
    const option = document.createElement("option");
    option.value = String(w);
    option.textContent = String(w);
    return option;
  }));
  menu.disabled = false;
  menu.value = String(nearest(weights, weight ?? 700));
  loadGoogleFont();
}

async function loadGoogleFont() {
  const { family } = parseGoogleSpec($("gfont").value.trim());
  if (!family) return;
  const weight = Number($("gweight").value);
  const key = `${family} ${weight}`;
  const request = ++googleRequest;
  try {
    if (!googleCache[key]) {
      setStatus(`Downloading ${key} from Google Fonts…`);
      const query = `${encodeURIComponent(family).replace(/%20/g, "+")}:wght@${weight}`;
      const response = await fetch(`https://fonts.googleapis.com/css2?family=${query}`);
      if (!response.ok) throw new Error(`Google Fonts has no ${key}`);
      const css = await response.text();
      // Google splits a font by alphabet; the "latin" part has A-Z.
      const block = css.match(/\/\*\s*latin\s*\*\/\s*@font-face\s*{[^}]*}/)?.[0] ?? css;
      const url = block.match(/url\((https:\/\/fonts\.gstatic\.com\/[^)]+)\)/)?.[1];
      if (!url) throw new Error(`no font file found for ${key}`);
      const bytes = new Uint8Array(await (await fetch(url)).arrayBuffer());
      if (!brotliLoaded) {                  // needed to unpack .woff2 files
        await pyodide.loadPackage("brotli");
        brotliLoaded = true;
      }
      googleCache[key] = web.install_google_font(bytes, family, weight);
      const face = new FontFace(`G ${key}`, bytes);
      document.fonts.add(await face.load());
    }
    if (request !== googleRequest) return;
    googleFont = { name: key, path: googleCache[key] };
    $("gsample").style.fontFamily = `"G ${key}"`;
    $("gsample").textContent = "KCEHRW";
    if (choice("font") === "google") scheduleRefresh();
  } catch (e) {
    if (request !== googleRequest) return;
    googleFont = null;
    setStatus(`Could not load ${key}: ${e.message}`, true);
  }
}

async function loadGoogleList() {
  googleFonts = await (await fetchFresh("google_fonts.json")).json();
  const list = $("gfont-list");
  for (const name of Object.keys(googleFonts)) {
    lowerNames[name.toLowerCase()] = name;
    const option = document.createElement("option");
    option.value = name;
    list.append(option);
  }
}

function setUpGoogleFonts() {
  let typing = null;
  $("gfont").addEventListener("input", () => {
    clearTimeout(typing);
    typing = setTimeout(onGoogleTyping, 500);
  });
  $("gfont").addEventListener("change", () => {   // left the box, or Enter
    clearTimeout(typing);
    onGoogleInput();
  });
  $("gfont").addEventListener("focus", () => {
    if ($("gfont").value.trim()) $("fonts").querySelector('input[value="google"]').checked = true;
  });
  $("gweight").addEventListener("change", loadGoogleFont);
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

const fetchBytes = async (url) => new Uint8Array(await (await fetchFresh(url)).arrayBuffer());

// Put a preset font into Pyodide's file system; the preview waits on it only
// if that font is chosen before it has arrived.
function installFont(f, bytes) {
  fontReady[f.name] = bytes.then((b) => {
    pyodide.FS.writeFile(`/fonts/${f.name}.ttf`, b);
    delete fontReady[f.name];
  });
  return fontReady[f.name];
}

async function start() {
  buildFontChoices();
  // Fetch the Python files and the default font while Python itself loads.
  const sources = [["../seqlogo.py", "/app/seqlogo.py"], ["logo_web.py", "/app/logo_web.py"]]
    .map(([src, dest]) => fetchFresh(src).then((r) => r.text()).then((text) => [dest, text]));
  const firstFont = fetchBytes(`fonts/${FONTS[0].file}`);
  pyodide = await loadPyodide();
  setStatus("Loading numpy and matplotlib…");
  await pyodide.loadPackage(["numpy", "matplotlib"]);

  pyodide.FS.mkdirTree("/app");
  pyodide.FS.mkdirTree("/fonts");
  for (const [dest, text] of await Promise.all(sources)) pyodide.FS.writeFile(dest, text);
  await installFont(FONTS[0], firstFont);
  pyodide.runPython("import sys; sys.path.insert(0, '/app')");
  web = pyodide.pyimport("logo_web");
  $("version").textContent = ` ${pyodide.runPython("import seqlogo; seqlogo.__version__")}`;
  setUpGoogleFonts();
  googleListReady = loadGoogleList();   // not waited for
  makeLogo = web.make_logo;
  const sets = web.color_sets().toJs({ dict_converter: Object.fromEntries });
  buildColorChoices(sets);

  // The preview is always a PNG; the download format only matters for Download.
  document.querySelectorAll('.options input:not([name="format"]):not(#gfont), input[name="background"], #fullpreview')
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
  // Everything else downloads quietly in the background.
  for (const f of FONTS.slice(1)) installFont(f, fetchBytes(`fonts/${f.file}`));
}

start().catch((e) => setStatus(`Could not start: ${e.message}`, true));
