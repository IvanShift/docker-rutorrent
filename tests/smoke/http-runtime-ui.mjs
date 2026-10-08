// Exercise the real WebUI iframe transport and multipart upload result channel.
// Usage: node http-runtime-ui.mjs <devtools-port> <base-url> <tag> <fixtures.json>
// Loads the UI, records every HTTP response, then submits the real add-torrent
// form (target=uploadfrm iframe): one valid torrent, one invalid file and two
// batches. Valid torrents come from tests/smoke/torrent-fixture.php through the
// caller's manifest, which also records their names and info hashes.
import { readFileSync } from 'node:fs';

const [port, base, tag, manifest] = process.argv.slice(2);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fixtures = JSON.parse(readFileSync(manifest, 'utf8'));
const torrent = (fixture) => readFileSync(fixture.file);

const version = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
const ws = new WebSocket(version.webSocketDebuggerUrl);
await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
let nextId = 1;
const pending = new Map();
const responses = [];
const consoleLines = [];
let sessionId = null;
ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); return; }
  if (msg.method === 'Network.responseReceived') {
    const r = msg.params.response;
    const h = Object.fromEntries(Object.entries(r.headers).map(([k, v]) => [k.toLowerCase(), v]));
    responses.push({ url: r.url.replace(base, ''), status: r.status, type: msg.params.type, xfo: h['x-frame-options'] || null });
  } else if (msg.method === 'Runtime.consoleAPICalled') {
    consoleLines.push(`console.${msg.params.type}: ` + msg.params.args.map((a) => a.value ?? a.description ?? '').join(' '));
  } else if (msg.method === 'Log.entryAdded') {
    consoleLines.push(`log.${msg.params.entry.level}: ${msg.params.entry.text}`);
  } else if (msg.method === 'Runtime.exceptionThrown') {
    consoleLines.push(`exception: ${msg.params.exceptionDetails.exception?.description ?? msg.params.exceptionDetails.text}`);
  }
};
function send(method, params = {}, sid = sessionId) {
  const id = nextId++;
  const payload = { id, method, params };
  if (sid) payload.sessionId = sid;
  ws.send(JSON.stringify(payload));
  return new Promise((r) => pending.set(id, r));
}
async function evaluate(expression) {
  const res = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  if (res.result?.exceptionDetails) return { error: res.result.exceptionDetails.text };
  return res.result?.result?.value;
}

const { result: { targetId } } = await send('Target.createTarget', { url: 'about:blank' }, null);
sessionId = (await send('Target.attachToTarget', { targetId, flatten: true }, null)).result.sessionId;
for (const d of ['Page', 'Runtime', 'Log', 'Network']) await send(`${d}.enable`);
await send('Page.navigate', { url: base + '/' });
let ready = false;
for (let i = 0; i < 60 && !ready; i++) {
  await sleep(500);
  ready = await evaluate(`typeof theWebUI === 'object' && !!document.getElementById('torrent_file') && !!document.querySelector('iframe[name=uploadfrm]') && typeof $ === 'function' && $('#List').length > 0`);
}
await sleep(4000);
const loadSummary = {
  ready,
  total: responses.length,
  failing: responses.filter((r) => r.status >= 400),
  php: responses.filter((r) => /\.php/.test(r.url)).map((r) => `${r.status} ${r.url}`),
};

async function upload(files) {
  const encoded = files.map(({ name, bytes }) => ({ name, b64: Buffer.from(bytes).toString('base64') }));
  const before = await evaluate(`document.getElementById('lcont') ? document.getElementById('lcont').innerText.length : -1`);
  const submitted = await evaluate(`(() => {
    const dt = new DataTransfer();
    for (const file of ${JSON.stringify(encoded)}) {
      const bytes = Uint8Array.from(atob(file.b64), (c) => c.charCodeAt(0));
      dt.items.add(new File([bytes], file.name, { type: 'application/x-bittorrent' }));
    }
    const input = document.getElementById('torrent_file');
    input.files = dt.files;
    input.dispatchEvent(new Event('change', { bubbles: true }));
    const stopped = document.getElementById('torrents_start_stopped');
    if (stopped) stopped.checked = true;
    window.__crdk3Loads = 0;
    document.querySelector('iframe[name=uploadfrm]').addEventListener('load', () => { window.__crdk3Loads++; }, { once: false });
    document.getElementById('add_button').click();
    return { disabled: document.getElementById('add_button').disabled, action: document.getElementById('addtorrent').action };
  })()`);
  await sleep(6000);
  const after = await evaluate(`(() => {
    const frame = document.querySelector('iframe[name=uploadfrm]');
    let doc = 'unreadable';
    try { doc = frame.contentDocument ? ('readable:' + frame.contentDocument.location.href.replace(location.origin, '')) : 'null'; } catch (e) { doc = 'throws:' + e.name; }
    const log = document.getElementById('lcont') ? document.getElementById('lcont').innerText : '';
    return { iframeLoads: window.__crdk3Loads, iframeDocument: doc, logTail: log.slice(${before} > 0 ? ${before} : 0).trim().split('\\n').filter(Boolean), notyBars: document.querySelectorAll('.noty_bar').length };
  })()`);
  return { names: files.map((file) => file.name), submitted, after };
}

const good = await upload([{ name: `${tag}-good.torrent`, bytes: torrent(fixtures.good) }]);
const bad = await upload([{ name: `${tag}-bad.torrent`, bytes: Buffer.from('invalid torrent') }]);
const batch = await upload(fixtures.batch.map((fixture, i) => ({
  name: `Мастер и Маргарита (2024) WEB-DL 1080p серия ${String(i).padStart(2, '0')}.torrent`,
  bytes: torrent(fixture),
})));
// Near the filesystem's 255-byte filename limit; the old redirect exceeded both
// FastCGI's first buffer and nginx's request-line limit after following it.
const longBatch = await upload(Array.from({ length: 100 }, (_, i) => ({
  name: `${String(i).padStart(3, '0')}-${'Я'.repeat(120)}.torrent`,
  bytes: Buffer.from('invalid torrent'),
})));
const addResponses = responses.filter((r) => /addtorrent\.php/.test(r.url)).map((r) => `${r.status} ${r.type} xfo=${r.xfo} ${r.url.slice(0, 120)}`);
console.log(JSON.stringify({ tag, loadSummary, good, bad, batch, longBatch, addResponses, console: consoleLines.filter((l) => /frame|X-Frame|Refused|exception|error/i.test(l)).slice(0, 20) }, null, 2));
await send('Target.closeTarget', { targetId }, null);
ws.close();
process.exit(0);
