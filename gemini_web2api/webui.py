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
<meta name="theme-color" content="#eaecef" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0a0c11" media="(prefers-color-scheme: dark)">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0' stop-color='%234285f4'/%3E%3Cstop offset='.5' stop-color='%239b72cb'/%3E%3Cstop offset='1' stop-color='%23d96570'/%3E%3C/linearGradient%3E%3C/defs%3E%3Cpath fill='url(%23g)' d='M16 2C17 10.2 21.8 15 30 16C21.8 17 17 21.8 16 30C15 21.8 10.2 17 2 16C10.2 15 15 10.2 16 2Z'/%3E%3C/svg%3E">
<style>
/* ═══════════════════════════════════════════════════════════════════════════
   gemini-web2api console — Liquid Glass design system
   ───────────────────────────────────────────────────────────────────────────
   1. Tokens     : one place for colour, material, depth, space, motion
   2. Base       : reset, typography, canvas
   3. Materials  : glass-1 / glass-2 / glass-3 (+ tint), edge light, shadows
   4. Chrome     : masthead, floating dock, tabs, glider, segmented control
   5. Controls   : buttons, icon buttons, fields, switches, tags, tooltips
   6. Surfaces   : panels, cards, lists, tables, charts
   7. Overlays   : dialogs, command palette, toasts
   8. States     : skeletons, empty states, progress, reduced motion
   Layer order (z): canvas 0 · content 1 · panels 2 · dock 30 · popovers 40 ·
                    dialogs 60 · toasts 70.
   ═══════════════════════════════════════════════════════════════════════════ */

/* ── 1. Tokens ───────────────────────────────────────────────────────────── */
:root{
  color-scheme:light;

  /* Palette — light. Ink scale drives text; tints carry state. */
  --bg:#eaecef;
  --bg-top:#f1f2f6;
  --bg-bottom:#e5e8ee;
  --fg:#14181f;
  --fg-2:#525b69;
  --fg-3:#5f6875;
  --accent:#3355cf;
  --accent-fill:#3557d3;
  --accent-fg:#ffffff;
  --accent-soft:rgba(51,85,207,.11);
  --ok:#156d46;
  --ok-fill:#17764c;
  --warn:#7d5400;
  --warn-fill:#8a5e00;
  --bad:#b02934;
  --bad-fill:#b62d38;
  --info:#1b5c86;

  /* Glass — one material system, four strengths. */
  --glass-1:rgba(255,255,255,.58);          /* panels, cards, tables        */
  --glass-2:rgba(255,255,255,.72);          /* dock, sidebars, bars         */
  --glass-3:rgba(255,255,255,.86);          /* menus, dialogs, toasts       */
  --glass-4:rgba(255,255,255,.94);          /* glider, tooltips, highest    */
  --glass-2-stuck:rgba(255,255,255,.82);    /* dock after content scrolls   */
  --fill-1:rgba(20,29,48,.045);             /* quiet fills: hover, bars     */
  --fill-2:rgba(20,29,48,.085);
  --fill-3:rgba(20,29,48,.14);
  --field:rgba(17,26,44,.045);              /* input wells                  */
  --field-focus:rgba(255,255,255,.75);
  --code-bg:rgba(16,24,42,.05);
  --scrim:rgba(19,23,32,.32);               /* behind dialogs               */

  /* Hairlines and edge light */
  --line-1:rgba(18,26,43,.10);
  --line-2:rgba(18,26,43,.18);
  --edge:rgba(255,255,255,.9);
  --edge-2:rgba(255,255,255,.55);

  /* Tints — text stays fully opaque on tinted glass */
  --acc-bg:rgba(51,85,207,.10);   --acc-line:rgba(51,85,207,.26);   --acc-ring:rgba(51,85,207,.20);
  --ok-bg:rgba(21,109,70,.10);    --ok-line:rgba(21,109,70,.26);
  --warn-bg:rgba(138,94,0,.11);   --warn-line:rgba(138,94,0,.28);
  --bad-bg:rgba(176,41,52,.09);   --bad-line:rgba(176,41,52,.26);
  --neu-bg:rgba(82,91,105,.10);   --neu-line:rgba(82,91,105,.20);

  /* Depth — ambient, never dramatic */
  --sh-1:0 1px 1.5px rgba(16,22,38,.045), 0 3px 10px rgba(16,22,38,.045);
  --sh-2:0 1px 2px rgba(16,22,38,.05),  0 8px 22px rgba(16,22,38,.07);
  --sh-3:0 1px 2px rgba(16,22,38,.05),  0 14px 34px rgba(16,22,38,.11);
  --sh-4:0 8px 20px rgba(16,22,38,.09), 0 32px 76px rgba(16,22,38,.20);
  --sh-inset:inset 0 1px 0 var(--edge);
  --sh-press:inset 0 1px 2px rgba(16,22,38,.10);

  /* Blur */
  --blur-1:9px; --blur-2:16px; --blur-3:24px; --blur-4:34px; --blur-veil:11px;

  /* Radii */
  --r-1:7px; --r-2:11px; --r-3:15px; --r-4:20px; --r-5:26px; --r-full:999px;

  /* Space */
  --sp-1:4px; --sp-2:8px; --sp-3:12px; --sp-4:16px; --sp-5:20px; --sp-6:24px;
  --sp-7:32px; --sp-8:44px; --sp-9:60px;

  /* Type */
  --fs-micro:11px; --fs-meta:12.5px; --fs-sm:13.5px; --fs-body:15px;
  --fs-lead:16.5px; --fs-h3:18px; --fs-h2:22px; --fs-h1:clamp(26px,2.4vw,31px);
  --track-tight:-.021em; --track-wide:.075em;
  --lh-tight:1.24; --lh-body:1.62;

  /* Motion — fast in response, smooth in motion */
  --d-1:130ms; --d-2:190ms; --d-3:280ms; --d-4:440ms;
  --e-out:cubic-bezier(.22,.61,.21,1);
  --e-inout:cubic-bezier(.4,0,.2,1);
  --e-spring:cubic-bezier(.34,1.3,.42,1);
  --e-enter:cubic-bezier(.16,.84,.3,1);

  /* Z */
  --z-canvas:-1; --z-panel:2; --z-dock:30; --z-pop:40; --z-dialog:60; --z-toast:70;

  --sans:-apple-system,BlinkMacSystemFont,"SF Pro Display","SF Pro Text","Inter",system-ui,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
  --mono:ui-monospace,"SF Mono",SFMono-Regular,"JetBrains Mono",Menlo,Consolas,monospace;
  --maxw:1220px;
}

/* Palette — dark. Kept in sync with the media block below. */
:root[data-theme="dark"]{
  color-scheme:dark;
  --bg:#0a0c11;
  --bg-top:#101319;
  --bg-bottom:#080a0e;
  --fg:#e9ecf2; --fg-2:#a5aebd; --fg-3:#949ead;
  --accent:#93aaff; --accent-fill:#3f63e0; --accent-fg:#ffffff;
  --accent-soft:rgba(147,170,255,.14);
  --ok:#4fcb8d; --ok-fill:#2f9d68; --warn:#e6b258; --warn-fill:#a5761a;
  --bad:#ff8b83; --bad-fill:#c0392f; --info:#7cc4ea;

  --glass-1:rgba(255,255,255,.048);
  --glass-2:rgba(255,255,255,.068);
  --glass-3:rgba(34,38,47,.72);
  --glass-4:rgba(42,47,58,.86);
  --glass-2-stuck:rgba(26,29,36,.82);
  --fill-1:rgba(255,255,255,.05);
  --fill-2:rgba(255,255,255,.09);
  --fill-3:rgba(255,255,255,.15);
  --field:rgba(255,255,255,.055);
  --field-focus:rgba(255,255,255,.09);
  --code-bg:rgba(0,0,0,.34);
  --scrim:rgba(4,6,10,.58);

  --line-1:rgba(255,255,255,.10);
  --line-2:rgba(255,255,255,.18);
  --edge:rgba(255,255,255,.14);
  --edge-2:rgba(255,255,255,.07);

  --acc-bg:rgba(147,170,255,.13); --acc-line:rgba(147,170,255,.30); --acc-ring:rgba(147,170,255,.26);
  --ok-bg:rgba(79,203,141,.12);   --ok-line:rgba(79,203,141,.30);
  --warn-bg:rgba(230,178,88,.12); --warn-line:rgba(230,178,88,.30);
  --bad-bg:rgba(255,139,131,.11); --bad-line:rgba(255,139,131,.30);
  --neu-bg:rgba(160,170,190,.12); --neu-line:rgba(160,170,190,.24);

  --sh-1:0 1px 1.5px rgba(0,0,0,.4), 0 3px 10px rgba(0,0,0,.26);
  --sh-2:0 1px 2px rgba(0,0,0,.45), 0 10px 26px rgba(0,0,0,.34);
  --sh-3:0 2px 4px rgba(0,0,0,.5), 0 16px 38px rgba(0,0,0,.46);
  --sh-4:0 10px 26px rgba(0,0,0,.5), 0 36px 84px rgba(0,0,0,.6);
  --sh-inset:inset 0 1px 0 var(--edge);
  --sh-press:inset 0 1px 2px rgba(0,0,0,.5);
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    color-scheme:dark;
    --bg:#0a0c11;
    --bg-top:#101319;
    --bg-bottom:#080a0e;
    --fg:#e9ecf2; --fg-2:#a5aebd; --fg-3:#949ead;
    --accent:#93aaff; --accent-fill:#3f63e0; --accent-fg:#ffffff;
    --accent-soft:rgba(147,170,255,.14);
    --ok:#4fcb8d; --ok-fill:#2f9d68; --warn:#e6b258; --warn-fill:#a5761a;
    --bad:#ff8b83; --bad-fill:#c0392f; --info:#7cc4ea;

    --glass-1:rgba(255,255,255,.048);
    --glass-2:rgba(255,255,255,.068);
    --glass-3:rgba(34,38,47,.72);
    --glass-4:rgba(42,47,58,.86);
    --glass-2-stuck:rgba(26,29,36,.82);
    --fill-1:rgba(255,255,255,.05);
    --fill-2:rgba(255,255,255,.09);
    --fill-3:rgba(255,255,255,.15);
    --field:rgba(255,255,255,.055);
    --field-focus:rgba(255,255,255,.09);
    --code-bg:rgba(0,0,0,.34);
    --scrim:rgba(4,6,10,.58);

    --line-1:rgba(255,255,255,.10);
    --line-2:rgba(255,255,255,.18);
    --edge:rgba(255,255,255,.14);
    --edge-2:rgba(255,255,255,.07);

    --acc-bg:rgba(147,170,255,.13); --acc-line:rgba(147,170,255,.30); --acc-ring:rgba(147,170,255,.26);
    --ok-bg:rgba(79,203,141,.12);   --ok-line:rgba(79,203,141,.30);
    --warn-bg:rgba(230,178,88,.12); --warn-line:rgba(230,178,88,.30);
    --bad-bg:rgba(255,139,131,.11); --bad-line:rgba(255,139,131,.30);
    --neu-bg:rgba(160,170,190,.12); --neu-line:rgba(160,170,190,.24);

    --sh-1:0 1px 1.5px rgba(0,0,0,.4), 0 3px 10px rgba(0,0,0,.26);
    --sh-2:0 1px 2px rgba(0,0,0,.45), 0 10px 26px rgba(0,0,0,.34);
    --sh-3:0 2px 4px rgba(0,0,0,.5), 0 16px 38px rgba(0,0,0,.46);
    --sh-4:0 10px 26px rgba(0,0,0,.5), 0 36px 84px rgba(0,0,0,.6);
    --sh-inset:inset 0 1px 0 var(--edge);
    --sh-press:inset 0 1px 2px rgba(0,0,0,.5);
  }
}

/* No blur available → opaque-enough surfaces, same geometry and rhythm. */
@supports not ((backdrop-filter:blur(4px)) or (-webkit-backdrop-filter:blur(4px))){
  :root{
    --glass-1:rgba(249,250,252,.94); --glass-2:rgba(251,252,253,.97);
    --glass-3:rgba(253,253,255,.99); --glass-4:rgba(255,255,255,1);
    --glass-2-stuck:rgba(252,253,254,.99);
    --field-focus:#ffffff;
  }
  :root[data-theme="dark"]{
    --glass-1:rgba(23,26,32,.95); --glass-2:rgba(26,29,36,.97);
    --glass-3:rgba(30,34,42,.99); --glass-4:rgba(34,38,47,1);
    --glass-2-stuck:rgba(24,27,33,.99);
    --field-focus:rgba(255,255,255,.10);
  }
}
@media (prefers-color-scheme:dark){
  @supports not ((backdrop-filter:blur(4px)) or (-webkit-backdrop-filter:blur(4px))){
    :root:not([data-theme="light"]){
      --glass-1:rgba(23,26,32,.95); --glass-2:rgba(26,29,36,.97);
      --glass-3:rgba(30,34,42,.99); --glass-4:rgba(34,38,47,1);
      --glass-2-stuck:rgba(24,27,33,.99);
      --field-focus:rgba(255,255,255,.10);
    }
  }
}
/* Users who ask for less transparency get calm, near-solid surfaces. */
@media (prefers-reduced-transparency:reduce){
  :root{
    --glass-1:rgba(249,250,252,.94); --glass-2:rgba(252,253,254,.96);
    --glass-3:rgba(253,253,255,.98); --glass-4:rgba(255,255,255,.99);
    --glass-2-stuck:rgba(252,253,254,.99); --field-focus:#fff;
  }
}

/* ── 2. Base ─────────────────────────────────────────────────────────────── */
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
html,body{min-height:100%}
html{height:100%}
body{
  margin:0;
  /* The shell is exactly one viewport tall and the content pane scrolls inside
     it: the composer, the dock and the masthead never leave the screen, which
     is what makes a long transcript readable. Short viewports still work —
     the pane scrolls instead of clipping. */
  height:100%;overflow:hidden;
  color:var(--fg);
  background:var(--bg);
  font:400 var(--fs-body)/var(--lh-body) var(--sans);
  -webkit-font-smoothing:antialiased;
  -moz-osx-font-smoothing:grayscale;
  text-rendering:optimizeLegibility;
  font-feature-settings:"kern" 1,"liga" 1,"cv11" 1;
  overflow-x:hidden;
}
body.no-scroll{overflow:hidden}
h1,h2,h3,p,figure{margin:0}
button,input,select,textarea{font:inherit;color:inherit}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
svg{flex:none}
::selection{background:var(--acc-bg)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:6px}
input:focus-visible,select:focus-visible,textarea:focus-visible{outline:none}

/* Canvas: ambient colour fields, barely visible until glass moves over them. */
.canvas{
  position:fixed;inset:0;z-index:var(--z-canvas);pointer-events:none;
  background:
    radial-gradient(58% 44% at 82% 2%, rgba(63,99,224,.17), transparent 62%),
    radial-gradient(46% 38% at 6% 14%, rgba(133,96,214,.13), transparent 64%),
    radial-gradient(62% 46% at 44% 106%, rgba(24,150,116,.11), transparent 66%),
    linear-gradient(178deg, var(--bg-top) 0%, var(--bg) 44%, var(--bg-bottom) 100%);
}
.canvas::after{ /* fine grain so the soft fields never band */
  content:"";position:absolute;inset:0;opacity:.032;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.82' numOctaves='2' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3C/filter%3E%3Crect width='200' height='200' filter='url(%23n)'/%3E%3C/svg%3E");
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]) .canvas::after{opacity:.05}
}
:root[data-theme="dark"] .canvas::after{opacity:.05}
:root[data-theme="dark"] .canvas{
  background:
    radial-gradient(58% 44% at 82% 2%, rgba(88,120,255,.16), transparent 62%),
    radial-gradient(46% 38% at 6% 14%, rgba(140,102,226,.13), transparent 64%),
    radial-gradient(62% 46% at 44% 106%, rgba(30,150,116,.12), transparent 66%),
    linear-gradient(178deg, var(--bg-top) 0%, var(--bg) 44%, var(--bg-bottom) 100%);
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]) .canvas{
    background:
      radial-gradient(58% 44% at 82% 2%, rgba(88,120,255,.16), transparent 62%),
      radial-gradient(46% 38% at 6% 14%, rgba(140,102,226,.13), transparent 64%),
      radial-gradient(62% 46% at 44% 106%, rgba(30,150,116,.12), transparent 66%),
      linear-gradient(178deg, var(--bg-top) 0%, var(--bg) 44%, var(--bg-bottom) 100%);
  }
}

/* ── 3. Materials ────────────────────────────────────────────────────────── */
/* One material system: strength rises with elevation. */
.glass-1,.glass-2,.glass-3,.glass-4{position:relative;border:1px solid var(--line-1)}
.glass-1{
  background:var(--glass-1);
  -webkit-backdrop-filter:blur(var(--blur-2)) saturate(158%);
  backdrop-filter:blur(var(--blur-2)) saturate(158%);
  box-shadow:var(--sh-1), var(--sh-inset);
}
.glass-2{
  background:var(--glass-2);
  -webkit-backdrop-filter:blur(var(--blur-3)) saturate(166%);
  backdrop-filter:blur(var(--blur-3)) saturate(166%);
  box-shadow:var(--sh-2), var(--sh-inset);
}
.glass-3{
  background:var(--glass-3);
  -webkit-backdrop-filter:blur(var(--blur-3)) saturate(172%);
  backdrop-filter:blur(var(--blur-3)) saturate(172%);
  box-shadow:var(--sh-3), var(--sh-inset);
}
.glass-4{
  background:var(--glass-4);
  -webkit-backdrop-filter:blur(var(--blur-4)) saturate(178%);
  backdrop-filter:blur(var(--blur-4)) saturate(178%);
  box-shadow:var(--sh-4), var(--sh-inset);
}
/* Tinted glass — for contextual states, never for body text. */
.tint-acc{background:var(--acc-bg);border-color:var(--acc-line)}
.tint-ok{background:var(--ok-bg);border-color:var(--ok-line)}
.tint-warn{background:var(--warn-bg);border-color:var(--warn-line)}
.tint-bad{background:var(--bad-bg);border-color:var(--bad-line)}

/* ── 4. Chrome ───────────────────────────────────────────────────────────── */
.shell{
  width:100%;max-width:var(--maxw);margin:0 auto;
  padding:var(--sp-5) clamp(16px,2.4vw,30px) var(--sp-6);
  display:flex;flex-direction:column;height:100%;min-height:0;
}
.skip{
  position:absolute;left:-9999px;top:0;z-index:var(--z-toast);
  background:var(--glass-4);border:1px solid var(--line-2);border-radius:var(--r-2);
  padding:10px 16px;font-size:var(--fs-sm);font-weight:560;
  -webkit-backdrop-filter:blur(var(--blur-3));backdrop-filter:blur(var(--blur-3));
}
.skip:focus{left:16px;top:16px}

