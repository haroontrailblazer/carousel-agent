// Browser checks against a local preview. All API calls are mocked.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.SHADOW_TEST_ORIGIN || 'http://127.0.0.1:4183';

(async () => {
  const browser = await chromium.launch({ headless: true });
  fs.mkdirSync('.work', { recursive: true });
  try {
    for (const viewport of [{ width: 1365, height: 900 }, { width: 390, height: 844 }]) {
      const context = await browser.newContext({ viewport, reducedMotion: 'reduce' });
      let designs = [];
      await context.route('**/*', async route => {
        const req = route.request(), url = new URL(req.url());
        if (url.origin !== origin) return route.abort();
        if (!url.pathname.startsWith('/api/')) return route.continue();
        const send = data => route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
        if (url.pathname === '/api/auth/config') return send({ configured: true, supabase_url: 'https://test.supabase.co', supabase_anon_key: 'test-only' });
        if (url.pathname === '/api/auth/me') return send({ id: '11111111-1111-4111-8111-111111111111', email: 'designer@example.test', source: 'cookie', is_admin: false });
        if (url.pathname === '/api/designs') {
          if (req.method() === 'PUT') designs = req.postDataJSON().items;
          return send({ items: designs });
        }
        if (url.pathname === '/api/runs' || url.pathname === '/api/queue') return send({ items: [] });
        if (url.pathname === '/api/meta') return send({ accounts: [], agents: [], limits: {} });
        return send({});
      });
      const page = await context.newPage(), errors = [];
      page.on('pageerror', error => errors.push(error.message));
      try {
        await page.goto(origin + '/designs');
        await page.getByRole('link', { name: 'Use design' }).waitFor();
        const selected = await page.getByLabel('Saved design').inputValue();
        await page.getByRole('button', { name: /Black shadow/ }).click();
        const mask = page.locator('.simple-slide-shadow').first();
        const before = await mask.evaluate(canvas => canvas.toDataURL());
        const setSlider = async (name, value, max) => {
          const slider = page.getByRole('slider', { name, exact: true });
          await slider.press('End');
          for (let i = max; i > value; i--) await slider.press('ArrowLeft');
          assert.equal(await slider.inputValue(), String(value));
        };
        await setSlider('Shadow height', 64, 72);
        await setSlider('Blur', 80, 100);
        await setSlider('Edge curve', 70, 100);
        assert.notEqual(await mask.evaluate(canvas => canvas.toDataURL()), before);
        await page.getByRole('link', { name: 'Use design' }).waitFor();
        const saved = designs.find(design => design.id === selected).cover;
        assert.deepEqual([saved.shadow_height, saved.shadow_softness, saved.shadow_curve], [64, 80, 70]);
        await page.screenshot({ path: `.work/shadow-controls-${viewport.width}.png`, fullPage: true });
        await page.reload();
        await page.getByRole('link', { name: 'Use design' }).waitFor();
        await page.getByRole('button', { name: /Black shadow/ }).click();
        for (const [name, value] of [['Shadow height', 64], ['Blur', 80], ['Edge curve', 70]])
          assert.equal(await page.getByRole('slider', { name, exact: true }).inputValue(), String(value));
        await page.getByRole('button', { name: 'Reset shadow' }).click();
        for (const [name, value] of [['Shadow height', 52], ['Blur', 65], ['Edge curve', 0]])
          assert.equal(await page.getByRole('slider', { name, exact: true }).inputValue(), String(value));
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        assert.deepEqual(errors, []);
        console.log(`PASS ${viewport.width}px: live shadow changes, saved control values, reload, reset, no horizontal overflow`);
      } catch (error) {
        console.error(page.url(), errors, (await page.locator('body').innerText()).slice(-3000));
        throw error;
      } finally { await context.close(); }
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
