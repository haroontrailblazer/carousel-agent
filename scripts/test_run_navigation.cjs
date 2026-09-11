// Local integration tests only: no real credentials, generation or deletes.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.RUN_TEST_ORIGIN || 'http://127.0.0.1:4183';
const delayGate = () => { let release; const promise = new Promise(r => { release = r; }); return { promise, release }; };

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const viewport of [{ width: 1365, height: 900 }, { width: 390, height: 844 }]) {
      const context = await browser.newContext({ viewport, reducedMotion: 'reduce' });
      let stories = [1, 2, 3].map(i => ({ id: `story-${i}`, title: `Test story ${i}`,
        summary: 'An article used for local navigation checks.', source_url: `https://example.test/${i}`,
        created_at: new Date().toISOString() }));
      let designs = [], runs = [], startGate = delayGate();
      const deleteGate = delayGate(), pendingDeletes = [], errors = [], calls = [];
      await context.route('**/*', async route => {
        const req = route.request(), url = new URL(req.url());
        if (url.origin !== origin) return route.abort();
        if (!url.pathname.startsWith('/api/')) return route.continue();
        const p = url.pathname;
        calls.push([req.method(), p]);
        const send = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
        if (p === '/api/auth/config') return send({ configured: true, supabase_url: 'https://test.supabase.co', supabase_anon_key: 'test-only' });
        if (p === '/api/auth/me') return send({ id: '11111111-1111-4111-8111-111111111111', email: 'reader@example.test', source: 'cookie', is_admin: false });
        if (p === '/api/designs') {
          if (req.method() === 'PUT') designs = req.postDataJSON().items;
          return send({ items: designs });
        }
        if (p === '/api/meta') return send({ accounts: [], agents: [], limits: {} });
        if (p === '/api/pulse') return send({ running: runs.length, awaiting_review: 0, queued: stories.length });
        if (p === '/api/runs' && req.method() === 'POST') {
          const payload = req.postDataJSON();
          await startGate.promise;
          const run = { run_id: `run-${runs.length + 1}`, title: payload.news_id ? 'Carousel from story' : 'Carousel from prompt',
            status: 'running', phase: 'generate', is_live: true, review_round: 0, source: payload.source,
            created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
          runs.push(run);
          stories = stories.filter(s => s.id !== payload.news_id);
          return send(run, 202);
        }
        if (p === '/api/runs') return send({ items: runs });
        if (p === '/api/queue') return send({ items: stories, fetching: false });
        if (p.startsWith('/api/queue/') && req.method() === 'DELETE') {
          pendingDeletes.push(p);
          if (p.endsWith('story-2')) await deleteGate.promise;
          stories = stories.filter(s => !p.endsWith(s.id));
          return send({ result: 'deleted' });
        }
        return send({});
      });
      const page = await context.newPage();
      page.on('pageerror', e => errors.push(e.message));
      const card = i => page.getByRole('article', { name: `Test story ${i}`, exact: true });
      const navigate = async (label, pathname) => {
        if (viewport.width < 768) await page.getByRole('button', { name: 'Open menu', exact: true }).click();
        await page.locator(`nav[aria-label="Main navigation"]:visible a[href="${pathname}"]`).click();
        await page.waitForURL(`${origin}${pathname}`);
      };
      try {
        await page.goto(`${origin}/newsroom`);
        await card(1).getByRole('button', { name: 'Create carousel' }).click();
        await page.getByRole('dialog').getByRole('radio').first().check();
        await page.getByRole('dialog').getByRole('button', { name: 'Create carousel' }).click();
        await page.getByRole('dialog').waitFor({ state: 'hidden' });
        await card(1).getByRole('button', { name: 'Creating…' }).waitFor();
        assert(await card(1).getByRole('button', { name: 'Delete', exact: true }).isDisabled());
        assert(await card(2).getByRole('button', { name: 'Delete', exact: true }).isEnabled());
        await card(2).getByRole('button', { name: 'Delete', exact: true }).click();
        await card(2).getByRole('button', { name: 'Deleting…' }).waitFor();
        assert(await card(3).getByRole('button', { name: 'Delete', exact: true }).isEnabled());
        await card(3).getByRole('button', { name: 'Delete', exact: true }).click();
        await card(3).waitFor({ state: 'hidden' });
        await navigate('Tasks', '/tasks');
        await navigate('Newsroom', '/newsroom');
        await card(1).getByRole('button', { name: 'Creating…' }).waitFor();
        await card(2).getByRole('button', { name: 'Deleting…' }).waitFor();
        deleteGate.release();
        await card(2).waitFor({ state: 'hidden' });
        startGate.release();
        await card(1).waitFor({ state: 'hidden' });
        await navigate('Tasks', '/tasks');
        await page.getByRole('heading', { name: 'Carousel from story', exact: true }).waitFor();
        await page.reload();
        await page.getByRole('heading', { name: 'Carousel from story', exact: true }).waitFor();
        assert.equal(runs.length, 1);
        assert.equal(pendingDeletes.length, 2);

        // Leaving the composer before start acceptance must not redirect back.
        startGate = delayGate();
        await page.goto(`${origin}/new?design=studio-light`);
        await page.getByRole('textbox', { name: 'Carousel prompt', exact: true }).fill('A new research topic');
        await page.getByRole('textbox', { name: 'Carousel prompt', exact: true }).press('Enter');
        await page.locator('.studio-launch-handoff').waitFor();
        await navigate('Newsroom', '/newsroom');
        startGate.release();
        await page.getByText('Your carousel has started', { exact: true }).waitFor();
        assert.equal(new URL(page.url()).pathname, '/newsroom');
        assert(!calls.some(([method, path]) => method === 'POST' && path.endsWith('/cancel')));
        assert.deepEqual(errors, []);
        console.log(`PASS ${viewport.width}px: independent create/delete, pending state after navigation, refresh retains task, no forced redirect or cancellation`);
      } catch (error) {
        console.error('Browser location:', page.url(), 'Errors:', errors);
        console.error((await page.locator('body').innerText()).slice(-4500));
        throw error;
      } finally {
        startGate.release(); deleteGate.release();
        await context.close();
      }
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