/* Masthead — identity and health, quiet enough to scroll away. */
.masthead{display:flex;align-items:center;gap:var(--sp-4);flex-wrap:wrap;padding:var(--sp-1) 2px var(--sp-5)}
.brand{
  display:flex;align-items:center;gap:var(--sp-3);min-width:0;
  appearance:none;background:none;border:0;padding:0;cursor:pointer;text-align:left;
  border-radius:var(--r-3);
}
.brand:focus-visible{outline:2px solid var(--accent);outline-offset:4px}
.mark{
  width:38px;height:38px;flex:0 0 38px;border-radius:var(--r-2);
  display:grid;place-items:center;position:relative;
  background:var(--glass-3);border:1px solid var(--line-1);
  box-shadow:var(--sh-1), var(--sh-inset);
  transition:transform var(--d-2) var(--e-spring), box-shadow var(--d-2) var(--e-out);
}
.mark svg{width:21px;height:21px;display:block}
.brand:hover .mark{transform:translateY(-1px) scale(1.03);box-shadow:var(--sh-2), var(--sh-inset)}
.brand:active .mark{transform:scale(.97)}
.brand-text{min-width:0}
.brand-text h1{
  font-size:16.5px;font-weight:625;letter-spacing:var(--track-tight);line-height:1.15;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.brand-text p{
  font-size:var(--fs-meta);color:var(--fg-3);margin-top:1px;line-height:1.3;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.masthead-end{display:flex;align-items:center;gap:var(--sp-2);margin-left:auto;flex-wrap:wrap;justify-content:flex-end}

/* Health pill — a dot that breathes, and words for the same fact. */
.health{
  display:inline-flex;align-items:center;gap:8px;
  padding:6px 12px 6px 10px;border-radius:var(--r-full);
  font-size:var(--fs-meta);font-weight:560;color:var(--fg-2);
  background:var(--fill-1);border:1px solid var(--line-1);
  font-variant-numeric:tabular-nums;white-space:nowrap;
  transition:background var(--d-2) var(--e-out),border-color var(--d-2) var(--e-out),color var(--d-2) var(--e-out);
}
.health.is-ok{color:var(--ok);background:var(--ok-bg);border-color:var(--ok-line)}
.health.is-warn{color:var(--warn);background:var(--warn-bg);border-color:var(--warn-line)}
.health.is-bad{color:var(--bad);background:var(--bad-bg);border-color:var(--bad-line)}
.dot{width:8px;height:8px;flex:0 0 8px;border-radius:50%;background:var(--fg-3);position:relative;
  transition:background var(--d-2) var(--e-out)}
.dot.ok{background:var(--ok-fill)} .dot.warn{background:var(--warn-fill)} .dot.bad{background:var(--bad-fill)}
.dot.ok::after,.dot.warn::after,.dot.bad::after{content:"";position:absolute;inset:0;border-radius:50%;background:currentColor}
.dot.ok::after{background:var(--ok-fill);animation:breathe 3s var(--e-out) infinite}
.dot.warn::after{background:var(--warn-fill);animation:breathe 2.2s var(--e-out) infinite}
.dot.bad::after{background:var(--bad-fill);animation:breathe 1.6s var(--e-out) infinite}
@keyframes breathe{0%{transform:scale(1);opacity:.45}70%,100%{transform:scale(2.6);opacity:0}}
.pill--meta{
  display:inline-flex;align-items:center;gap:7px;padding:6px 12px;border-radius:var(--r-full);
  font-size:var(--fs-micro);font-weight:560;color:var(--fg-3);letter-spacing:.01em;
  background:var(--fill-1);border:1px solid var(--line-1);
  font-variant-numeric:tabular-nums;white-space:nowrap;
}

/* Floating dock — navigation that hovers over the content it governs. */
.dock-wrap{position:sticky;top:clamp(8px,1.4vw,16px);z-index:var(--z-dock);margin-bottom:var(--sp-5)}
.dock{
  display:flex;align-items:center;gap:var(--sp-3);
  padding:6px;border-radius:var(--r-full);
  transition:box-shadow var(--d-3) var(--e-out),background-color var(--d-3) var(--e-out),border-color var(--d-3) var(--e-out);
}
.dock.is-stuck{
  background:var(--glass-2-stuck);
  box-shadow:var(--sh-3), var(--sh-inset);
  border-color:var(--line-2);
}
.dock-end{display:flex;align-items:center;gap:var(--sp-2);margin-left:auto;padding-right:4px}

nav.tabs{
  position:relative;display:flex;align-items:center;gap:2px;
  min-width:0;overflow-x:auto;overflow-y:hidden;
  scrollbar-width:none;-ms-overflow-style:none;
  padding:2px;border-radius:var(--r-full);
}
nav.tabs::-webkit-scrollbar{display:none}
nav.tabs .glider{
  position:absolute;left:0;top:2px;bottom:2px;width:0;border-radius:var(--r-full);
  background:var(--glass-4);border:1px solid var(--line-2);
  box-shadow:var(--sh-2), var(--sh-inset);
  opacity:0;pointer-events:none;
  transition:transform var(--d-3) var(--e-spring), width var(--d-3) var(--e-spring), opacity var(--d-2) var(--e-out);
  will-change:transform,width;
}
nav.tabs .glider.ready{opacity:1}
.tab-btn{
  position:relative;z-index:1;display:inline-flex;align-items:center;gap:8px;
  appearance:none;background:none;border:0;cursor:pointer;
  padding:8px 15px;border-radius:var(--r-full);
  font-size:var(--fs-sm);font-weight:560;letter-spacing:-.005em;color:var(--fg-2);
  white-space:nowrap;
  transition:color var(--d-1) var(--e-out),transform var(--d-1) var(--e-out);
}
.tab-btn .ico{width:17px;height:17px;display:block;opacity:.72;transition:opacity var(--d-2) var(--e-out),transform var(--d-2) var(--e-spring)}
.tab-btn:hover{color:var(--fg)}
.tab-btn:hover .ico{opacity:.95}
.tab-btn[aria-selected="true"]{color:var(--fg)}
.tab-btn[aria-selected="true"] .ico{opacity:1;transform:translateY(-.5px) scale(1.04)}
.tab-btn:active{transform:scale(.975)}
.tab-btn:focus-visible{outline-offset:-2px}

/* Segmented control — theme (and any future tri-state). */
.seg{
  display:inline-flex;align-items:center;gap:2px;position:relative;
  padding:3px;border-radius:var(--r-full);
  background:var(--fill-1);border:1px solid var(--line-1);
}
.seg-btn{
  appearance:none;border:0;background:none;cursor:pointer;
  display:inline-flex;align-items:center;justify-content:center;gap:6px;
  min-width:32px;height:28px;padding:0 9px;border-radius:var(--r-full);
  font-size:var(--fs-micro);font-weight:600;color:var(--fg-3);
  transition:color var(--d-2) var(--e-out),background var(--d-2) var(--e-out),box-shadow var(--d-2) var(--e-out),transform var(--d-1) var(--e-out);
}
.seg-btn svg{width:15px;height:15px;display:block}
.seg-btn:hover{color:var(--fg)}
.seg-btn[aria-checked="true"]{
  color:var(--fg);background:var(--glass-4);
  box-shadow:var(--sh-1), var(--sh-inset);
}
.seg-btn:active{transform:scale(.96)}

/* Progress hairline — shows work without moving the layout. */
.hairline{
  position:relative;height:2px;border-radius:var(--r-full);overflow:hidden;
  background:var(--fill-1);opacity:0;transition:opacity var(--d-2) var(--e-out);
}
.hairline.on{opacity:1}
.hairline::after{
  content:"";position:absolute;inset:0;border-radius:inherit;
  background:linear-gradient(90deg,transparent,var(--accent),transparent);
  transform:translateX(-100%);
  animation:sweep 1.15s var(--e-inout) infinite;
}
@keyframes sweep{0%{transform:translateX(-100%)}100%{transform:translateX(100%)}}
/* ── 5. Controls ─────────────────────────────────────────────────────────── */
.btn{
  position:relative;display:inline-flex;align-items:center;justify-content:center;gap:8px;
  appearance:none;cursor:pointer;user-select:none;
  min-height:38px;padding:0 16px;border-radius:var(--r-2);
  border:1px solid transparent;
  font-size:var(--fs-sm);font-weight:580;letter-spacing:-.004em;white-space:nowrap;
  transition:transform var(--d-1) var(--e-out),box-shadow var(--d-2) var(--e-out),
             background-color var(--d-2) var(--e-out),border-color var(--d-2) var(--e-out),
             color var(--d-1) var(--e-out),filter var(--d-2) var(--e-out),opacity var(--d-2) var(--e-out);
}
.btn svg{width:16px;height:16px;display:block}
.btn:active{transform:scale(.978)}
.btn:disabled,.btn[aria-disabled="true"]{opacity:.46;cursor:not-allowed;transform:none;filter:saturate(.5)}
.btn--primary{
  background:var(--accent-fill);color:var(--accent-fg);
  box-shadow:0 1px 1.5px rgba(16,22,38,.16), 0 8px 20px var(--acc-ring), inset 0 1px 0 rgba(255,255,255,.24);
}
.btn--primary:hover:not(:disabled){filter:brightness(1.08);transform:translateY(-1px);
  box-shadow:0 2px 4px rgba(16,22,38,.16), 0 12px 26px var(--acc-ring), inset 0 1px 0 rgba(255,255,255,.24)}
.btn--primary:active:not(:disabled){transform:translateY(0) scale(.978);
  box-shadow:0 1px 2px rgba(16,22,38,.16), 0 3px 10px var(--acc-ring), inset 0 1px 0 rgba(255,255,255,.2)}
.btn--glass{
  background:var(--glass-2);color:var(--fg);border-color:var(--line-1);
  -webkit-backdrop-filter:blur(var(--blur-2)) saturate(150%);
  backdrop-filter:blur(var(--blur-2)) saturate(150%);
  box-shadow:var(--sh-1), var(--sh-inset);
}
.btn--glass:hover:not(:disabled){background:var(--glass-3);border-color:var(--line-2);
  box-shadow:var(--sh-2), var(--sh-inset)}
.btn--glass:active:not(:disabled){box-shadow:var(--sh-1), var(--sh-press)}
.btn--ghost{background:var(--fill-1);color:var(--fg);border-color:var(--line-1)}
.btn--ghost:hover:not(:disabled){background:var(--fill-2);border-color:var(--line-2)}
.btn--quiet{background:transparent;color:var(--fg-2);border-color:transparent;min-height:34px;padding:0 10px}
.btn--quiet:hover:not(:disabled){background:var(--fill-1);color:var(--fg)}
.btn--quiet[aria-pressed="true"],.btn--quiet.is-on{background:var(--acc-bg);color:var(--accent);border-color:var(--acc-line)}
.btn--quiet-danger:hover:not(:disabled){background:var(--bad-bg);color:var(--bad)}
.btn--sm{min-height:32px;padding:0 12px;font-size:var(--fs-meta);border-radius:var(--r-2)}
.btn--block{width:100%}
.btn--icon{width:38px;min-width:38px;padding:0;border-radius:var(--r-2)}
.btn--icon.btn--sm{width:32px;min-width:32px}
.btn .spin{animation:spin 900ms linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
/* Busy slot: the send control morphs into stop without moving a pixel. */
.slot{position:relative;display:inline-flex}
.slot>[hidden]{display:none !important}
.morph{animation:morphIn var(--d-2) var(--e-enter)}
@keyframes morphIn{from{opacity:0;transform:scale(.86)}to{opacity:1;transform:none}}

/* Fields */
.field{display:flex;flex-direction:column;gap:6px;min-width:0}
.field>label,.lbl{
  font-size:var(--fs-micro);font-weight:600;color:var(--fg-3);
  text-transform:uppercase;letter-spacing:var(--track-wide);
}
input[type="text"],input[type="password"],input[type="search"],input[type="number"],select,textarea{
  width:100%;min-width:0;
  background:var(--field);color:var(--fg);
  border:1px solid var(--line-1);border-radius:var(--r-2);
  padding:9px 12px;font-size:var(--fs-sm);
  transition:border-color var(--d-2) var(--e-out),box-shadow var(--d-2) var(--e-out),
             background-color var(--d-2) var(--e-out);
}
input::placeholder,textarea::placeholder{color:var(--fg-3)}
input:hover:not(:disabled),select:hover:not(:disabled),textarea:hover:not(:disabled){border-color:var(--line-2)}
input:focus,select:focus,textarea:focus{
  outline:none;border-color:var(--accent);background:var(--field-focus);
  box-shadow:0 0 0 3.5px var(--acc-ring);
}
input:disabled,select:disabled,textarea:disabled{opacity:.55;cursor:not-allowed}
input[aria-invalid="true"]{border-color:var(--bad-fill);box-shadow:0 0 0 3.5px var(--bad-bg)}
select{
  appearance:none;-webkit-appearance:none;cursor:pointer;padding-right:34px;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='11' height='7' viewBox='0 0 11 7'%3E%3Cpath d='M1.2 1.4 5.5 5.6l4.3-4.2' fill='none' stroke='%23525b69' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 13px center;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]) select{
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='11' height='7' viewBox='0 0 11 7'%3E%3Cpath d='M1.2 1.4 5.5 5.6l4.3-4.2' fill='none' stroke='%23a5aebd' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
  }
}
:root[data-theme="dark"] select{
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='11' height='7' viewBox='0 0 11 7'%3E%3Cpath d='M1.2 1.4 5.5 5.6l4.3-4.2' fill='none' stroke='%23a5aebd' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
}
/* Search field with a leading glyph */
.search{position:relative;display:flex;align-items:center;min-width:0}
.search svg{position:absolute;left:12px;width:15px;height:15px;color:var(--fg-3);pointer-events:none}
.search input{padding-left:34px}

/* Switch — a real checkbox, restyled, with a check that draws itself in.
   The track, thumb and check live on a <span> rather than on the input:
   pseudo-elements on replaced elements are not guaranteed, and the input keeps
   its own semantics (focus, keyboard, label click) untouched. */
.switch{position:relative;display:inline-flex;align-items:center;gap:9px;cursor:pointer;
  user-select:none;font-size:var(--fs-sm);color:var(--fg-2);white-space:nowrap}
.switch:hover{color:var(--fg)}
.switch input{
  position:absolute;left:0;top:50%;width:1px;height:1px;margin:0;padding:0;border:0;
  opacity:0;pointer-events:none;
}
.switch .sw{
  --sw-w:40px; --sw-h:24px; --sw-pad:3px; --sw-travel:16px;
  position:relative;flex:0 0 auto;width:var(--sw-w);height:var(--sw-h);
  border-radius:var(--r-full);
  background:var(--fill-2);border:1px solid var(--line-1);
  transition:background-color var(--d-2) var(--e-out),border-color var(--d-2) var(--e-out);
}
.switch .sw::after{ /* thumb */
  content:"";position:absolute;top:var(--sw-pad);left:var(--sw-pad);
  width:calc(var(--sw-h) - var(--sw-pad) * 2);height:calc(var(--sw-h) - var(--sw-pad) * 2);
  border-radius:50%;background:#fff;
  box-shadow:0 1px 2px rgba(16,22,38,.28), 0 2px 6px rgba(16,22,38,.14);
  transition:transform var(--d-3) var(--e-spring), box-shadow var(--d-2) var(--e-out);
}
.switch .sw::before{ /* check: centred, then shifted by half the travel */
  content:"";position:absolute;inset:0;margin:auto;width:5px;height:9px;
  border-right:2px solid #fff;border-bottom:2px solid #fff;opacity:0;
  transform:translateX(calc(var(--sw-travel) / 2)) rotate(45deg) scaleY(0);
  transform-origin:center bottom;
  transition:transform var(--d-2) var(--e-out), opacity var(--d-1) var(--e-out);
}
.switch input:checked + .sw{background:var(--accent-fill);border-color:transparent}
.switch input:checked + .sw::after{transform:translateX(var(--sw-travel))}
.switch input:checked + .sw::before{opacity:1;transform:translateX(calc(var(--sw-travel) / 2)) rotate(45deg) scaleY(1);
  transition:transform var(--d-2) var(--e-out) 60ms, opacity var(--d-1) var(--e-out) 60ms}
.switch:hover input:not(:checked) + .sw{background:var(--fill-3)}
.switch input:active + .sw::after{box-shadow:0 1px 3px rgba(16,22,38,.34), 0 2px 6px rgba(16,22,38,.18)}
.switch input:focus-visible + .sw{outline:2px solid var(--accent);outline-offset:2px}
.switch input:disabled + .sw{opacity:.5}
.switch input:disabled + .sw,.switch input:disabled ~ *{cursor:not-allowed}
.switch.is-sm .sw{--sw-w:34px; --sw-h:20px; --sw-pad:2.5px; --sw-travel:14px}
.switch.is-sm .sw::before{width:4px;height:7.5px;border-width:0 1.8px 1.8px 0}
/* A switch whose text is a tooltip target keeps its own layout. */
.switch .sw-txt{color:inherit}

/* Tag / status pill — tinted glass, opaque text. */
.tag{
  display:inline-flex;align-items:center;justify-content:center;gap:5px;
  max-width:100%;padding:2px 9px;border-radius:var(--r-full);
  font-size:var(--fs-micro);font-weight:600;letter-spacing:.01em;white-space:nowrap;
  overflow-wrap:anywhere;
  background:var(--neu-bg);border:1px solid var(--neu-line);color:var(--fg-2);
  font-variant-numeric:tabular-nums;
}
.tag .tdot{width:6px;height:6px;border-radius:50%;background:currentColor;opacity:.85;flex:0 0 6px}
.tag.ok{color:var(--ok);background:var(--ok-bg);border-color:var(--ok-line)}
.tag.bad{color:var(--bad);background:var(--bad-bg);border-color:var(--bad-line)}
.tag.warn{color:var(--warn);background:var(--warn-bg);border-color:var(--warn-line)}
.tag.acc{color:var(--accent);background:var(--acc-bg);border-color:var(--acc-line)}
.tag.cookie{color:var(--warn);background:var(--warn-bg);border-color:var(--warn-line)}
.tag--mono{font-family:var(--mono);font-size:10.5px}

/* Tooltip — a glass chip that belongs to the control that owns it. */
[data-tip]{position:relative}
[data-tip]::after{
  content:attr(data-tip);position:absolute;left:50%;bottom:calc(100% + 9px);
  transform:translate(-50%,4px) scale(.97);transform-origin:bottom center;
  padding:6px 10px;border-radius:var(--r-1);max-width:260px;width:max-content;
  background:var(--glass-4);border:1px solid var(--line-2);color:var(--fg);
  font-size:var(--fs-micro);font-weight:520;letter-spacing:.005em;line-height:1.4;white-space:normal;
  -webkit-backdrop-filter:blur(var(--blur-3)) saturate(170%);
  backdrop-filter:blur(var(--blur-3)) saturate(170%);
  box-shadow:var(--sh-3), var(--sh-inset);
  opacity:0;pointer-events:none;z-index:var(--z-pop);
  transition:opacity var(--d-2) var(--e-out),transform var(--d-2) var(--e-out);
}
[data-tip]:hover::after,[data-tip]:focus-visible::after{opacity:1;transform:translate(-50%,0) scale(1)}
[data-tip-pos="below"]::after{bottom:auto;top:calc(100% + 9px);transform:translate(-50%,-4px) scale(.97);
  transform-origin:top center}
[data-tip-pos="below"]:hover::after,[data-tip-pos="below"]:focus-visible::after{transform:translate(-50%,0) scale(1)}
[data-tip-pos="left"]::after{left:auto;right:calc(100% + 9px);bottom:auto;top:50%;
  transform:translate(4px,-50%) scale(.97);transform-origin:right center}
[data-tip-pos="left"]:hover::after,[data-tip-pos="left"]:focus-visible::after{transform:translate(0,-50%) scale(1)}
[data-tip-pos="right"]::after{left:calc(100% + 9px);bottom:auto;top:50%;
  transform:translate(-4px,-50%) scale(.97);transform-origin:left center}
[data-tip-pos="right"]:hover::after,[data-tip-pos="right"]:focus-visible::after{transform:translate(0,-50%) scale(1)}

/* ── 6. Surfaces ─────────────────────────────────────────────────────────── */
main.content{
  flex:1;display:flex;flex-direction:column;min-height:0;
  overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;
  scrollbar-gutter:stable;
}

/* Sections never squeeze: a section that outgrows the pane makes the pane
   scroll, and nothing overlaps the footer. The chat section bounds its own
   transcript instead, via a definite height on .chat below. */
section.tab{display:none;flex-direction:column;gap:var(--sp-5);min-width:0;flex:0 0 auto}
section.tab.active{display:flex;animation:panelIn var(--d-3) var(--e-enter)}
@keyframes panelIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}

