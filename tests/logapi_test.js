/* THE BET LOGGER'S NETWORK LAYER — tools/log.html
 *
 *   node tests/logapi_test.js
 *
 * This file had no test at all, and it is the one page that WRITES. Every other screen in the
 * suite reads data and draws it; this one takes a season of settled bets, merges a batch into
 * it and pushes the result back over the GitHub contents API. A mistake here does not render
 * badly, it loses history.
 *
 * What went wrong, on 2026-11-04:
 *
 *   api() called r.json() on the response without looking at it first. GitHub answers a repeat
 *   GET with 304 Not Modified and an EMPTY BODY once the browser holds an ETag for the file -
 *   and this file is read before every save, so the second save of a session is the one that
 *   breaks. r.json() on an empty body rejects with
 *
 *       Failed to execute 'json' on 'Response': Unexpected end of JSON input
 *
 *   which names neither the file nor the status, so there is nothing in it to act on. That is
 *   the message that appeared on screen.
 *
 * The fix is two things, and this checks both: ask for no-store so the 304 cannot happen, and
 * parse the body as text so that if it IS empty - 304, 204, a proxy cutting it short - the
 * caller gets back a status instead of a rejected promise.
 *
 * The third check is the one that matters most. A 200 means the file exists; an empty 200 means
 * we could not read its sha. Writing without the sha asks GitHub to CREATE a file that is
 * already there, which fails - but only AFTER the merge step has thrown away what was in it.
 * The save has to refuse, not try.
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

let pass = 0, fail = 0;
function ok(what, cond, extra) {
  if (cond) { pass++; console.log('  ok   ' + what); }
  else { fail++; console.log('  FAIL ' + what + (extra ? ' ' + extra : '')); }
}

/* Build a Response-alike. The real failure was a body that is empty while the status says
   something useful, so text() returning '' is the whole point of this helper. */
function res(status, body) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status: status,
    text: () => Promise.resolve(body === undefined ? '' : body),
    json: () => (body ? Promise.resolve(JSON.parse(body))
                      : Promise.reject(new SyntaxError(
                          "Failed to execute 'json' on 'Response': Unexpected end of JSON input"))),
  });
}

