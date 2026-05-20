const { chromium } = require('@playwright/test');

(async () => {
  const executablePath = process.argv[2];
  const browser = await chromium.launch({ headless: true, executablePath });

  // Zoom into the bottom nav area
  async function shotCrop(name, url, theme, w, h, cropY) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: theme === 'light' ? 'light' : 'dark' });
    const page = await ctx.newPage();
    await page.addInitScript((t) => localStorage.setItem('theme', t), theme);
    await page.goto(url, { waitUntil: 'networkidle', timeout: 15000 });
    await page.waitForTimeout(800);
    await page.screenshot({ path: `/tmp/${name}.png`, clip: { x: 0, y: cropY, width: w, height: h - cropY } });
    await ctx.close();
    console.log(`saved /tmp/${name}.png`);
  }

  await shotCrop('nav_dark', 'http://localhost:3456/', 'dark', 375, 812, 700);
  await shotCrop('nav_light', 'http://localhost:3456/', 'light', 375, 812, 700);
  await shotCrop('nav_more', 'http://localhost:3456/more', 'dark', 375, 812, 700);

  // Also grab the sidebar brand area
  const ctx2 = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'dark' });
  const page2 = await ctx2.newPage();
  await page2.addInitScript(() => localStorage.setItem('theme', 'dark'));
  await page2.goto('http://localhost:3456/', { waitUntil: 'networkidle', timeout: 15000 });
  await page2.waitForTimeout(800);
  await page2.screenshot({ path: '/tmp/sidebar_brand.png', clip: { x: 0, y: 0, width: 240, height: 900 } });
  await ctx2.close();
  console.log('saved /tmp/sidebar_brand.png');

  await browser.close();
  console.log('done');
})();
