import { createRequire } from 'module';
const require = createRequire('/Users/z1nex/ya-games/studio/');
const { chromium } = require('playwright-core');
const base = 'http://localhost:8792';
const out = new URL('./img/', import.meta.url).pathname;
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
const page = await ctx.newPage();
const pages = ['/', '/replay/', '/how/', '/pult/', '/proof/', '/guide/'];
for (const p of pages) {
  await page.goto(base + p, { waitUntil: 'networkidle' });
  await page.waitForTimeout(2500);
  const name = p === '/' ? 'home' : p.replaceAll('/', '');
  const h = await page.evaluate(() => document.documentElement.scrollHeight);
  console.log(p, h);
  const steps = Math.min(Math.ceil(h / 900), 6);
  for (let i = 0; i < steps; i++) {
    await page.evaluate(y => window.scrollTo(0, y), i * 900);
    await page.waitForTimeout(1500);
    await page.screenshot({ path: `${out}${name}-${i}.png` });
  }
}
await page.setViewportSize({ width: 1440, height: 900 });
await page.goto('file:///Users/z1nex/mgimo_hackaton/osnovanie/projects/meganeuroseti-ref/outputs/trust-map.html', { waitUntil: 'load' });
await page.waitForTimeout(800);
console.log('trust', await page.evaluate(() => document.documentElement.scrollHeight));
await page.screenshot({ path: `${out}trust-0.png` });
await page.screenshot({ path: `${out}trust-full.png`, fullPage: true });
await browser.close();
