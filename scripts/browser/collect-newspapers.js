// Newspaper collector for Chronicling America, run in a browser console.
//
// The Library of Congress search API sits behind a bot check that scripts cannot pass,
// so this part of the collection runs inside a normal browser tab on www.loc.gov.
//
//   1. Open any loc.gov search in JSON, for example
//      https://www.loc.gov/collections/chronicling-america/?q=titanic&fo=json&c=1&at=pagination
//      and wait until the page shows JSON (tick the check box if one appears).
//   2. In the console, set the events:  window.NEWS_EVENTS = <the "newspaper_events" array from data/reference/events.json>
//   3. Paste this file and press Enter. Progress is in window.NEWS.log.
//   4. If the bot check returns (NEWS.blocked is true), reload the tab, wait for JSON, and paste again.
//      Finished search pages, candidates and papers are kept in browser storage, so it carries on.
//   5. At the end the browser downloads newspapers.json. scripts/import_newspapers.py
//      turns it into data/raw/newspapers/<event>.csv.
//
// Phase 1 searches every event. Phase 2 reads each paper's pages in date order from
// tile.loc.gov (no bot check) and keeps the first page whose OCR text reports the event.

(() => {
if (window.NEWS && window.NEWS.running) return 'already running';
const EVENTS = window.NEWS_EVENTS;
if (!Array.isArray(EVENTS)) return 'set window.NEWS_EVENTS first';

let saved = null; try { saved = JSON.parse(localStorage.getItem('NEWS') || 'null'); } catch (e) {}
const F = window.NEWS = saved || {rows: {}, done: {}};
F.cand = {}; for (const k of Object.keys(sessionStorage)) if (k.startsWith('FC|')) { try { F.cand[k.slice(3)] = JSON.parse(sessionStorage.getItem(k)); } catch (e) {} }
F.log = F.log || []; F.failed = F.failed || []; F.errors = 0; F.running = true; F.blocked = false;

const say = m => { F.log.push(new Date().toISOString().slice(11, 19) + ' ' + m); if (F.log.length > 300) F.log.shift(); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const save = () => { try { localStorage.setItem('NEWS', JSON.stringify({rows: F.rows, done: F.done, failed: F.failed})); } catch (e) { say('save failed'); } };

// one request at a time per host, spaced to stay under the Library's rate limits
const slots = {www: {next: 0, gap: 4000}, tile: {next: 0, gap: 450}};
async function get(url, host) {
  for (let a = 0; a < 7; a++) {
    const s = slots[host]; const at = Math.max(Date.now(), s.next); s.next = at + s.gap;
    if (at > Date.now()) await sleep(at - Date.now());
    const ac = new AbortController(); const to = setTimeout(() => ac.abort(), 120000);
    try {
      const r = await fetch(url, {signal: ac.signal});
      clearTimeout(to);
      if (r.status === 200) { try { return JSON.parse(await r.text()); } catch (e) { F.errors++; await sleep(20000); continue; } }
      if (r.status === 404) return null;
      if (r.status === 403 && host === 'www') { say('BLOCKED: reload the tab and paste again'); F.blocked = true; save(); throw new Error('blocked'); }
      say(`HTTP ${r.status} ${host}`); F.errors++;
      await sleep(r.status === 429 ? 120000 * (a + 1) : 15000 * (a + 1));
    } catch (e) { clearTimeout(to); if (e.message === 'blocked') throw e; say('ERR ' + e.name + ' ' + host); F.errors++; await sleep(20000 * (a + 1)); }
  }
  return null;
}

// Phase 1: search
const first = v => Array.isArray(v) ? (v[0] ?? '') : (v ?? '');
const searchURL = (q, d1, d2, sp, fp) => 'https://www.loc.gov/collections/chronicling-america/?' + new URLSearchParams({
  q, dates: `${d1}/${d2}`, dl: 'page', fo: 'json', c: 160, sp, at: 'results,pagination', ...(fp ? {front_pages_only: 'true'} : {})});
const toHits = d => (d.results || []).map(r => {
  const img = (r.image_url || []).filter(x => x.includes('image-services/iiif/'));
  return {date: first(r.date).slice(0, 10), lccn: first(r.number_lccn), paper: first(r.partof_title), city: first(r.location_city),
    county: first(r.location_county), state: first(r.location_state), frequency: first(r.publication_frequency),
    page: String(first(r.number_page)).replace(/^0+/, '') || '1', url: r.url || r.id || '', iiif: img.length ? img[0].split('/full/')[0] : ''};
});
// each finished result page is kept for the session, so a reload does not repeat it
async function searchPage(q, d1, d2, sp, fp) {
  const k = 'FSP|' + [q, d1, d2, sp, fp ? 1 : 0].join('|');
  try { const c = sessionStorage.getItem(k); if (c) return JSON.parse(c); } catch (e) {}
  const d = await get(searchURL(q, d1, d2, sp, fp), 'www');
  if (!d) return null;
  const o = {pagination: d.pagination, hits: toHits(d)};
  try { sessionStorage.setItem(k, JSON.stringify(o)); } catch (e) {}
  return o;
}
async function search(q, d1, d2, fp) {
  const d = await searchPage(q, d1, d2, 1, fp);
  if (!d) { say(`search failed '${q}'`); return []; }
  const total = (d.pagination || {}).total || 1;
  say(`'${q}': ${(d.pagination || {}).of} pages, ${total} requests`);
  let hits = d.hits;
  const rest = []; for (let sp = 2; sp <= total; sp++) rest.push(sp);
  await Promise.all([0, 1].map(async () => { while (rest.length) {
    const sp = rest.shift(); let dd = await searchPage(q, d1, d2, sp, fp);
    if (!dd) { await sleep(20000); dd = await searchPage(q, d1, d2, sp, fp); }
    if (dd) hits = hits.concat(dd.hits); else { say(`page ${sp} of '${q}' failed twice`); F.failed.push([q, d1, d2, sp]); }
  } }));
  return hits;
}

// Phase 2: read
const segOf = h => { if (!h.iiif) return null; const sid = decodeURIComponent(h.iiif.split('image-services/iiif/')[1]); return '/service/' + sid.replace('service:', '').split(':').join('/') + '.xml'; };
const normText = s => s.toLowerCase().replace(/ſ/g, 's').replace(/([a-z])-\s+([a-z])/g, '$1$2').replace(/\s+/g, ' ');
async function pageText(seg) {
  const j = await get('https://tile.loc.gov/text-services/word-coordinates-service?' + new URLSearchParams({segment: seg, format: 'alto_xml', full_text: 1}), 'tile');
  const v = j && Object.values(j)[0]; return v && v.full_text != null ? normText(String(v.full_text)) : null;
}
// the first match that is not a denial, a rumour or a different person (see reject_near in events.json)
function findMatch(text, rx, reject) {
  const g = new RegExp(rx.source, 'g'); let m;
  while ((m = g.exec(text))) {
    if (!reject) return m;
    const ctx = text.slice(Math.max(0, m.index - 300), m.index + m[0].length + 300);
    if (!reject.test(ctx) && !(/rumou?r/.test(ctx) && !/confirm/.test(ctx))) return m;
    if (g.lastIndex === m.index) g.lastIndex++;
  }
  return null;
}
const quote = (text, m) => { const a = Math.max(0, m.index - 220), b = Math.min(text.length, m.index + m[0].length + 220); return (a ? '…' : '') + text.slice(a, b).trim() + (b < text.length ? '…' : ''); };

async function readPaper(ev, rx, reject, lccn, pages) {
  pages = pages.filter(p => p.date >= ev.first_valid).sort((a, b) => a.date < b.date ? -1 : a.date > b.date ? 1 : (+a.page || 99) - (+b.page || 99));
  const h0 = pages[0];
  const row = {lccn, paper: h0.paper, city: h0.city, county: h0.county, state: h0.state, frequency: h0.frequency, event: ev.id,
    first_hit_date: h0.date, candidates: pages.length, verified: 'no', date: '', page: '', url: '', iiif: '', match: '', snippet: '', box_pct: '', pages_read: 0};
  // Searches whose terms only appear once the event has happened (trust_search) count the Library's own match.
  // One page is still read for a quotation; a paper whose quote cannot be found is kept and flagged 'search'.
  const trusted = ev.trust_search === 'all' || (ev.trust_search === 'after-first-day' && h0.date > ev.first_valid);
  if (trusted) {
    Object.assign(row, {verified: 'search', date: h0.date, page: h0.page, url: h0.url, iiif: h0.iiif});
    const seg = segOf(h0); if (!seg) return row;
    const text = await pageText(seg); row.pages_read = 1;
    const m = text && rx.exec(text);
    if (m) Object.assign(row, {verified: 'yes', match: m[0].slice(0, 120), snippet: quote(text, m)});
    return row;
  }
  const dates = new Set(); let anyText = false;
  for (const h of pages.slice(0, 24)) {
    dates.add(h.date); if (dates.size > 12) break;
    const seg = segOf(h); if (!seg) continue;
    const text = await pageText(seg); row.pages_read++;
    if (!text) continue; anyText = true;
    const m = findMatch(text, rx, reject);
    if (m) { Object.assign(row, {verified: 'yes', date: h.date, page: h.page, url: h.url, iiif: h.iiif, match: m[0].slice(0, 120), snippet: quote(text, m)}); break; }
  }
  if (!anyText) Object.assign(row, {verified: 'no_text', date: h0.date, page: h0.page, url: h0.url, iiif: h0.iiif});
  return row;
}

window.NEWS_DOWNLOAD = () => {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify({rows: F.rows, failed: F.failed})], {type: 'application/json'}));
  a.download = 'newspapers.json'; document.body.appendChild(a); a.click(); return 'downloaded';
};