.phead{display:flex;align-items:flex-end;gap:var(--sp-4);flex-wrap:wrap;padding:var(--sp-1) 2px 0}
.phead-text{min-width:0;max-width:70ch}
.phead h2{
  font-size:var(--fs-h1);font-weight:640;letter-spacing:var(--track-tight);line-height:var(--lh-tight);
}
.phead p{color:var(--fg-2);font-size:var(--fs-sm);margin-top:7px;line-height:1.55}
.phead-actions{display:flex;align-items:center;gap:var(--sp-2);margin-left:auto;flex-wrap:wrap;justify-content:flex-end}

/* Section label — small caps, generous space above. */
.slabel{
  display:flex;align-items:center;gap:var(--sp-3);
  font-size:var(--fs-micro);font-weight:640;color:var(--fg-3);
  text-transform:uppercase;letter-spacing:var(--track-wide);
  margin-top:var(--sp-3);
}
.slabel::after{content:"";flex:1;height:1px;background:var(--line-1)}
.slabel .count{font-weight:560;letter-spacing:.01em;text-transform:none;color:var(--fg-3);font-size:var(--fs-meta)}

.panel{
  border-radius:var(--r-4);padding:var(--sp-4);
  display:flex;flex-direction:column;gap:var(--sp-3);min-width:0;
}
.panel--flush{padding:0;overflow:hidden}
.panel-head{
  display:flex;align-items:center;gap:var(--sp-3);flex-wrap:wrap;
  padding:var(--sp-4) var(--sp-4) var(--sp-3);
}
.panel-head h3{font-size:var(--fs-h3);font-weight:600;letter-spacing:-.014em}
.panel-head .sub{font-size:var(--fs-meta);color:var(--fg-3);margin-top:2px}
.panel-body{padding:0 var(--sp-4) var(--sp-4)}

/* Tiles — the numerical face of the console. */
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(196px,1fr));gap:var(--sp-3)}
.tile{
  position:relative;border-radius:var(--r-3);padding:13px 15px;min-width:0;
  display:flex;flex-direction:column;gap:3px;
  transition:transform var(--d-2) var(--e-out),box-shadow var(--d-2) var(--e-out),
             border-color var(--d-2) var(--e-out),background-color var(--d-2) var(--e-out);
  animation:riseIn var(--d-3) var(--e-enter) both;animation-delay:calc(var(--i,0) * 22ms);
}
@keyframes riseIn{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:none}}
.tile:hover{transform:translateY(-2px);border-color:var(--line-2);box-shadow:var(--sh-2), var(--sh-inset)}
.tile .k{
  font-size:var(--fs-micro);font-weight:600;color:var(--fg-3);
  text-transform:uppercase;letter-spacing:var(--track-wide);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
/* Narrow tiles: a wrapped label beats a clipped one. */
@media (max-width:820px){
  .tile .k{white-space:normal;overflow:visible;letter-spacing:.045em}
}
.tile .v{
  font-size:var(--fs-body);font-weight:545;letter-spacing:-.011em;line-height:1.4;
  font-variant-numeric:tabular-nums;overflow-wrap:anywhere;
}
.tile .v.mono{font-family:var(--mono);font-size:var(--fs-sm);font-weight:500}
.tile .hintline{font-size:var(--fs-micro);color:var(--fg-3);margin-top:2px}
.tile--accent{border-color:var(--acc-line)}
.tile--accent .v{color:var(--accent)}
/* A card is a panel-sized surface for one idea; used sparingly. */
.card{border-radius:var(--r-3);padding:var(--sp-4);min-width:0}

/* Callouts — health findings, errors, disclaimers. */
.callout{
  display:flex;gap:11px;align-items:flex-start;
  border-radius:var(--r-3);padding:12px 14px;font-size:var(--fs-sm);line-height:1.55;
  border:1px solid var(--line-1);background:var(--fill-1);
}
.callout .ico{width:18px;height:18px;margin-top:1px;color:var(--fg-2);flex:0 0 18px}
.callout strong{font-weight:620}
.callout code{font-size:.94em}
.callout--ok{background:var(--ok-bg);border-color:var(--ok-line)}
.callout--ok .ico{color:var(--ok)}
.callout--warn{background:var(--warn-bg);border-color:var(--warn-line)}
.callout--warn .ico{color:var(--warn)}
.callout--bad{background:var(--bad-bg);border-color:var(--bad-line)}
.callout--bad .ico{color:var(--bad)}
.callout .ctx{display:block;margin-top:4px;color:var(--fg-2);font-size:var(--fs-meta)}
.callout-list{display:grid;gap:var(--sp-2)}
code,kbd{
  font-family:var(--mono);font-size:.92em;
  background:var(--code-bg);border:1px solid var(--line-1);
  padding:1px 5px;border-radius:5px;overflow-wrap:anywhere;
}
kbd{font-size:11.5px;padding:2px 6px;box-shadow:var(--sh-inset)}
.ks,.pal-item .kbd{display:inline-flex;align-items:center;gap:4px;flex-wrap:wrap}
.composer-bar .hint kbd{margin-left:2px}
.composer-bar .hint kbd:first-child{margin-left:0}
pre code{background:none;border:0;padding:0;font-size:inherit;overflow-wrap:normal}

/* Tables — one glass surface, hairline rows, quiet hover. */
.tablewrap{
  border-radius:var(--r-4);overflow:hidden;
  display:flex;flex-direction:column;min-width:0;
}
.scroller{overflow:auto;min-width:0;-webkit-overflow-scrolling:touch}
.scroller.grow{flex:1;min-height:180px}
table{width:100%;border-collapse:separate;border-spacing:0;font-size:var(--fs-sm)}
caption.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
th,td{text-align:left;padding:10px 14px;vertical-align:top;border-bottom:1px solid var(--line-1)}
thead th{
  position:sticky;top:0;z-index:1;
  background:var(--glass-2);
  -webkit-backdrop-filter:blur(var(--blur-3)) saturate(160%);
  backdrop-filter:blur(var(--blur-3)) saturate(160%);
  font-size:var(--fs-micro);font-weight:640;color:var(--fg-3);
  text-transform:uppercase;letter-spacing:var(--track-wide);
  padding:9px 14px;border-bottom:1px solid var(--line-2);
  white-space:nowrap;
}
tbody td{transition:background-color var(--d-1) var(--e-out)}
tbody tr:hover td{background:var(--fill-1)}
tbody tr:last-child td{border-bottom:0}
td.code{font-family:var(--mono);font-size:var(--fs-meta);color:var(--fg-2);white-space:nowrap;
  font-variant-numeric:tabular-nums}
td .sub{display:block;margin-top:3px;font-size:var(--fs-meta);color:var(--fg-3)}
tr.is-new{animation:rowIn var(--d-4) var(--e-enter)}
@keyframes rowIn{from{background-color:var(--acc-bg)}to{background-color:transparent}}
.tnote{padding:11px 14px;border-top:1px solid var(--line-1);font-size:var(--fs-meta);color:var(--fg-3);
  background:var(--fill-1)}
.empty-cell{padding:22px 14px !important;color:var(--fg-3)}

/* Meters — small inline bars (account cooldowns, distribution). */
.meter{display:flex;align-items:center;gap:8px;min-width:96px}
.meter .track{position:relative;height:5px;flex:1;border-radius:var(--r-full);background:var(--fill-2);overflow:hidden}
.meter .fill{position:absolute;inset:0 auto 0 0;border-radius:var(--r-full);background:currentColor;opacity:.7;
  transition:width var(--d-4) var(--e-out)}
.meter .val{font-size:var(--fs-micro);font-family:var(--mono);color:var(--fg-3);
  font-variant-numeric:tabular-nums;min-width:34px;text-align:right}

/* ── 7. Chat ─────────────────────────────────────────────────────────────── */
/* A definite height (viewport-relative, clamped) is what lets the transcript
   scroll inside its panel while the composer stays on screen: percentages
   would need the pane to be the size of its own content. */
.chat{
  display:grid;grid-template-columns:250px minmax(0,1fr);gap:var(--sp-4);
  height:clamp(360px, calc(100dvh - 300px), 680px);min-height:0;
}
.rail{
  border-radius:var(--r-4);padding:var(--sp-3);display:flex;flex-direction:column;gap:var(--sp-2);
  min-height:0;min-width:0;
}
.rail-head{display:flex;align-items:center;gap:var(--sp-2);padding:2px 4px 6px}
.rail-head .lbl{flex:1}
.rail-list{flex:1;min-height:0;overflow:auto;display:flex;flex-direction:column;gap:2px;
  padding-right:2px;scrollbar-width:thin}
.conv{
  display:flex;align-items:center;gap:9px;padding:9px 10px;border-radius:var(--r-2);
  cursor:pointer;border:1px solid transparent;font-size:var(--fs-sm);min-width:0;
  transition:background-color var(--d-1) var(--e-out),border-color var(--d-1) var(--e-out),
             transform var(--d-1) var(--e-out);
}
.conv:hover{background:var(--fill-1)}
.conv:active{transform:scale(.99)}
.conv .t{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:520}
.conv .n{
  font-size:10.5px;color:var(--fg-3);font-family:var(--mono);
  font-variant-numeric:tabular-nums;flex:0 0 auto;
}
.conv.sel{
  background:var(--acc-bg);border-color:var(--acc-line);
  box-shadow:var(--sh-inset);
}
.conv.sel .t{font-weight:580;color:var(--accent)}
.conv .x{
  appearance:none;border:0;background:none;color:var(--fg-3);cursor:pointer;
  display:grid;place-items:center;width:22px;height:22px;border-radius:6px;flex:0 0 22px;
  opacity:0;transition:opacity var(--d-1) var(--e-out),color var(--d-1) var(--e-out),background-color var(--d-1) var(--e-out);
}
.conv .x svg{width:13px;height:13px}
.conv:hover .x,.conv:focus-within .x{opacity:1}
.conv .x:hover{color:var(--bad);background:var(--bad-bg)}
.rail-empty{padding:10px;font-size:var(--fs-meta);color:var(--fg-3);line-height:1.5}
.rail-foot{padding:4px 6px 2px;font-size:var(--fs-micro);color:var(--fg-3);line-height:1.45}

.thread{
  border-radius:var(--r-4);overflow:hidden;display:flex;flex-direction:column;
  min-width:0;min-height:0; /* the transcript scrolls, the panel does not grow */
}
.thread-bar{
  display:flex;align-items:center;gap:var(--sp-2);flex-wrap:wrap;
  padding:10px var(--sp-4);border-bottom:1px solid var(--line-1);
  background:var(--fill-1);
}
.thread-bar .spacer{flex:1}
.thread-bar select{width:auto;min-width:132px;max-width:230px;padding:7px 30px 7px 11px;font-size:var(--fs-meta);
  border-radius:var(--r-2)}
.thread-bar .key-field{position:relative;display:flex;align-items:center}
.thread-bar .key-field svg{position:absolute;left:10px;width:13px;height:13px;color:var(--fg-3)}
.thread-bar input[type="password"]{width:205px;padding:7px 11px 7px 29px;font-size:var(--fs-meta);
  border-radius:var(--r-2)}

.thread-body{position:relative;flex:1;display:flex;flex-direction:column;min-height:0}
.msgs{flex:1;overflow:auto;padding:var(--sp-5) var(--sp-5) var(--sp-6);
  display:flex;flex-direction:column;gap:var(--sp-5);scroll-behavior:smooth}
.msg{display:grid;grid-template-columns:28px minmax(0,1fr);gap:12px;animation:msgIn var(--d-3) var(--e-enter)}
@keyframes msgIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.msg-avatar{
  width:28px;height:28px;border-radius:var(--r-1);display:grid;place-items:center;
  background:var(--glass-3);border:1px solid var(--line-1);box-shadow:var(--sh-1), var(--sh-inset);
  color:var(--accent);margin-top:2px;
}
.msg-avatar svg{width:15px;height:15px}
.msg.user .msg-avatar{color:var(--fg-2)}
.msg.err .msg-avatar{color:var(--bad)}
.msg-body{min-width:0}
.msg-head{display:flex;align-items:baseline;gap:9px;margin-bottom:6px}
.msg-who{font-size:var(--fs-meta);font-weight:600;letter-spacing:-.006em}
.msg-time{font-size:var(--fs-micro);color:var(--fg-3);font-variant-numeric:tabular-nums}
.msg .bub{
  font-size:var(--fs-body);line-height:1.66;overflow-wrap:anywhere;min-width:0;
}
.msg.assistant .bub{padding:0 2px}
.msg.user .bub{
  display:inline-block;max-width:min(100%,72ch);
  background:var(--acc-bg);border:1px solid var(--acc-line);
  border-radius:var(--r-3);border-top-left-radius:6px;
  padding:11px 15px;
  box-shadow:var(--sh-inset);
}
.msg.err .bub{
  background:var(--bad-bg);border:1px solid var(--bad-line);color:var(--bad);
  border-radius:var(--r-3);border-top-left-radius:6px;padding:11px 15px;
}
.msg .bub p{margin:0 0 10px}
.msg .bub p:last-child{margin-bottom:0}
.msg .bub .mdh{font-weight:620;font-size:17px;letter-spacing:-.014em;margin:16px 0 7px;line-height:1.35}
.msg .bub .mdh:first-child{margin-top:0}
.msg .bub pre{
  margin:11px 0;padding:12px 14px;border-radius:var(--r-2);overflow:auto;
  background:var(--code-bg);border:1px solid var(--line-1);
  font-family:var(--mono);font-size:var(--fs-meta);line-height:1.6;
  scrollbar-width:thin;
}
.msg .bub code{font-family:var(--mono);font-size:.92em;background:var(--fill-2);
  border-color:transparent;padding:1.5px 5px;border-radius:5px}
.msg .bub pre code{background:none;border:0;padding:0}
.msg .bub strong{font-weight:640}
.mmeta{
  display:flex;align-items:center;gap:8px;flex-wrap:wrap;
  margin-top:9px;font-size:var(--fs-micro);color:var(--fg-3);
  font-family:var(--mono);font-variant-numeric:tabular-nums;
}
.mmeta .mchip{padding:2px 8px;border-radius:var(--r-full);background:var(--fill-1);border:1px solid var(--line-1)}
.cursor{
  display:inline-block;width:7px;height:16px;margin-left:3px;border-radius:2px;
  background:var(--accent);
  background:linear-gradient(180deg,var(--accent),color-mix(in srgb,var(--accent) 55%,transparent));
  vertical-align:text-bottom;animation:blink 1.05s var(--e-inout) infinite;
}
@keyframes blink{0%,100%{opacity:.9}50%{opacity:.15}}
.thread-anchor{display:flex;align-items:center;gap:10px;font-size:var(--fs-micro);color:var(--fg-3);padding-top:2px}
.thread-anchor::before,.thread-anchor::after{content:"";height:1px;flex:1;background:var(--line-1)}

.tobottom{
  position:absolute;right:18px;bottom:16px;z-index:3;
  width:38px;height:38px;border-radius:var(--r-full);cursor:pointer;
  display:grid;place-items:center;color:var(--fg);
  background:var(--glass-3);border:1px solid var(--line-2);
  -webkit-backdrop-filter:blur(var(--blur-3)) saturate(170%);
  backdrop-filter:blur(var(--blur-3)) saturate(170%);
  box-shadow:var(--sh-3), var(--sh-inset);
  opacity:0;pointer-events:none;transform:translateY(6px) scale(.96);
  transition:opacity var(--d-2) var(--e-out),transform var(--d-3) var(--e-spring),background-color var(--d-2) var(--e-out);
}
.tobottom svg{width:16px;height:16px}
.tobottom.show{opacity:1;pointer-events:auto;transform:none}
.tobottom:hover{background:var(--glass-4)}

.composer{
  border-top:1px solid var(--line-1);
  padding:var(--sp-3) var(--sp-4) var(--sp-4);
  display:flex;flex-direction:column;gap:9px;
  background:linear-gradient(180deg, transparent, var(--fill-1));
}
.composer .well{
  border-radius:var(--r-3);border:1px solid var(--line-1);background:var(--field);
  padding:11px 12px 9px;
  transition:border-color var(--d-2) var(--e-out),box-shadow var(--d-2) var(--e-out),background-color var(--d-2) var(--e-out);
}
.composer .well:focus-within{border-color:var(--accent);background:var(--field-focus);
  box-shadow:0 0 0 3.5px var(--acc-ring)}
.composer textarea{
  width:100%;min-height:26px;max-height:220px;resize:none;padding:0;border:0;background:none;
  font-size:var(--fs-body);line-height:1.6;box-shadow:none;
}
.composer textarea:focus{box-shadow:none;background:none}
.composer-bar{display:flex;align-items:center;gap:var(--sp-2);flex-wrap:wrap}
.composer-bar .spacer{flex:1}
.composer-bar .hint{font-size:var(--fs-micro);color:var(--fg-3);display:flex;align-items:center;gap:6px}
.composer-bar .hint kbd{font-size:10.5px}
.chat-footnote{font-size:var(--fs-meta);color:var(--fg-3);line-height:1.55;max-width:88ch;padding:0 2px}

/* Empty states — icon, title, one line, one action. */
.empty{
  margin:auto;text-align:center;max-width:46ch;padding:var(--sp-7) var(--sp-4);
  display:flex;flex-direction:column;align-items:center;gap:4px;
  animation:panelIn var(--d-3) var(--e-enter);
}
.empty-icon{
  width:52px;height:52px;border-radius:var(--r-3);display:grid;place-items:center;
  color:var(--accent);background:var(--glass-3);border:1px solid var(--line-1);
  box-shadow:var(--sh-2), var(--sh-inset);margin-bottom:var(--sp-3);
}
.empty-icon svg{width:24px;height:24px}
.empty h4,.empty-title{font-size:var(--fs-lead);font-weight:600;letter-spacing:-.012em;color:var(--fg)}
.empty p{font-size:var(--fs-sm);color:var(--fg-2);line-height:1.6;margin-top:2px}
.empty .btn{margin-top:var(--sp-4)}
.chips{display:flex;gap:var(--sp-2);flex-wrap:wrap;justify-content:center;margin-top:var(--sp-4)}
.chip{
  appearance:none;cursor:pointer;font-family:inherit;
  font-size:var(--fs-meta);font-weight:520;color:var(--fg-2);
  background:var(--glass-1);border:1px solid var(--line-1);border-radius:var(--r-full);
  padding:7px 13px;box-shadow:var(--sh-1), var(--sh-inset);
  transition:color var(--d-1) var(--e-out),background-color var(--d-1) var(--e-out),
             border-color var(--d-1) var(--e-out),transform var(--d-2) var(--e-spring),box-shadow var(--d-2) var(--e-out);
}
.chip:hover{color:var(--accent);background:var(--acc-bg);border-color:var(--acc-line);
  transform:translateY(-1px);box-shadow:var(--sh-2), var(--sh-inset)}
.chip:active{transform:translateY(0) scale(.98)}

/* ── 8. Charts ───────────────────────────────────────────────────────────── */
.chart{border-radius:var(--r-4);padding:var(--sp-4) var(--sp-4) var(--sp-3);display:flex;flex-direction:column;gap:var(--sp-3)}
.chart-head{display:flex;align-items:baseline;gap:var(--sp-3);flex-wrap:wrap}
.chart-head h3{font-size:var(--fs-sm);font-weight:600;letter-spacing:-.008em}
.chart-head .sub{font-size:var(--fs-meta);color:var(--fg-3)}
.chart-head .spacer{flex:1}
.chart-legend{display:flex;align-items:center;gap:6px;font-size:var(--fs-micro);color:var(--fg-3)}
.chart-legend .swatch{width:9px;height:9px;border-radius:3px;background:var(--accent);opacity:.62}
.plot{position:relative;padding-left:44px;padding-bottom:20px;min-height:150px}
.gridlines{position:absolute;inset:0 0 20px 44px;display:flex;flex-direction:column;justify-content:space-between;pointer-events:none}
.gridlines span{position:relative;border-top:1px dashed var(--line-1);flex:0 0 auto}
.gridlines span i{
  position:absolute;left:-44px;top:-8px;width:40px;text-align:right;font-style:normal;
  font-family:var(--mono);font-size:10px;color:var(--fg-3);
}
.bars{position:relative;display:flex;align-items:flex-end;gap:clamp(4px,1.1vw,10px);height:132px}
.bar{
  position:relative;flex:1 1 0;min-width:0;height:100%;display:flex;align-items:flex-end;
  background:none;border:0;padding:0;cursor:default;
}
.bar .fill{
  width:100%;border-radius:6px 6px 3px 3px;
  /* Plain fill first: an engine without color-mix() still gets a solid bar. */
  background:var(--accent);opacity:.72;
  background:linear-gradient(180deg, color-mix(in srgb,var(--accent) 78%,transparent), color-mix(in srgb,var(--accent) 34%,transparent));
  opacity:1;
  border:1px solid var(--acc-line);border-bottom:0;
  height:max(3px,var(--h));transform-origin:bottom;animation:barGrow var(--d-4) var(--e-enter) both;
  animation-delay:calc(var(--i,0) * 34ms);
  transition:filter var(--d-2) var(--e-out),box-shadow var(--d-2) var(--e-out);
}
@keyframes barGrow{from{transform:scaleY(.02);opacity:.4}to{transform:scaleY(1);opacity:1}}
.bar:hover .fill,.bar:focus-visible .fill{filter:brightness(1.12);box-shadow:0 0 0 1px var(--acc-line)}
.bar .xlab{
  position:absolute;left:50%;transform:translateX(-50%);bottom:-20px;white-space:nowrap;
  font-family:var(--mono);font-size:10px;color:var(--fg-3);
}
.bar[data-tip]::after{bottom:calc(100% + 6px)}
.chart-x{display:flex;align-items:center;gap:6px;font-size:var(--fs-micro);color:var(--fg-3);padding-left:44px}

/* ── 6b. Cards, model tiles, copy pills ──────────────────────────────────── */
/* A card earns its place: it groups one idea, not every row of the page. */
.model-card{
  display:flex;flex-direction:column;gap:9px;padding:15px 15px 14px;
  grid-column:span 1;min-width:0;
}
.model-card[hidden]{display:none}
.model-top{display:flex;align-items:flex-start;gap:8px;min-width:0}
.model-id{
  flex:1 1 auto;min-width:0;background:none;border:0;padding:0;
  font-family:var(--mono);font-size:var(--fs-sm);font-weight:560;letter-spacing:-.012em;
  overflow-wrap:break-word;
}
.model-tags{display:flex;align-items:center;gap:6px;flex-wrap:wrap;min-width:0}
/* A long technical tag (FLASH_DYNAMIC_THINKING) folds onto a second line: the
   fully-rounded radius becomes a stadium around two lines, which still reads
   as a chip - far better than a pill poking past the card, or a truncated one. */
.model-tags .tag{white-space:normal;line-height:1.35;padding-top:3px;padding-bottom:3px}
.model-desc{font-size:var(--fs-meta);color:var(--fg-2);line-height:1.55}
.model-card .hintline{margin-top:2px}
button.copy{
  appearance:none;cursor:pointer;flex:0 0 auto;
  font-family:inherit;font-size:var(--fs-micro);font-weight:600;color:var(--fg-2);
  background:var(--fill-1);border:1px solid var(--line-1);border-radius:var(--r-full);
  padding:3px 10px;white-space:nowrap;
  transition:color var(--d-1) var(--e-out),background-color var(--d-1) var(--e-out),
             border-color var(--d-1) var(--e-out),transform var(--d-1) var(--e-out);
}
button.copy:hover{color:var(--accent);background:var(--acc-bg);border-color:var(--acc-line)}
button.copy:active{transform:scale(.95)}
button.copy.done{color:var(--ok);background:var(--ok-bg);border-color:var(--ok-line)}
td .copy{margin-top:1px}

/* Conversation rows: the whole row is a target, the title is the button. */
button.conv-open{
  appearance:none;border:0;background:none;padding:0;margin:0;cursor:pointer;
  font:inherit;font-size:var(--fs-sm);color:inherit;text-align:left;
  flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}
button.conv-open:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:6px}

