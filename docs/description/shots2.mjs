import { createRequire } from 'module';
const require = createRequire('/Users/z1nex/ya-games/studio/');
const { chromium } = require('playwright-core');
const out = new URL('./img/', import.meta.url).pathname;
const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
await page.goto('file:///Users/z1nex/mgimo_hackaton/osnovanie/projects/meganeuroseti-ref/outputs/trust-map.html');
await page.waitForTimeout(500);
await page.locator('.ref').first().click();
await page.waitForTimeout(500);
await page.screenshot({ path: out + 'trust-click.png' });
for (const t of ['product', 'communication', 'validation', 'audit']) {
  await page.locator(`button[data-t="${t}"]`).click();
  await page.waitForTimeout(400);
  const h = await page.evaluate(() => document.documentElement.scrollHeight);
  console.log(t, h);
  await page.screenshot({ path: out + `trust-${t}.png`, fullPage: true });
}
await browser.close();