(async () => {
  for (const ev of EVENTS) {
    if (F.done[ev.id] || F.cand[ev.id]) continue;
    say('SEARCH ' + ev.id);
    const hits = {};
    for (const q of ev.queries) for (const h of await search(q, ev.first_valid, ev.window_end, ev.front_pages_only))
      if (h.lccn && h.date && !hits[h.url]) hits[h.url] = h;
    F.cand[ev.id] = Object.values(hits);
    try { sessionStorage.setItem('FC|' + ev.id, JSON.stringify(F.cand[ev.id])); } catch (e) { say('could not keep candidates; a reload will search again'); }
    for (const k of Object.keys(sessionStorage)) if (k.startsWith('FSP|')) sessionStorage.removeItem(k);
    say(`${ev.id}: ${F.cand[ev.id].length} candidate pages, ${new Set(F.cand[ev.id].map(h => h.lccn)).size} papers`);
  }
  say('SEARCHES DONE');
  for (const ev of EVENTS) {
    if (F.done[ev.id]) continue;
    say('READ ' + ev.id);
    const rx = new RegExp(ev.verify), reject = ev.reject_near ? new RegExp(ev.reject_near) : null;
    const by = {}; for (const h of F.cand[ev.id]) (by[h.lccn] = by[h.lccn] || []).push(h);
    const rows = F.rows[ev.id] = F.rows[ev.id] || [];
    const seen = new Set(rows.map(r => r.lccn));
    const todo = Object.entries(by).sort().filter(([l, p]) => !seen.has(l) && p.some(h => h.date >= ev.first_valid));
    const total = todo.length + rows.length;
    await Promise.all([0, 1, 2].map(async () => { while (todo.length) {
      const [l, p] = todo.shift(); rows.push(await readPaper(ev, rx, reject, l, p));
      if (rows.length % 10 === 0) { save(); say(`${ev.id}: ${rows.length}/${total} papers, ${rows.filter(r => r.verified === 'yes').length} confirmed`); }
    } }));
    F.done[ev.id] = true; save(); sessionStorage.removeItem('FC|' + ev.id);
    say(`${ev.id} DONE: ${rows.length} papers, ${rows.filter(r => r.verified === 'yes').length} confirmed`);
  }
  F.running = false; say('ALL DONE');
  window.NEWS_DOWNLOAD();
})().catch(e => { say('FATAL ' + e.message); F.running = false; });
return 'started';
})()