/* Callout levels beyond the semantic three, and skeleton rows that must not
   respond to the pointer as if they held data. */
.callout--info{background:var(--acc-bg);border-color:var(--acc-line)}
.callout--info .ico{color:var(--accent)}
/* One line of admonition must not stretch across a wide monitor: past ~72
   characters the eye loses the line it is on. */
.callout .ctx-body{min-width:0;max-width:72ch}
.toast--info .ico{color:var(--accent)}
tr.sk-row:hover td{background:transparent}
/* ── 9. Overlays: veils, dialogs, command palette, toasts ────────────────── */
.veil{
  position:fixed;inset:0;z-index:var(--z-dialog);
  display:flex;align-items:center;justify-content:center;
  padding:clamp(12px,3vw,32px);
  background:var(--scrim);
  -webkit-backdrop-filter:blur(var(--blur-veil)) saturate(120%);
  backdrop-filter:blur(var(--blur-veil)) saturate(120%);
  animation:veilIn var(--d-3) var(--e-out) both;
}
.veil[hidden]{display:none}
.veil--top{align-items:flex-start;padding-top:clamp(48px,10vh,120px)}
.veil.is-closing{animation:veilOut var(--d-2) var(--e-out) both}
@keyframes veilIn{from{opacity:0;-webkit-backdrop-filter:blur(0) saturate(100%);backdrop-filter:blur(0) saturate(100%)}to{opacity:1}}
@keyframes veilOut{from{opacity:1}to{opacity:0}}
.sheet{
  width:min(560px,100%);max-height:min(86vh,760px);
  border-radius:var(--r-5);display:flex;flex-direction:column;min-height:0;
  animation:sheetIn var(--d-4) var(--e-spring) both;
  outline:none;
}
.veil.is-closing .sheet{animation:sheetOut var(--d-2) var(--e-inout) both}
@keyframes sheetIn{from{opacity:0;transform:translateY(14px) scale(.965)}to{opacity:1;transform:none}}
@keyframes sheetOut{from{opacity:1;transform:none}to{opacity:0;transform:translateY(6px) scale(.985)}}
.sheet-head{
  display:flex;align-items:center;gap:var(--sp-3);padding:var(--sp-4) var(--sp-4) var(--sp-3);
  border-bottom:1px solid var(--line-1);
}
.sheet-head h3{font-size:var(--fs-h3);font-weight:600;letter-spacing:-.014em;flex:1;min-width:0}
.sheet-head .sub{display:block;font-size:var(--fs-meta);color:var(--fg-3);font-weight:440;margin-top:2px}
.sheet-body{padding:var(--sp-4);overflow:auto;display:flex;flex-direction:column;gap:var(--sp-3);min-height:0}
.sheet-foot{
  display:flex;align-items:center;gap:var(--sp-2);padding:var(--sp-3) var(--sp-4);
  border-top:1px solid var(--line-1);flex-wrap:wrap;
}
.sheet-foot .spacer{flex:1}
.sheet--sm{width:min(430px,100%)}

/* Command palette */
.palette{width:min(620px,100%);border-radius:var(--r-4);overflow:hidden;display:flex;flex-direction:column;
  animation:sheetIn var(--d-3) var(--e-spring) both}
.pal-input{display:flex;align-items:center;gap:11px;padding:14px 16px;border-bottom:1px solid var(--line-1)}
.pal-input svg{width:17px;height:17px;color:var(--fg-3)}
.pal-input input{
  flex:1;min-width:0;border:0;background:none;box-shadow:none;padding:0;
  font-size:var(--fs-lead);letter-spacing:-.012em;
}
.pal-input input:focus{box-shadow:none;background:none}
.pal-list{max-height:min(52vh,420px);overflow:auto;padding:8px;display:flex;flex-direction:column;gap:2px}
.pal-group{padding:10px 10px 5px;font-size:var(--fs-micro);font-weight:640;color:var(--fg-3);
  text-transform:uppercase;letter-spacing:var(--track-wide)}
.pal-item{
  appearance:none;border:1px solid transparent;background:none;cursor:pointer;width:100%;
  display:flex;align-items:center;gap:11px;padding:9px 11px;border-radius:var(--r-2);
  font-size:var(--fs-sm);color:var(--fg);text-align:left;
}
.pal-item .ico{width:17px;height:17px;color:var(--fg-3);flex:0 0 17px}
.pal-item .t{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pal-item .kbd{font-size:10.5px;color:var(--fg-3)}
.pal-item[aria-selected="true"]{background:var(--acc-bg);border-color:var(--acc-line)}
.pal-item[aria-selected="true"] .ico{color:var(--accent)}
.pal-empty{padding:26px 16px;text-align:center;color:var(--fg-3);font-size:var(--fs-sm)}
.pal-foot{display:flex;align-items:center;gap:14px;padding:9px 16px;border-top:1px solid var(--line-1);
  font-size:var(--fs-micro);color:var(--fg-3);background:var(--fill-1)}
.pal-foot span{display:inline-flex;align-items:center;gap:5px}

/* Shortcut list (help dialog) */
.keys{display:grid;gap:2px}
.key-row{display:flex;align-items:center;gap:var(--sp-3);padding:8px 10px;border-radius:var(--r-2)}
.key-row:nth-child(odd){background:var(--fill-1)}
.key-row .t{flex:1;font-size:var(--fs-sm);color:var(--fg-2)}
.key-row .ks{display:flex;gap:5px;flex-wrap:wrap;justify-content:flex-end}

/* Toasts — the topmost layer, above everything that floats. */
.toasts{
  position:fixed;z-index:var(--z-toast);right:22px;bottom:22px;
  display:flex;flex-direction:column;gap:10px;align-items:flex-end;
  pointer-events:none;max-width:min(400px,calc(100vw - 32px));
}
.toast{
  pointer-events:auto;display:flex;align-items:flex-start;gap:11px;
  padding:12px 14px;border-radius:var(--r-3);min-width:250px;max-width:100%;
  animation:toastIn var(--d-4) var(--e-spring) both;
}
.toast.is-closing{animation:toastOut var(--d-2) var(--e-out) both}
@keyframes toastIn{from{opacity:0;transform:translateY(12px) scale(.97)}to{opacity:1;transform:none}}
@keyframes toastOut{to{opacity:0;transform:translateY(6px) scale(.98)}}
.toast .ico{width:18px;height:18px;margin-top:1px;color:var(--accent);flex:0 0 18px}
.toast--ok .ico{color:var(--ok)}
.toast--warn .ico{color:var(--warn)}
.toast--bad .ico{color:var(--bad)}
.toast .txt{flex:1;min-width:0;font-size:var(--fs-sm);line-height:1.5}
.toast .txt strong{display:block;font-weight:600;margin-bottom:1px}
.toast .txt .sub{color:var(--fg-2);font-size:var(--fs-meta)}
.toast .x{
  appearance:none;border:0;background:none;color:var(--fg-3);cursor:pointer;
  width:24px;height:24px;border-radius:6px;display:grid;place-items:center;flex:0 0 24px;
}
.toast .x:hover{background:var(--fill-2);color:var(--fg)}
.toast .x svg{width:13px;height:13px}

/* ── 10. States: skeletons, spinners, utilities ──────────────────────────── */
.sk{position:relative;overflow:hidden;border-radius:6px;background:var(--fill-2);color:transparent}
.sk::after{
  content:"";position:absolute;inset:0;
  background:linear-gradient(90deg,transparent,var(--fill-3),transparent);
  transform:translateX(-100%);animation:sweep 1.6s var(--e-inout) infinite;
}
.sk--line{height:11px}
.sk--val{height:16px;margin-top:4px;width:70%}
.sk-tile{pointer-events:none}
.sk-tile:hover{transform:none;box-shadow:var(--sh-1), var(--sh-inset)}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
  clip:rect(0 0 0 0);white-space:nowrap;border:0}
.spacer{flex:1}
.stack{display:flex;flex-direction:column;gap:var(--sp-4)}
.row{display:flex;align-items:center;gap:var(--sp-2);flex-wrap:wrap}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
.muted{color:var(--fg-2)}
.note{font-size:var(--fs-meta);color:var(--fg-3);line-height:1.55}
.note strong{color:var(--fg-2);font-weight:600}
.nowrap{white-space:nowrap}
footer.foot{
  padding:var(--sp-6) 4px var(--sp-3);font-size:var(--fs-meta);color:var(--fg-3);
}
/* The flex parent is the span the script fills, not the footer around it:
   an inline span would swallow the gap and collapse the separator dots. */
footer.foot #foot{display:flex;align-items:center;gap:var(--sp-3);flex-wrap:wrap}
footer.foot #foot>span{min-width:0}
footer.foot .dot-sep{
  flex:0 0 auto;display:inline-block;width:3px;height:3px;border-radius:50%;background:var(--fill-3);
}
::-webkit-scrollbar{width:11px;height:11px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--fill-2);border-radius:var(--r-full);
  border:3px solid transparent;background-clip:content-box}
::-webkit-scrollbar-thumb:hover{background:var(--fill-3);background-clip:content-box;border:3px solid transparent}

/* ── 11. Responsive: intentional behaviour, not a shrunken desktop ───────── */
@media (max-width:1080px){
  .chat{grid-template-columns:216px minmax(0,1fr)}
  .rail-head .lbl{display:none}
}
@media (max-width:900px){
  .shell{padding:var(--sp-4) clamp(12px,3vw,20px) var(--sp-6)}
  .masthead{padding-bottom:var(--sp-4)}
  /* Navigation becomes a thumb-reachable, floating bottom bar. */
  .dock-wrap{position:static;margin-bottom:var(--sp-4)}
  .dock{
    position:fixed;left:50%;bottom:calc(14px + env(safe-area-inset-bottom,0px));
    transform:translateX(-50%);
    width:min(calc(100vw - 24px),560px);
    padding:6px;border-radius:var(--r-full);z-index:var(--z-dock);
  }
  nav.tabs{flex:1;justify-content:space-between}
  .tab-btn{flex:1 1 0;flex-direction:column;gap:3px;padding:7px 4px 6px;font-size:10.5px;min-width:0}
  .tab-btn .ico{width:19px;height:19px;opacity:.8}
  .tab-btn span.lbl{max-width:100%;overflow:hidden;text-overflow:ellipsis}
  .dock-end{display:none}
  main.content{padding-bottom:calc(84px + env(safe-area-inset-bottom,0px))}
  .chat{grid-template-columns:minmax(0,1fr);gap:var(--sp-3);
    height:clamp(400px, calc(100dvh - 320px), 760px)}
  /* The conversation rail becomes a horizontal strip: no half-empty card, and
     the primary action keeps a written label once the sidebar is gone. */
  .rail{padding:0;background:none;border:0;box-shadow:none;backdrop-filter:none;-webkit-backdrop-filter:none;
    overflow:visible}
  .rail-head{display:flex;flex-direction:row-reverse;align-items:center;justify-content:flex-end;
    gap:var(--sp-2);padding:0}
  .rail-head .lbl{display:flex;align-items:center;gap:7px;min-height:36px;padding:0 13px;
    border-radius:var(--r-full);background:var(--fill-1);border:1px solid var(--line-1);
    font-size:var(--fs-meta);font-weight:560;color:var(--fg-2);white-space:nowrap}
  .rail-head .lbl::before{content:"";width:11px;height:11px;flex:0 0 auto;
    background:currentColor;opacity:.72;
    -webkit-mask:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='M12 5.4v13.2M5.4 12h13.2' fill='none' stroke='%23000' stroke-width='2.1' stroke-linecap='round'/%3E%3C/svg%3E") center/contain no-repeat;
    mask:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='M12 5.4v13.2M5.4 12h13.2' fill='none' stroke='%23000' stroke-width='2.1' stroke-linecap='round'/%3E%3C/svg%3E") center/contain no-repeat}
  .rail-head .btn{width:36px;min-height:36px;border-radius:var(--r-full);border-color:var(--line-1)}
  .rail-list{flex-direction:row;gap:var(--sp-2);overflow-x:auto;overflow-y:hidden;
    padding:0 2px 2px;scroll-snap-type:x proximity}
  .rail-list:empty{display:none}
  .conv{flex:0 0 auto;max-width:230px;scroll-snap-align:start;
    background:var(--fill-1);border-color:var(--line-1)}
  .conv .x{opacity:1}
  .rail-foot,.rail-empty{display:none}
  .thread{min-height:0}
  /* Landscape phones: keep a usable transcript and let the pane scroll. */
  @media (max-height:560px){ .thread{min-height:max(46vh,300px)} }
  .thread-bar{padding:9px var(--sp-3)}
  .msgs{padding:var(--sp-4) var(--sp-3) var(--sp-5);gap:var(--sp-4)}
  .composer{padding:var(--sp-3)}
  .toasts{right:50%;transform:translateX(50%);bottom:calc(84px + env(safe-area-inset-bottom,0px));
    align-items:center;left:12px;max-width:none}
  .toast{width:100%;min-width:0}
  .slabel{margin-top:var(--sp-2)}
}
@media (max-width:760px){
  .phead{align-items:flex-start}
  .phead-actions{width:100%;justify-content:flex-start;margin-left:0}
  .tiles{grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}
  /* Tables reflow into stacked rows: label above value, no sideways scroll. */
  table.stack-rows thead{display:none}
  table.stack-rows,table.stack-rows tbody,table.stack-rows tr,table.stack-rows td{display:block;width:100%}
  table.stack-rows tr{padding:11px 14px;border-bottom:1px solid var(--line-1)}
  table.stack-rows tr:last-child{border-bottom:0}
  table.stack-rows td{border:0;padding:2px 0;display:flex;gap:12px;justify-content:space-between;
    align-items:baseline;text-align:right}
  table.stack-rows td::before{
    content:attr(data-label);flex:0 0 auto;font-size:var(--fs-micro);font-weight:600;
    color:var(--fg-3);text-transform:uppercase;letter-spacing:var(--track-wide);text-align:left;
  }
  table.stack-rows td.code{white-space:normal;overflow-wrap:anywhere}
  table.stack-rows td:empty{display:none}
  .panel,.card{border-radius:var(--r-3)}
  .sheet,.palette{width:100%;border-radius:var(--r-4)}
  .veil{align-items:flex-end;padding:10px}
  .veil--top{align-items:flex-start}
  .plot{padding-left:34px}
  .gridlines{left:34px}
  .gridlines span i{left:-34px;width:30px}
}
@media (max-width:620px){
  /* Stack the chat toolbar and let the API-key field take the full row: a
     shrinking text field is worse than one extra line. */
  .thread-bar{flex-wrap:wrap;gap:var(--sp-2);padding:10px var(--sp-3)}
  .thread-bar .spacer{display:none}
  .thread-bar select{flex:1 1 46%;min-width:0;max-width:calc(50% - 4px)}
  .thread-bar .switch{flex:0 0 auto;order:3}
  .thread-bar .key-field{order:2;flex:0 0 100%;width:100%}
  .thread-bar input[type="password"]{width:100%;min-width:0;font-size:var(--fs-body);padding-top:9px;padding-bottom:9px}
}
@media (max-width:470px){
  /* One control per row: side-by-side selects would wrap their own labels. */
  .thread-bar select{flex:1 1 100%;max-width:100%}
  .thread-bar .switch{order:4}
}
@media (max-width:560px){
  :root{--fs-h1:25px}
  #verPill{display:none}
  .brand-text p{display:none}
  .brand-text h1{font-size:15.5px}
  .masthead{gap:var(--sp-3)}
  .tiles{grid-template-columns:repeat(auto-fill,minmax(138px,1fr));gap:var(--sp-2)}
  .tile{padding:11px 12px}
  .seg-btn{padding:0 8px}
  .msg{grid-template-columns:24px minmax(0,1fr);gap:9px}
  .msg-avatar{width:24px;height:24px;border-radius:6px}
  .msg-avatar svg{width:13px;height:13px}
  .msg.user .bub{max-width:100%}
  .composer-bar .hint{display:none}
  .bars{height:108px}
  .toasts{left:10px;right:10px;transform:none;align-items:stretch}
  footer.foot{padding-bottom:6px}
}
@media (max-width:400px){
  .health{padding:6px 10px 6px 9px}
  .brand-text{display:none}
  .tab-btn span.lbl{font-size:10px}
}

/* Coarse pointers get roomier targets, never smaller type. */
@media (pointer:coarse){
  .tab-btn{padding-top:9px;padding-bottom:8px}
  .btn{min-height:40px}
  .btn--sm{min-height:36px}
  /* Copy chips are mouse-sized by design; touch needs a real target. */
  button.copy{min-height:34px;padding:6px 13px}
  .switch .sw{--sw-w:44px; --sw-h:26px; --sw-pad:3px; --sw-travel:18px}
}

/* ── 12. Motion preferences ──────────────────────────────────────────────── */
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{
    animation-duration:.01ms !important;
    animation-iteration-count:1 !important;
    transition-duration:.01ms !important;
    scroll-behavior:auto !important;
  }
  .tile,.msg,.bar .fill,section.tab.active,.toast,.sheet,.veil{animation:none !important}
  .glider{transition:none !important}
  .dot.ok::after,.dot.warn::after,.dot.bad::after{display:none}
}
@media print{
  .dock-wrap,.masthead,.toasts,.canvas{display:none !important}
}

