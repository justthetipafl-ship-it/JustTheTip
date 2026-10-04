/* JTT test harness — load the real page under jsdom against real data, in the repo.
 *
 *   node tests/harness.js            # sanity: boots every sport and reports
 *   require('./harness') from a test file for boot(), ok(), done()
 *
 * WHY THIS LIVES IN THE REPO
 * The previous suite — about 2,000 assertions — existed only in a working container and was lost
 * when that container reset. Nothing here is clever; the point is that it is committed, so it
 * cannot vanish, and CI can run it on every change to index.html.
 *
 * Data comes from tests/serve/, which tests/fetch_data.js populates from the live repo.
 */
const fs = require('fs');
const path = require('path');
const { JSDOM, ResourceLoader } = require('jsdom');   // ResourceLoader, NOT requestInterceptor

const ROOT = path.join(__dirname, 'serve');
const PAGE = path.join(__dirname, '..', 'index.html');

const localPath = u => path.join(ROOT, String(u).replace(/^https?:\/\/[^/]+/, '').split('?')[0]);
const readLocal = f => (fs.existsSync(f) ? fs.readFileSync(f, 'utf8') : null);

class LocalLoader extends ResourceLoader {
  fetch(url) {
    const body = readLocal(localPath(url));
    return body == null ? null : Promise.resolve(Buffer.from(body));
  }
}

const wait = ms => new Promise(r => setTimeout(r, ms));

let pass = 0, fail = 0;
function ok(name, cond, detail) {
  if (cond) { pass++; console.log('  ok   ' + name + (detail ? '  ' + detail : '')); }
  else { fail++; console.log('  FAIL ' + name + (detail ? '  ' + detail : '')); }
  return !!cond;
}
function done() {
  console.log('\n' + (fail ? 'FAILED ' : 'ALL GREEN ') + pass + '/' + (pass + fail));
  process.exit(fail ? 1 : 0);
}

/* Boot the page with one sport active and its data loaded.
   Returns { w, LAB, S } once the sport is ready; focus(i) waits for tier-3 too. */
async function boot(sport, opts) {
  opts = opts || {};
  if (!fs.existsSync(PAGE)) throw new Error('no index.html at ' + PAGE);
  if (!fs.existsSync(path.join(ROOT, sport === 'afl' ? 'AFL' : sport)))
    throw new Error('no data for ' + sport + ' — run: node tests/fetch_data.js ' + sport);

  const dom = new JSDOM(fs.readFileSync(PAGE, 'utf8'), {
    url: 'http://localhost/index.html', runScripts: 'dangerously',
    resources: new LocalLoader(), pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = u => {
        const t = readLocal(localPath(u));
        if (t == null) return Promise.resolve({ ok: false, status: 404,
          json: () => Promise.reject(new Error('404')), text: () => Promise.resolve('') });
        return Promise.resolve({ ok: true, status: 200,
          json: () => Promise.resolve(JSON.parse(t)), text: () => Promise.resolve(t) });
      };
      w.scrollTo = () => {};
    }
  });
  const w = dom.window;
  setTimeout(() => { try { w._authUnlock && w._authUnlock(); } catch (e) {} }, 50);
  await wait(opts.bootMs || 2500);

  const LAB = w.LAB;
  if (!LAB) throw new Error('the page did not boot — LAB is undefined');
  Object.keys(LAB.state.view.active).forEach(k => {
    if (!!LAB.state.view.active[k] !== (k === sport)) LAB.toggle(k);
  });
  const S = LAB.state.sports[sport];
  for (let i = 0; i < 120 && S.status !== 'ready'; i++) await wait(250);
  if (S.status !== 'ready') throw new Error(sport + ' never finished loading (status: ' + S.status + ')');

  const focus = async (i) => {
    LAB.focus(sport, i || 0);
    for (let n = 0; n < 200 && S.t3 !== 'ready'; n++) await wait(400);
    await wait(600);
  };
  return { w, LAB, S, focus, app: () => w.document.getElementById('app'), wait };
}

module.exports = { boot, ok, done, wait, ROOT, PAGE };

if (require.main === module) {
  (async () => {
    for (const sp of (process.argv[3] ? [process.argv[3]] : ['nfl', 'nhl', 'mlb', 'nbl', 'epl', 'afl'])) {
      try {
        const t = await boot(sp);
        const counts = {
          players: (t.S.idx.players || []).length,
          gamelogs: ((t.S.data.gamelogs) || []).length,
          odds: ((t.S.data.odds || {}).books || []).length + ((t.S.data.odds || {}).alt || []).length
        };
        ok(sp + ' boots with data', counts.players > 0 && counts.gamelogs > 0,
           '(' + counts.players + ' players, ' + counts.gamelogs + ' logs, ' + counts.odds + ' odds rows)');
      } catch (e) {
        ok(sp + ' boots with data', false, '(' + e.message.slice(0, 80) + ')');
      }
    }
    done();
  })();
}
