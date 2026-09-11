// Browser checks against a local preview. All API calls are mocked.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FONT_TEST_ORIGIN || 'http://127.0.0.1:4183';

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
        const manifest = JSON.parse(fs.readFileSync('frontend/public/fonts/carousel/manifest.json', 'utf8'));
        const expected = { cover: 'sans', inside: 'serif', cta: 'condensed' };
        const spacing = { cover: [4, 8, 140], inside: [-1, 12, 130], cta: [2, 6, 160] };
        const spacingLabels = ['Letter spacing', 'Word spacing', 'Line height'];
        const setSpacing = async values => {
          for (const [index, label] of spacingLabels.entries()) {
            const slider = page.getByRole('slider', { name: label, exact: true });
            await slider.press("Home");
            for (let step = Number(await slider.getAttribute("min")); step < values[index]; step++) await slider.press("ArrowRight");
          }
        };
        const surfaceName = { cover: 'Cover 01', inside: 'Inside slide 02', cta: 'CTA 03' };
        for (const [surface, family] of Object.entries(expected)) {
          await page.getByRole('button', { name: surfaceName[surface], exact: true }).click();
          await page.getByRole('button', { name: 'Text', exact: true }).click();
          // Exercise every font on every surface, then persist a distinct choice.
          for (const choice of [...Object.keys(manifest), family]) {
            await page.getByLabel('Font', { exact: true }).selectOption(choice);
            const loaded = await page.evaluate(async face => {
              const heading = await document.fonts.load(`700 76px "${face}"`);
              const body = await document.fonts.load(`400 36px "${face}"`);
              return [...heading, ...body].map(font => font.status);
            }, manifest[choice].family);
            assert.deepEqual(loaded, ['loaded', 'loaded']);
            const text = page.locator(`.design-canvas[data-thumbnail="false"][data-surface="${surface}"] .simple-slide-text`).first();
            const style = await text.evaluate(el => {
              const head = getComputedStyle(el), body = el.querySelector('p');
              return { family: head.fontFamily.replaceAll('"', ''), weight: head.fontWeight,
                body: body && { family: getComputedStyle(body).fontFamily.replaceAll('"', ''), weight: getComputedStyle(body).fontWeight } };
            });
            assert.equal(style.family, manifest[choice].family);
            assert.equal(style.weight, '700');
            if (surface !== 'cover') assert.deepEqual(style.body, { family: manifest[choice].family, weight: '400' });
          }
          await setSpacing(spacing[surface]);
          await page.getByRole('link', { name: 'Use design' }).waitFor();
          const saved = designs.find(design => design.id === selected)[surface];
          assert.deepEqual([saved.letter_spacing, saved.word_spacing, saved.line_height], spacing[surface]);
          assert.equal(saved.font_family, family);
          const metrics = await page.locator(`.design-canvas[data-thumbnail="false"][data-surface="${surface}"]`).first().evaluate(canvas => {
            const text = canvas.querySelector('.simple-slide-text'), style = getComputedStyle(text);
            const scale = canvas.clientWidth / 1080;
            return [parseFloat(style.letterSpacing) / scale, parseFloat(style.wordSpacing) / scale, parseFloat(style.lineHeight) / parseFloat(style.fontSize) * 100];
          });
          metrics.forEach((value, i) => assert(Math.abs(value - spacing[surface][i]) < 0.1));

          await page.screenshot({ path: `.work/fonts-${surface}-${viewport.width}.png`, fullPage: true });
        }
        await page.reload();
        await page.getByRole('link', { name: 'Use design' }).waitFor();
        for (const [surface, family] of Object.entries(expected)) {
          await page.getByRole('button', { name: surfaceName[surface], exact: true }).click();
          await page.getByRole('button', { name: 'Text', exact: true }).click();
          assert.equal(await page.getByLabel('Font', { exact: true }).inputValue(), family);
          for (const [i, label] of spacingLabels.entries()) assert.equal(await page.getByRole('slider', { name: label, exact: true }).inputValue(), String(spacing[surface][i]));
        }
        await page.getByRole('button', { name: 'Reset spacing' }).click();
        for (const [i, label] of spacingLabels.entries()) assert.equal(await page.getByRole('slider', { name: label, exact: true }).inputValue(), String([0, 0, 120][i]));
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        assert.deepEqual(errors, []);
        console.log(`PASS ${viewport.width}px: all bundled font faces and weights loaded, cover/body/CTA font and spacing selections persisted, spacing reset, no horizontal overflow`);
      } catch (error) {
        console.error(page.url(), errors, (await page.locator('body').innerText()).slice(-3000));
        throw error;
      } finally { await context.close(); }
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