/* ── 13. Last-word rules ─────────────────────────────────────────────────── */
/* One source of truth for the hidden attribute: several components set their
   own display, and `[hidden]` must win over all of them. */
[hidden]{display:none !important}
/* Touch devices get no hover tooltips — they would fire on every tap. */
@media (hover:none){
  [data-tip]::after{display:none !important}
}
/* Room for the segmented control on narrow screens. */
@media (max-width:720px){
  #verPill{display:none}
}
/* Empty rows keep their own layout instead of showing an empty label column. */
table.stack-rows td[data-label=""]::before{display:none}
</style>
</head>
<body>
<div class="canvas" aria-hidden="true"></div>
<a class="skip" href="#main">Skip to content</a>

<div class="shell">
  <!-- ── Masthead: identity, health, appearance ─────────────────────────── -->
  <header class="masthead">
    <div class="brand">
      <span class="mark" aria-hidden="true">
        <svg viewBox="0 0 32 32" role="img">
          <defs>
            <linearGradient id="brandGrad" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="#4285f4"/>
              <stop offset=".5" stop-color="#9b72cb"/>
              <stop offset="1" stop-color="#d96570"/>
            </linearGradient>
          </defs>
          <path fill="url(#brandGrad)" d="M16 2C17 10.2 21.8 15 30 16C21.8 17 17 21.8 16 30C15 21.8 10.2 17 2 16C10.2 15 15 10.2 16 2Z"/>
        </svg>
      </span>
      <div class="brand-text">
        <h1>gemini-web2api</h1>
        <p>Gemini Web &rarr; OpenAI-compatible API gateway</p>
      </div>
    </div>

    <div class="masthead-end">
      <span class="health" id="health" data-tip="Live from /health, refreshed every 15 s" data-tip-pos="below">
        <span class="dot" id="statusDot" aria-hidden="true"></span>
        <span id="statusText" aria-live="polite">checking&hellip;</span>
      </span>
      <span class="pill--meta" id="verPill" data-tip="Build and uptime" data-tip-pos="below">
        v<span id="version">&mdash;</span><span class="dot-sep" aria-hidden="true"></span><span id="uptime">&mdash;</span>
      </span>

      <div class="seg" id="themeSeg" role="radiogroup" aria-label="Appearance">
        <button class="seg-btn" type="button" role="radio" aria-checked="true" data-theme-opt="auto"
                data-tip="Follow the system" data-tip-pos="below">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <rect x="3" y="4.5" width="18" height="12.5" rx="2.5"/><path d="M9 20.5h6M12 17v3.5"/>
          </svg>
          <span class="sr-only">System</span>
        </button>
        <button class="seg-btn" type="button" role="radio" aria-checked="false" data-theme-opt="light"
                data-tip="Light" data-tip-pos="below">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="12" cy="12" r="4"/><path d="M12 3.2v2.2M12 18.6v2.2M3.2 12h2.2M18.6 12h2.2M5.8 5.8l1.6 1.6M16.6 16.6l1.6 1.6M18.2 5.8l-1.6 1.6M7.4 16.6l-1.6 1.6"/>
          </svg>
          <span class="sr-only">Light</span>
        </button>
        <button class="seg-btn" type="button" role="radio" aria-checked="false" data-theme-opt="dark"
                data-tip="Dark" data-tip-pos="below">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M19.5 14.2A7.8 7.8 0 0 1 9.8 4.5 7.8 7.8 0 1 0 19.5 14.2Z"/>
          </svg>
          <span class="sr-only">Dark</span>
        </button>
      </div>

      <button class="btn btn--quiet btn--icon" id="helpBtn" type="button" aria-label="Keyboard shortcuts"
              data-tip="Keyboard shortcuts (?)" data-tip-pos="below">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="8.6"/><path d="M9.7 9.5a2.4 2.4 0 1 1 3.2 2.2c-.7.3-1 .9-1 1.6v.5M12 16.8h.01"/>
        </svg>
      </button>
    </div>
  </header>

  <!-- ── Dock: navigation that floats above the content ─────────────────── -->
  <div class="dock-wrap">
    <div class="dock glass-2" id="dock">
      <nav class="tabs" id="tabs" role="tablist" aria-label="Console sections" aria-orientation="horizontal">
        <span class="glider" aria-hidden="true"></span>
        <button class="tab-btn" type="button" role="tab" id="tabbtn-chat" data-tab="chat"
                aria-selected="true" aria-controls="tab-chat" tabindex="0"
                data-tip="Talk to the models &#8984;1" data-tip-pos="below">
          <svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M20.5 12.1c0 3.7-3.8 6.7-8.5 6.7-1 0-2-.14-2.9-.4L4.6 19.8l1.1-3.3c-1.3-1.2-2.2-2.7-2.2-4.4 0-3.7 3.8-6.7 8.5-6.7s8.5 3 8.5 6.7Z"/>
          </svg>
          <span class="lbl">Chat</span>
        </button>
        <button class="tab-btn" type="button" role="tab" id="tabbtn-status" data-tab="status"
                aria-selected="false" aria-controls="tab-status" tabindex="-1"
                data-tip="Live status and metrics &#8984;2" data-tip-pos="below">
          <svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M3 13h3.7l2-5.4 3.3 9.8 2.2-6.5 1.6 3.4H21"/>
          </svg>
          <span class="lbl">Status</span>
        </button>
        <button class="tab-btn" type="button" role="tab" id="tabbtn-activity" data-tab="activity"
                aria-selected="false" aria-controls="tab-activity" tabindex="-1"
                data-tip="Recent requests &#8984;3" data-tip-pos="below">
          <svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M4 6.6h16M4 12h11.5M4 17.4h7.5"/>
          </svg>
          <span class="lbl">Activity</span>
        </button>
        <button class="tab-btn" type="button" role="tab" id="tabbtn-models" data-tab="models"
                aria-selected="false" aria-controls="tab-models" tabindex="-1"
                data-tip="Available models &#8984;4" data-tip-pos="below">
          <svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <rect x="3.6" y="3.6" width="7.2" height="7.2" rx="2"/><rect x="13.2" y="3.6" width="7.2" height="7.2" rx="2"/>
            <rect x="3.6" y="13.2" width="7.2" height="7.2" rx="2"/><rect x="13.2" y="13.2" width="7.2" height="7.2" rx="2"/>
          </svg>
          <span class="lbl">Models</span>
        </button>
        <button class="tab-btn" type="button" role="tab" id="tabbtn-api" data-tab="api"
                aria-selected="false" aria-controls="tab-api" tabindex="-1"
                data-tip="Endpoints and client setup &#8984;5" data-tip-pos="below">
          <svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M9.2 8.4 4.9 12l4.3 3.6M14.8 8.4 19.1 12l-4.3 3.6"/>
          </svg>
          <span class="lbl">API</span>
        </button>
      </nav>
      <div class="dock-end">
        <button class="btn btn--ghost btn--sm" id="paletteBtn" type="button"
                data-tip="Search, jump, act &#8984;K" data-tip-pos="below">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="11" cy="11" r="6.2"/><path d="m15.6 15.6 3.9 3.9"/>
          </svg>
          <span>Search</span>
          <kbd>&#8984;K</kbd>
        </button>
      </div>
    </div>
  </div>

  <main class="content" id="main">
    <!-- ── Chat ──────────────────────────────────────────────────────────── -->
    <section class="tab active" id="tab-chat" role="tabpanel" aria-labelledby="tabbtn-chat" tabindex="-1">
      <div class="phead">
        <div class="phead-text">
          <h2>Chat</h2>
          <p>Send a prompt straight through this server and watch it arrive token by token.
             Transcripts live in this browser; the upstream is single-turn, so every turn resends the conversation.</p>
        </div>
      </div>

      <div class="chat">
        <aside class="rail glass-1" aria-label="Conversations">
          <div class="rail-head">
            <span class="lbl">Conversations</span>
            <button class="btn btn--ghost btn--icon btn--sm" id="newChat" type="button"
                    aria-label="New chat" data-tip="New chat &#8984;N" data-tip-pos="below">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M12 5.5v13M5.5 12h13"/>
              </svg>
            </button>
          </div>
          <div class="rail-list" id="convList"></div>
          <p class="rail-foot">Saved in this browser only. Nothing is stored server-side.</p>
        </aside>

        <div class="thread glass-1">
          <div class="thread-bar">
            <label class="sr-only" for="model">Model</label>
            <select id="model" data-tip="Model ID" data-tip-pos="below"></select>
            <label class="sr-only" for="think">Thinking depth</label>
            <select id="think" data-tip="Thinking depth" data-tip-pos="below">
              <option value="">think: model default</option>
              <option value="0">think 0 — deepest</option>
              <option value="1">think 1</option>
              <option value="2">think 2 — medium</option>
              <option value="3">think 3</option>
              <option value="4">think 4 — shallowest</option>
            </select>
            <label class="switch is-sm" data-tip="Stream tokens as they are generated" data-tip-pos="below">
              <input type="checkbox" id="stream" checked>
              <span class="sw" aria-hidden="true"></span><span class="sw-txt">stream</span>
            </label>
            <div class="spacer"></div>
            <span class="key-field" data-tip="Sent as a bearer token, kept in this browser only" data-tip-pos="below">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <rect x="5" y="10.5" width="14" height="9.5" rx="2.6"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/>
              </svg>
              <input id="apikey" type="password" placeholder="API key (if required)" aria-label="API key"
                     autocomplete="off" spellcheck="false">
            </span>
          </div>

          <div class="thread-body">
            <div class="msgs" id="msgs" role="log" aria-live="polite" aria-relevant="additions text"
                 aria-label="Conversation transcript"></div>
            <button class="tobottom" id="toBottom" type="button" aria-label="Scroll to the latest message"
                    data-tip="Latest message" data-tip-pos="left">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="m5.5 9 6.5 6.5L18.5 9"/>
              </svg>
            </button>
          </div>

          <div class="composer">
            <div class="well">
              <label class="sr-only" for="prompt">Message</label>
              <textarea id="prompt" rows="1" placeholder="Ask something&hellip;" enterkeyhint="send"></textarea>
            </div>
            <div class="composer-bar">
              <span class="slot">
                <button class="btn btn--primary" id="send" type="button" data-tip="Send — Enter" data-tip-pos="below">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M12 19V5.6M12 5.6 6.2 11.4M12 5.6l5.8 5.8"/>
                  </svg>
                  Send
                </button>
                <button class="btn btn--glass" id="stop" type="button" hidden>
                  <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                    <rect x="7.5" y="7.5" width="9" height="9" rx="2.2"/>
                  </svg>
                  Stop
                </button>
              </span>
              <button class="btn btn--ghost" id="clearConv" type="button" data-tip="Clear this chat" data-tip-pos="below">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M5 7.6h14M9.6 7.6V5.9c0-.7.6-1.3 1.3-1.3h2.2c.7 0 1.3.6 1.3 1.3v1.7M6.9 7.6l.8 10.2c.06.8.73 1.4 1.53 1.4h5.54c.8 0 1.47-.6 1.53-1.4l.8-10.2"/>
                </svg>
                Clear
              </button>
              <div class="spacer"></div>
              <span class="note mono" id="chatMeta" aria-live="polite"></span>
              <span class="hint"><kbd>Enter</kbd> send &middot; <kbd>Shift</kbd>+<kbd>Enter</kbd> new line</span>
            </div>
          </div>
        </div>
      </div>

      <p class="chat-footnote">Very long transcripts cost more tokens upstream and can eventually exceed what
        the upstream accepts — clearing or starting a new chat keeps things fast.</p>
    </section>

    <!-- ── Status ────────────────────────────────────────────────────────── -->
    <section class="tab" id="tab-status" role="tabpanel" aria-labelledby="tabbtn-status" tabindex="-1">
      <div class="phead">
        <div class="phead-text">
          <h2>Status</h2>
          <p>Configuration, health checks, live metrics and the accounts this server can draw on.
             Everything here comes from <code>/health</code> and <code>/status</code>.</p>
        </div>
        <div class="phead-actions">
          <span class="note" id="statusMeta" aria-live="polite"></span>
          <label class="switch is-sm"><input type="checkbox" id="autoStatus" checked>
            <span class="sw" aria-hidden="true"></span><span class="sw-txt">Auto 10s</span></label>
          <button class="btn btn--glass btn--sm" id="refreshStatus" type="button">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M4.6 12a7.4 7.4 0 0 1 12.6-5.2L20 9.4M20 4.6v4.8h-4.8M19.4 12a7.4 7.4 0 0 1-12.6 5.2L4 14.6M4 19.4v-4.8h4.8"/>
            </svg>
            Refresh
          </button>
        </div>
      </div>

      <div class="hairline" id="statusProgress" aria-hidden="true"></div>
      <div class="callout-list" id="statusWarn"></div>

      <h3 class="slabel">Runtime</h3>
      <div class="tiles" id="runtime"></div>

      <h3 class="slabel">Health checks</h3>
      <div class="callout-list" id="checks"></div>

      <h3 class="slabel">Accounts</h3>
      <div id="accountsNote"></div>
      <div class="tablewrap glass-1 panel--flush">
        <div class="scroller">
          <table class="stack-rows">
            <caption class="sr-only">Cookie pool accounts and their current state</caption>
            <thead><tr><th>Account</th><th>Google index</th><th>SAPISID</th><th>Uses</th><th>State</th></tr></thead>
            <tbody id="accounts"></tbody>
          </table>
        </div>
      </div>

      <h3 class="slabel">Counters</h3>
      <div class="tiles" id="counters"></div>

      <h3 class="slabel">Latency</h3>
      <div class="tiles" id="latency"></div>
      <div class="chart glass-1" id="latencyChart"></div>

      <h3 class="slabel">By model</h3>
      <div class="tablewrap glass-1 panel--flush">
        <div class="scroller">
          <table class="stack-rows">
            <caption class="sr-only">Upstream calls per model</caption>
            <thead><tr><th>Model</th><th>Requests</th><th>Average</th><th>Slowest</th></tr></thead>
            <tbody id="byModel"></tbody>
          </table>
        </div>
      </div>

      <h3 class="slabel">Status codes</h3>
      <div class="tablewrap glass-1 panel--flush">
        <div class="scroller">
          <table class="stack-rows">
            <caption class="sr-only">Responses by status code</caption>
            <thead><tr><th>Code</th><th>Count</th></tr></thead>
            <tbody id="byStatus"></tbody>
          </table>
        </div>
      </div>

      <h3 class="slabel">Configuration (redacted)</h3>
      <div class="tablewrap glass-1 panel--flush">
        <div class="scroller">
          <table class="stack-rows">
            <caption class="sr-only">Effective configuration with secrets redacted</caption>
            <thead><tr><th>Key</th><th>Value</th></tr></thead>
            <tbody id="cfgTable"></tbody>
          </table>
        </div>
      </div>
    </section>

    <!-- ── Activity ──────────────────────────────────────────────────────── -->
    <section class="tab" id="tab-activity" role="tabpanel" aria-labelledby="tabbtn-activity" tabindex="-1">
      <div class="phead">
        <div class="phead-text">
          <h2>Activity</h2>
          <p>The most recent requests this server handled, newest first. Operational facts only —
             never prompts, response bodies or credentials.</p>
        </div>
        <div class="phead-actions">
          <span class="note" id="actMeta" aria-live="polite"></span>
          <label class="switch is-sm"><input type="checkbox" id="autoAct" checked>
            <span class="sw" aria-hidden="true"></span><span class="sw-txt">Auto 5s</span></label>
          <label class="sr-only" for="actFilter">Filter requests</label>
          <select id="actFilter" style="width:auto">
            <option value="">All requests</option>
            <option value="err">Errors only</option>
            <option value="/v1/chat">Chat completions</option>
            <option value="/v1/responses">Responses</option>
            <option value="/v1beta">Google-native</option>
          </select>
          <button class="btn btn--glass btn--sm" id="refreshAct" type="button">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M4.6 12a7.4 7.4 0 0 1 12.6-5.2L20 9.4M20 4.6v4.8h-4.8M19.4 12a7.4 7.4 0 0 1-12.6 5.2L4 14.6M4 19.4v-4.8h4.8"/>
            </svg>
            Refresh
          </button>
        </div>
      </div>

      <div class="hairline" id="actProgress" aria-hidden="true"></div>
      <div class="callout-list" id="actNote"></div>

      <div class="tablewrap glass-1 panel--flush">
        <div class="scroller grow">
          <table class="stack-rows">
            <caption class="sr-only">Recent requests</caption>
            <thead><tr><th>Time</th><th>Method</th><th>Path</th><th>Status</th>
              <th>Model</th><th>Latency</th><th>Client</th><th>Request ID</th></tr></thead>
            <tbody id="actRows"></tbody>
          </table>
        </div>
        <p class="tnote">Query strings are stripped before recording, because Google-native clients
          may pass an API key in one. Retention is set by <code>history_max</code>.</p>
      </div>
    </section>

    <!-- ── Models ────────────────────────────────────────────────────────── -->
    <section class="tab" id="tab-models" role="tabpanel" aria-labelledby="tabbtn-models" tabindex="-1">
      <div class="phead">
        <div class="phead-text">
          <h2>Models</h2>
          <p>Every ID accepted by <code>/v1/chat/completions</code>. Append <code>@think=N</code> to override
             thinking depth, <code>0</code> deepest to <code>4</code> shallowest.</p>
        </div>
        <div class="phead-actions">
          <span class="search">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <circle cx="11" cy="11" r="6.2"/><path d="m15.6 15.6 3.9 3.9"/>
            </svg>
            <label class="sr-only" for="modelFilter">Filter models</label>
            <input id="modelFilter" type="search" placeholder="Filter models" autocomplete="off" spellcheck="false">
          </span>
          <span class="note mono" id="modelCount"></span>
        </div>
      </div>
      <div class="tiles" id="models"></div>
      <div class="empty" id="modelsEmpty" hidden>
        <span class="empty-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="11" cy="11" r="6.2"/><path d="m15.6 15.6 3.9 3.9"/>
          </svg>
        </span>
        <h4>No model matches that filter</h4>
        <p>Try a shorter query — every ID accepted by <code>/v1/chat/completions</code> is listed here.</p>
        <button class="btn btn--ghost btn--sm" id="modelFilterClear" type="button">Clear filter</button>
      </div>
      <p class="chat-footnote">Models marked <span class="tag cookie">cookie</span> need an entitled Google
        session to route for real; without one they silently fall back to Flash.</p>
    </section>

    <!-- ── API ───────────────────────────────────────────────────────────── -->
    <section class="tab" id="tab-api" role="tabpanel" aria-labelledby="tabbtn-api" tabindex="-1">
      <div class="phead">
        <div class="phead-text">
          <h2>API</h2>
          <p>Drop-in OpenAI compatibility plus a Google-native surface for Gemini CLI.
             Full reference in <code>docs/API.md</code> in the repository.</p>
        </div>
      </div>

      <h3 class="slabel">Endpoints</h3>
      <div class="tablewrap glass-1 panel--flush">
        <div class="scroller">
          <table class="stack-rows">
            <caption class="sr-only">HTTP endpoints</caption>
            <thead><tr><th>Method</th><th>Path</th><th>Purpose</th></tr></thead>
            <tbody id="endpoints"></tbody>
          </table>
        </div>
      </div>

      <h3 class="slabel">Client configuration</h3>
      <div class="tablewrap glass-1 panel--flush">
        <div class="scroller">
          <table class="stack-rows">
            <caption class="sr-only">Values to configure in a client</caption>
            <thead><tr><th>Field</th><th>Value</th><th><span class="sr-only">Copy</span></th></tr></thead>
            <tbody id="clientCfg"></tbody>
          </table>
        </div>
      </div>

      <h3 class="slabel">curl</h3>
      <div class="panel glass-1 panel--flush">
        <div class="panel-head">
          <h3>Copy-ready examples</h3>
          <span class="sub">Replace <code>YOUR_KEY</code> when authentication is enabled.</span>
          <span class="spacer"></span>
          <button class="copy btn btn--ghost btn--sm" id="copyCurl" type="button" data-copy-target="curlBox">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <rect x="9" y="9" width="11.5" height="11.5" rx="2.6"/>
              <path d="M5.5 15.5A2.5 2.5 0 0 1 3 13V5.5A2.5 2.5 0 0 1 5.5 3H13a2.5 2.5 0 0 1 2.5 2.5"/>
            </svg>
            <span class="lbl">Copy</span>
          </button>
        </div>
        <div class="panel-body"><pre id="curlBox"></pre></div>
      </div>
    </section>
  <footer class="foot">
    <span id="foot"></span>
  </footer>
  </main>

