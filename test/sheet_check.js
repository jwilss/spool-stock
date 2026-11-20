// Checks a sticker PDF against a label sheet's layout: each QR code must decode and sit inside a label, and no ink
// may fall in the gaps between labels. Used by test/run.js.
//
// It reads the codes with the app's own jsQR, so it needs only Node and Poppler's pdftoppm, no OpenCV. jsQR finds one
// code per image and gets confused by several touching codes, so the page is read in overlapping square windows a
// little bigger than a label is tall, and each code is kept once however many windows it turns up in.
const {execFileSync} = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const jsQR = require("../site/vendor/jsQR.js");

const DPI = 300; // the ink check works at this resolution
const SCALE = 3; // the codes are read at DPI / SCALE, which is plenty for a code over 20 mm across and much faster

// Renders every page of a PDF to 8-bit greyscale and returns [{w, h, px}], one per page.
function renderPages(pdf) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "sheet-"));
  try {
    // Poppler warns about fonts it has no display copy of (Symbol, ArialUnicode); those don't matter here.
    execFileSync("pdftoppm", ["-r", String(DPI), "-gray", pdf, path.join(dir, "p")], {stdio: "ignore"});
  } catch (e) {
    if (e.code === "ENOENT")
      throw new Error("Needs pdftoppm (Poppler) on the PATH. Install it, open a new terminal, then run this again.");
    throw e;
  }
  const pages = fs
    .readdirSync(dir)
    .filter(f => f.endsWith(".pgm"))
    .sort()
    .map(f => {
      // PGM: "P5", width, height and the largest value, separated by whitespace, then one byte per pixel
      const buf = fs.readFileSync(path.join(dir, f));
      const m = buf.toString("latin1", 0, 64).match(/^P5\s+(\d+)\s+(\d+)\s+\d+\s/);
      return {w: +m[1], h: +m[2], px: buf.subarray(m[0].length)};
    });
  fs.rmSync(dir, {recursive: true, force: true});
  return pages;
}

// Averages each SCALE x SCALE block, so the codes are read from a smaller, smoother picture.
function shrink({w, h, px}) {
  const sw = Math.floor(w / SCALE),
    sh = Math.floor(h / SCALE),
    out = new Uint8Array(sw * sh);
  for (let y = 0; y < sh; y++)
    for (let x = 0; x < sw; x++) {
      let sum = 0;
      for (let dy = 0; dy < SCALE; dy++)
        for (let dx = 0; dx < SCALE; dx++) sum += px[(y * SCALE + dy) * w + x * SCALE + dx];
      out[y * sw + x] = sum / (SCALE * SCALE);
    }
  return {w: sw, h: sh, px: out};
}

// Finds every QR code on a page by reading overlapping windows of `win` mm, moving `step` mm at a time.
// Returns [{value, x, y, size}] in mm: the code's centre and its width.
function findCodes(page, dpi, win, step) {
  const k = dpi / 25.4,
    S = Math.round(win * k),
    st = Math.max(1, Math.round(step * k));
  const rgba = new Uint8ClampedArray(S * S * 4);
  const codes = [];
  for (let y0 = 0; y0 < page.h; y0 += st)
    for (let x0 = 0; x0 < page.w; x0 += st) {
      rgba.fill(255);
      for (let y = 0; y < S && y0 + y < page.h; y++)
        for (let x = 0; x < S && x0 + x < page.w; x++) {
          const g = page.px[(y0 + y) * page.w + x0 + x],
            i = (y * S + x) * 4;
          rgba[i] = rgba[i + 1] = rgba[i + 2] = g;
        }
      const q = jsQR(rgba, S, S, {inversionAttempts: "dontInvert"});
      if (!q || !q.data) continue;
      const corners = ["topLeftCorner", "topRightCorner", "bottomLeftCorner", "bottomRightCorner"].map(
        n => q.location[n],
      );
      const xs = corners.map(p => (p.x + x0) / k),
        ys = corners.map(p => (p.y + y0) / k);
      const code = {
        value: q.data,
        x: xs.reduce((a, b) => a + b) / 4,
        y: ys.reduce((a, b) => a + b) / 4,
        size: Math.max(...xs) - Math.min(...xs),
      };
      // the same code seen from another window lands within a millimetre or so of where it was first found
      if (!codes.some(c => c.value === code.value && Math.hypot(c.x - code.x, c.y - code.y) < 3)) codes.push(code);
    }
  return codes;
}

// The layout is in mm: label width and height, columns and rows, the first label's left and top, and the step
// from one label to the next across and down. Returns what test/run.js checks:
// {pages, codes, inside, right, size, inkOutside}.
function sheetCheck(pdf, {w, h, cols, rows, left, top, stepAcross, stepDown}) {
  const pages = renderPages(pdf);
  const page = pages[0];
  const labels = [];
  for (let r = 0; r < rows; r++)
    for (let c = 0; c < cols; c++) labels.push([left + c * stepAcross, top + r * stepDown]);

  const codes = findCodes(shrink(page), DPI / SCALE, Math.min(w, h) + 7, 6);
  const inside = codes.filter(({x, y}) => labels.some(([lx, ly]) => lx <= x && x <= lx + w && ly <= y && y <= ly + h));
  const right = codes.filter(({x, y}) =>
    labels.some(([lx, ly]) => lx + w * 0.6 <= x && x <= lx + w && ly <= y && y <= ly + h),
  );
  const size = codes.length ? Math.round(Math.min(...codes.map(c => c.size)) * 10) / 10 : 0;

  // Ink: anything darker than 200 outside the labels, each label trimmed by 0.3 mm to allow for rounding
  const k = DPI / 25.4,
    onLabel = new Uint8Array(page.w * page.h);
  for (const [lx, ly] of labels)
    for (let y = Math.floor((ly + 0.3) * k); y < Math.floor((ly + h - 0.3) * k) && y < page.h; y++)
      onLabel.fill(
        1,
        y * page.w + Math.floor((lx + 0.3) * k),
        y * page.w + Math.min(page.w, Math.floor((lx + w - 0.3) * k)),
      );
  let stray = 0;
  for (let i = 0; i < page.px.length; i++) if (page.px[i] < 200 && !onLabel[i]) stray++;

  return {
    pages: pages.length,
    codes: codes.length,
    inside: inside.length,
    right: right.length,
    size,
    inkOutside: stray > 50 ? 1 : 0,
  };
}

module.exports = {sheetCheck};
