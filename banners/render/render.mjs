// Frame-stepped banner renderer: node render.mjs JOB.json
//
// Driven by banners/render.py; never run by hand in the pipeline. The job
// (all paths absolute):
//   {
//     "chromium": ".../chrome-headless-shell.exe",
//     "width": 1080, "height": 1920,
//     "page": "index.html",                    // a key of "files"
//     "files": {"index.html": "C:/.../index.html", "fonts/x.ttf": "C:/..."},
//     "frames": [0, 1, 2, ...],                // frame indices to capture
//     "out_dir": "C:/.../frames",              // writes f%05d.png
//     "hide_text_frames": [0, 120],            // also capture with text hidden: bg%05d.png
//     "layout": "C:/.../layout.json",          // what apply() returned per frame, + fonts used
//     "locale": "en-US",
//     "transparent": false                     // true: keep the page's alpha (logo art)
//   }
//
// The page provides window.artpipe:
//   ready()        -> {text: [element ids of text layers], ...}   after fonts and images load
//   apply(n)       -> the layout of frame n (any JSON), after setting every layer's state
//   hideText(on)   hides / shows the text layers (the background pass for contrast checks)
//
// Time never runs: the page has no CSS animations and no timers. For frame n
// the page applies the precomputed state of every layer
// (window.artpipe.apply(n)), then the frame is captured. So a slow machine
// gives the same frames as a fast one, and no frame is ever dropped.
//
// The page loads from http://banner.invalid/ (a reserved name that never
// resolves). Every request is answered from "files" or refused, so a
// template can't pull a font or an image from the network, and a missing
// file is an error, not a silent fallback.
import { chromium } from "playwright";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { extname, join } from "node:path";

const ORIGIN = "http://banner.invalid/";
const TYPES = {
  ".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript",
  ".json": "application/json", ".png": "image/png", ".jpg": "image/jpeg",
  ".ttf": "font/ttf", ".otf": "font/otf", ".woff2": "font/woff2", ".svg": "image/svg+xml",
};
// Software raster, no LCD subpixel text, no hinting, sRGB output: the
// settings that make text and gradients the same bytes on every run.
const ARGS = [
  "--disable-gpu", "--force-color-profile=srgb", "--font-render-hinting=none",
  "--disable-lcd-text", "--disable-font-subpixel-positioning", "--hide-scrollbars",
  "--disable-features=PaintHolding", "--force-device-scale-factor=1",
];

function fail(message) {
  process.stderr.write("render.mjs: " + message + "\n");
  process.exit(2);
}

const job = JSON.parse(readFileSync(process.argv[2], "utf8"));
const refused = [];
const browser = await chromium.launch({ executablePath: job.chromium, args: ARGS, headless: true });
try {
  const context = await browser.newContext({
    viewport: { width: job.width, height: job.height }, deviceScaleFactor: 1,
    colorScheme: "light", reducedMotion: "reduce", locale: job.locale || "en-US", timezoneId: "UTC",
  });
  await context.route("**/*", async (route) => {
    const url = route.request().url();
    const key = url.startsWith(ORIGIN) ? decodeURIComponent(url.slice(ORIGIN.length).split("?")[0]) : null;
    if (key === null || !(key in job.files)) {
      refused.push(url);
      return route.abort("blockedbyclient");
    }
    const body = readFileSync(job.files[key]);
    return route.fulfill({ status: 200, body, contentType: TYPES[extname(key).toLowerCase()] || "application/octet-stream" });
  });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(ORIGIN + job.page, { waitUntil: "load" });
  // Fonts and images must be decoded before the first capture.
  const ready = await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all([...document.images].map((i) => i.decode().catch(() => null)));
    return window.artpipe ? window.artpipe.ready() : { error: "page has no window.artpipe" };
  });
  if (ready.error) fail(ready.error);
  if (errors.length) fail("page errors: " + errors.join("; "));

  // Which fonts really drew each text layer. A missing font, or a glyph the
  // font lacks (probed: Fredoka has no Cyrillic), falls back to a system
  // font without any error, and document.fonts.check() still says true.
  // Chromium's own record of the fonts a node used is the only honest answer.
  const fonts = {};
  const cdp = await context.newCDPSession(page);
  await cdp.send("DOM.enable");
  await cdp.send("CSS.enable");
  const { root } = await cdp.send("DOM.getDocument", { depth: -1 });
  for (const id of ready.text || []) {
    const { nodeId } = await cdp.send("DOM.querySelector", { nodeId: root.nodeId, selector: "#" + id });
    if (!nodeId) fail("text layer #" + id + " is not in the page");
    fonts[id] = (await cdp.send("CSS.getPlatformFontsForNode", { nodeId })).fonts
      .map((f) => ({ family: f.familyName, postscript: f.postScriptName, custom: f.isCustomFont, glyphs: f.glyphCount }));
  }

  mkdirSync(job.out_dir, { recursive: true });
  const layout = {};
  const hide = new Set(job.hide_text_frames || []);
  const name = (prefix, n) => join(job.out_dir, prefix + String(n).padStart(5, "0") + ".png");
  const opts = { type: "png", animations: "disabled", caret: "initial", omitBackground: !!job.transparent };
  for (const n of job.frames) {
    layout[n] = await page.evaluate((f) => window.artpipe.apply(f), n);
    writeFileSync(name("f", n), await page.screenshot(opts));
    if (hide.has(n)) {
      await page.evaluate(() => window.artpipe.hideText(true));
      writeFileSync(name("bg", n), await page.screenshot(opts));
      await page.evaluate(() => window.artpipe.hideText(false));
    }
  }
  if (errors.length) fail("page errors: " + errors.join("; "));
  if (job.layout) {
    writeFileSync(job.layout, JSON.stringify({ browser: browser.version(), ready, refused, fonts, frames: layout }));
  }
} catch (e) {
  fail(String(e && e.stack || e));
} finally {
  await browser.close();
}