(async () => {
  const file = path.join(__dirname, '..', 'tools', 'log.html');
  const html = fs.readFileSync(file, 'utf8');

  /* THE STATIC READS first - they do not need the page running, and they are the clearest
     statement of the rule. A r.json() anywhere in this file is the bug coming back. */
  const src = html.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  ok('no response is parsed with r.json() any more',
     src.indexOf('r.json()') < 0, '(' + (src.match(/r\.json\(\)/g) || []).length + ' left)');
  ok('the GitHub call asks for no-store, as the data fetches already did',
     /function api\([\s\S]*?opts\.cache = 'no-store'[\s\S]*?\n\}/.test(src));
  ok('and reads the body as text', /function api\([\s\S]*?r\.text\(\)[\s\S]*?\n\}/.test(src));

  /* NOW RUN IT. The page is loaded with fetch stubbed, so api() is exercised for real rather
     than read for shape. Everything it does on load - sport data, config scripts - is allowed
     to fail quietly; this test is only about the network layer. */
  const calls = [];
  let reply = () => res(200, '{}');
  const dom = new JSDOM(html, {
    runScripts: 'dangerously',
    url: 'https://justthetipaus.com/tools/log.html',
    beforeParse(w) {
      w.fetch = (url, opts) => { calls.push({ url: String(url), opts: opts || {} }); return reply(url, opts); };
      w.TextEncoder = TextEncoder; w.TextDecoder = TextDecoder;
      w.alert = () => {};
    },
  });
  const w = dom.window;
  await new Promise((r) => setTimeout(r, 400));

  const L = w.JTT_ADMIN;
  if (!L || typeof L.api !== 'function') {
    ok('the page exposes its network layer to drive', false, '(page did not finish loading)');
    return done();
  }
  ok('the page exposes its network layer to drive', true);

  /* A NORMAL READ still parses. */
  reply = () => res(200, JSON.stringify({ sha: 'abc', content: '' }));
  let r1 = await L.api('/contents/data/results.json');
  ok('a 200 with a body parses as before',
     r1.ok === true && r1.status === 200 && r1.body && r1.body.sha === 'abc');

  /* THE BUG: an empty body must come back as a status, not a rejection. */
  reply = () => res(304);
  let threw = null, r2 = null;
  try { r2 = await L.api('/contents/data/results.json'); } catch (e) { threw = e; }
  ok('a 304 with an empty body no longer rejects the chain',
     !threw, threw ? '(' + threw.message + ')' : '');
  ok('and it comes back with the status intact so the message can name it',
     !!r2 && r2.status === 304 && r2.body === null);

  /* Same for a 204, and for a body that is present but not JSON - a proxy error page, say. */
  reply = () => res(204);
  const r3 = await L.api('/x');
  ok('a 204 is handled the same way', r3.status === 204 && r3.body === null);
  reply = () => res(502, '<html>Bad Gateway</html>');
  const r4 = await L.api('/x');
  ok('so is a non-JSON body', r4.status === 502 && r4.body === null);

  /* A 404 still reads as "file is not there yet", which is a legitimate first save. */
  reply = () => res(404, JSON.stringify({ message: 'Not Found' }));
  const r5 = await L.api('/x');
  ok('a 404 keeps its body, so a first save can still tell it apart',
     r5.status === 404 && r5.body && r5.body.message === 'Not Found');

  /* NO CACHING. This is the half of the fix that stops the 304 ever being produced. */
  calls.length = 0;
  reply = () => res(200, '{}');
  await L.api('/x');
  ok('every GitHub call is sent with no-store (' + (calls[0] && calls[0].opts.cache) + ')',
     calls.length === 1 && calls[0].opts.cache === 'no-store');
  await L.api('/x', { method: 'PUT', body: '{}' });
  ok('including the write', calls[1] && calls[1].opts.cache === 'no-store');

  /* THE ONE THAT PROTECTS THE DATA. An unreadable 200 must stop the save, not push a file
     built from an empty merge. Drive the real save button with a batch in hand. */
  w.document.getElementById('tok').value = 'x-token-not-real';
  const saveBtn = w.document.getElementById('save');
  ok('the save button is there to drive', !!saveBtn);
  if (saveBtn) {
    L.setBatch([{ id: 'z1', date: '2026-11-04', sport: 'NBA', type: 'Prop',
                 bet: 'test', units: 1, odds: 2, result: 'pending' }]);
    calls.length = 0;
    reply = () => res(200, '');                 // the file is there, but we cannot read it
    saveBtn.disabled = false;
    saveBtn.onclick();
    await new Promise((r) => setTimeout(r, 300));
    const wrote = calls.filter((c) => (c.opts.method || 'GET') !== 'GET');
    ok('an unreadable 200 does not write anything (' + wrote.length + ' writes)',
       wrote.length === 0);
    const shown = (w.document.getElementById('msg') || {}).textContent || '';
    ok('and says so, naming the file (' + JSON.stringify(shown.slice(0, 60)) + ')',
       /results\.json/.test(shown) && /empty/i.test(shown));
    ok('the old cryptic message is gone', shown.indexOf('Unexpected end of JSON input') < 0);
  }

  /* AND THE SAVE STILL SAVES. Every check above is about refusing to write; this one is the
     other half - a readable file must still be merged into and pushed, or the fix has traded
     one broken save for another. */
  if (saveBtn) {
    const prior = { unitSize: 1, updated: '2026-11-03T00:00:00Z',
                    bets: [{ id: 'old1', date: '2026-11-01', sport: 'NBA', type: 'Prop',
                             bet: 'kept', units: 1, odds: 2, result: 'win', returnedUnits: 2 }] };
    const content = Buffer.from(JSON.stringify(prior), 'utf8').toString('base64');
    L.setBatch([{ id: 'new1', date: '2026-11-04', sport: 'NBA', type: 'Prop',
                  bet: 'added', units: 1, odds: 2, result: 'pending' }]);
    calls.length = 0;
    reply = (url, opts) => ((opts && opts.method === 'PUT')
      ? res(200, JSON.stringify({ content: { sha: 'new' } }))
      : res(200, JSON.stringify({ sha: 'abc', content: content })));
    saveBtn.disabled = false;
    saveBtn.onclick();
    await new Promise((r) => setTimeout(r, 400));
    const put = calls.filter((c) => (c.opts.method || 'GET') === 'PUT')[0];
    ok('a readable file is still written back', !!put);
    if (put) {
      const sent = JSON.parse(put.opts.body);
      ok('with the sha it just read, so nothing is overwritten blind', sent.sha === 'abc');
      const merged = JSON.parse(Buffer.from(sent.content, 'base64').toString('utf8'));
      const ids = (merged.bets || []).map((b) => b.id);
      ok('the bet already on file survives the merge (' + ids.join(', ') + ')',
         ids.indexOf('old1') >= 0);
      ok('and the new one is added', ids.indexOf('new1') >= 0);
    }
  }

  done();

  function done() {
    console.log('\n' + (fail ? 'FAILED ' : 'ALL GREEN ') + pass + '/' + (pass + fail));
    process.exit(fail ? 1 : 0);
  }
})();
