// Local-only browser regression: upload, crop, recolor, persist and clear.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = 'http://127.0.0.1:4183';
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [1365, 390]) {
      const context = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: 'reduce' });
      let designs = [];
      await context.route('**/*', route => {
        const request = route.request(), url = new URL(request.url());
        if (url.origin !== origin) return route.abort();
        if (!url.pathname.startsWith('/api/')) return route.continue();
        const send = data => route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
        if (url.pathname === '/api/auth/config') return send({ configured: true, supabase_url: 'https://test.supabase.co', supabase_anon_key: 'test-only' });
        if (url.pathname === '/api/auth/me') return send({ id: '11111111-1111-4111-8111-111111111111', email: 'designer@example.test', source: 'cookie' });
        if (url.pathname === '/api/designs') {
          if (request.method() === 'PUT') designs = request.postDataJSON().items;
          return send({ items: designs });
        }
        if (url.pathname === '/api/meta') return send({ accounts: [], agents: [], limits: {} });
        return send({ items: [] });
      });
      const page = await context.newPage(), errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.goto(origin + '/designs');
      await page.getByRole('link', { name: 'Use design' }).waitFor();
      const id = await page.getByLabel('Saved design').inputValue();
      const png = await page.evaluate(() => {
        const canvas = document.createElement('canvas'); canvas.width = canvas.height = 128;
        const ctx = canvas.getContext('2d'); ctx.fillStyle = '#ff00ff'; ctx.fillRect(48, 48, 32, 32);
        return canvas.toDataURL('image/png').split(',')[1];
      });
      await page.getByLabel('Upload design logo', { exact: true }).setInputFiles({ name: 'transparent.png', mimeType: 'image/png', buffer: Buffer.from(png, 'base64') });
      const dialog = page.getByRole('dialog');
      await dialog.getByRole('button', { name: 'White logo background', exact: true }).click();
      const pixel = await dialog.locator('.logo-crop-stage canvas').evaluate(c => Array.from(c.getContext('2d').getImageData(256, 80, 1, 1).data));
      assert.deepEqual(pixel, [255, 255, 255, 255]);
      await dialog.getByRole('button', { name: 'Use this crop' }).click();
      await page.getByRole('link', { name: 'Use design' }).waitFor();
      assert.equal(designs.find(d => d.id === id).logo_background, '#ffffff');
      const original = designs.find(d => d.id === id).logo_data_url;
      await page.getByLabel('Custom logo background', { exact: true }).fill('#123456');
      await page.getByRole('link', { name: 'Use design' }).waitFor();
      await page.reload();
      await page.getByRole('link', { name: 'Use design' }).waitFor();
      assert.equal(await page.getByLabel('Custom logo background', { exact: true }).inputValue(), '#123456');
      for (const name of ['Cover 01', 'Inside slide 02', 'CTA 03']) {
        await page.getByRole('button', { name, exact: true }).click();
        const circles = page.locator('.design-canvas[data-thumbnail="false"] .simple-slide-logo circle');
        assert(await circles.count() > 0);
        for (const circle of await circles.all()) assert.equal(await circle.getAttribute('fill'), '#123456');
      }
      await page.getByRole('button', { name: 'Transparent', exact: true }).scrollIntoViewIfNeeded();
      await page.screenshot({ path: `.work/logo-background-${width}.png`, fullPage: true });
      await page.getByRole('button', { name: 'Transparent', exact: true }).click();
      await page.getByRole('link', { name: 'Use design' }).waitFor();
      assert.equal(designs.find(d => d.id === id).logo_background, '');
      assert.equal(designs.find(d => d.id === id).logo_data_url, original);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      assert.deepEqual(errors, []);
      console.log(`PASS ${width}px: crop color, custom color, persistence, all slide previews, transparency reset, no overflow`);
      await context.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