</div>

<!-- ── Toasts: layer 6, above everything that floats ───────────────────── -->
<div class="toasts" id="toasts" role="status" aria-live="polite" aria-atomic="false"></div>

<!-- ── Command palette: layer 5 ────────────────────────────────────────── -->
<div class="veil veil--top" id="paletteVeil" hidden>
  <div class="palette glass-4" role="dialog" aria-modal="true" aria-labelledby="paletteLabel">
    <h3 class="sr-only" id="paletteLabel">Search and jump</h3>
    <div class="pal-input">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <circle cx="11" cy="11" r="6.2"/><path d="m15.6 15.6 3.9 3.9"/>
      </svg>
      <label class="sr-only" for="paletteInput">Search sections, conversations and actions</label>
      <input id="paletteInput" type="text" placeholder="Jump to a section, a chat, or an action&hellip;"
             autocomplete="off" spellcheck="false" role="combobox" aria-expanded="true"
             aria-controls="paletteList" aria-autocomplete="list">
      <kbd>Esc</kbd>
    </div>
    <div class="pal-list" id="paletteList" role="listbox" aria-label="Results"></div>
    <div class="pal-foot">
      <span><kbd>&uarr;</kbd><kbd>&darr;</kbd> navigate</span>
      <span><kbd>Enter</kbd> run</span>
      <span><kbd>Esc</kbd> close</span>
    </div>
  </div>
</div>

<!-- ── Keyboard shortcuts: layer 5 ────────────────────────────────────── -->
<div class="veil" id="helpVeil" hidden>
  <div class="sheet sheet--sm glass-4" role="dialog" aria-modal="true" aria-labelledby="helpLabel">
    <div class="sheet-head">
      <h3 id="helpLabel">Keyboard shortcuts<span class="sub">A keyboard is optional — every one of these has a button.</span></h3>
      <button class="btn btn--quiet btn--icon" id="helpClose" type="button" aria-label="Close">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>
        </svg>
      </button>
    </div>
    <div class="sheet-body">
      <div class="keys" id="keyList"></div>
    </div>
  </div>
</div>

<script>
const STATE = @@STATE@@;

/* ─────────────────────────────────────────────────────────────────────────
   Small helpers
   ───────────────────────────────────────────────────────────────────────── */
const $ = (id) => document.getElementById(id);
const $$ = (sel, root) => Array.prototype.slice.call((root || document).querySelectorAll(sel));
const LS = {
  key: 'gw2a.key', convs: 'gw2a.convs', active: 'gw2a.active',
  model: 'gw2a.model', think: 'gw2a.think', stream: 'gw2a.stream',
  theme: 'gw2a.theme',
};
const get = (k, d) => { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (_) { return d; } };
const set = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) {} };
const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));
const motionOK = () => !(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
const coarse = () => !!(window.matchMedia && window.matchMedia('(pointer: coarse)').matches);
const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');
const modKey = isMac ? '\u2318' : 'Ctrl';

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
const fmtMs = (ms) => ms == null ? '\u2014' : (ms < 1000 ? Math.round(ms) + ' ms' : (ms/1000).toFixed(2) + ' s');
const fmtTime = (ts) => {
  if (!ts) return '\u2014';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], {hour12:false});
};
/* Relative time for lists: compact, monotonic, and cheap. */
const fmtAgo = (ts) => {
  if (!ts) return '\u2014';
  const secs = Math.max(0, Date.now()/1000 - ts);
  if (secs < 45) return 'just now';
  if (secs < 3600) return Math.floor(secs/60) + 'm ago';
  if (secs < 86400) return Math.floor(secs/3600) + 'h ago';
  if (secs < 604800) return Math.floor(secs/86400) + 'd ago';
  return new Date(ts * 1000).toLocaleDateString([], {month:'short', day:'numeric'});
};
/* Compact numbers for axis labels: 1200 -> 1.2k */
const fmtCount = (n) => {
  n = Number(n) || 0;
  if (n < 1000) return String(n);
  if (n < 1000000) return (Math.round(n/100)/10) + 'k';
  return (Math.round(n/100000)/10) + 'M';
};
const pill = (text, level) => '<span class="tag ' + (level || '') + '">' + esc(text) + '</span>';
const statusPill = (code) => {
  const lvl = code >= 500 ? 'bad' : code >= 400 ? 'warn' : 'ok';
  return '<span class="tag ' + lvl + '"><span class="tdot"></span>' + esc(code) + '</span>';
};

/* ── the cookie pool ───────────────────────────────────────────────────
 *
 * Two pure functions rather than inline template code in refreshStatus, so the
 * Node harness can execute them: the account *source* is a path the operator
 * chose and the last error is a string from upstream, and both end up in
 * innerHTML. Anything that reaches innerHTML from outside this file is escaped,
 * and this is the only way to prove it rather than assert it.
 *
 * Both emit only the tags the harness knows about (tr/td/p/code/span), so the
 * hostile-input check can strip this file's own markup and prove that nothing
 * else survives.
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
    return '<tr><td colspan="5" class="empty-cell" data-label="">' +
           '<span class="note">No accounts configured.</span></td></tr>';
  }
  const total = Number(c.cooldown_sec) || 0;
  return entries.map((a) => {
    const cooling = !a.usable;
    const secs = Number(a.cooldown_remaining_sec) || 0;
    // `pill` escapes its own text, so the error string is passed through raw —
    // escaping it here as well would render "&" as "&amp;amp;".
    const state = cooling
      ? pill('cooling ' + (secs > 0 ? Math.ceil(secs) + 's' : '') +
             (a.last_error ? ' (HTTP ' + a.last_error + ')' : ''), 'warn')
      : pill('ready', 'ok');
    // The cooldown fraction comes from the pool's own window, so the meter is
    // data, not decoration: it shows how long the account still rests.
    const pct = cooling && total > 0 ? Math.min(100, Math.max(0, Math.round(secs / total * 100))) : 0;
    const meter = cooling && pct > 0
      ? '<span class="meter" aria-hidden="true"><span class="track">' +
        '<span class="fill" style="width:' + pct + '%"></span></span>' +
        '<span class="val">' + esc(Math.ceil(secs)) + 's</span></span>'
      : '';
    return '<tr><td data-label="Account"><code>' + esc(a.source) + '</code></td>' +
      '<td data-label="Google index">' + esc(a.auth_user == null ? '\u2014' : a.auth_user) + '</td>' +
      '<td data-label="SAPISID">' + (a.has_sapisid ? 'yes' : '<span class="note">no</span>') + '</td>' +
      '<td data-label="Uses">' + esc(Number(a.uses) || 0) + '</td>' +
      '<td data-label="State">' + state + meter + '</td></tr>';
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

/* ─────────────────────────────────────────────────────────────────────────
   Icons shared by generated markup (same geometry as the static ones)
   ───────────────────────────────────────────────────────────────────────── */
const ICON = {
  spark: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
    '<path d="M12 2.6c.5 3.7 1.65 4.85 5.35 5.35-3.7.5-4.85 1.65-5.35 5.35-.5-3.7-1.65-4.85-5.35-5.35C10.35 7.45 11.5 6.3 12 2.6Z"/>' +
    '<path d="M17.8 14.2c.28 2.1.94 2.76 3.04 3.04-2.1.28-2.76.94-3.04 3.04-.28-2.1-.94-2.76-3.04-3.04 2.1-.28 2.76-.94 3.04-3.04Z"/></svg>',
  user: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<circle cx="12" cy="9" r="3.4"/><path d="M5.6 19.6c.9-3.3 3.4-5 6.4-5s5.5 1.7 6.4 5"/></svg>',
  warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M12 4.6 3.4 19.2h17.2L12 4.6Z"/><path d="M12 10v4.1M12 17h.01"/></svg>',
  ok: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<circle cx="12" cy="12" r="8.6"/><path d="m8.3 12.3 2.6 2.6 4.8-5.2"/></svg>',
  bad: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<circle cx="12" cy="12" r="8.6"/><path d="M12 7.6v5.2M12 16.4h.01"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<circle cx="12" cy="12" r="8.6"/><path d="M12 11.2v5M12 8h.01"/></svg>',
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<rect x="9" y="9" width="11.5" height="11.5" rx="2.6"/>' +
    '<path d="M5.5 15.5A2.5 2.5 0 0 1 3 13V5.5A2.5 2.5 0 0 1 5.5 3H13a2.5 2.5 0 0 1 2.5 2.5"/></svg>',
  close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/></svg>',
  bubble: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M20.5 12.1c0 3.7-3.8 6.7-8.5 6.7-1 0-2-.14-2.9-.4L4.6 19.8l1.1-3.3c-1.3-1.2-2.2-2.7-2.2-4.4 0-3.7 3.8-6.7 8.5-6.7s8.5 3 8.5 6.7Z"/></svg>',
  pulse: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M3 13h3.7l2-5.4 3.3 9.8 2.2-6.5 1.6 3.4H21"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M4.6 12a7.4 7.4 0 0 1 12.6-5.2L20 9.4M20 4.6v4.8h-4.8M19.4 12a7.4 7.4 0 0 1-12.6 5.2L4 14.6M4 19.4v-4.8h4.8"/></svg>',
  moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M19.5 14.2A7.8 7.8 0 0 1 9.8 4.5 7.8 7.8 0 1 0 19.5 14.2Z"/></svg>',
};

/* ─────────────────────────────────────────────────────────────────────────
   Layer 6 — toasts
   ───────────────────────────────────────────────────────────────────────── */
const TOAST_ICON = { info: ICON.info, ok: ICON.ok, warn: ICON.warn, bad: ICON.bad };
const TOAST_MS = { info: 3200, ok: 3000, warn: 4200, bad: 5200 };
let toastTimer = null;

function toast(message, kind, opts){
  const region = $('toasts');
  if (!region) return;
  const level = TOAST_ICON[kind] ? kind : 'info';
  const o = opts || {};
  const el = document.createElement('div');
  el.className = 'toast glass-3 toast--' + level;
  el.innerHTML =
    '<span class="ico">' + TOAST_ICON[level] + '</span>' +
    '<span class="txt">' + (o.title ? '<strong>' + esc(o.title) + '</strong>' : '') +
    '<span class="' + (o.title ? 'sub' : '') + '">' + esc(message) + '</span></span>' +
    '<button class="x" type="button" aria-label="Dismiss">' + ICON.close + '</button>';
  // Cap the stack: the oldest leaves first so the newest is always readable.
  while (region.children.length >= 4) region.removeChild(region.firstChild);
  const go = () => {
    el.classList.add('is-closing');
    clearTimeout(toastTimer);
    setTimeout(() => { if (el.parentNode) el.parentNode.removeChild(el); }, 200);
  };
  el.querySelector('.x').addEventListener('click', go);
  region.appendChild(el);
  el._dismiss = go;
  toastTimer = setTimeout(go, o.ms || TOAST_MS[level]);
  return el;
}

/* ─────────────────────────────────────────────────────────────────────────
   Layer 5 — dialogs (command palette, shortcuts, anything modal)
   ───────────────────────────────────────────────────────────────────────── */
const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
let lastFocus = null;

function openVeil(veil){
  if (!veil || !veil.hidden) return;
  lastFocus = document.activeElement;
  veil.hidden = false;
  veil.classList.remove('is-closing');
  document.body.classList.add('no-scroll');
  const target = veil.querySelector('[data-autofocus]') || veil.querySelector(FOCUSABLE);
  if (target) setTimeout(() => target.focus(), 30);
}
function closeVeil(veil){
  if (!veil || veil.hidden) return;
  veil.classList.add('is-closing');
  const done = () => {
    veil.hidden = true;
    veil.classList.remove('is-closing');
    if (!$$('.veil:not([hidden])').length) document.body.classList.remove('no-scroll');
    if (lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch (_) {} }
    lastFocus = null;
  };
  if (motionOK()) setTimeout(done, 200); else done();
}
function veilKeydown(ev){
  const veil = ev.currentTarget;
  if (ev.key === 'Escape') { ev.preventDefault(); closeVeil(veil); return; }
  if (ev.key !== 'Tab') return;
  const items = $$(FOCUSABLE, veil).filter((n) => n.offsetParent !== null || n === document.activeElement);
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
  else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
}
$$('.veil').forEach((v) => {
  v.addEventListener('keydown', veilKeydown);
  // A click on the scrim itself (not the panel) dismisses.
  v.addEventListener('mousedown', (ev) => { if (ev.target === v) closeVeil(v); });
});

/* ─────────────────────────────────────────────────────────────────────────
   Chrome — theme, tabs, dock state
   ───────────────────────────────────────────────────────────────────────── */
const THEME_ORDER = ['auto', 'light', 'dark'];
function applyTheme(mode){
  const root = document.documentElement;
  if (mode === 'auto') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', mode);
  $$('#themeSeg .seg-btn').forEach((b) =>
    b.setAttribute('aria-checked', String(b.dataset.themeOpt === mode)));
  const dark = mode === 'dark' ||
    (mode === 'auto' && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
  root.style.colorScheme = dark ? 'dark' : 'light';
}
function pickTheme(mode){
  set(LS.theme, mode);
  applyTheme(mode);
}
$$('#themeSeg .seg-btn').forEach((btn) => {
  btn.addEventListener('click', () => pickTheme(btn.dataset.themeOpt));
  btn.addEventListener('keydown', (ev) => {
    if (ev.key !== 'ArrowLeft' && ev.key !== 'ArrowRight') return;
    ev.preventDefault();
    const opts = $$('#themeSeg .seg-btn');
    const i = opts.indexOf(btn);
    const next = opts[(i + (ev.key === 'ArrowRight' ? 1 : opts.length - 1)) % opts.length];
    next.focus(); pickTheme(next.dataset.themeOpt);
  });
});
applyTheme(get(LS.theme, 'auto'));
if (window.matchMedia) {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change', () => {
    if (get(LS.theme, 'auto') === 'auto') applyTheme('auto');
  });
}

/* Tabs: one roving tabindex, a glider that travels, panels that fade in. */
const tabButtons = $$('nav.tabs .tab-btn');
const glider = document.querySelector('nav.tabs .glider');
let activeTab = 'chat';

function moveGlider(instant){
  const btn = tabButtons.find((b) => b.dataset.tab === activeTab);
  if (!glider || !btn) return;
  if (instant) glider.style.transition = 'none';
  glider.style.width = btn.offsetWidth + 'px';
  glider.style.transform = 'translateX(' + btn.offsetLeft + 'px)';
  if (instant) { void glider.offsetWidth; glider.style.transition = ''; }
  glider.classList.add('ready');
}
function selectTab(name, opts){
  const o = opts || {};
  if (!tabButtons.some((b) => b.dataset.tab === name)) return;
  activeTab = name;
  tabButtons.forEach((b) => {
    const on = b.dataset.tab === name;
    b.setAttribute('aria-selected', String(on));
    b.tabIndex = on ? 0 : -1;
  });
  $$('section.tab').forEach((s) => s.classList.toggle('active', s.id === 'tab-' + name));
  document.title = (TAB_TITLES[name] || 'Console') + ' · gemini-web2api';
  moveGlider(false);
  if (name === 'status') refreshStatus(o.force);
  if (name === 'activity') refreshActivity(o.force);
  if (name === 'chat' && !coarse()) $('prompt').focus();
  resetPaneScroll(o.keepScroll);
}
/* The hint follows the platform: a Mac user reads ⌘, everyone else Ctrl. */
$$('#paletteBtn kbd').forEach((k) => { k.textContent = modKey + ' K'; });
const TAB_TITLES = {chat: 'Chat', status: 'Status', activity: 'Activity', models: 'Models', api: 'API'};
tabButtons.forEach((btn) => {
  btn.addEventListener('click', () => selectTab(btn.dataset.tab));
  btn.addEventListener('keydown', (ev) => {
    const keys = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -1, ArrowDown: 1 };
    if (ev.key in keys) {
      ev.preventDefault();
      const i = tabButtons.indexOf(btn);
      const next = tabButtons[clamp(i + keys[ev.key], 0, tabButtons.length - 1)];
      next.focus(); selectTab(next.dataset.tab, {keepScroll: true});
    } else if (ev.key === 'Home' || ev.key === 'End') {
      ev.preventDefault();
      const next = ev.key === 'Home' ? tabButtons[0] : tabButtons[tabButtons.length - 1];
      next.focus(); selectTab(next.dataset.tab, {keepScroll: true});
    }
  });
});
window.addEventListener('resize', () => moveGlider(true));
// The glider must follow the labels, not just the layout: fonts land late and
// a webfont-free page still reflows once the CSS is fully applied.
if (window.ResizeObserver) new ResizeObserver(() => moveGlider(true)).observe(document.querySelector('nav.tabs'));
if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => moveGlider(true));

/* Switching sections returns the reader to the top of the new one. */
function resetPaneScroll(keep){
  if (keep) return;
  const pane = $('main');
  if (!pane || pane.scrollTop <= 140) return;
  if (motionOK()) pane.scrollTo({top: 0, behavior: 'smooth'});
  else pane.scrollTop = 0;
}

