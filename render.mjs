// Renders a piece's index.html frame by frame with Playwright.
//
//   node render.mjs beats [--at 0.5]   one frame per beat (optionally offset in beats) + contact sheet → out/qa/
//   node render.mjs times 4.5 5.25     specific seconds → out/qa/t-4.500.png
//   node render.mjs full [--workers 4] 4 subframes per 60 fps frame → out/sub/00000.png …
//
// --dir rtp-channel renders another piece (default: the UI morph at the repo root). The page sets
// its own frame size (MORPH.SIZE). Reads beats.json (measured tempo) and track.json (title, artist,
// duration) from the piece's folder, and writes out/timeline.json (loop + UI sound cues) for audio.py.
import http from 'node:http';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { execSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ARGS = process.argv.slice(2);
const flag = (name, def) => {
  const i = ARGS.indexOf(`--${name}`);
  return i >= 0 ? ARGS[i + 1] : def;
};
const DIR = flag('dir', '.');
const PIECE = path.join(HERE, DIR);
const OUT = path.join(PIECE, 'out');
const WEB = DIR === '.' ? '' : `${DIR.replace(/\/+$/, '')}/`;   // piece path as served
const FPS = 60;
const SUB = 4;
let SIZE = 1440;

async function loadPlaywright() {
  try {
    return await import('playwright');
  } catch {
    const root = execSync('npm root -g').toString().trim();
    return import(pathToFileURL(path.join(root, 'playwright', 'index.mjs')).href);
  }
}

const TYPES = { '.html': 'text/html', '.woff2': 'font/woff2', '.png': 'image/png', '.json': 'application/json' };
function serve() {
  return new Promise((resolve) => {
    const server = http.createServer(async (req, res) => {
      const rel = decodeURIComponent(new URL(req.url, 'http://local').pathname).replace(/^\/+/, '') || 'index.html';
      const file = path.join(HERE, rel);
      if (!file.startsWith(HERE)) return res.writeHead(403).end();
      try {
        const body = await readFile(file);
        res.writeHead(200, { 'content-type': TYPES[path.extname(file)] || 'application/octet-stream' }).end(body);
      } catch {
        res.writeHead(404).end();
      }
    });
    server.listen(0, '127.0.0.1', () => resolve(server));
  });
}

async function readJSON(name) {
  const file = path.join(PIECE, name);
  return existsSync(file) ? JSON.parse(await readFile(file, 'utf8')) : null;
}

async function pageConfig() {
  const beats = await readJSON('beats.json');
  const track = await readJSON('track.json');
  const cfg = {};
  if (beats?.bpm) cfg.__GRID__ = { bpm: beats.bpm };
  if (track) cfg.__TRACK__ = track;
  return cfg;
}

async function openPage(browser, url, cfg) {
  const page = await browser.newPage({ viewport: { width: SIZE, height: SIZE }, deviceScaleFactor: 1 });
  await page.addInitScript((c) => {
    window.__RENDER__ = true;
    Object.assign(window, c);
  }, cfg);
  await page.goto(url);
  await page.waitForFunction(() => window.__ready === true);
  SIZE = await page.evaluate(() => window.MORPH.SIZE || 1440);
  await page.setViewportSize({ width: SIZE, height: SIZE });
  return page;
}

async function shoot(page, t, file) {
  await page.evaluate((tt) => window.seek(tt), t);
  await page.screenshot({ path: file, type: 'png', clip: { x: 0, y: 0, width: SIZE, height: SIZE } });
}

const barBeat = (n) => `${Math.floor(n / 4) + 1}.${(Math.floor(n) % 4) + 1}${n % 1 ? `+${(n % 1).toFixed(2).slice(1)}` : ''}`;

async function contactSheet(browser, base, frames, file, title) {
  const cells = frames
    .map((f) => `<figure><img src="${base}/${f.src}"><figcaption><b>${f.label}</b><span>${f.sub}</span></figcaption></figure>`)
    .join('');
  const html = `<!doctype html><html><head><style>
    @font-face{font-family:Geist;src:url(${base}/fonts/Geist-Variable.woff2) format("woff2");font-weight:100 900}
    body{margin:0;background:#fff;font-family:Geist,sans-serif;color:#111}
    h1{font-size:22px;font-weight:600;margin:24px 24px 0}
    main{display:grid;grid-template-columns:repeat(7,300px);gap:14px;padding:18px 24px 24px}
    figure{margin:0} img{display:block;width:300px;height:300px;border-radius:6px}
    figcaption{display:flex;justify-content:space-between;font-size:14px;margin-top:6px}
    figcaption span{color:#777;font-variant-numeric:tabular-nums}
  </style></head><body><h1>${title}</h1><main>${cells}</main></body></html>`;
  const page = await browser.newPage({ viewport: { width: 7 * 300 + 6 * 14 + 48, height: 800 }, deviceScaleFactor: 1 });
  await page.setContent(html);
  await page.evaluate(() => document.fonts.ready);
  await page.waitForFunction(() => [...document.images].every((i) => i.complete));
  await page.screenshot({ path: file, fullPage: true });
  await page.close();
}

async function main() {
  const [mode = 'beats', ...rest] = ARGS;
  const { chromium } = await loadPlaywright();
  const server = await serve();
  const base = `http://127.0.0.1:${server.address().port}`;
  const cfg = await pageConfig();
  const browser = await chromium.launch();

  try {
    const page = await openPage(browser, `${base}/${WEB}index.html`, cfg);
    const info = await page.evaluate(() => ({ T: window.MORPH.T, BEAT: window.MORPH.BEAT, BPM: window.MORPH.BPM, NB: window.MORPH.NB, SFX: window.MORPH.SFX }));
    await mkdir(OUT, { recursive: true });
    await writeFile(path.join(OUT, 'timeline.json'), JSON.stringify({ ...info, fps: FPS, frames: Math.round(info.T * FPS) }, null, 2));

    if (mode === 'beats') {
      const at = parseFloat(flag('at', '0'));
      const dir = path.join(OUT, 'qa');
      await mkdir(dir, { recursive: true });
      const frames = [];
      for (let n = 0; n < info.NB; n++) {
        const beat = n + at;
        const name = `beat-${String(n).padStart(2, '0')}${at ? `-${at}` : ''}.png`;
        await shoot(page, beat * info.BEAT, path.join(dir, name));
        frames.push({ src: `${WEB}out/qa/${name}`, label: barBeat(beat), sub: `${(beat * info.BEAT).toFixed(2)}s` });
      }
      const sheet = path.join(dir, `contact${at ? `-${at}` : ''}.png`);
      await contactSheet(browser, base, frames, sheet, `One frame per beat${at ? ` (+${at} beat)` : ''} · ${info.BPM.toFixed(2)} BPM`);
      console.log(`wrote ${frames.length} frames and ${path.relative(HERE, sheet)}`);
    } else if (mode === 'times') {
      const dir = path.join(OUT, 'qa');
      await mkdir(dir, { recursive: true });
      for (const s of rest.filter((a) => !a.startsWith('--'))) {
        const t = parseFloat(s);
        await shoot(page, t, path.join(dir, `t-${t.toFixed(3)}.png`));
      }
      console.log('done');
    } else if (mode === 'full') {
      const frames = Math.round(info.T * FPS);
      const total = frames * SUB;
      const workers = parseInt(flag('workers', '4'), 10);
      const from = parseInt(flag('from', '0'), 10);
      const dir = path.join(OUT, 'sub');
      await mkdir(dir, { recursive: true });
      // Subframes are centred on each output frame; times before 0 wrap to the end of the loop.
      const timeOf = (i) => (Math.floor(i / SUB) + ((i % SUB) + 0.5) / SUB - 0.5) / FPS;
      const chunk = Math.ceil((total - from) / workers);
      let done = 0;
      const started = Date.now();
      await Promise.all(
        Array.from({ length: workers }, async (_, w) => {
          const own = await chromium.launch();
          const p = await openPage(own, `${base}/${WEB}index.html`, cfg);
          for (let i = from + w * chunk; i < Math.min(total, from + (w + 1) * chunk); i++) {
            await shoot(p, timeOf(i), path.join(dir, `${String(i).padStart(5, '0')}.png`));
            if (++done % 200 === 0) {
              const rate = done / ((Date.now() - started) / 1000);
              console.log(`${done}/${total - from} subframes · ${rate.toFixed(1)}/s · eta ${((total - from - done) / rate).toFixed(0)}s`);
            }
          }
          await own.close();
        }),
      );
      console.log(`rendered ${total - from} subframes (${frames} frames × ${SUB}) in ${((Date.now() - started) / 1000).toFixed(0)}s`);
    } else {
      throw new Error(`unknown mode ${mode}`);
    }
  } finally {
    await browser.close();
    server.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
