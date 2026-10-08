"""Self-contained dashboard served at ``GET /``.

Three things live here:

* **Status** — what the backend is doing right now: configuration, health
  checks, live metrics, latency distribution and per-model breakdown.
* **Chat** — talk to the models directly, with conversations kept in the
  browser so there is history to come back to.
* **Activity** — the most recent requests the server handled.

Everything is inlined: no CDN, no build step, no external requests. The page
works offline and inside an air-gapped container, which is a tested property.

The visual system is a Liquid Glass-inspired material language defined once
in the ``:root`` design tokens: layered translucent surfaces with backdrop
blur and saturation, hairline borders with a top edge highlight, soft ambient
shadows, a floating glass top bar and tab bar (with a gliding active pill),
tinted semantic pills, and quiet motion (150–450ms, spring-like for interactive
elements). Light and dark schemes are first-class, ``prefers-reduced-motion``
is respected, and every floating surface shares the same tokens so the console
reads as one product rather than a collection of styled parts.

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
<meta name="description" content="gemini-web2api console — chat, accounts and request activity for the Gemini Web → OpenAI-compatible API gateway.">
<meta name="robots" content="noindex">
<meta name="theme-color" content="#f2f3f5" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0d0f12" media="(prefers-color-scheme: dark)">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0' stop-color='%234285f4'/%3E%3Cstop offset='.5' stop-color='%239b72cb'/%3E%3Cstop offset='1' stop-color='%23d96570'/%3E%3C/defs%3E%3Cpath fill='url(%23g)' d='M16 2l3.1 8.4a8 8 0 005 5L32.5 18l-8.4 3.1a8 8 0 00-5 5L16 34l-3.1-8.4a8 8 0 00-5-5L-.5 18l8.4-3.1a8 8 0 005-5z' transform='translate(0 -2) scale(1)'/%3E%3C/svg%3E">
<style>
/* ═══════════════════════════════════════════════════════════════════
   Design tokens — a Liquid Glass material system.
   Layers: bg (0) → content (1) → cards/panels (2) → nav (3) →
   floating controls (4) → dialogs (5). Each layer gets its own
   opacity, blur, shadow and hairline so depth reads spatially.
   ═══════════════════════════════════════════════════════════════════ */
:root{
  color-scheme:light dark;

  /* ── palette ── */
  --bg:#f2f3f5; --fg:#1d2129; --muted:#5b6472; --faint:#8b93a1;
  --accent:#3e63dd; --accent-fill:#3e63dd; --accent-fg:#ffffff;
  --ok:#1f7a4d; --warn:#9a6a00; --bad:#c0363f;

  /* ── glass surfaces, by elevation ── */
  --surface-1:rgba(255,255,255,.55);   /* secondary glass: cards, tables */
  --surface-2:rgba(255,255,255,.72);   /* primary glass: nav, panels    */
  --surface-3:rgba(255,255,255,.9);    /* floating glass: glider, tile  */
  --fill-1:rgba(28,39,60,.045);        /* quiet fills: hover, bars      */
  --fill-2:rgba(28,39,60,.085);
  --field:rgba(18,28,48,.04);          /* input fields                  */
  --pre-bg:rgba(15,23,42,.045);         /* code blocks                   */

  /* ── hairlines & edge light ── */
  --line:rgba(25,35,55,.09);
  --line-strong:rgba(25,35,55,.17);
  --highlight:rgba(255,255,255,.85);

  /* ── semantic tints (text on tinted glass stays fully opaque) ── */
  --acc-bg:rgba(62,99,221,.10);  --acc-line:rgba(62,99,221,.30);  --acc-ring:rgba(62,99,221,.22);
  --ok-bg:rgba(31,122,77,.10);   --ok-line:rgba(31,122,77,.32);
  --warn-bg:rgba(154,106,0,.11); --warn-line:rgba(154,106,0,.34);
  --bad-bg:rgba(192,54,63,.09);  --bad-line:rgba(192,54,63,.32);
  --neu-bg:rgba(96,106,122,.10); --neu-line:rgba(96,106,122,.24);
  --user-bg:rgba(62,99,221,.09); --user-line:rgba(62,99,221,.24);

  /* ── material ── */
  --blur-sm:10px; --blur-md:18px; --blur-lg:30px;
  --r-sm:9px; --r-md:14px; --r-lg:20px; --r-xl:28px; --r-full:999px;
  --shadow-1:0 1px 2px rgba(15,23,42,.04),0 4px 14px rgba(15,23,42,.05);
  --shadow-2:0 1px 3px rgba(15,23,42,.05),0 12px 30px rgba(15,23,42,.09);
  --shadow-3:0 2px 6px rgba(15,23,42,.06),0 22px 52px rgba(15,23,42,.15);

  /* ── motion: fast in response, smooth in motion ── */
  --t-fast:150ms; --t-med:280ms; --t-slow:450ms;
  --ease-out:cubic-bezier(.22,.61,.21,1);
  --ease-spring:cubic-bezier(.3,1.35,.45,1);
  --ease-smooth:cubic-bezier(.4,0,.2,1);

  --sans:-apple-system,BlinkMacSystemFont,"SF Pro Display","SF Pro Text","Inter",system-ui,"Segoe UI",Roboto,sans-serif;
  --mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#0d0f12; --fg:#e7eaef; --muted:#98a2b3; --faint:#626d7d;
    --accent:#8fabff; --accent-fill:#4a72e8; --accent-fg:#ffffff;
    --ok:#4cc27e; --warn:#e0a83e; --bad:#ff7b72;

    --surface-1:rgba(255,255,255,.045);
    --surface-2:rgba(255,255,255,.07);
    --surface-3:rgba(255,255,255,.11);
    --fill-1:rgba(255,255,255,.05);
    --fill-2:rgba(255,255,255,.09);
    --field:rgba(255,255,255,.055);
    --pre-bg:rgba(0,0,0,.32);

    --line:rgba(255,255,255,.09);
    --line-strong:rgba(255,255,255,.18);
    --highlight:rgba(255,255,255,.16);

    --acc-bg:rgba(122,156,255,.13);  --acc-line:rgba(122,156,255,.34);  --acc-ring:rgba(122,156,255,.30);
    --ok-bg:rgba(76,194,126,.12);   --ok-line:rgba(76,194,126,.34);
    --warn-bg:rgba(224,168,62,.12); --warn-line:rgba(224,168,62,.34);
    --bad-bg:rgba(255,123,114,.11); --bad-line:rgba(255,123,114,.34);
    --neu-bg:rgba(150,160,175,.13); --neu-line:rgba(150,160,175,.28);
    --user-bg:rgba(122,156,255,.11); --user-line:rgba(122,156,255,.28);

    --shadow-1:0 1px 2px rgba(0,0,0,.35),0 4px 14px rgba(0,0,0,.28);
    --shadow-2:0 2px 4px rgba(0,0,0,.40),0 14px 34px rgba(0,0,0,.45);
    --shadow-3:0 4px 10px rgba(0,0,0,.45),0 26px 60px rgba(0,0,0,.55);
  }
}
/* Opaque fallback for engines without backdrop-filter. */
@supports not ((backdrop-filter:blur(4px)) or (-webkit-backdrop-filter:blur(4px))){
  :root{
    --surface-1:rgba(250,250,252,.96); --surface-2:rgba(252,252,254,.98);
    --surface-3:rgba(255,255,255,1);   --field:rgba(18,28,48,.06);
  }
}
@media (prefers-color-scheme:dark){
  @supports not ((backdrop-filter:blur(4px)) or (-webkit-backdrop-filter:blur(4px))){
    :root{
      --surface-1:rgba(24,27,33,.96); --surface-2:rgba(20,23,28,.98);
      --surface-3:rgba(28,31,37,1);   --field:rgba(255,255,255,.08);
    }
  }
}

/* ── glass materials (reused by every floating surface) ── */
.glass-1,.card,table{
  background:var(--surface-1);
  -webkit-backdrop-filter:blur(var(--blur-md)) saturate(160%);
  backdrop-filter:blur(var(--blur-md)) saturate(160%);
  border:1px solid var(--line);
  box-shadow:var(--shadow-1), inset 0 1px 0 var(--highlight);
}
.glass-2,.side,.main{
  background:var(--surface-2);
  -webkit-backdrop-filter:blur(var(--blur-lg)) saturate(170%);
  backdrop-filter:blur(var(--blur-lg)) saturate(170%);
  border:1px solid var(--line);
  box-shadow:var(--shadow-2), inset 0 1px 0 var(--highlight);
}
.glass-3{
  background:var(--surface-3);
  -webkit-backdrop-filter:blur(var(--blur-lg)) saturate(180%);
  backdrop-filter:blur(var(--blur-lg)) saturate(180%);
  border:1px solid var(--line);
  box-shadow:var(--shadow-3), inset 0 1px 0 var(--highlight);
}

/* ── base ── */
*{box-sizing:border-box}
html,body{height:100%}
body{
  margin:0; color:var(--fg);
  font:15px/1.6 var(--sans);
  background:var(--bg);
  display:flex; flex-direction:column;
  -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility;
}
/* Ambient color fields: nearly invisible until glass moves over them. */
body::before{
  content:""; position:fixed; inset:0; z-index:-2; pointer-events:none;
  background:
    radial-gradient(1100px 720px at 88% -12%, rgba(62,99,221,.09), transparent 62%),
    radial-gradient(900px 640px at -12% 18%, rgba(148,102,222,.07), transparent 60%),
    radial-gradient(1300px 820px at 50% 118%, rgba(38,150,110,.07), transparent 62%),
    var(--bg);
}
/* Fine grain so the soft gradients never band. */
body::after{
  content:""; position:fixed; inset:0; z-index:-1; pointer-events:none; opacity:.05;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3C/filter%3E%3Crect width='180' height='180' filter='url(%23n)'/%3E%3C/svg%3E");
}
@media (prefers-color-scheme:dark){
  body::before{
    background:
      radial-gradient(1100px 720px at 88% -12%, rgba(88,118,255,.10), transparent 62%),
      radial-gradient(900px 640px at -12% 18%, rgba(136,98,220,.08), transparent 60%),
      radial-gradient(1300px 820px at 50% 118%, rgba(40,150,112,.07), transparent 62%),
      var(--bg);
  }
  body::after{opacity:.06}
}
::selection{background:var(--acc-ring)}

.wrap{max-width:1180px;margin:0 auto;padding:18px 20px 0;width:100%;flex:1;
  display:flex;flex-direction:column;min-height:0}
.spacer{flex:1}

/* ── floating top bar ── */
header.topbar{
  position:sticky; top:10px; z-index:40;
  display:flex; align-items:center; gap:14px; flex-wrap:wrap;
  padding:10px 14px; margin-bottom:14px;
  border-radius:var(--r-lg);
  transition:box-shadow var(--t-med) var(--ease-out);
}
header.topbar.scrolled{box-shadow:var(--shadow-3), inset 0 1px 0 var(--highlight)}
.brand{display:flex;align-items:center;gap:11px;min-width:0}
.logo-tile{
  width:40px;height:40px;flex:0 0 40px;border-radius:12px;
  display:grid;place-items:center;
  background:var(--surface-3);
  border:1px solid var(--line);
  box-shadow:var(--shadow-1), inset 0 1px 0 var(--highlight);
}
.logo{width:24px;height:24px;display:block}
h1{font-size:16.5px;margin:0;font-weight:650;letter-spacing:-.02em;line-height:1.2}
.sub{color:var(--muted);font-size:12.5px;margin:1px 0 0}
.topbar-pills{display:flex;gap:8px;flex-wrap:wrap}
.pill{
  display:inline-flex;align-items:center;gap:7px;
  padding:5px 11px;border-radius:var(--r-full);
  font-size:12px;font-weight:600;color:var(--muted);
  background:var(--fill-1);border:1px solid var(--line);
  font-variant-numeric:tabular-nums;white-space:nowrap;
}
.dot{width:8px;height:8px;flex:0 0 8px;border-radius:50%;background:var(--ok);position:relative}
.dot::after{
  content:"";position:absolute;inset:0;border-radius:50%;background:var(--ok);
  animation:dotPulse 2.6s var(--ease-out) infinite;
}
.dot.bad{background:var(--bad)} .dot.bad::after{background:var(--bad)}
.dot.warn{background:var(--warn)} .dot.warn::after{background:var(--warn)}
@keyframes dotPulse{0%{transform:scale(1);opacity:.5}70%,100%{transform:scale(2.5);opacity:0}}

/* ── floating tab bar with a gliding active pill ── */
nav.tabs{
  position:relative; z-index:30; align-self:flex-start; max-width:100%;
  display:flex; gap:2px; padding:4px; margin-bottom:16px;
  border-radius:var(--r-full); white-space:nowrap;
  scrollbar-width:none;
}
nav.tabs::-webkit-scrollbar{display:none}
nav.tabs .glider{
  position:absolute;top:4px;bottom:4px;left:0;width:0;
  border-radius:var(--r-full);
  background:var(--surface-3);
  border:1px solid var(--line-strong);
  box-shadow:var(--shadow-1), inset 0 1px 0 var(--highlight);
  opacity:0;pointer-events:none;
  transition:transform var(--t-med) var(--ease-spring), width var(--t-med) var(--ease-spring);
}
nav.tabs .glider.ready{opacity:1}
nav.tabs button{
  position:relative;z-index:1;appearance:none;background:none;border:0;cursor:pointer;
  font:inherit;font-size:13.5px;font-weight:600;color:var(--muted);
  padding:7px 16px;border-radius:var(--r-full);
  transition:color var(--t-fast) var(--ease-out);
}
nav.tabs button:hover{color:var(--fg)}
nav.tabs button[aria-selected="true"]{color:var(--fg)}

/* ── tab panels: quick fade + rise ── */
section.tab{display:none;flex:1;min-height:0;flex-direction:column;padding-bottom:22px}
section.tab.active{display:flex;animation:panelIn var(--t-med) var(--ease-out)}
@keyframes panelIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}

h2{font-size:11.5px;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);
  margin:26px 0 10px;font-weight:650}
h2:first-child{margin-top:0}

/* ── stat cards ── */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}
.card{
  min-width:0;padding:12px 14px;border-radius:var(--r-md);
  transition:transform var(--t-fast) var(--ease-out),box-shadow var(--t-fast) var(--ease-out),border-color var(--t-fast) var(--ease-out);
}
.card:hover{
  transform:translateY(-2px);border-color:var(--line-strong);
  box-shadow:var(--shadow-2), inset 0 1px 0 var(--highlight);
}
.card .k{font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;font-weight:600}
.card .v{font-size:13.5px;margin-top:3px;font-family:var(--mono);word-break:break-all;
  font-variant-numeric:tabular-nums}
.codecard{padding:14px 16px}
.codecard pre{margin:0;overflow:auto;max-height:340px;font-family:var(--mono);
  font-size:12.5px;line-height:1.65;color:var(--fg)}

/* ── tables: one glass panel, hairline rows, quiet hover ── */
.scroll{overflow:auto;min-height:0;padding:6px;margin:-6px;border-radius:var(--r-lg)}
.scroll.grow{flex:1}
table{width:100%;border-collapse:separate;border-spacing:0;border-radius:var(--r-lg);
  overflow:hidden;font-size:13.5px}
th,td{text-align:left;padding:9px 13px;border-bottom:1px solid var(--line);vertical-align:top}
thead th{
  background:var(--fill-1);
  font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;font-weight:650;color:var(--muted);
}
tbody td{transition:background var(--t-fast) var(--ease-out)}
tbody tr:hover td{background:var(--fill-1)}
tbody tr:last-child td{border-bottom:0}
thead th:first-child{border-top-left-radius:calc(var(--r-lg) - 1px)}
thead th:last-child{border-top-right-radius:calc(var(--r-lg) - 1px)}
tbody tr:last-child td:first-child{border-bottom-left-radius:calc(var(--r-lg) - 1px)}
tbody tr:last-child td:last-child{border-bottom-right-radius:calc(var(--r-lg) - 1px)}
td.code{font-family:var(--mono);font-size:12.5px;color:var(--muted);white-space:nowrap;
  font-variant-numeric:tabular-nums}
td .sub{display:block;margin-top:2px;font-size:12px}

/* ── inline code ── */
code{font-family:var(--mono);font-size:.92em;background:var(--fill-1);
  border:1px solid var(--line);padding:1.5px 6px;border-radius:6px;word-break:break-word}
pre code{background:none;border:0;padding:0;font-size:inherit;border-radius:0}

/* ── tinted pills & tags ── */
.tag{
  display:inline-flex;align-items:center;padding:2px 9px;border-radius:var(--r-full);
  font-size:11px;font-weight:600;white-space:nowrap;
  background:var(--neu-bg);border:1px solid var(--neu-line);color:var(--muted);
}
.tag.ok{color:var(--ok);background:var(--ok-bg);border-color:var(--ok-line)}
.tag.bad{color:var(--bad);background:var(--bad-bg);border-color:var(--bad-line)}
.tag.warn{color:var(--warn);background:var(--warn-bg);border-color:var(--warn-line)}
.tag.acc{color:var(--accent);background:var(--acc-bg);border-color:var(--acc-line)}
.tag.cookie{color:var(--warn);background:var(--warn-bg);border-color:var(--warn-line)}

.copy{
  cursor:pointer;font-family:inherit;font-size:11.5px;font-weight:600;color:var(--muted);
  background:var(--fill-1);border:1px solid var(--line);border-radius:var(--r-full);
  padding:3px 11px;white-space:nowrap;
  transition:color var(--t-fast) var(--ease-out),background var(--t-fast) var(--ease-out),border-color var(--t-fast) var(--ease-out),transform var(--t-fast) var(--ease-out);
}
.copy:hover{color:var(--accent);background:var(--acc-bg);border-color:var(--acc-line)}
.copy:active{transform:scale(.95)}
.copy.done{color:var(--ok);background:var(--ok-bg);border-color:var(--ok-line)}

.note{font-size:12.5px;color:var(--muted);margin:8px 0 0;line-height:1.55}
section.tab > .note{margin-top:12px}
.warnbox{
  background:var(--warn-bg);
  -webkit-backdrop-filter:blur(var(--blur-sm)) saturate(160%);
  backdrop-filter:blur(var(--blur-sm)) saturate(160%);
  border:1px solid var(--warn-line);border-radius:var(--r-md);
  padding:11px 14px;font-size:13px;margin:10px 0;line-height:1.55;
  box-shadow:inset 0 1px 0 var(--highlight);
}

/* ── forms ── */
input,select,textarea{
  font:inherit;font-size:13.5px;color:var(--fg);
  background:var(--field);border:1px solid var(--line);border-radius:var(--r-sm);
  padding:8px 11px;min-width:0;
  transition:border-color var(--t-fast) var(--ease-out),box-shadow var(--t-fast) var(--ease-out);
}
input::placeholder,textarea::placeholder{color:var(--faint)}
input:hover,select:hover,textarea:hover{border-color:var(--line-strong)}
input:focus,select:focus,textarea:focus{
  outline:none;border-color:var(--accent);box-shadow:0 0 0 3.5px var(--acc-ring);
}
select{
  appearance:none;-webkit-appearance:none;cursor:pointer;padding-right:32px;
  background-color:var(--field);
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath d='M1 1l4 4 4-4' fill='none' stroke='%238b93a1' stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 12px center;
}
@media (prefers-color-scheme:dark){
  select{
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath d='M1 1l4 4 4-4' fill='none' stroke='%23626d7d' stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
  }
}
input[type="checkbox"]{
  appearance:auto;accent-color:var(--accent-fill);
  width:15px;height:15px;padding:0;cursor:pointer;flex:0 0 auto;
}
label.chk{display:inline-flex;align-items:center;gap:7px;font-size:13px;font-weight:500;
  color:var(--muted);cursor:pointer;white-space:nowrap}

/* ── buttons ── */
button{font-family:inherit}
button.primary{
  appearance:none;cursor:pointer;
  background:var(--accent-fill);color:var(--accent-fg);
  border:1px solid transparent;border-radius:12px;
  padding:9px 18px;font-size:14px;font-weight:600;letter-spacing:.005em;
  box-shadow:0 1px 2px rgba(15,23,42,.12),0 8px 20px var(--acc-ring),inset 0 1px 0 rgba(255,255,255,.25);
  transition:transform var(--t-fast) var(--ease-out),box-shadow var(--t-fast) var(--ease-out),filter var(--t-fast) var(--ease-out);
}
button.primary:hover{
  filter:brightness(1.07);transform:translateY(-1px);
  box-shadow:0 2px 4px rgba(15,23,42,.14),0 12px 26px var(--acc-ring),inset 0 1px 0 rgba(255,255,255,.25);
}
button.primary:active{
  transform:translateY(0) scale(.98);
  box-shadow:0 1px 2px rgba(15,23,42,.12),0 4px 10px var(--acc-ring),inset 0 1px 0 rgba(255,255,255,.25);
}
button.primary:disabled{opacity:.55;cursor:not-allowed;transform:none;filter:none}
button.ghost{
  appearance:none;cursor:pointer;
  background:var(--fill-1);color:var(--fg);
  border:1px solid var(--line);border-radius:11px;
  padding:8px 14px;font-size:13px;font-weight:500;white-space:nowrap;
  transition:background var(--t-fast) var(--ease-out),border-color var(--t-fast) var(--ease-out),transform var(--t-fast) var(--ease-out);
}
button.ghost:hover{background:var(--fill-2);border-color:var(--line-strong)}
button.ghost:active{transform:scale(.98)}
button.ghost:disabled{opacity:.5;cursor:not-allowed;transform:none}
button:focus-visible,a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
input:focus-visible,select:focus-visible,textarea:focus-visible{outline:none}

.row{display:flex;gap:9px;flex-wrap:wrap;align-items:center}
.toolbar{display:flex;gap:9px;flex-wrap:wrap;align-items:center;margin-bottom:14px}

/* ── scrollbars: thin & translucent ── */
.scroll,.msgs,.list{scrollbar-width:thin;scrollbar-color:var(--fill-2) transparent}
.scroll::-webkit-scrollbar,.msgs::-webkit-scrollbar,.list::-webkit-scrollbar{width:10px;height:10px}
.scroll::-webkit-scrollbar-thumb,.msgs::-webkit-scrollbar-thumb,.list::-webkit-scrollbar-thumb{
  background:var(--fill-2);border-radius:var(--r-full);
  border:3px solid transparent;background-clip:content-box;
}
.scroll::-webkit-scrollbar-track,.msgs::-webkit-scrollbar-track,.list::-webkit-scrollbar-track{background:transparent}

/* ── Chat ── */
.chat{display:flex;gap:14px;flex:1;min-height:0}
.side{width:240px;flex:0 0 240px;min-height:0;border-radius:var(--r-lg);
  display:flex;flex-direction:column;gap:10px;padding:10px}
.side .primary{width:100%}
.side .list{flex:1;min-height:0;overflow:auto;display:flex;flex-direction:column;gap:3px}
.side .note{padding:2px 6px 4px;margin:0}
.conv{
  display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:10px;
  cursor:pointer;border:1px solid transparent;font-size:13px;
  transition:background var(--t-fast) var(--ease-out),border-color var(--t-fast) var(--ease-out);
}
.conv:hover{background:var(--fill-1)}
.conv.sel{background:var(--acc-bg);border-color:var(--acc-line);box-shadow:inset 0 1px 0 var(--highlight)}
.conv .t{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:500}
.conv .n{color:var(--faint);font-size:11px;font-family:var(--mono);font-variant-numeric:tabular-nums}
.conv .x{
  border:0;background:none;color:var(--faint);cursor:pointer;
  font-size:15px;line-height:1;padding:1px 5px;border-radius:6px;visibility:hidden;
  transition:color var(--t-fast) var(--ease-out),background var(--t-fast) var(--ease-out);
}
.conv:hover .x,.conv .x:focus-visible{visibility:visible}
.conv .x:hover{color:var(--bad);background:var(--bad-bg)}
.main{flex:1;min-width:0;min-height:0;border-radius:var(--r-lg);overflow:hidden;
  display:flex;flex-direction:column}
.bar{
  display:flex;gap:9px;align-items:center;flex-wrap:wrap;
  padding:10px 12px;border-bottom:1px solid var(--line);background:var(--fill-1);
}
.bar select{max-width:100%}
#apikey{width:210px;max-width:100%}
.msgs-wrap{position:relative;flex:1;display:flex;flex-direction:column;min-height:0}
.msgs{flex:1;overflow:auto;padding:18px 20px;display:flex;flex-direction:column;gap:16px}
.tobottom{position:absolute;right:20px;bottom:18px;z-index:5;width:40px;height:40px;border-radius:var(--r-full);
  border:1px solid var(--line);cursor:pointer;color:var(--fg);font-size:17px;line-height:1;
  background:var(--surface-3);-webkit-backdrop-filter:blur(var(--blur-md));backdrop-filter:blur(var(--blur-md));
  box-shadow:var(--shadow-2), inset 0 1px 0 var(--highlight);
  opacity:0;pointer-events:none;transform:translateY(6px);
  transition:opacity var(--t-fast) var(--ease-out),transform var(--t-fast) var(--ease-out)}
.tobottom.show{opacity:1;pointer-events:auto;transform:none}
.tobottom:hover{background:var(--surface-3);box-shadow:var(--shadow-3), inset 0 1px 0 var(--highlight)}
.msg{display:flex;gap:12px;max-width:100%}
.msg .who{
  flex:0 0 64px;font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;
  color:var(--faint);padding-top:4px;text-align:right;font-weight:650;
}
.msg.user .who{color:var(--accent)}
.msg.err .who{color:var(--bad)}
.msg .bub{
  flex:1;min-width:0;padding:10px 14px;font-size:14px;line-height:1.6;
  border-radius:16px;border-top-left-radius:6px;
  background:var(--fill-1);border:1px solid var(--line);overflow-wrap:anywhere;
}
.msg.user .bub{background:var(--user-bg);border-color:var(--user-line);border-radius:16px;border-top-right-radius:6px}
.msg.err .bub{background:var(--bad-bg);border-color:var(--bad-line);color:var(--bad)}
.msg .bub pre{
  margin:9px 0;padding:11px 13px;border-radius:var(--r-sm);overflow:auto;
  background:var(--pre-bg);border:1px solid var(--line);
  font-family:var(--mono);font-size:12.5px;line-height:1.55;white-space:pre;
}
.msg .bub code{font-family:var(--mono);font-size:12.5px;background:var(--fill-2);
  border-color:transparent;padding:1.5px 5px;border-radius:5px}
.msg .bub pre code{background:none;border:0;padding:0}
.msg .bub strong{font-weight:650}
.msg .bub .mdh{font-weight:650;font-size:15px;margin:10px 0 4px;letter-spacing:-.01em}
.msg .bub .mdh:first-child{margin-top:0}
.mmeta{font-size:11px;color:var(--faint);margin-top:8px;font-family:var(--mono);
  font-variant-numeric:tabular-nums}
.cursor{
  display:inline-block;width:7px;height:15px;margin-left:2px;border-radius:2px;
  background:var(--accent);vertical-align:text-bottom;
  animation:cursorBlink 1.1s ease-in-out infinite;
}
@keyframes cursorBlink{0%,100%{opacity:1}50%{opacity:.2}}
.empty{margin:auto;text-align:center;color:var(--muted);max-width:440px;padding:28px 20px;
  display:flex;flex-direction:column;align-items:center}
.empty-icon{
  width:56px;height:56px;border-radius:18px;display:grid;place-items:center;
  color:var(--accent);background:var(--surface-3);
  border:1px solid var(--line);
  box-shadow:var(--shadow-1), inset 0 1px 0 var(--highlight);
  margin-bottom:14px;
}
.empty-icon svg{width:26px;height:26px;display:block}
.empty-title{font-size:15.5px;font-weight:650;color:var(--fg);margin:0 0 6px}
.empty p{margin:0;font-size:13.5px;line-height:1.6}
.chips{display:flex;gap:8px;flex-wrap:wrap;justify-content:center;margin-top:16px}
.chip{
  cursor:pointer;font-family:inherit;font-size:12.5px;font-weight:500;color:var(--muted);
  background:var(--fill-1);border:1px solid var(--line);border-radius:var(--r-full);
  padding:6px 13px;
  transition:color var(--t-fast) var(--ease-out),background var(--t-fast) var(--ease-out),border-color var(--t-fast) var(--ease-out),transform var(--t-fast) var(--ease-out);
}
.chip:hover{color:var(--accent);background:var(--acc-bg);border-color:var(--acc-line);transform:translateY(-1px)}
.chip:active{transform:scale(.97)}
.composer{border-top:1px solid var(--line);padding:12px 14px;display:flex;flex-direction:column;gap:10px}
.composer textarea{
  width:100%;min-height:56px;max-height:220px;resize:none;
  font-family:inherit;font-size:14px;line-height:1.55;border-radius:12px;
}

footer{padding:16px 20px 22px;font-size:12.5px;color:var(--faint);text-align:center}
footer a{color:var(--accent);text-decoration:none}
footer a:hover{text-decoration:underline}

/* ── responsive ── */
@media (max-width:900px){
  .grid{grid-template-columns:repeat(auto-fill,minmax(170px,1fr))}
}
@media (max-width:820px){
  .wrap{padding:12px 12px 0}
  header.topbar{top:8px;padding:9px 11px;margin-bottom:12px}
  nav.tabs{align-self:stretch;overflow-x:auto}
  nav.tabs button{flex:0 0 auto;padding:7px 13px}
  .chat{flex-direction:column;gap:10px}
  .side{width:100%;flex:0 0 auto}
  .side .list{flex-direction:row;overflow-x:auto;overflow-y:hidden;max-height:132px;padding-bottom:2px}
  .conv{flex:0 0 auto;max-width:200px}
  .main{min-height:62vh}
  .msg{gap:8px}
  .msg .who{flex-basis:52px;font-size:10px}
}
@media (max-width:520px){
  h1{font-size:15px}
  .brand-text .sub{font-size:11.5px}
  .grid{grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:9px}
  .msgs{padding:14px 12px}
  .msg{flex-direction:column;gap:4px}
  .msg .who{flex-basis:auto;text-align:left;padding-top:0}
  .composer{padding:10px}
  button.primary{padding:9px 14px}
  .topbar-pills{width:100%}
}

/* ── reduced motion: keep state changes, lose the travel ── */
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{
    animation-duration:.01ms !important;
    animation-iteration-count:1 !important;
    transition-duration:.01ms !important;
    scroll-behavior:auto !important;
  }
}
</style>
</head>
<body>
<div class="wrap">
  <header class="topbar glass-2">
    <div class="brand">
      <span class="logo-tile">
        <svg class="logo" viewBox="0 0 32 32" aria-hidden="true">
          <defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stop-color="#4285f4"/><stop offset=".5" stop-color="#9b72cb"/>
            <stop offset="1" stop-color="#d96570"/></linearGradient></defs>
          <path fill="url(#lg)" d="M16 1l3.6 9.8a7.6 7.6 0 004.6 4.6L34 19l-9.8 3.6a7.6 7.6 0 00-4.6 4.6L16 37l-3.6-9.8a7.6 7.6 0 00-4.6-4.6L-2 19l9.8-3.6a7.6 7.6 0 004.6-4.6z" transform="translate(0 -3)"/>
        </svg>
      </span>
      <div class="brand-text">
        <h1>gemini-web2api</h1>
        <p class="sub">Gemini Web &rarr; OpenAI-compatible API</p>
      </div>
    </div>
    <div class="spacer"></div>
    <div class="topbar-pills">
      <span class="pill"><span class="dot" id="statusDot"></span><span id="statusText">checking</span></span>
      <span class="pill" id="verPill">v<span id="version"></span> &middot; <span id="uptime"></span></span>
    </div>
  </header>

  <nav class="tabs glass-2" role="tablist" aria-label="Console sections">
    <span class="glider" aria-hidden="true"></span>
    <button type="button" role="tab" data-tab="chat" aria-selected="true">Chat</button>
    <button type="button" role="tab" data-tab="status" aria-selected="false">Status</button>
    <button type="button" role="tab" data-tab="activity" aria-selected="false">Activity</button>
    <button type="button" role="tab" data-tab="models" aria-selected="false">Models</button>
    <button type="button" role="tab" data-tab="api" aria-selected="false">API</button>
  </nav>

  <!-- ── Chat ─────────────────────────────────────────── -->
  <section class="tab active" id="tab-chat" role="tabpanel">
    <div class="chat">
      <aside class="side">
        <button class="primary" id="newChat" type="button">+ New chat</button>
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
                 aria-label="API key" autocomplete="off">
        </div>
        <div class="msgs-wrap">
          <div class="msgs" id="msgs"></div>
          <button class="tobottom" id="toBottom" type="button" aria-label="Scroll to latest message"
                  title="Scroll to latest message">↓</button>
        </div>
        <div class="composer">
          <textarea id="prompt" placeholder="Ask something…  (Enter to send, Shift+Enter for a new line)"
                    aria-label="Message"></textarea>
          <div class="row">
            <button class="primary" id="send" type="button">Send</button>
            <button class="ghost" id="stop" type="button" style="display:none">Stop</button>
            <button class="ghost" id="clearConv" type="button">Clear this chat</button>
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
    <div class="toolbar">
      <button class="ghost" id="refreshStatus" type="button">Refresh</button>
      <label class="chk"><input type="checkbox" id="autoStatus" checked> auto-refresh (10s)</label>
      <div class="spacer"></div>
      <span class="note" id="statusMeta"></span>
    </div>
    <div id="statusWarn"></div>
    <h2>Runtime</h2>
    <div class="grid" id="runtime"></div>
    <h2>Health checks</h2>
    <div id="checks"></div>
    <h2>Accounts</h2>
    <div id="accountsNote"></div>
    <div class="scroll"><table>
      <thead><tr><th>Account</th><th>Google index</th><th>SAPISID</th><th>Uses</th><th>State</th></tr></thead>
      <tbody id="accounts"></tbody>
    </table></div>
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
    <div class="toolbar">
      <button class="ghost" id="refreshAct" type="button">Refresh</button>
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
    <div class="scroll grow"><table>
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
    <div class="card codecard"><pre id="curlBox"></pre></div>
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

/* ── the cookie pool ───────────────────────────────────────────────────
 *
 * Two pure functions rather than inline template code in refreshStatus, so the
 * Node harness can execute them: the account *source* is a path the operator
 * chose and the last error is a string from upstream, and both end up in
 * innerHTML. Anything that reaches innerHTML from outside this file is escaped,
 * and this is the only way to prove it rather than assert it.
 * ────────────────────────────────────────────────────────────────────────── */
function accountsNote(credentials){
  const c = credentials || {};
  const size = Number(c.size) || 0;
  if (!size) {
    return '<p class="note">No cookie file configured &mdash; requests go to Gemini ' +
           'anonymously. Add <code>cookie_file</code> or <code>cookie_files</code> ' +
           'to use an account.</p>';
  }
  const parts = [
    size + (size === 1 ? ' account' : ' accounts'),
    (Number(c.available) || 0) + ' available',
  ];
  parts.push(c.rotating ? 'rotation enabled' : 'no rotation (one account)');
  if (c.rotating && c.cooldown_sec != null) parts.push('cooldown ' + c.cooldown_sec + 's');
  // Escaped per part, then joined with an entity. Escaping the joined string
  // would turn the separator itself into `&amp;middot;`, and un-doing that with
  // a replace afterwards is how a helper quietly becomes wrong.
  return '<p class="note">' + parts.map((p) => esc(p)).join(' &middot; ') + '</p>';
}
function accountRows(credentials){
  const c = credentials || {};
  const entries = c.entries || [];
  if (!entries.length) {
    return '<tr><td colspan="5" class="note">No accounts configured.</td></tr>';
  }
  return entries.map((a) => {
    const cooling = !a.usable;
    const secs = Number(a.cooldown_remaining_sec) || 0;
    // `pill` escapes its own text, so the error string is passed through raw —
    // escaping it here as well would render "&" as "&amp;amp;".
    const state = cooling
      ? pill('cooling ' + (secs > 0 ? Math.ceil(secs) + 's' : '') +
             (a.last_error ? ' (HTTP ' + a.last_error + ')' : ''), 'warn')
      : pill('ready', 'ok');
    return '<tr><td><code>' + esc(a.source) + '</code></td>' +
      '<td>' + esc(a.auth_user == null ? '\u2014' : a.auth_user) + '</td>' +
      '<td>' + (a.has_sapisid ? 'yes' : '<span class="note">no</span>') + '</td>' +
      '<td>' + esc(Number(a.uses) || 0) + '</td>' +
      '<td>' + state + '</td></tr>';
  }).join('');
}

/* ── minimal, safe markdown: escape first, then format ─────────────── */
function inlineMd(t){
  let s = esc(t);
  s = s.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|\n)#{1,4}\s+([^\n]+)/g, '$1<div class="mdh">$2</div>');
  return s.replace(/\n/g, '<br>');
}
function renderMd(src){
  // Coerced like esc() does, rather than relying on the call site's `|| ''`.
  // `re.exec(src)` would coerce implicitly, but `src.slice()` below throws on a
  // non-string, so a null `content` — which the server legitimately emits for an
  // empty reply — would break the Chat tab exactly when the user most needs to
  // see "(empty response)".
  src = String(src ?? '');
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
/* The floating tab bar's active pill glides between tabs instead of
 * snapping: measure the selected button, translate the glider to it. */
function moveGlider(instant){
  const nav = document.querySelector('nav.tabs');
  const glider = nav && nav.querySelector('.glider');
  const btn = nav && nav.querySelector('button[aria-selected="true"]');
  if (!glider || !btn) return;
  if (instant) glider.style.transition = 'none';
  glider.style.width = btn.offsetWidth + 'px';
  glider.style.transform = 'translateX(' + btn.offsetLeft + 'px)';
  if (instant) { void glider.offsetWidth; glider.style.transition = ''; }
  glider.classList.add('ready');
}
function selectTab(name){
  document.querySelectorAll('nav.tabs button').forEach((b) =>
    b.setAttribute('aria-selected', String(b.dataset.tab === name)));
  document.querySelectorAll('section.tab').forEach((s) =>
    s.classList.toggle('active', s.id === 'tab-' + name));
  moveGlider(false);
  if (name === 'status') refreshStatus();
  if (name === 'activity') refreshActivity();
  if (name === 'chat') $('prompt').focus();
}
window.addEventListener('resize', () => moveGlider(true));

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
  btn.classList.add('done');
  setTimeout(() => { btn.textContent = old; btn.classList.remove('done'); }, 1100);
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

    $('accountsNote').innerHTML = accountsNote(s.credentials);
    $('accounts').innerHTML = accountRows(s.credentials);

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
    box.innerHTML = '<div class="empty">' +
      '<div class="empty-icon"><svg viewBox="0 0 24 24" aria-hidden="true">' +
      '<path fill="currentColor" d="M12 2C12.6 6.8 14.2 8.4 19 9 14.2 9.6 12.6 11.2 12 16 11.4 11.2 9.8 9.6 5 9 9.8 8.4 11.4 6.8 12 2Z"/></svg></div>' +
      '<p class="empty-title">Start a conversation</p>' +
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
const topbar = document.querySelector('header.topbar');
const onScroll = () => topbar && topbar.classList.toggle('scrolled', window.scrollY > 6);
window.addEventListener('scroll', onScroll, {passive: true});
onScroll();
$('refreshAct').addEventListener('click', refreshActivity);
$('actFilter').addEventListener('change', refreshActivity);

renderStatic();
renderConvs();
renderMsgs();
ping();
requestAnimationFrame(() => moveGlider(true));
window.addEventListener('load', () => moveGlider(true));
setInterval(ping, 15000);
setInterval(() => { if ($('autoStatus').checked && $('tab-status').classList.contains('active')) refreshStatus(); }, 10000);
setInterval(() => { if ($('autoAct').checked && $('tab-activity').classList.contains('active')) refreshActivity(); }, 5000);

/* ── scroll-to-bottom affordance ───────────────────────────────────────
   Long transcripts make the newest message easy to lose; the button appears
   only when the viewport is not already at the bottom. */
(function () {
  const box = $('msgs'), btn = $('toBottom');
  if (!box || !btn) return;
  const update = () => {
    const distance = box.scrollHeight - box.scrollTop - box.clientHeight;
    btn.classList.toggle('show', distance > 80);
  };
  box.addEventListener('scroll', update, {passive: true});
  btn.addEventListener('click', () => { box.scrollTop = box.scrollHeight; update(); });
  // Content changes (render, streaming chunks) do not always fire scroll.
  if (window.MutationObserver) {
    new MutationObserver(update).observe(box, {childList: true, subtree: true});
  }
  update();
})();
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