/* Content that slides under the dock deepens its shadow, nothing more. The
   page itself does not scroll - the content pane does - so watch both. */
const dock = $('dock');
const scroller = $('main');
const onScroll = () => {
  if (!dock) return;
  const y = Math.max(window.scrollY || 0, scroller ? scroller.scrollTop : 0);
  dock.classList.toggle('is-stuck', y > 8);
};
if (scroller) scroller.addEventListener('scroll', onScroll, {passive: true});
window.addEventListener('scroll', onScroll, {passive: true});
onScroll();

/* ─────────────────────────────────────────────────────────────────────────
   Command palette — search, jump, act
   ───────────────────────────────────────────────────────────────────────── */
let palItems = [];
let palIndex = 0;

function paletteSource(){
  const items = [];
  tabButtons.forEach((b, i) => items.push({
    group: 'Sections', label: b.querySelector('.lbl').textContent.trim(), hint: modKey + (i + 1),
    icon: b.querySelector('.ico').outerHTML, run: () => selectTab(b.dataset.tab),
  }));
  items.push({
    group: 'Actions', label: 'New chat', hint: 'New conversation in this browser', icon: ICON.bubble,
    run: () => { selectTab('chat'); newChat(); },
  });
  items.push({
    group: 'Actions', label: 'Refresh this view', hint: 'Re-read /status',
    icon: ICON.refresh, run: () => { if (activeTab === 'activity') refreshActivity(true); else refreshStatus(true); },
  });
  items.push({
    group: 'Actions', label: 'Copy base URL', hint: STATE.base_url || '', icon: ICON.copy,
    run: () => copyText(STATE.base_url || '', 'Base URL'),
  });
  items.push({
    group: 'Actions', label: 'Appearance: cycle light / dark / auto', hint: 'Follows the system by default',
    icon: ICON.moon, run: () => {
      const cur = get(LS.theme, 'auto');
      const next = THEME_ORDER[(THEME_ORDER.indexOf(cur) + 1) % THEME_ORDER.length];
      set(LS.theme, next); applyTheme(next); toast('Appearance: ' + next, 'ok');
    },
  });
  items.push({
    group: 'Actions', label: 'Keyboard shortcuts', hint: '?', icon: ICON.info, run: () => openVeil($('helpVeil')),
  });
  convs.slice(0, 30).forEach((c) => items.push({
    group: 'Conversations', label: c.title || 'New chat',
    hint: c.messages.filter((m) => m.role === 'user').length + ' messages',
    icon: ICON.bubble,
    run: () => { selectTab('chat'); activeId = c.id; persist(); renderConvs(); renderMsgs(); },
  }));
  return items;
}
function palMatch(item, q){
  if (!q) return true;
  // Substring first, subsequence second, and only ever against the label:
  // matching the group name as well lets "act" match everything in Actions.
  const label = item.label.toLowerCase();
  const needle = q.toLowerCase();
  if (label.indexOf(needle) !== -1) return true;
  let i = 0;
  for (const ch of needle) {
    i = label.indexOf(ch, i);
    if (i === -1) return false;
    i += 1;
  }
  return true;
}
function renderPalette(){
  const q = $('paletteInput').value.trim();
  palItems = paletteSource().filter((it) => palMatch(it, q));
  palIndex = clamp(palIndex, 0, Math.max(0, palItems.length - 1));
  const list = $('paletteList');
  if (!palItems.length) {
    list.innerHTML = '<p class="pal-empty">Nothing matches &ldquo;' + esc(q) + '&rdquo;.</p>';
    $('paletteInput').removeAttribute('aria-activedescendant');
    return;
  }
  let html = '', group = null;
  palItems.forEach((it, i) => {
    if (it.group !== group) {
      group = it.group;
      html += '<div class="pal-group" role="group" aria-label="' + esc(group) + '">' + esc(group) + '</div>';
    }
    html += '<div class="pal-item" role="option" id="pal-opt-' + i + '" data-i="' + i + '"' +
      ' aria-selected="' + (i === palIndex) + '">' +
      '<span class="ico">' + it.icon + '</span>' +
      '<span class="t">' + esc(it.label) + '</span>' +
      (it.hint ? '<span class="kbd">' + esc(it.hint) + '</span>' : '') + '</div>';
  });
  list.innerHTML = html;
  $('paletteInput').setAttribute('aria-activedescendant', 'pal-opt-' + palIndex);
  const sel = list.querySelector('[aria-selected="true"]');
  if (sel && sel.scrollIntoView) sel.scrollIntoView({block: 'nearest'});
}
function palMove(delta){
  if (!palItems.length) return;
  palIndex = (palIndex + delta + palItems.length) % palItems.length;
  renderPalette();
}
function palRun(){
  const it = palItems[palIndex];
  closeVeil($('paletteVeil'));
  if (it) setTimeout(() => it.run(), 60);
}
function openPalette(){
  palIndex = 0;
  $('paletteInput').value = '';
  renderPalette();
  openVeil($('paletteVeil'));
  setTimeout(() => $('paletteInput').focus(), 40);
}
if ($('paletteBtn')) $('paletteBtn').addEventListener('click', openPalette);
$('paletteInput').addEventListener('input', () => { palIndex = 0; renderPalette(); });
$('paletteInput').addEventListener('keydown', (ev) => {
  if (ev.key === 'ArrowDown') { ev.preventDefault(); palMove(1); }
  else if (ev.key === 'ArrowUp') { ev.preventDefault(); palMove(-1); }
  else if (ev.key === 'Enter') { ev.preventDefault(); palRun(); }
});
$('paletteList').addEventListener('click', (ev) => {
  const opt = ev.target.closest('.pal-item');
  if (!opt) return;
  palIndex = Number(opt.dataset.i) || 0;
  palRun();
});
$('paletteList').addEventListener('mousemove', (ev) => {
  const opt = ev.target.closest('.pal-item');
  if (opt && Number(opt.dataset.i) !== palIndex) { palIndex = Number(opt.dataset.i) || 0; renderPalette(); }
});

/* Shortcut sheet — the same list the global handler implements. */
const SHORTCUTS = [
  [modKey + ' K', 'Search sections, chats and actions'],
  [modKey + ' 1 – 5', 'Jump to a section'],
  ['?', 'Show this list'],
  ['/', 'Focus the message box'],
  ['Enter', 'Send the message'],
  ['Shift Enter', 'New line in the message'],
  ['Esc', 'Close dialogs, dismiss focus'],
  [modKey + ' N', 'New chat'],
];
$('keyList').innerHTML = SHORTCUTS.map(([k, label]) =>
  '<div class="key-row"><span class="t">' + esc(label) + '</span><span class="ks">' +
  k.split(' ').map((part) => '<kbd>' + esc(part) + '</kbd>').join('') + '</span></div>').join('');
$('helpBtn').addEventListener('click', () => openVeil($('helpVeil')));
$('helpClose').addEventListener('click', () => closeVeil($('helpVeil')));

document.addEventListener('keydown', (ev) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((ev.target.tagName || '')) || ev.target.isContentEditable;
  const mod = ev.metaKey || ev.ctrlKey;
  if (mod && ev.key.toLowerCase() === 'k') { ev.preventDefault(); $('paletteVeil').hidden ? openPalette() : closeVeil($('paletteVeil')); return; }
  if (mod && /^[1-5]$/.test(ev.key)) {
    const name = ['chat', 'status', 'activity', 'models', 'api'][Number(ev.key) - 1];
    ev.preventDefault(); selectTab(name); return;
  }
  if (mod && ev.key.toLowerCase() === 'n' && !typing) { ev.preventDefault(); selectTab('chat'); newChat(); return; }
  if (typing) return;
  if (ev.key === '?') { ev.preventDefault(); openVeil($('helpVeil')); return; }
  if (ev.key === '/') { ev.preventDefault(); selectTab('chat', {keepScroll: true}); $('prompt').focus(); }
});

/* ─────────────────────────────────────────────────────────────────────────
   Clipboard — secure contexts get the API, everything else a fallback
   ───────────────────────────────────────────────────────────────────────── */
async function copyText(text, what){
  let ok = false;
  try {
    if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); ok = true; }
  } catch (_) { ok = false; }
  if (!ok) {
    // http:// deployments on a LAN address have no clipboard API; the textarea
    // trick is the only way to keep Copy working there.
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.cssText = 'position:fixed;top:-1000px;opacity:0';
      document.body.appendChild(ta);
      ta.select();
      ok = document.execCommand('copy');
      document.body.removeChild(ta);
    } catch (_) { ok = false; }
  }
  toast(ok ? (what || 'Copied') + ' copied to the clipboard' : 'Copy failed — select the text and copy it manually',
        ok ? 'ok' : 'warn', {title: ok ? undefined : 'Clipboard unavailable'});
  return ok;
}
document.addEventListener('click', (ev) => {
  const btn = ev.target.closest('.copy');
  if (!btn) return;
  const target = btn.dataset.copyTarget;
  const text = target ? ($(target) ? $(target).textContent : '') : (btn.dataset.copy || '');
  if (!text) return;
  copyText(text, btn.dataset.copyLabel || (target ? 'Snippet' : 'Value'));
  // The inline confirmation stays: the button is where the user is looking,
  // and only its label span is touched so an icon survives untouched.
  const lbl = btn.querySelector('.lbl');
  if (!lbl) return;
  const old = lbl.textContent;
  btn.classList.add('done');
  lbl.textContent = 'Copied';
  setTimeout(() => { btn.classList.remove('done'); lbl.textContent = old; }, 1200);
});

/* ─────────────────────────────────────────────────────────────────────────
   Layer 1–2 — the console itself
   ───────────────────────────────────────────────────────────────────────── */
function setProgress(id, on){
  const el = $(id);
  if (el) el.classList.toggle('on', !!on);
}
/* Numbers that count to their new value: legible change, no layout shift. */
function setNum(el, value){
  const text = String(value ?? '0');
  const prev = el.dataset.n;
  el.dataset.n = text;
  if (prev === undefined || prev === text || !/^\d{1,7}$/.test(text) || !motionOK()) {
    el.textContent = text;
    return;
  }
  const from = Number(prev), to = Number(text), t0 = performance.now(), dur = 420;
  const step = (now) => {
    const p = clamp((now - t0) / dur, 0, 1);
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = String(Math.round(from + (to - from) * eased));
    if (p < 1) requestAnimationFrame(step); else el.textContent = text;
  };
  requestAnimationFrame(step);
}
function tile(k, v, opts){
  const o = opts || {};
  return '<div class="tile glass-1' + (o.accent ? ' tile--accent' : '') + '" style="--i:' + (o.i || 0) + '">' +
    '<span class="k">' + esc(k) + '</span>' +
    '<span class="v' + (o.mono ? ' mono' : '') + '"' + (o.num ? ' data-n="' + esc(v) + '"' : '') + '>' +
    esc(v ?? '\u2014') + '</span>' +
    (o.hint ? '<span class="hintline">' + esc(o.hint) + '</span>' : '') + '</div>';
}
function skeletonTiles(n, id){
  const box = $(id);
  if (!box) return;
  let html = '';
  for (let i = 0; i < n; i++) {
    html += '<div class="tile glass-1 sk-tile" aria-hidden="true"><span class="k sk sk--line" style="width:52%">.</span>' +
      '<span class="v sk sk--val">.</span></div>';
  }
  box.innerHTML = html;
}
function skeletonRows(tbody, cols, rows){
  const box = $(tbody);
  if (!box) return;
  let html = '';
  for (let r = 0; r < rows; r++) {
    html += '<tr aria-hidden="true" class="sk-row">';
    for (let c = 0; c < cols; c++) {
      html += '<td data-label=""><span class="sk sk--line" style="width:' + (c === 0 ? 62 : 40) + '%">.</span></td>';
    }
    html += '</tr>';
  }
  box.innerHTML = html;
}

/* ── Runtime summary ─────────────────────────────────────────────────────── */
function renderStatic(){
  $('version').textContent = STATE.version;
  $('uptime').textContent = fmtUptime(STATE.uptime_sec);

  const cards = [
    ['Base URL', STATE.base_url, true],
    ['Streaming', STATE.streaming],
    ['API keys', STATE.api_keys],
    ['Cookie', STATE.cookie],
    ['Default model', STATE.default_model],
    ['Build tag (bl)', STATE.gemini_bl],
    ['Proxy', STATE.proxy || 'system / none'],
    ['Rate limit', STATE.rate_limit],
    ['Temporary chats', STATE.temporary_chats ? 'yes' : 'no'],
    ['Requests served', STATE.requests_served, true, true],
    ['History retained', STATE.history_enabled ? 'yes' : 'disabled'],
    ['Python', STATE.python],
  ];
  $('runtime').innerHTML = cards.map(([k, v, mono, num], i) =>
    tile(k, v, {mono: !!mono, num: !!num, i: i})).join('');

  const models = STATE.models || [];
  $('modelCount').textContent = models.length + (models.length === 1 ? ' model' : ' models');
  $('models').innerHTML = models.map((m, i) =>
    '<article class="tile glass-1 model-card" style="--i:' + i + '" data-search="' +
      esc([m.id, m.category, m.desc, m.output, m.think].join(' ').toLowerCase()) + '">' +
      '<div class="model-top"><code class="model-id">' + esc(m.id) + '</code>' +
      '<button class="copy" type="button" data-copy="' + esc(m.id) + '" data-copy-label="Model ID"' +
      ' aria-label="Copy model ID ' + esc(m.id) + '"><span class="lbl">copy</span></button></div>' +
      '<div class="model-tags">' + pill(m.category) +
        (m.needs_cookie ? ' ' + pill('cookie', 'cookie') : '') +
        (m.think == null ? '' : ' ' + pill('think ' + m.think, 'acc')) + '</div>' +
      '<p class="model-desc">' + esc(m.desc) + '</p>' +
      '<p class="hintline">typical output ' + esc(m.output || 'varies') + '</p>' +
    '</article>').join('');

  $('endpoints').innerHTML = (STATE.endpoints || []).map((e) =>
    '<tr><td data-label="Method">' + pill(e[0]) + '</td>' +
    '<td data-label="Path"><code>' + esc(e[1]) + '</code></td>' +
    '<td data-label="Purpose">' + esc(e[2]) + '</td></tr>').join('');

  const base = STATE.base_url || '';
  $('clientCfg').innerHTML = [
    ['Base URL', base],
    ['API key', STATE.auth_enabled ? '(one of your configured api_keys)' : '(anything — auth is disabled)'],
    ['Model', STATE.default_model],
  ].map(([k, v]) =>
    '<tr><td data-label="Field">' + esc(k) + '</td>' +
    '<td data-label="Value"><code>' + esc(v) + '</code></td>' +
    '<td data-label=""><button class="copy" type="button" data-copy="' + esc(v) +
    '" data-copy-label="' + esc(k) + '" aria-label="Copy ' + esc(k) + '"><span class="lbl">copy</span></button></td></tr>').join('');

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

  $('foot').innerHTML =
    '<span>gemini-web2api v' + esc(STATE.version) + '</span>' +
    '<span class="dot-sep" aria-hidden="true"></span><span>MIT licensed</span>' +
    '<span class="dot-sep" aria-hidden="true"></span><span>documentation in <code>docs/</code></span>';

  $('model').innerHTML = models.map((m) =>
    '<option value="' + esc(m.id) + '"' + (m.id === STATE.default_model ? ' selected' : '') +
    '>' + esc(m.id) + '</option>').join('');

  const savedModel = get(LS.model, null);
  if (savedModel && models.some((m) => m.id === savedModel)) $('model').value = savedModel;
  const savedThink = get(LS.think, '');
  if (savedThink !== '') $('think').value = String(savedThink);
  $('stream').checked = get(LS.stream, true);
  const savedKey = localStorage.getItem(LS.key);
  if (savedKey) $('apikey').value = savedKey;
}

/* Model filter — a view over the same list, never a second source of truth. */
$('modelFilter').addEventListener('input', () => {
  const q = $('modelFilter').value.trim().toLowerCase();
  const cards = $$('#models .model-card');
  let shown = 0;
  cards.forEach((c) => {
    const hit = !q || (c.dataset.search || '').indexOf(q) !== -1;
    c.hidden = !hit;
    if (hit) shown += 1;
  });
  const total = cards.length;
  $('modelCount').textContent = q ? shown + ' of ' + total : total + (total === 1 ? ' model' : ' models');
  const empty = $('modelsEmpty');
  if (empty) empty.hidden = shown !== 0;
});
$('modelFilterClear').addEventListener('click', () => {
  $('modelFilter').value = '';
  $('modelFilter').dispatchEvent(new Event('input'));
  $('modelFilter').focus();
});

/* ── authenticated fetch of /status ──────────────────────────────────────── */
async function fetchStatus(){
  const headers = {};
  const key = $('apikey').value.trim();
  if (key) headers['Authorization'] = 'Bearer ' + key;
  const res = await fetch('status', {headers});
  if (res.status === 401) { const e = new Error('401'); e.code = 401; throw e; }
  if (!res.ok) throw new Error('HTTP ' + res.status);
  return res.json();
}
function callout(kind, html){
  const icons = {ok: ICON.ok, warn: ICON.warn, bad: ICON.bad, info: ICON.info};
  // The body is its own block so the message keeps a readable measure (max-width
  // in CSS) instead of tracking the full width of a wide panel.
  return '<div class="callout callout--' + kind + '"><span class="ico">' + icons[kind] + '</span>' +
    '<div class="ctx-body">' + html + '</div></div>';
}
function needKeyNote(where, extra){
  $(where).innerHTML = callout('warn',
    '<strong>An API key unlocks this view.</strong> This panel reads <code>/status</code>, which ' +
    'requires one when authentication is enabled. Enter your key in the <strong>Chat</strong> tab' +
    (extra ? ' ' + esc(extra) : '') + '.');
}

let statusLoadedAt = 0;
let statusBusy = false;
async function refreshStatus(force){
  if (statusBusy) return;
  // Flipping between tabs re-reads nothing that was read a moment ago; a
  // manual Refresh always goes to the server.
  if (!force && statusLoadedAt && Date.now() - statusLoadedAt < 4000) return;
  statusBusy = true;
  setProgress('statusProgress', true);
  try {
    const s = await fetchStatus();
    statusLoadedAt = Date.now();
    $('statusWarn').innerHTML = '';
    const m = s.metrics || {}, c = m.counters || {};
    $('statusMeta').textContent = 'updated ' + new Date().toLocaleTimeString([], {hour12:false});

    const fatal = (s.checks && s.checks.fatal) || [];
    const warns = (s.checks && s.checks.warnings) || [];
    $('checks').innerHTML =
      fatal.map((f) => callout('bad', '<strong>Not ready.</strong> ' + esc(f))).join('') +
      warns.map((w) => callout('warn', esc(w))).join('') +
      (!fatal.length && !warns.length
        ? callout('ok', 'Every startup check passed. Streaming, authentication and cookie state are as configured.')
        : '');

    $('counters').innerHTML = Object.keys(c).map((k, i) =>
      tile(k.replace(/_/g, ' '), c[k], {num: true, i: i})).join('');

    const hist = m.latency_histogram_ms || {};
    $('latency').innerHTML = [
      ['Average', fmtMs(m.latency_ms_avg)],
      ['Samples', m.latency_ms_samples ?? 0],
      ['Uptime', fmtUptime(m.uptime_sec)],
    ].map(([k, v], i) => tile(k, v, {i: i, mono: k !== 'Uptime'})).join('');
    renderChart(hist, m.latency_ms_samples ?? 0);

    const models = m.models || {};
    const names = Object.keys(models);
    $('byModel').innerHTML = names.length ? names.map((n) =>
      '<tr><td data-label="Model"><code>' + esc(n) + '</code></td>' +
      '<td data-label="Requests" class="mono">' + esc(models[n].requests) + '</td>' +
      '<td data-label="Average" class="mono">' + esc(fmtMs(models[n].avg_ms)) + '</td>' +
      '<td data-label="Slowest" class="mono">' + esc(fmtMs(models[n].max_ms)) + '</td></tr>').join('')
      : '<tr><td colspan="4" class="empty-cell" data-label=""><span class="note">No upstream calls yet — ' +
        'this table fills in as traffic arrives.</span></td></tr>';

    const codes = m.status_codes || {};
    const keys = Object.keys(codes);
    $('byStatus').innerHTML = keys.length ? keys.map((k) =>
      '<tr><td data-label="Code">' + statusPill(Number(k)) + '</td>' +
      '<td data-label="Count" class="mono">' + esc(codes[k]) + '</td></tr>').join('')
      : '<tr><td colspan="2" class="empty-cell" data-label=""><span class="note">No responses recorded yet.</span></td></tr>';

    $('accountsNote').innerHTML = accountsNote(s.credentials);
    $('accounts').innerHTML = accountRows(s.credentials);

    const cfg = s.config || {};
    $('cfgTable').innerHTML = Object.keys(cfg).sort().map((k) =>
      '<tr><td data-label="Key"><code>' + esc(k) + '</code></td>' +
      '<td data-label="Value"><code>' + esc(fmtCfg(cfg[k])) + '</code></td></tr>').join('');
  } catch (err) {
    if (err.code === 401) {
      needKeyNote('statusWarn');
      $('statusMeta').textContent = 'authorisation required';
    } else {
      $('statusWarn').innerHTML = callout('bad', '<strong>Could not read /status.</strong> ' +
        esc(err.message || err));
      $('statusMeta').textContent = 'unreachable';
    }
  } finally {
    statusBusy = false;
    setProgress('statusProgress', false);
  }
}
function fmtCfg(v){
  if (v === null || v === undefined) return 'null';
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (Array.isArray(v)) return v.length ? '[' + v.length + ' items]' : '[]';
  return String(v);
}

