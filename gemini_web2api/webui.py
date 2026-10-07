"""Self-contained dashboard served at ``GET /``.

Three things live here:

* **Status** — what the backend is doing right now: configuration, health
  checks, live metrics, latency distribution and per-model breakdown.
* **Chat** — talk to the models directly, with conversations kept in the
  browser so there is history to come back to.
* **Activity** — the most recent requests the server handled.

Everything is inlined: no CDN, no build step, no external requests. The page
works offline and inside an air-gapped container, which is a tested property.

Two deliberate boundaries:

1. ``GET /`` is public, so the state injected into the page carries no history
   and no secrets. Live metrics and request history are fetched from
   ``/status``, which is auth-gated when API keys are configured — history
   contains client addresses, so it must not be in the public render.
2. Chat conversations are stored in ``localStorage``. The upstream is
   single-turn, so the browser resends the whole transcript each turn exactly as
   any OpenAI client does; nothing is persisted server-side.

Programmatic clients are unaffected — ``GET /`` still returns JSON unless the
caller sends ``Accept: text/html`` (content negotiation in ``server.py``).
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
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0' stop-color='%234285f4'/%3E%3Cstop offset='.5' stop-color='%239b72cb'/%3E%3Cstop offset='1' stop-color='%23d96570'/%3E%3C/defs%3E%3Cpath fill='url(%23g)' d='M16 2l3.1 8.4a8 8 0 005 5L32.5 18l-8.4 3.1a8 8 0 00-5 5L16 34l-3.1-8.4a8 8 0 00-5-5L-.5 18l8.4-3.1a8 8 0 005-5z' transform='translate(0 -2) scale(1)'/%3E%3C/svg%3E">
<style>
:root{
  color-scheme:light dark;
  --bg:#f6f7f9; --card:#fff; --fg:#1f2328; --muted:#656d76; --line:#d8dee4;
  --accent:#1a73e8; --ok:#188038; --warn:#b06000; --bad:#c5221f;
  --chip:#eef1f5; --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  --user:#e8f0fe;
}
@media (prefers-color-scheme:dark){
  :root{ --bg:#0d1117; --card:#161b22; --fg:#e6edf3; --muted:#9198a1; --line:#30363d;
         --accent:#6ea8fe; --ok:#3fb950; --warn:#d29922; --bad:#f85149; --chip:#21262d;
         --user:#1c2b41; }
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  display:flex;flex-direction:column}
.wrap{max-width:1180px;margin:0 auto;padding:20px 20px 0;width:100%;flex:1;
  display:flex;flex-direction:column;min-height:0}
header{display:flex;align-items:center;gap:14px;margin-bottom:14px;flex-wrap:wrap}
.logo{width:38px;height:38px;flex:0 0 38px}
h1{font-size:20px;margin:0;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:13px;margin:0}
.pill{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;
  font-size:12px;font-weight:600;background:var(--chip);border:1px solid var(--line)}
.dot{width:7px;height:7px;border-radius:50%;background:var(--ok)}
.dot.bad{background:var(--bad)} .dot.warn{background:var(--warn)}
.spacer{flex:1}

nav.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin-bottom:18px;flex-wrap:wrap}
nav.tabs button{background:none;border:0;border-bottom:2px solid transparent;color:var(--muted);
  font:inherit;font-size:14px;font-weight:600;padding:9px 14px;cursor:pointer;border-radius:6px 6px 0 0}
nav.tabs button:hover{color:var(--fg);background:var(--chip)}
nav.tabs button[aria-selected="true"]{color:var(--accent);border-bottom-color:var(--accent)}

section.tab{display:none;flex:1;min-height:0;flex-direction:column;padding-bottom:24px}
section.tab.active{display:flex}

h2{font-size:12.5px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);
  margin:26px 0 10px;font-weight:650}
h2:first-child{margin-top:0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(215px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:11px 13px}
.card .k{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.card .v{font-size:14px;margin-top:3px;font-family:var(--mono);word-break:break-all}
table{width:100%;border-collapse:collapse;background:var(--card);
  border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:13.5px}
th,td{text-align:left;padding:8px 11px;border-bottom:1px solid var(--line);vertical-align:top}
th{background:var(--chip);font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
tr:last-child td{border-bottom:0}
td code,p code,li code,.code{font-family:var(--mono);font-size:12.5px;background:var(--chip);
  padding:1.5px 5px;border-radius:5px;word-break:break-all}
.copy{cursor:pointer;border:1px solid var(--line);background:var(--chip);color:var(--fg);
  border-radius:6px;padding:2px 8px;font-size:11.5px;font-family:inherit}
.copy:hover{border-color:var(--accent);color:var(--accent)}
.tag{display:inline-block;padding:1px 7px;border-radius:5px;font-size:11px;font-weight:600;
  background:var(--chip);border:1px solid var(--line);color:var(--muted);white-space:nowrap}
.tag.cookie{color:var(--warn);border-color:currentColor}
.tag.ok{color:var(--ok);border-color:currentColor}
.tag.bad{color:var(--bad);border-color:currentColor}
.tag.warn{color:var(--warn);border-color:currentColor}
.tag.acc{color:var(--accent);border-color:currentColor}
.note{font-size:12.5px;color:var(--muted);margin:8px 0 0}
.warnbox{background:var(--card);border:1px solid var(--warn);border-left-width:3px;
  border-radius:8px;padding:10px 12px;font-size:13px;margin:10px 0}
input,select,textarea{font:inherit;font-size:13.5px;color:var(--fg);background:var(--bg);
  border:1px solid var(--line);border-radius:7px;padding:7px 10px}
input:focus,select:focus,textarea:focus{outline:2px solid var(--accent);outline-offset:-1px}
button.primary{background:var(--accent);border:1px solid transparent;color:#fff;font-weight:600;
  padding:8px 16px;border-radius:7px;cursor:pointer;font-size:14px;font-family:inherit}
button.primary:disabled{opacity:.5;cursor:not-allowed}
button.ghost{background:var(--chip);border:1px solid var(--line);color:var(--fg);
  padding:7px 13px;border-radius:7px;cursor:pointer;font-size:13px;font-family:inherit}
button.ghost:hover{border-color:var(--accent);color:var(--accent)}
.row{display:flex;gap:9px;flex-wrap:wrap;align-items:center}
label.chk{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--muted)}
.scroll{overflow:auto}

/* ── Chat ─────────────────────────────────────────────── */
.chat{display:flex;gap:14px;flex:1;min-height:0}
.side{width:236px;flex:0 0 236px;display:flex;flex-direction:column;gap:8px;min-height:0}
.side .list{overflow:auto;flex:1;display:flex;flex-direction:column;gap:4px;
  background:var(--card);border:1px solid var(--line);border-radius:10px;padding:6px}
.conv{display:flex;align-items:center;gap:6px;padding:7px 8px;border-radius:7px;cursor:pointer;
  border:1px solid transparent;font-size:13px}
.conv:hover{background:var(--chip)}
.conv.sel{background:var(--chip);border-color:var(--line)}
.conv .t{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.conv .n{color:var(--muted);font-size:11px;font-family:var(--mono)}
.conv .x{border:0;background:none;color:var(--muted);cursor:pointer;font-size:14px;
  line-height:1;padding:2px 4px;border-radius:4px;visibility:hidden}
.conv:hover .x{visibility:visible}
.conv .x:hover{color:var(--bad);background:var(--card)}
.main{flex:1;display:flex;flex-direction:column;min-width:0;min-height:0;
  background:var(--card);border:1px solid var(--line);border-radius:10px}
.msgs{flex:1;overflow:auto;padding:16px;display:flex;flex-direction:column;gap:14px}
.msg{display:flex;gap:10px;max-width:100%}
.msg .who{flex:0 0 74px;font-size:11px;text-transform:uppercase;letter-spacing:.05em;
  color:var(--muted);padding-top:3px;text-align:right;font-weight:650}
.msg .bub{flex:1;min-width:0;padding:10px 13px;border-radius:10px;border:1px solid var(--line);
  background:var(--bg);font-size:14px;overflow-wrap:anywhere}
.msg.user .bub{background:var(--user)}
.msg.err .bub{border-color:var(--bad);color:var(--bad)}
.msg .bub pre{margin:8px 0;padding:10px;background:var(--chip);border:1px solid var(--line);
  border-radius:7px;overflow:auto;font-family:var(--mono);font-size:12.5px;white-space:pre}
.msg .bub code{font-family:var(--mono);font-size:12.5px;background:var(--chip);
  padding:1.5px 5px;border-radius:5px}
.msg .bub pre code{background:none;padding:0}
.msg .bub strong{font-weight:650}
.msg .bub .mdh{font-weight:650;font-size:15px;margin:8px 0 3px}
.mmeta{font-size:11.5px;color:var(--muted);margin-top:7px;font-family:var(--mono)}
.empty{margin:auto;text-align:center;color:var(--muted);max-width:430px;padding:20px}
.chips{display:flex;gap:7px;flex-wrap:wrap;justify-content:center;margin-top:14px}
.chip{cursor:pointer;border:1px solid var(--line);background:var(--chip);color:var(--fg);
  border-radius:999px;padding:5px 12px;font-size:12.5px;font-family:inherit}
.chip:hover{border-color:var(--accent);color:var(--accent)}
.composer{border-top:1px solid var(--line);padding:11px 13px;display:flex;flex-direction:column;gap:9px}
.composer textarea{width:100%;min-height:56px;max-height:220px;resize:vertical;font-family:inherit;
  font-size:14px;line-height:1.5}
.cursor{display:inline-block;width:7px;height:15px;background:var(--accent);
  vertical-align:text-bottom;animation:blink 1s steps(2) infinite}
@keyframes blink{0%{opacity:1}50%{opacity:0}}
.bar{display:flex;gap:9px;align-items:center;flex-wrap:wrap;padding:9px 13px;
  border-bottom:1px solid var(--line)}
footer{padding:14px 20px;border-top:1px solid var(--line);font-size:12.5px;color:var(--muted);
  text-align:center}
footer a{color:var(--accent);text-decoration:none}
@media(max-width:820px){
  .chat{flex-direction:column}
  .side{width:100%;flex:0 0 auto;max-height:170px}
  .side .list{flex-direction:row;overflow-x:auto}
  .conv{flex:0 0 auto;max-width:190px}
  .msg .who{flex-basis:56px}
}
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
    <div>
      <h1>gemini-web2api</h1>
      <p class="sub">Gemini Web &rarr; OpenAI-compatible API</p>
    </div>
    <div class="spacer"></div>
    <span class="pill"><span class="dot" id="statusDot"></span><span id="statusText">checking</span></span>
    <span class="pill" id="verPill">v<span id="version"></span> &middot; <span id="uptime"></span></span>
  </header>

  <nav class="tabs" role="tablist">
    <button role="tab" data-tab="chat" aria-selected="true">Chat</button>
    <button role="tab" data-tab="status" aria-selected="false">Status</button>
    <button role="tab" data-tab="activity" aria-selected="false">Activity</button>
    <button role="tab" data-tab="models" aria-selected="false">Models</button>
    <button role="tab" data-tab="api" aria-selected="false">API</button>
  </nav>

  <!-- ── Chat ─────────────────────────────────────────── -->
  <section class="tab active" id="tab-chat" role="tabpanel">
    <div class="chat">
      <aside class="side">
        <button class="primary" id="newChat" style="width:100%">+ New chat</button>
        <div class="list" id="convList"></div>
        <p class="note">Conversations are saved in this browser only.</p>
      </aside>
      <div class="main">
        <div class="bar">
          <select id="model" aria-label="Model"></select>
          <select id="think" aria-label="Thinking depth">
            <option value="">think: model default</option>
            <option value="0">think 0 — deepest</option>
            <option value="1">think 1</option>
            <option value="2">think 2 — medium</option>
            <option value="3">think 3</option>
            <option value="4">think 4 — shallowest</option>
          </select>
          <label class="chk"><input type="checkbox" id="stream" checked> stream</label>
          <div class="spacer"></div>
          <input id="apikey" type="password" placeholder="API key (if required)"
                 style="width:190px" aria-label="API key" autocomplete="off">
        </div>
        <div class="msgs" id="msgs"></div>
        <div class="composer">
          <textarea id="prompt" placeholder="Ask something…  (Enter to send, Shift+Enter for a new line)"
                    aria-label="Message"></textarea>
          <div class="row">
            <button class="primary" id="send">Send</button>
            <button class="ghost" id="stop" style="display:none">Stop</button>
            <button class="ghost" id="clearConv">Clear this chat</button>
            <div class="spacer"></div>
            <span class="note" id="chatMeta"></span>
          </div>
        </div>
      </div>
    </div>
    <p class="note">The upstream conversation is single-turn: this page resends the whole
      transcript each turn, exactly as an OpenAI client does. Very long chats therefore cost
      more tokens and can eventually exceed what the upstream accepts.</p>
  </section>

  <!-- ── Status ───────────────────────────────────────── -->
  <section class="tab" id="tab-status" role="tabpanel">
    <div class="row" style="margin-bottom:14px">
      <button class="ghost" id="refreshStatus">Refresh</button>
      <label class="chk"><input type="checkbox" id="autoStatus" checked> auto-refresh (10s)</label>
      <div class="spacer"></div>
      <span class="note" id="statusMeta"></span>
    </div>
    <div id="statusWarn"></div>
    <h2>Runtime</h2>
    <div class="grid" id="runtime"></div>
    <h2>Health checks</h2>
    <div id="checks"></div>
    <h2>Counters</h2>
    <div class="grid" id="counters"></div>
    <h2>Latency</h2>
    <div class="grid" id="latency"></div>
    <div id="histWrap"></div>
    <h2>By model</h2>
    <div class="scroll"><table>
      <thead><tr><th>Model</th><th>Requests</th><th>Avg</th><th>Max</th></tr></thead>
      <tbody id="byModel"></tbody>
    </table></div>
    <h2>Status codes</h2>
    <div class="scroll"><table>
      <thead><tr><th>Code</th><th>Count</th></tr></thead>
      <tbody id="byStatus"></tbody>
    </table></div>
    <h2>Configuration (redacted)</h2>
    <div class="scroll"><table>
      <thead><tr><th>Key</th><th>Value</th></tr></thead>
      <tbody id="cfgTable"></tbody>
    </table></div>
  </section>

  <!-- ── Activity ─────────────────────────────────────── -->
  <section class="tab" id="tab-activity" role="tabpanel">
    <div class="row" style="margin-bottom:14px">
      <button class="ghost" id="refreshAct">Refresh</button>
      <label class="chk"><input type="checkbox" id="autoAct" checked> auto-refresh (5s)</label>
      <select id="actFilter" aria-label="Filter">
        <option value="">all requests</option>
        <option value="err">errors only</option>
        <option value="/v1/chat">chat completions</option>
        <option value="/v1/responses">responses</option>
        <option value="/v1beta">google-native</option>
      </select>
      <div class="spacer"></div>
      <span class="note" id="actMeta"></span>
    </div>
    <div id="actNote"></div>
    <div class="scroll" style="flex:1"><table>
      <thead><tr><th>Time</th><th>Method</th><th>Path</th><th>Status</th>
        <th>Model</th><th>Latency</th><th>Client</th><th>Request ID</th></tr></thead>
      <tbody id="actRows"></tbody>
    </table></div>
    <p class="note">Entries hold operational facts only — never prompts, response bodies or
      credentials. Query strings are stripped before recording, because Google-native clients
      may pass an API key in one. Retention is set by <code>history_max</code>.</p>
  </section>

  <!-- ── Models ───────────────────────────────────────── -->
  <section class="tab" id="tab-models" role="tabpanel">
    <div class="scroll"><table>
      <thead><tr><th>Model ID</th><th>Category</th><th>Description</th><th></th></tr></thead>
      <tbody id="models"></tbody>
    </table></div>
    <p class="note">Append <code>@think=N</code> to any model ID to override thinking depth
      (<code>0</code> deepest &rarr; <code>4</code> shallowest), e.g.
      <code>gemini-3.5-flash-thinking@think=2</code>. Models marked
      <span class="tag cookie">cookie</span> need an entitled Google session to route for real;
      without one they silently fall back to Flash.</p>
  </section>

  <!-- ── API ──────────────────────────────────────────── -->
  <section class="tab" id="tab-api" role="tabpanel">
    <h2>Endpoints</h2>
    <div class="scroll"><table>
      <thead><tr><th>Method</th><th>Path</th><th>Purpose</th></tr></thead>
      <tbody id="endpoints"></tbody>
    </table></div>
    <h2>Client configuration</h2>
    <div class="scroll"><table>
      <thead><tr><th>Field</th><th>Value</th><th></th></tr></thead>
      <tbody id="clientCfg"></tbody>
    </table></div>
    <h2>curl</h2>
    <div class="card"><pre style="margin:0;overflow:auto;font-family:var(--mono);font-size:12.5px" id="curlBox"></pre></div>
    <p class="note">Full reference in <code>docs/API.md</code> in the repository.</p>
  </section>
</div>

<footer><span id="foot"></span></footer>

<script>
const STATE = @@STATE@@;

const $ = (id) => document.getElementById(id);
const LS = {
  key: 'gw2a.key', convs: 'gw2a.convs', active: 'gw2a.active',
  model: 'gw2a.model', think: 'gw2a.think', stream: 'gw2a.stream',
};
const get = (k, d) => { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (_) { return d; } };
const set = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) {} };

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

const fmtUptime = (s) => {
  s = Math.max(0, Math.floor(s || 0));
  const d = Math.floor(s/86400), h = Math.floor(s%86400/3600), m = Math.floor(s%3600/60);
  if (d) return d + 'd ' + h + 'h';
  if (h) return h + 'h ' + m + 'm';
  if (m) return m + 'm ' + (s%60) + 's';
  return s + 's';
};
const fmtMs = (ms) => ms == null ? '—' : (ms < 1000 ? Math.round(ms) + ' ms' : (ms/1000).toFixed(2) + ' s');
const fmtTime = (ts) => {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], {hour12:false});
};
const pill = (text, level) => '<span class="tag ' + (level||'') + '">' + esc(text) + '</span>';
const statusPill = (code) => {
  const lvl = code >= 500 ? 'bad' : code >= 400 ? 'warn' : 'ok';
  return pill(code, lvl);
};

/* ── minimal, safe markdown: escape first, then format ─────────────── */
function inlineMd(t){
  let s = esc(t);
  s = s.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|\n)#{1,4}\s+([^\n]+)/g, '$1<div class="mdh">$2</div>');
  return s.replace(/\n/g, '<br>');
}
function renderMd(src){
  const out = [];
  const re = /```([a-zA-Z0-9_+\-]*)\n?([\s\S]*?)```/g;
  let last = 0, m;
  while ((m = re.exec(src)) !== null) {
    out.push(inlineMd(src.slice(last, m.index)));
    out.push('<pre><code>' + esc(m[2].replace(/\n$/, '')) + '</code></pre>');
    last = m.index + m[0].length;
  }
  out.push(inlineMd(src.slice(last)));
  return out.join('');
}

/* ── tabs ──────────────────────────────────────────────────────────── */
document.querySelectorAll('nav.tabs button').forEach((btn) => {
  btn.addEventListener('click', () => selectTab(btn.dataset.tab));
});
function selectTab(name){
  document.querySelectorAll('nav.tabs button').forEach((b) =>
    b.setAttribute('aria-selected', String(b.dataset.tab === name)));
  document.querySelectorAll('section.tab').forEach((s) =>
    s.classList.toggle('active', s.id === 'tab-' + name));
  if (name === 'status') refreshStatus();
  if (name === 'activity') refreshActivity();
  if (name === 'chat') $('prompt').focus();
}

/* ── static render from injected STATE ─────────────────────────────── */
function renderStatic(){
  $('version').textContent = STATE.version;
  $('uptime').textContent = fmtUptime(STATE.uptime_sec);

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
    ['History retained', STATE.history_enabled ? 'yes' : 'disabled'],
    ['Python', STATE.python],
  ];
  $('runtime').innerHTML = cards.map(([k,v]) =>
    '<div class="card"><div class="k">' + esc(k) + '</div><div class="v">' +
    esc(v ?? '—') + '</div></div>').join('');

  $('models').innerHTML = STATE.models.map((m) => '<tr>' +
      '<td><code>' + esc(m.id) + '</code></td>' +
      '<td>' + pill(m.category) + (m.needs_cookie ? ' ' + pill('cookie','cookie') : '') + '</td>' +
      '<td>' + esc(m.desc) + '<br><span class="sub">typical output ' +
        esc(m.output || 'varies') + '</span></td>' +
      '<td><button class="copy" data-copy="' + esc(m.id) + '">copy</button></td>' +
    '</tr>').join('');

  $('endpoints').innerHTML = STATE.endpoints.map((e) =>
    '<tr><td>' + pill(e[0]) + '</td><td><code>' + esc(e[1]) + '</code></td><td>' +
    esc(e[2]) + '</td></tr>').join('');

  const base = STATE.base_url || '';
  $('clientCfg').innerHTML = [
    ['Base URL', base],
    ['API key', STATE.auth_enabled ? '(one of your configured api_keys)' : '(anything — auth is disabled)'],
    ['Model', STATE.default_model],
  ].map(([k,v]) => '<tr><td>' + esc(k) + '</td><td><code>' + esc(v) +
    '</code></td><td><button class="copy" data-copy="' + esc(v) + '">copy</button></td></tr>').join('');

  $('curlBox').textContent =
    'curl ' + base + '/chat/completions \\\n' +
    '  -H "Content-Type: application/json" \\\n' +
    (STATE.auth_enabled ? '  -H "Authorization: Bearer YOUR_KEY" \\\n' : '') +
    '  -d \'{"model":"' + STATE.default_model + '","messages":[{"role":"user","content":"Hello!"}]}\'\n\n' +
    '# streaming (note -N, which disables curl\'s own buffering)\n' +
    'curl -N ' + base + '/chat/completions \\\n' +
    '  -H "Content-Type: application/json" \\\n' +
    (STATE.auth_enabled ? '  -H "Authorization: Bearer YOUR_KEY" \\\n' : '') +
    '  -d \'{"model":"' + STATE.default_model + '","stream":true,' +
    '"messages":[{"role":"user","content":"Count to five"}]}\'\n\n' +
    '# health and status\n' +
    'curl ' + (base.replace(/\/v1$/, '')) + '/health\n' +
    'curl ' + (base.replace(/\/v1$/, '')) + '/status' +
    (STATE.auth_enabled ? ' -H "Authorization: Bearer YOUR_KEY"' : '');

  $('foot').innerHTML = 'gemini-web2api v' + esc(STATE.version) +
    ' &middot; MIT licensed &middot; documentation in the <code>docs/</code> folder';

  $('model').innerHTML = STATE.models.map((m) =>
    '<option value="' + esc(m.id) + '"' + (m.id === STATE.default_model ? ' selected' : '') +
    '>' + esc(m.id) + '</option>').join('');

  const savedModel = get(LS.model, null);
  if (savedModel && STATE.models.some((m) => m.id === savedModel)) $('model').value = savedModel;
  const savedThink = get(LS.think, '');
  if (savedThink !== '') $('think').value = String(savedThink);
  $('stream').checked = get(LS.stream, true);
  const savedKey = localStorage.getItem(LS.key);
  if (savedKey) $('apikey').value = savedKey;
}

document.addEventListener('click', (ev) => {
  const btn = ev.target.closest('.copy');
  if (!btn) return;
  const text = btn.dataset.copy;
  if (navigator.clipboard) navigator.clipboard.writeText(text);
  const old = btn.textContent;
  btn.textContent = 'copied';
  setTimeout(() => { btn.textContent = old; }, 1100);
});

/* ── authenticated fetch of /status ────────────────────────────────── */
async function fetchStatus(){
  const headers = {};
  const key = $('apikey').value.trim();
  if (key) headers['Authorization'] = 'Bearer ' + key;
  const res = await fetch('status', {headers});
  if (res.status === 401) { const e = new Error('401'); e.code = 401; throw e; }
  if (!res.ok) throw new Error('HTTP ' + res.status);
  return res.json();
}

function needKeyNote(where, extra){
  $(where).innerHTML = '<div class="warnbox">This view reads <code>/status</code>, which ' +
    'requires an API key when authentication is enabled. Enter your key in the ' +
    '<strong>Chat</strong> tab' + (extra ? ' ' + esc(extra) : '') + '.</div>';
}

async function refreshStatus(){
  try {
    const s = await fetchStatus();
    $('statusWarn').innerHTML = '';
    const m = s.metrics || {}, c = m.counters || {};
    $('statusMeta').textContent = 'updated ' + new Date().toLocaleTimeString([], {hour12:false});

    const fatal = (s.checks && s.checks.fatal) || [];
    const warns = (s.checks && s.checks.warnings) || [];
    $('checks').innerHTML =
      (fatal.length ? '<div class="warnbox" style="border-color:var(--bad)"><strong>Not ready:</strong><br>' +
        fatal.map(esc).join('<br>') + '</div>' : '') +
      (warns.length ? '<div class="warnbox">' + warns.map((w) => '&#9888; ' + esc(w)).join('<br>') + '</div>' : '') +
      (!fatal.length && !warns.length ? '<p class="note">No problems reported.</p>' : '');

    $('counters').innerHTML = Object.keys(c).map((k) =>
      '<div class="card"><div class="k">' + esc(k.replace(/_/g,' ')) + '</div><div class="v">' +
      esc(c[k]) + '</div></div>').join('');

    const hist = m.latency_histogram_ms || {};
    const bars = Object.keys(hist).filter((k) => hist[k] > 0);
    $('latency').innerHTML = [
      ['Average', fmtMs(m.latency_ms_avg)],
      ['Samples', m.latency_ms_samples ?? 0],
      ['Uptime', fmtUptime(m.uptime_sec)],
    ].map(([k,v]) => '<div class="card"><div class="k">' + esc(k) + '</div><div class="v">' +
      esc(v) + '</div></div>').join('');
    $('histWrap').innerHTML = bars.length
      ? '<p class="note">Upper bound (ms) &rarr; requests: ' +
        bars.map((k) => '<code>' + esc(k) + '</code> ' + esc(hist[k])).join(' &middot; ') + '</p>'
      : '<p class="note">No latency samples yet — latency is recorded for upstream calls only.</p>';

    const models = m.models || {};
    const names = Object.keys(models);
    $('byModel').innerHTML = names.length ? names.map((n) =>
      '<tr><td><code>' + esc(n) + '</code></td><td>' + esc(models[n].requests) + '</td><td>' +
      fmtMs(models[n].avg_ms) + '</td><td>' + fmtMs(models[n].max_ms) + '</td></tr>').join('')
      : '<tr><td colspan="4" class="note">No upstream calls yet.</td></tr>';

    const codes = m.status_codes || {};
    const keys = Object.keys(codes);
    $('byStatus').innerHTML = keys.length ? keys.map((k) =>
      '<tr><td>' + statusPill(Number(k)) + '</td><td>' + esc(codes[k]) + '</td></tr>').join('')
      : '<tr><td colspan="2" class="note">No responses recorded yet.</td></tr>';

    const cfg = s.config || {};
    $('cfgTable').innerHTML = Object.keys(cfg).sort().map((k) =>
      '<tr><td><code>' + esc(k) + '</code></td><td><code>' + esc(fmtCfg(cfg[k])) + '</code></td></tr>').join('');
  } catch (err) {
    if (err.code === 401) { needKeyNote('statusWarn'); $('statusMeta').textContent = 'authorisation required'; }
    else { $('statusWarn').innerHTML = '<div class="warnbox" style="border-color:var(--bad)">Could not read /status: ' + esc(err.message || err) + '</div>'; }
  }
}
function fmtCfg(v){
  if (v === null || v === undefined) return 'null';
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (Array.isArray(v)) return v.length ? '[' + v.length + ' items]' : '[]';
  return String(v);
}

/* ── activity ──────────────────────────────────────────────────────── */
let lastHistory = [];
async function refreshActivity(){
  if (!STATE.history_enabled) {
    $('actNote').innerHTML = '<div class="warnbox">Request history is disabled ' +
      '(<code>history_max: 0</code>). Set <code>history_max</code> to a positive number to enable it.</div>';
    $('actRows').innerHTML = '';
    return;
  }
  try {
    const s = await fetchStatus();
    $('actNote').innerHTML = '';
    lastHistory = s.history || [];
    const filter = $('actFilter').value;
    const rows = lastHistory.filter((r) => {
      if (!filter) return true;
      if (filter === 'err') return r.status >= 400;
      return String(r.path || '').indexOf(filter) === 0;
    });
    $('actMeta').textContent = rows.length + ' of ' + lastHistory.length + ' shown';
    $('actRows').innerHTML = rows.length ? rows.map((r) =>
      '<tr><td class="code">' + esc(fmtTime(r.ts)) + '</td>' +
      '<td>' + pill(r.method) + '</td>' +
      '<td><code>' + esc(r.path) + '</code></td>' +
      '<td>' + statusPill(r.status) + '</td>' +
      '<td>' + (r.model ? '<code>' + esc(r.model) + '</code>' : '<span class="note">—</span>') + '</td>' +
      '<td class="code">' + esc(fmtMs(r.ms)) + '</td>' +
      '<td class="code">' + esc(r.client || '—') + '</td>' +
      '<td class="code">' + esc(r.id || '—') + '</td></tr>').join('')
      : '<tr><td colspan="8" class="note">No matching requests yet.</td></tr>';
  } catch (err) {
    if (err.code === 401) { needKeyNote('actNote'); $('actMeta').textContent = 'authorisation required'; }
    else { $('actNote').innerHTML = '<div class="warnbox" style="border-color:var(--bad)">Could not read /status: ' + esc(err.message || err) + '</div>'; }
    $('actRows').innerHTML = '';
  }
}

/* ── conversations ─────────────────────────────────────────────────── */
let convs = get(LS.convs, null);
if (!Array.isArray(convs)) convs = [];
let activeId = get(LS.active, null);

const newId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
function activeConv(){ return convs.find((c) => c.id === activeId) || null; }
function persist(){ set(LS.convs, convs); set(LS.active, activeId); }

function ensureConv(){
  let c = activeConv();
  if (!c) { c = {id: newId(), title: 'New chat', created: Date.now()/1000, messages: []};
            convs.unshift(c); activeId = c.id; }
  return c;
}
function renderConvs(){
  const list = $('convList');
  if (!convs.length) { list.innerHTML = '<p class="note" style="padding:8px">No conversations yet.</p>'; return; }
  list.innerHTML = convs.map((c) =>
    '<div class="conv' + (c.id === activeId ? ' sel' : '') + '" data-id="' + esc(c.id) + '">' +
      '<span class="t">' + esc(c.title || 'New chat') + '</span>' +
      '<span class="n">' + esc(c.messages.filter((m) => m.role === 'user').length) + '</span>' +
      '<button class="x" data-del="' + esc(c.id) + '" title="Delete" aria-label="Delete conversation">&times;</button>' +
    '</div>').join('');
}
function renderMsgs(){
  const c = activeConv();
  const box = $('msgs');
  if (!c || !c.messages.length) {
    box.innerHTML = '<div class="empty"><div style="font-size:15px;font-weight:600;color:var(--fg)">Start a conversation</div>' +
      '<p>Pick a model above and ask something. Responses stream in as they are generated.</p>' +
      '<div class="chips">' +
      ['Explain what this server does','Write a haiku about the sea','Compare Python and Go for CLI tools',
       'Give me a JSON schema for a user record']
        .map((s) => '<button class="chip" data-sug="' + esc(s) + '">' + esc(s) + '</button>').join('') +
      '</div></div>';
    return;
  }
  box.innerHTML = c.messages.map((m, i) => msgHtml(m, i)).join('');
  box.scrollTop = box.scrollHeight;
}
function msgHtml(m, i){
  const cls = m.role === 'user' ? 'user' : (m.error ? 'err' : 'assistant');
  const who = m.role === 'user' ? 'You' : (m.error ? 'Error' : 'Gemini');
  const body = m.streaming ? esc(m.content) + '<span class="cursor"></span>'
    : (m.role === 'user' ? inlineMd(m.content) : renderMd(m.content || ''));
  return '<div class="msg ' + cls + '" data-i="' + i + '"><div class="who">' + who + '</div>' +
    '<div class="bub">' + (body || '<span class="note">(empty)</span>') +
    (m.meta ? '<div class="mmeta">' + esc(m.meta) + '</div>' : '') + '</div></div>';
}

$('convList').addEventListener('click', (ev) => {
  const del = ev.target.closest('[data-del]');
  if (del) {
    ev.stopPropagation();
    const id = del.dataset.del;
    convs = convs.filter((c) => c.id !== id);
    if (activeId === id) activeId = convs.length ? convs[0].id : null;
    persist(); renderConvs(); renderMsgs();
    return;
  }
  const row = ev.target.closest('.conv');
  if (row) { activeId = row.dataset.id; persist(); renderConvs(); renderMsgs(); }
});
document.addEventListener('click', (ev) => {
  const sug = ev.target.closest('[data-sug]');
  if (!sug) return;
  $('prompt').value = sug.dataset.sug;
  $('prompt').focus();
});
$('newChat').addEventListener('click', () => {
  const c = {id: newId(), title: 'New chat', created: Date.now()/1000, messages: []};
  convs.unshift(c); activeId = c.id; persist(); renderConvs(); renderMsgs(); $('prompt').focus();
});
$('clearConv').addEventListener('click', () => {
  const c = activeConv();
  if (!c) return;
  c.messages = []; c.title = 'New chat'; persist(); renderConvs(); renderMsgs();
});

/* ── sending ───────────────────────────────────────────────────────── */
let controller = null;

function chosenModel(){
  const base = $('model').value;
  const think = $('think').value;
  return think === '' ? base : base + '@think=' + think;
}
function setBusy(busy){
  $('send').disabled = busy;
  $('stop').style.display = busy ? '' : 'none';
  $('prompt').disabled = busy;
}

async function send(){
  const text = $('prompt').value.trim();
  if (!text) { $('prompt').focus(); return; }
  const key = $('apikey').value.trim();
  if (key) localStorage.setItem(LS.key, key);
  set(LS.model, $('model').value); set(LS.think, $('think').value); set(LS.stream, $('stream').checked);

  const conv = ensureConv();
  conv.messages.push({role: 'user', content: text});
  if (conv.title === 'New chat') conv.title = text.slice(0, 42) + (text.length > 42 ? '…' : '');
  const asst = {role: 'assistant', content: '', streaming: true, meta: ''};
  conv.messages.push(asst);
  const idx = conv.messages.length - 1;
  persist(); renderConvs(); renderMsgs();
  $('prompt').value = '';
  $('chatMeta').textContent = '';
  setBusy(true);

  const headers = {'Content-Type': 'application/json'};
  if (key) headers['Authorization'] = 'Bearer ' + key;
  // Only user/assistant turns are sent; the upstream has no system role on this
  // endpoint, and system content is folded into the prompt server-side.
  const payload = {
    model: chosenModel(),
    stream: $('stream').checked,
    messages: conv.messages.slice(0, idx).filter((m) => !m.error && m.content)
      .map((m) => ({role: m.role, content: m.content})),
  };

  const started = performance.now();
  let chars = 0;
  controller = new AbortController();
  const bump = () => {
    const box = $('msgs');
    const node = box.querySelector('.msg[data-i="' + idx + '"] .bub');
    if (node) {
      node.innerHTML = esc(asst.content) + '<span class="cursor"></span>';
      box.scrollTop = box.scrollHeight;
    }
  };

  try {
    const res = await fetch('v1/chat/completions', {
      method: 'POST', headers, body: JSON.stringify(payload), signal: controller.signal});
    if (!res.ok) {
      let detail = '';
      try { const j = await res.json(); detail = (j.error && j.error.message) || JSON.stringify(j); }
      catch (_) { detail = (await res.text()).slice(0, 300); }
      throw new Error('HTTP ' + res.status + ' — ' + detail);
    }
    if ($('stream').checked && res.body) {
      const reader = res.body.getReader(), dec = new TextDecoder();
      let buf = '';
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        buf += dec.decode(value, {stream: true});
        let nl;
        while ((nl = buf.indexOf('\n\n')) >= 0) {
          const block = buf.slice(0, nl); buf = buf.slice(nl + 2);
          const line = block.split('\n').find((l) => l.indexOf('data: ') === 0);
          if (!line) continue;
          const data = line.slice(6);
          if (data === '[DONE]') continue;
          try {
            const choice = (JSON.parse(data).choices || [])[0];
            if (!choice) continue;
            if (choice.delta && choice.delta.content) { chars += choice.delta.content.length; asst.content += choice.delta.content; bump(); }
            if (choice.finish_reason === 'tool_calls') asst.meta = 'finish_reason: tool_calls';
          } catch (_) {}
        }
        $('chatMeta').textContent = chars + ' chars · ' + Math.round(performance.now() - started) + ' ms';
      }
    } else {
      const data = await res.json();
      const choice = (data.choices || [])[0] || {};
      asst.content = (choice.message && choice.message.content) || '';
      if (choice.message && choice.message.tool_calls) {
        asst.meta = 'tool_calls: ' + choice.message.tool_calls.map((t) => t.function && t.function.name).filter(Boolean).join(', ');
      }
      const u = data.usage || {};
      chars = asst.content.length;
      $('chatMeta').textContent = chars + ' chars · ' + Math.round(performance.now() - started) + ' ms · ~' +
        (u.prompt_tokens != null ? u.prompt_tokens : '?') + ' in / ~' +
        (u.completion_tokens != null ? u.completion_tokens : '?') + ' out (estimated)';
    }
    asst.streaming = false;
    if (!asst.meta) asst.meta = chars + ' chars · ' + Math.round(performance.now() - started) + ' ms · ' +
      ($('stream').checked ? 'streamed' : 'buffered');
    if (!asst.content) { asst.error = true; asst.content = asst.content || '(empty response)'; }
  } catch (err) {
    asst.streaming = false;
    if (err && err.name === 'AbortError') {
      asst.meta = 'stopped after ' + chars + ' chars';
      if (!asst.content) asst.content = '(stopped)';
    } else {
      asst.error = true;
      asst.content = String((err && err.message) || err);
    }
  } finally {
    controller = null;
    setBusy(false);
    // Drop empty failed turns so the transcript stays usable as context.
    if (asst.error && !chars) conv.messages.splice(idx, 1);
    persist(); renderMsgs(); refreshActivityIfVisible();
  }
}

$('send').addEventListener('click', send);
$('stop').addEventListener('click', () => { if (controller) controller.abort(); });
$('prompt').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (!$('send').disabled) send(); }
});
$('prompt').addEventListener('input', () => {
  const t = $('prompt');
  t.style.height = 'auto';
  t.style.height = Math.min(220, t.scrollHeight) + 'px';
});
['model','think','stream'].forEach((id) => $(id).addEventListener('change', () => {
  set(LS.model, $('model').value); set(LS.think, $('think').value); set(LS.stream, $('stream').checked);
}));
$('apikey').addEventListener('change', () => {
  const k = $('apikey').value.trim();
  if (k) localStorage.setItem(LS.key, k); else localStorage.removeItem(LS.key);
});

/* ── liveness + timers ─────────────────────────────────────────────── */
function refreshActivityIfVisible(){
  if ($('tab-activity').classList.contains('active')) refreshActivity();
}
async function ping(){
  try {
    const res = await fetch('health');
    if (!res.ok) throw new Error(res.status);
    const h = await res.json();
    $('uptime').textContent = fmtUptime(h.uptime_sec);
    const fatal = (h.checks && h.checks.fatal) || [];
    const warns = (h.checks && h.checks.warnings) || [];
    $('statusDot').className = 'dot' + (fatal.length ? ' bad' : (warns.length ? ' warn' : ''));
    $('statusText').textContent = fatal.length ? 'not ready' : (h.ready === false ? 'degraded' : 'running');
  } catch (_) {
    $('statusDot').className = 'dot bad';
    $('statusText').textContent = 'unreachable';
  }
}
$('refreshStatus').addEventListener('click', refreshStatus);
$('refreshAct').addEventListener('click', refreshActivity);
$('actFilter').addEventListener('change', refreshActivity);

renderStatic();
renderConvs();
renderMsgs();
ping();
setInterval(ping, 15000);
setInterval(() => { if ($('autoStatus').checked && $('tab-status').classList.contains('active')) refreshStatus(); }, 10000);
setInterval(() => { if ($('autoAct').checked && $('tab-activity').classList.contains('active')) refreshActivity(); }, 5000);
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
