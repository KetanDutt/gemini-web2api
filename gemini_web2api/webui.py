"""Self-contained status dashboard served at ``GET /``.

The server previously answered ``GET /`` with a bare JSON blob, which meant
there was no way to point a browser at a deployment and see whether it was
healthy, which models it exposed, or whether a cookie had been picked up.

Everything here is inlined: no CDN, no build step, no external requests. The
page works offline and inside an air-gapped container. Programmatic clients are
unaffected — ``GET /`` still returns JSON unless the caller sends
``Accept: text/html`` (content negotiation in ``server.py``), and ``/health``
is always JSON.
"""
import json

# Replaced with the live state dict when the page is rendered.
_STATE_TOKEN = "@@STATE@@"

_DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>gemini-web2api</title>
<meta name="robots" content="noindex">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0' stop-color='%234285f4'/%3E%3Cstop offset='.5' stop-color='%239b72cb'/%3E%3Cstop offset='1' stop-color='%23d96570'/%3E%3C/linearGradient%3E%3C/defs%3E%3Cpath fill='url(%23g)' d='M16 2l3.1 8.4a8 8 0 005 5L32.5 18l-8.4 3.1a8 8 0 00-5 5L16 34l-3.1-8.4a8 8 0 00-5-5L-.5 18l8.4-3.1a8 8 0 005-5z' transform='translate(0 -2) scale(1)'/%3E%3C/svg%3E">
<style>
:root{
  color-scheme:light dark;
  --bg:#f6f7f9; --card:#fff; --fg:#1f2328; --muted:#656d76; --line:#d8dee4;
  --accent:#1a73e8; --ok:#188038; --warn:#b06000; --bad:#c5221f;
  --chip:#eef1f5; --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){
  :root{ --bg:#0d1117; --card:#161b22; --fg:#e6edf3; --muted:#9198a1; --line:#30363d;
         --accent:#6ea8fe; --ok:#3fb950; --warn:#d29922; --bad:#f85149; --chip:#21262d; }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:28px 20px 64px}
header{display:flex;align-items:center;gap:14px;margin-bottom:6px}
.logo{width:40px;height:40px;flex:0 0 40px}
h1{font-size:21px;margin:0;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:13px;margin:0}
.pill{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;
  font-size:12px;font-weight:600;background:var(--chip);border:1px solid var(--line)}
.dot{width:7px;height:7px;border-radius:50%;background:var(--ok)}
.dot.bad{background:var(--bad)} .dot.warn{background:var(--warn)}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);
  margin:30px 0 10px;font-weight:650}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(228px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card .k{font-size:11.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.card .v{font-size:14px;margin-top:3px;font-family:var(--mono);word-break:break-all}
table{width:100%;border-collapse:collapse;background:var(--card);
  border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:13.5px}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th{background:var(--chip);font-size:11.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
tr:last-child td{border-bottom:0}
td code,p code,li code{font-family:var(--mono);font-size:12.5px;background:var(--chip);
  padding:1.5px 5px;border-radius:5px}
.copy{cursor:pointer;border:1px solid var(--line);background:var(--chip);color:var(--fg);
  border-radius:6px;padding:2px 8px;font-size:11.5px;font-family:inherit}
.copy:hover{border-color:var(--accent);color:var(--accent)}
.tag{display:inline-block;padding:1px 7px;border-radius:5px;font-size:11px;font-weight:600;
  background:var(--chip);border:1px solid var(--line);color:var(--muted)}
.tag.cookie{color:var(--warn);border-color:currentColor}
.play{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
input,select,textarea{font:inherit;font-size:13.5px;color:var(--fg);background:var(--bg);
  border:1px solid var(--line);border-radius:7px;padding:7px 10px}
input:focus,select:focus,textarea:focus{outline:2px solid var(--accent);outline-offset:-1px}
textarea{width:100%;min-height:82px;resize:vertical;font-family:inherit}
button.primary{background:var(--accent);border-color:transparent;color:#fff;font-weight:600;
  padding:8px 18px;border-radius:7px;cursor:pointer;font-size:14px}
button.primary:disabled{opacity:.55;cursor:progress}
label.chk{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--muted)}
.out{margin-top:12px;padding:12px;background:var(--bg);border:1px solid var(--line);
  border-radius:8px;font-family:var(--mono);font-size:13px;white-space:pre-wrap;
  word-break:break-word;min-height:60px;max-height:420px;overflow:auto}
.out.err{border-color:var(--bad);color:var(--bad)}
.meta{font-size:12px;color:var(--muted);margin-top:8px;font-family:var(--mono)}
footer{margin-top:34px;padding-top:16px;border-top:1px solid var(--line);
  font-size:12.5px;color:var(--muted)}
footer a{color:var(--accent);text-decoration:none} footer a:hover{text-decoration:underline}
.note{font-size:12.5px;color:var(--muted);margin:8px 0 0}
@media(max-width:560px){ .row{flex-direction:column;align-items:stretch} th:nth-child(3),td:nth-child(3){display:none} }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <svg class="logo" viewBox="0 0 32 32" aria-hidden="true">
      <defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#4285f4"/><stop offset=".5" stop-color="#9b72cb"/>
        <stop offset="1" stop-color="#d96570"/></linearGradient></defs>
      <path fill="url(#lg)" d="M16 1l3.6 9.8a7.6 7.6 0 004.6 4.6L34 19l-9.8 3.6a7.6 7.6 0 00-4.6 4.6L16 37l-3.6-9.8a7.6 7.6 0 00-4.6-4.6L-2 19l9.8-3.6a7.6 7.6 0 004.6-4.6z" transform="translate(0 -3) scale(1)"/>
    </svg>
    <div style="flex:1">
      <h1>gemini-web2api</h1>
      <p class="sub">Gemini Web &rarr; OpenAI-compatible API</p>
    </div>
    <span class="pill"><span class="dot" id="statusDot"></span><span id="statusText">checking</span></span>
  </header>
  <p class="sub">v<span id="version"></span> &middot; uptime <span id="uptime"></span></p>

  <h2>Runtime</h2>
  <div class="grid" id="runtime"></div>
  <p class="note" id="warnNote"></p>

  <h2>Models</h2>
  <table>
    <thead><tr><th>Model ID</th><th>Category</th><th>Description</th><th></th></tr></thead>
    <tbody id="models"></tbody>
  </table>
  <p class="note">Append <code>@think=N</code> to any model ID to override thinking depth
  (<code>0</code> deepest &rarr; <code>4</code> shallowest), e.g. <code>gemini-3.5-flash-thinking@think=2</code>.</p>

  <h2>Playground</h2>
  <div class="play">
    <div class="row">
      <select id="model" aria-label="Model"></select>
      <input id="apikey" type="password" placeholder="API key (if required)" style="flex:1;min-width:180px" aria-label="API key">
      <label class="chk"><input type="checkbox" id="stream" checked> stream</label>
      <button class="primary" id="send">Send</button>
    </div>
    <textarea id="prompt" placeholder="Ask something…" aria-label="Prompt"></textarea>
    <div class="out" id="out">Response appears here.</div>
    <div class="meta" id="outMeta"></div>
  </div>

  <h2>Endpoints</h2>
  <table>
    <thead><tr><th>Method</th><th>Path</th><th>Purpose</th></tr></thead>
    <tbody id="endpoints"></tbody>
  </table>

  <footer>
    <span id="foot"></span>
  </footer>
</div>

<script>
const STATE = @@STATE@@;

const $ = (id) => document.getElementById(id);
const fmtUptime = (s) => {
  s = Math.max(0, Math.floor(s));
  const d = Math.floor(s/86400), h = Math.floor(s%86400/3600), m = Math.floor(s%3600/60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s%60}s`;
  return `${s}s`;
};
const pill = (text, level) => `<span class="tag ${level||''}">${text}</span>`;

function render(){
  $('version').textContent = STATE.version;
  $('uptime').textContent = fmtUptime(STATE.uptime_sec);

  const healthy = STATE.upstream_reachable !== false;
  $('statusDot').className = 'dot' + (healthy ? '' : ' warn');
  $('statusText').textContent = healthy ? 'running' : 'running (upstream unverified)';

  const cards = [
    ['Base URL', STATE.base_url],
    ['Streaming', STATE.streaming],
    ['API keys', STATE.api_keys],
    ['Cookie', STATE.cookie],
    ['Default model', STATE.default_model],
    ['Build tag (bl)', STATE.gemini_bl],
    ['Proxy', STATE.proxy || 'system / none'],
    ['Rate limit', STATE.rate_limit],
    ['Temporary chats', STATE.temporary_chats ? 'yes' : 'no'],
    ['Requests served', STATE.requests_served],
    ['Python', STATE.python],
    ['Uptime', fmtUptime(STATE.uptime_sec)],
  ];
  $('runtime').innerHTML = cards.map(([k,v]) =>
    `<div class="card"><div class="k">${k}</div><div class="v">${v ?? '&mdash;'}</div></div>`).join('');

  $('warnNote').innerHTML = (STATE.warnings || []).map(w => `&#9888; ${w}`).join('<br>');

  $('models').innerHTML = STATE.models.map(m => `<tr>
      <td><code>${m.id}</code></td>
      <td>${pill(m.category)}${m.needs_cookie ? ' ' + pill('cookie', 'cookie') : ''}</td>
      <td>${m.desc}<br><span class="sub">typical output ${m.output || 'varies'}</span></td>
      <td><button class="copy" data-copy="${m.id}">copy</button></td>
    </tr>`).join('');

  $('endpoints').innerHTML = STATE.endpoints.map(e =>
    `<tr><td><span class="tag">${e[0]}</span></td><td><code>${e[1]}</code></td><td>${e[2]}</td></tr>`).join('');

  $('model').innerHTML = STATE.models.map(m =>
    `<option value="${m.id}"${m.id===STATE.default_model?' selected':''}>${m.id}</option>`).join('');

  $('foot').innerHTML =
    `gemini-web2api v${STATE.version} &middot; MIT licensed &middot; ` +
    `docs in the <code>docs/</code> folder of the repository`;

  const saved = localStorage.getItem('gw2a.key');
  if (saved) $('apikey').value = saved;
}

document.addEventListener('click', (ev) => {
  const btn = ev.target.closest('.copy');
  if (!btn) return;
  const text = btn.dataset.copy;
  navigator.clipboard?.writeText(text);
  btn.textContent = 'copied';
  setTimeout(() => btn.textContent = 'copy', 1200);
});

async function send(){
  const out = $('out'), meta = $('outMeta'), btn = $('send');
  const model = $('model').value, prompt = $('prompt').value.trim();
  out.className = 'out'; out.textContent = ''; meta.textContent = '';
  if (!prompt) { out.className = 'out err'; out.textContent = 'Enter a prompt first.'; return; }

  $('apikey').value && localStorage.setItem('gw2a.key', $('apikey').value);
  const headers = {'Content-Type': 'application/json'};
  if ($('apikey').value) headers['Authorization'] = 'Bearer ' + $('apikey').value;
  const body = JSON.stringify({
    model, stream: $('stream').checked,
    messages: [{role: 'user', content: prompt}],
  });

  btn.disabled = true;
  const started = performance.now();
  let chars = 0;
  try {
    const res = await fetch('v1/chat/completions', {method:'POST', headers, body});
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(`HTTP ${res.status} — ${detail.slice(0, 400)}`);
    }
    if ($('stream').checked) {
      const reader = res.body.getReader(), dec = new TextDecoder();
      let buf = '';
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        buf += dec.decode(value, {stream:true});
        let idx;
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const block = buf.slice(0, idx); buf = buf.slice(idx + 2);
          const line = block.split('\n').find(l => l.startsWith('data: '));
          if (!line) continue;
          const payload = line.slice(6);
          if (payload === '[DONE]') continue;
          try {
            const delta = JSON.parse(payload).choices?.[0]?.delta?.content;
            if (delta) { chars += delta.length; out.textContent += delta; out.scrollTop = out.scrollHeight; }
          } catch (_) {}
        }
        meta.textContent = `${chars} chars · ${(performance.now()-started).toFixed(0)} ms · streaming`;
      }
    } else {
      const data = await res.json();
      const text = data.choices?.[0]?.message?.content ?? '(empty)';
      out.textContent = text;
      const u = data.usage || {};
      meta.textContent = `${text.length} chars · ${(performance.now()-started).toFixed(0)} ms · ` +
        `~${u.prompt_tokens||'?'} in / ~${u.completion_tokens||'?'} out (estimated)`;
    }
  } catch (err) {
    out.className = 'out err';
    out.textContent = String(err.message || err);
  } finally {
    btn.disabled = false;
  }
}
$('send').addEventListener('click', send);
$('prompt').addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') send();
});

render();
// Keep the counters live without a page reload.
setInterval(async () => {
  try {
    const res = await fetch('health');
    if (!res.ok) throw new Error(res.status);
    const h = await res.json();
    $('uptime').textContent = fmtUptime(h.uptime_sec);
    $('statusDot').className = 'dot';
    $('statusText').textContent = 'running';
  } catch (_) {
    $('statusDot').className = 'dot bad';
    $('statusText').textContent = 'unreachable';
  }
}, 15000);
</script>
</body>
</html>
"""


def render_dashboard(state):
    """Return the dashboard HTML with the live state injected."""
    payload = json.dumps(state, ensure_ascii=False, default=str)
    # Prevent an early </script> in any interpolated value from breaking out.
    payload = payload.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return _DASHBOARD_HTML.replace(_STATE_TOKEN, payload).encode("utf-8")