/* ── latency histogram: bars, axis, and a readable summary ───────────────── */
function renderChart(hist, samples){
  const box = $('latencyChart');
  if (!box) return;
  const buckets = Object.keys(hist || {}).filter((k) => Number(hist[k]) > 0);
  if (!buckets.length) {
    box.removeAttribute('role');
    box.removeAttribute('aria-label');
    box.innerHTML = '<div class="empty">' +
      '<span class="empty-icon">' + ICON.pulse + '</span>' +
      '<h4>No latency samples yet</h4>' +
      '<p>Timing is recorded for upstream calls only, so this chart stays quiet until a model answers.</p>' +
      '</div>';
    return;
  }
  const values = buckets.map((k) => Number(hist[k]) || 0);
  const total = values.reduce((a, b) => a + b, 0) || 1;
  const max = Math.max.apply(null, values);
  const label = (k) => k === 'inf' ? '\u221e' : (Number(k) >= 1000 ? (Number(k) / 1000) + 's' : k + 'ms');
  const summary = buckets.map((k, i) => 'up to ' + label(k) + ': ' + values[i]).join(', ');
  box.setAttribute('role', 'img');
  box.setAttribute('aria-label',
    'Upstream latency histogram over ' + (samples || total) + ' samples. ' + summary + '.');
  const grid = [max, max * 0.5, 0].map((v) =>
    '<span><i>' + esc(fmtCount(Math.round(v))) + '</i></span>').join('');
  box.innerHTML =
    '<div class="chart-head"><h3>Upstream latency</h3>' +
    '<span class="sub">' + esc((samples || total)) + ' samples &middot; slowest bucket ' + esc(fmtCount(max)) + '</span>' +
    '<span class="spacer"></span>' +
    '<span class="chart-legend"><span class="swatch"></span>requests per upper bound</span></div>' +
    '<div class="plot"><div class="gridlines">' + grid + '</div><div class="bars">' +
    buckets.map((k, i) =>
      '<div class="bar" style="--i:' + i + '" data-tip="' + esc(label(k)) + ' &mdash; ' + esc(values[i]) +
      ' request' + (values[i] === 1 ? '' : 's') + ' (' + esc(Math.round(values[i] / total * 100)) + '%)">' +
      '<span class="fill" style="--h:' + esc(Math.max(2, Math.round(values[i] / max * 100))) + '%"></span>' +
      '<span class="xlab">' + esc(label(k)) + '</span></div>').join('') +
    '</div></div>' +
    '<div class="chart-x">Upper bound per bucket &rarr; requests &middot; hover a bar for exact counts</div>';
}

/* ── activity ────────────────────────────────────────────────────────────── */
let lastHistory = [];
let seenRequests = null;
let actLoadedAt = 0;
async function refreshActivity(force){
  if (!STATE.history_enabled) {
    $('actNote').innerHTML = callout('warn', '<strong>Request history is disabled.</strong> ' +
      'Set <code>history_max</code> to a positive number to record requests.');
    $('actRows').innerHTML = '';
    return;
  }
  if (!force && actLoadedAt && Date.now() - actLoadedAt < 2000) return;
  setProgress('actProgress', true);
  try {
    const s = await fetchStatus();
    actLoadedAt = Date.now();
    $('actNote').innerHTML = '';
    lastHistory = s.history || [];
    const first = seenRequests === null;
    if (first) seenRequests = new Set();
    renderActivityRows(first);
  } catch (err) {
    if (err.code === 401) { needKeyNote('actNote'); $('actMeta').textContent = 'authorisation required'; }
    else {
      $('actNote').innerHTML = callout('bad', '<strong>Could not read /status.</strong> ' + esc(err.message || err));
      $('actMeta').textContent = 'unreachable';
    }
    $('actRows').innerHTML = '';
  } finally {
    setProgress('actProgress', false);
  }
}
function renderActivityRows(first){
  const filter = $('actFilter').value;
  const rows = lastHistory.filter((r) => {
    if (!filter) return true;
    if (filter === 'err') return r.status >= 400;
    return String(r.path || '').indexOf(filter) === 0;
  });
  $('actMeta').textContent = rows.length + ' of ' + lastHistory.length + ' shown';
  $('actRows').innerHTML = rows.length ? rows.map((r) => {
    const fresh = !first && !seenRequests.has(r.id);
    seenRequests.add(r.id);
    return '<tr' + (fresh ? ' class="is-new"' : '') + '>' +
      '<td class="code" data-label="Time">' + esc(fmtAgo(r.ts)) +
        '<span class="sub">' + esc(fmtTime(r.ts)) + '</span></td>' +
      '<td data-label="Method">' + pill(r.method) + '</td>' +
      '<td data-label="Path"><code>' + esc(r.path) + '</code></td>' +
      '<td data-label="Status">' + statusPill(r.status) + '</td>' +
      '<td data-label="Model">' + (r.model ? '<code>' + esc(r.model) + '</code>' : '<span class="note">\u2014</span>') + '</td>' +
      '<td class="code" data-label="Latency">' + esc(fmtMs(r.ms)) + '</td>' +
      '<td class="code" data-label="Client">' + esc(r.client || '\u2014') + '</td>' +
      '<td class="code" data-label="Request ID">' + esc(r.id || '\u2014') + '</td></tr>';
  }).join('')
    : '<tr><td colspan="8" class="empty-cell" data-label=""><span class="note">' +
      (lastHistory.length ? 'No requests match this filter.' : 'No requests recorded yet.') + '</span></td></tr>';
}
function refreshActivityIfVisible(){
  if ($('tab-activity').classList.contains('active')) refreshActivity();
}

/* ─────────────────────────────────────────────────────────────────────────
   Chat
   ───────────────────────────────────────────────────────────────────────── */
let convs = get(LS.convs, null);
if (!Array.isArray(convs)) convs = [];
let activeId = get(LS.active, null);
// A pointer left behind by another tab (or by cleared storage) must not strand
// the panel on an empty thread while the rail still lists conversations.
if (!convs.some((c) => c.id === activeId)) activeId = convs.length ? convs[0].id : null;
if (activeId) persist();

const newId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
function activeConv(){ return convs.find((c) => c.id === activeId) || null; }
function persist(){ set(LS.convs, convs); set(LS.active, activeId); }

function ensureConv(){
  let c = activeConv();
  if (!c) {
    c = {id: newId(), title: 'New chat', created: Date.now()/1000, messages: []};
    convs.unshift(c); activeId = c.id;
  }
  return c;
}
function newChat(){
  const c = {id: newId(), title: 'New chat', created: Date.now()/1000, messages: []};
  convs.unshift(c); activeId = c.id; persist(); renderConvs(); renderMsgs();
  if (!coarse()) $('prompt').focus();
}
function convCount(c){ return c.messages.filter((m) => m.role === 'user').length; }
function renderConvs(){
  const list = $('convList');
  if (!convs.length) {
    list.innerHTML = '<p class="rail-empty">No conversations yet. Start one and it will be kept here.</p>';
    return;
  }
  list.innerHTML = convs.map((c) =>
    '<div class="conv' + (c.id === activeId ? ' sel' : '') + '" role="listitem">' +
      '<button class="conv-open t" type="button" data-id="' + esc(c.id) + '"' +
        ' aria-current="' + (c.id === activeId ? 'true' : 'false') + '">' + esc(c.title || 'New chat') + '</button>' +
      '<span class="n">' + esc(convCount(c)) + '</span>' +
      '<button class="x" type="button" data-del="' + esc(c.id) + '" aria-label="Delete conversation ' +
        esc(c.title || 'New chat') + '" title="Delete">' + ICON.close + '</button>' +
    '</div>').join('');
}
function msgHtml(m, i){
  const cls = m.role === 'user' ? 'user' : (m.error ? 'err' : 'assistant');
  const who = m.role === 'user' ? 'You' : (m.error ? 'Error' : (m.model || 'Gemini'));
  const icon = m.role === 'user' ? ICON.user : (m.error ? ICON.warn : ICON.spark);
  const body = m.streaming ? esc(m.content) + '<span class="cursor"></span>'
    : (m.role === 'user' ? inlineMd(m.content) : renderMd(m.content || ''));
  const meta = m.meta
    ? '<div class="mmeta">' + String(m.meta).split(' \u00b7 ').map((part) =>
        '<span class="mchip">' + esc(part) + '</span>').join('') + '</div>'
    : '';
  return '<article class="msg ' + cls + '" data-i="' + i + '">' +
    '<span class="msg-avatar" aria-hidden="true">' + icon + '</span>' +
    '<div class="msg-body"><div class="msg-head"><span class="msg-who">' + esc(who) + '</span>' +
      (m.t ? '<span class="msg-time">' + esc(fmtTime(m.t)) + '</span>' : '') + '</div>' +
    '<div class="bub">' + (body || '<span class="note">(empty)</span>') + '</div>' + meta + '</div></article>';
}
function renderMsgs(){
  const c = activeConv();
  const box = $('msgs');
  if (!c || !c.messages.length) {
    box.innerHTML = '<div class="empty">' +
      '<span class="empty-icon">' + ICON.spark + '</span>' +
      '<h4>Start a conversation</h4>' +
      '<p>Pick a model above and ask anything. Responses stream in as they are generated, and this ' +
      'transcript stays in your browser.</p>' +
      '<div class="chips">' +
      ['Explain what this server does', 'Write a haiku about the sea',
       'Compare Python and Go for CLI tools', 'Give me a JSON schema for a user record']
        .map((s) => '<button class="chip" type="button" data-sug="' + esc(s) + '">' + esc(s) + '</button>').join('') +
      '</div></div>';
    return;
  }
  box.innerHTML = c.messages.map((m, i) => msgHtml(m, i)).join('');
  box.scrollTop = box.scrollHeight;
}

$('convList').addEventListener('click', (ev) => {
  const del = ev.target.closest('[data-del]');
  if (del) {
    const id = del.dataset.del;
    const gone = convs.find((c) => c.id === id);
    convs = convs.filter((c) => c.id !== id);
    if (activeId === id) activeId = convs.length ? convs[0].id : null;
    persist(); renderConvs(); renderMsgs();
    toast('Deleted “' + ((gone && gone.title) || 'New chat') + '”', 'ok', {title: 'Conversation removed'});
    return;
  }
  const open = ev.target.closest('.conv-open');
  if (open) { activeId = open.dataset.id; persist(); renderConvs(); renderMsgs(); }
});
document.addEventListener('click', (ev) => {
  const sug = ev.target.closest('[data-sug]');
  if (!sug) return;
  $('prompt').value = sug.dataset.sug;
  growPrompt();
  $('prompt').focus();
});
$('newChat').addEventListener('click', newChat);
$('clearConv').addEventListener('click', () => {
  const c = activeConv();
  if (!c || !c.messages.length) return;
  c.messages = []; c.title = 'New chat'; persist(); renderConvs(); renderMsgs();
  toast('Cleared this transcript', 'ok', {title: 'Chat cleared'});
});

/* ── sending ─────────────────────────────────────────────────────────────── */
let controller = null;

function chosenModel(){
  const base = $('model').value;
  const think = $('think').value;
  return think === '' ? base : base + '@think=' + think;
}
function setBusy(busy){
  const send = $('send'), stop = $('stop');
  send.disabled = busy;
  send.hidden = busy;
  stop.hidden = !busy;
  stop.disabled = !busy;
  if (busy) { stop.classList.remove('morph'); void stop.offsetWidth; stop.classList.add('morph'); }
  $('prompt').disabled = busy;
}
function growPrompt(){
  const t = $('prompt');
  t.style.height = 'auto';
  t.style.height = Math.min(220, t.scrollHeight) + 'px';
}

async function send(){
  const text = $('prompt').value.trim();
  if (!text) { $('prompt').focus(); return; }
  const key = $('apikey').value.trim();
  if (key) localStorage.setItem(LS.key, key);
  set(LS.model, $('model').value); set(LS.think, $('think').value); set(LS.stream, $('stream').checked);

  const conv = ensureConv();
  const model = chosenModel();
  conv.messages.push({role: 'user', content: text, t: Date.now()/1000});
  if (conv.title === 'New chat') conv.title = text.slice(0, 42) + (text.length > 42 ? '…' : '');
  conv.updated = Date.now()/1000;
  const asst = {role: 'assistant', content: '', streaming: true, meta: '', t: Date.now()/1000, model: model};
  conv.messages.push(asst);
  const idx = conv.messages.length - 1;
  persist(); renderConvs(); renderMsgs();
  $('prompt').value = '';
  growPrompt();
  $('chatMeta').textContent = '';
  setBusy(true);

  const headers = {'Content-Type': 'application/json'};
  if (key) headers['Authorization'] = 'Bearer ' + key;
  // Only user/assistant turns are sent; the upstream has no system role on this
  // endpoint, and system content is folded into the prompt server-side.
  const payload = {
    model: model,
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
          let parsed = null;
          try { parsed = JSON.parse(data); } catch (_) { parsed = null; }
          if (!parsed) continue;
          // Upstream failures arrive as a chunk with an `error` object. Without
          // this the turn would end on a silent empty reply.
          if (parsed.error) throw new Error((parsed.error && parsed.error.message) || 'upstream error');
          const choice = (parsed.choices || [])[0];
          if (!choice) continue;
          if (choice.delta && choice.delta.content) { chars += choice.delta.content.length; asst.content += choice.delta.content; bump(); }
          if (choice.finish_reason === 'tool_calls') asst.meta = 'finish_reason: tool_calls';
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
      // A failed turn leaves no bubble behind, so say why out loud: the
      // transcript stays clean and the reason is still visible.
      toast(asst.content, 'bad', {title: 'Request failed'});
      $('chatMeta').textContent = 'last turn failed';
    }
  } finally {
    controller = null;
    setBusy(false);
    // Drop empty failed turns so the transcript stays usable as context.
    if (asst.error && !chars) conv.messages.splice(idx, 1);
    conv.updated = Date.now()/1000;
    persist(); renderMsgs(); refreshActivityIfVisible();
  }
}

$('send').addEventListener('click', send);
$('stop').addEventListener('click', () => { if (controller) controller.abort(); });
$('prompt').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (!$('send').disabled) send(); }
});
$('prompt').addEventListener('input', growPrompt);
['model', 'think', 'stream'].forEach((id) => $(id).addEventListener('change', () => {
  set(LS.model, $('model').value); set(LS.think, $('think').value); set(LS.stream, $('stream').checked);
}));
$('apikey').addEventListener('change', () => {
  const k = $('apikey').value.trim();
  if (k) { localStorage.setItem(LS.key, k); toast('Key kept in this browser and sent as a bearer token',
    'ok', {title: 'API key saved'}); }
  else { localStorage.removeItem(LS.key); }
});

/* ── liveness + timers ───────────────────────────────────────────────────── */
function setHealth(level, text){
  const dot = $('statusDot'), pill = $('health'), label = $('statusText');
  if (dot) dot.className = 'dot ' + (level || '');
  if (pill) pill.className = 'health' + (level ? ' is-' + level : '');
  // Only touch text that actually changed: identical writes would re-announce.
  if (label && label.textContent !== text) label.textContent = text;
}
async function ping(){
  try {
    const res = await fetch('health');
    if (!res.ok) throw new Error(res.status);
    const h = await res.json();
    $('uptime').textContent = fmtUptime(h.uptime_sec);
    const fatal = (h.checks && h.checks.fatal) || [];
    const warns = (h.checks && h.checks.warnings) || [];
    if (fatal.length) setHealth('bad', 'not ready');
    else if (warns.length) setHealth('warn', 'warnings');
    else if (h.ready === false) setHealth('warn', 'degraded');
    else setHealth('ok', 'running');
  } catch (_) {
    setHealth('bad', 'unreachable');
  }
}
$('refreshStatus').addEventListener('click', () => refreshStatus(true));
$('refreshAct').addEventListener('click', () => refreshActivity(true));
$('actFilter').addEventListener('change', () => {
  // Filtering is a view over the data already held; only a fetch can bring new
  // rows, so the filter never waits on the network.
  if (STATE.history_enabled) renderActivityRows(false);
  else refreshActivity(false);
});

/* Long transcripts make the newest message easy to lose; the affordance
   appears only when the viewport is not already at the bottom. */
(function (){
  const box = $('msgs'), btn = $('toBottom');
  if (!box || !btn) return;
  const update = () => {
    const distance = box.scrollHeight - box.scrollTop - box.clientHeight;
    btn.classList.toggle('show', distance > 80);
  };
  box.addEventListener('scroll', update, {passive: true});
  btn.addEventListener('click', () => {
    box.scrollTo({top: box.scrollHeight, behavior: motionOK() ? 'smooth' : 'auto'});
    update();
  });
  // Content changes (render, streaming chunks) do not always fire scroll.
  if (window.MutationObserver) new MutationObserver(update).observe(box, {childList: true, subtree: true});
  update();
})();

/* ── boot ────────────────────────────────────────────────────────────────── */
skeletonTiles(9, 'counters');
skeletonTiles(3, 'latency');
skeletonRows('accounts', 5, 2);
skeletonRows('actRows', 8, 5);
$('checks').innerHTML = callout('info', 'Reading startup checks&hellip;');

renderStatic();
$('latencyChart').innerHTML = '<div class="empty"><span class="empty-icon">' + ICON.pulse + '</span>' +
  '<h4>Latency</h4><p>Waiting for the first status read.</p></div>';
renderConvs();
renderMsgs();
growPrompt();
ping();
requestAnimationFrame(() => moveGlider(true));
window.addEventListener('load', () => moveGlider(true));
setInterval(ping, 15000);
setInterval(() => { if ($('autoStatus').checked && $('tab-status').classList.contains('active')) refreshStatus(false); }, 10000);
setInterval(() => { if ($('autoAct').checked && $('tab-activity').classList.contains('active')) refreshActivity(false); }, 5000);

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
