"""Self-contained dashboard served at ``GET /``.

Three things live here:

* **Status** — what the backend is doing right now: configuration, health
  checks, live metrics, latency distribution and per-model breakdown.
* **Chat** — talk to the models directly, with conversations kept in the
  browser so there is history to come back to.
* **Activity** — the most recent requests the server handled.

Everything is inlined: no CDN, no build step, no external requests. The page
works offline and inside an air-gapped container, which is a tested property.

The visual system is one flat material language, defined once in the ``:root``
design tokens. It is built for the thing the product actually is — an
instrument watching a signal path between a client, this server and Gemini —
so it borrows that world: paper-grey surfaces with hairline rules, mono figures
with tabular numerals, square status lamps instead of glowing dots, and no
transparency, blur or ambient colour anywhere. Colour is spent in two places
only: state (ok, warn, error) and the latency chart, which is the one rich
moment on the page. ``.glass-1``…``.glass-6`` survive as an elevation scale
(fill plus hairline, a shadow only for floating layers) so the markup and the
generated rows keep one vocabulary. A navigation column becomes a bottom bar
under 980 px. Light and dark are first-class schemes, and
``prefers-reduced-motion``, ``prefers-reduced-transparency`` and
``prefers-contrast`` are all honoured.

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
<meta name="theme-color" content="#f1f2ef" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0e1013" media="(prefers-color-scheme: dark)">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Cpath fill='%230f6d8c' d='M16 2C17 10.2 21.8 15 30 16C21.8 17 17 21.8 16 30C15 21.8 10.2 17 2 16C10.2 15 15 10.2 16 2Z'/%3E%3C/svg%3E">
<style>
/* ═══════════════════════════════════════════════════════════════════════════
   gemini-web2api console — "bench instrument" design system (v4)

   The product is a signal path: a client speaks OpenAI, this server translates,
   Gemini's web app answers. The interface is built like the instrument that
   watches that path — anodised paper-grey panels, silkscreen rules, mono
   readouts and square status lamps. No glass, no glows, no ambient colour.

   1  Tokens      colour, surface, line, depth, space, type, motion, layers
   2  Base        reset, canvas, typography, focus, scrollbars
   3  Surfaces    panel levels (the .glass-* classes are elevation, not glass)
   4  Shell       topbar, navigation rail, bottom bar, workspace, page heads
   5  Controls    buttons, fields, switches, segments, tags, tooltips
   6  Content     panels, readouts, tables, callouts, meters, empties
   7  Chat        conversations, transcript, composer
   8  Data        strip chart, model list, copy buttons
   9  Overlays    veils, dialogs, palette, toasts
   10 States      skeletons, utilities
   11 Responsive  intentional layouts per breakpoint
   12 Hierarchy   what leads, what follows
   13 Prefs       reduced motion, reduced transparency, contrast, print

   Layer order (z-index): canvas -1 · workspace 1 · chrome 30 · popovers 40 ·
   dialogs 60 · toasts 70. Nothing invents a value: a component picks a token.
   ═══════════════════════════════════════════════════════════════════════════ */

/* ── 1. Tokens ───────────────────────────────────────────────────────────── */
:root{
  color-scheme:light;

  /* Paper. A grey with a trace of green, the colour of a machine shop wall —
     not the blue-grey every dashboard template starts from. */
  --bg:#f1f2ef;
  --bg-top:#f6f7f4;
  --bg-bottom:#eaebe7;
  --fg:#15191a;
  --fg-2:#4c5451;
  --fg-3:#5f6864;
  /* Signal. A steel blue that reads as "channel open" without becoming a
     gradient, and clears 4.5:1 as text on every canvas stop. */
  --accent:#0d5d78;
  --accent-fill:#0f6d8c;
  --accent-hover:#0b5a76;
  --accent-fg:#ffffff;
  --accent-soft:rgba(15,109,140,.08);
  --ok:#1a6b45;   --ok-fill:#1f7a4e;
  --warn:#7a5200; --warn-fill:#b47c17;
  --bad:#a92b31;  --bad-fill:#c3363d;
  --info:#0d5d78;

  /* Elevation, not material. Six levels share one flat recipe: a fill, a
     hairline, and at the top two levels a shadow. */
  --glass-1:#fafbf9;   /* inset rows, quiet blocks                            */
  --glass-2:#ffffff;   /* panels, tables, the transcript                      */
  --glass-3:#ffffff;   /* chrome: topbar, rail                                */
  --glass-4:#ffffff;   /* popovers and menus                                  */
  --glass-5:#ffffff;   /* dialogs                                             */
  --glass-6:#ffffff;   /* toasts                                              */
  --pane:transparent;
  --pane-stuck:#ffffff;
  --fill-1:rgba(21,25,26,.045);
  --fill-2:rgba(21,25,26,.075);
  --fill-3:rgba(21,25,26,.11);
  --field:#ffffff;
  --field-hover:#ffffff;
  --field-focus:#ffffff;
  --code-bg:rgba(21,25,26,.05);
  --scrim:rgba(17,21,22,.42);
  --surface-solid:255,255,255;

  /* Hairlines define, they never decorate. */
  --line-1:rgba(21,25,26,.12);
  --line-2:rgba(21,25,26,.20);
  --line-3:rgba(21,25,26,.30);
  --edge-hi:rgba(255,255,255,.9);
  --edge-lo:rgba(21,25,26,.06);
  --edge-hi-strong:rgba(255,255,255,.8);
  --sheen:rgba(255,255,255,0);
  --sheen-fade:rgba(255,255,255,0);
  --fill-on-tint:rgba(255,255,255,.16);
  --on-accent:#ffffff;
  --knob:#ffffff;               /* switch thumb: a part, not a surface         */
  --grid:rgba(21,25,26,.055);   /* the chart's graph paper                     */

  /* Tints — a wash behind a state, never a surface colour. */
  --acc-bg:rgba(15,109,140,.075); --acc-line:rgba(15,109,140,.30); --acc-ring:rgba(15,109,140,.32);
  --ok-bg:rgba(31,122,78,.09);    --ok-line:rgba(31,122,78,.28);
  --warn-bg:rgba(180,124,23,.11); --warn-line:rgba(180,124,23,.32);
  --bad-bg:rgba(195,54,61,.09);   --bad-line:rgba(195,54,61,.28);
  --neu-bg:rgba(21,25,26,.05);    --neu-line:rgba(21,25,26,.16);

  /* Retained so the flat canvas never depends on a removed field. */
  --tint-a:rgba(0,0,0,0);
  --tint-b:rgba(0,0,0,0);
  --tint-c:rgba(0,0,0,0);

  /* Depth — a hairline plus one short shadow. */
  --sh-1:0 1px 2px rgba(21,25,30,.06);
  --sh-2:0 2px 6px rgba(21,25,30,.07);
  --sh-3:0 10px 24px -12px rgba(21,25,30,.24);
  --sh-4:0 18px 40px -18px rgba(21,25,30,.28);
  --sh-5:0 24px 56px -22px rgba(21,25,30,.32);
  --sh-inset:inset 0 1px 0 rgba(255,255,255,.75);
  --sh-lift-1:0 1px 1px rgba(21,25,30,.06);
  --sh-lift-2:0 1px 3px rgba(21,25,30,.10);
  --sh-knob:0 1px 2px rgba(21,25,30,.22);
  --sh-knob-press:0 1px 2px rgba(21,25,30,.30);
  --sh-well:inset 0 1px 2px rgba(21,25,30,.04);
  --sh-press:inset 0 1px 2px rgba(21,25,30,.10);

  /* Blur and saturation are gone from the product; the tokens stay so no
     surface can silently reintroduce a frosted layer. */
  --blur-1:0px; --blur-2:0px; --blur-3:0px; --blur-4:0px; --blur-5:0px;
  --blur-veil:0px;
  --sat-1:100%; --sat-2:100%; --sat-3:100%;

  /* Radii — sized by element, nested inside the parent's padding. */
  --r-1:4px; --r-2:6px; --r-3:8px; --r-4:10px; --r-5:12px; --r-6:14px;
  --r-full:999px;

  /* Space — 4px grid. */
  --sp-1:4px; --sp-2:8px; --sp-3:12px; --sp-4:16px; --sp-5:20px; --sp-6:24px;
  --sp-7:32px; --sp-8:40px; --sp-9:48px; --sp-10:64px;

  /* Type — one UI family plus the machine's own mono for figures. */
  --fs-micro:11.5px; --fs-meta:12.5px; --fs-sm:13.5px; --fs-body:14px;
  --fs-lead:16px; --fs-h3:15px; --fs-h2:20px; --fs-h1:26px;
  --track-tight:-.015em; --track-wide:.01em;
  --lh-tight:1.22; --lh-body:1.55;

  /* Motion — short, single curve, no overshoot. */
  --d-1:120ms; --d-2:160ms; --d-3:200ms; --d-4:240ms; --d-5:320ms;
  --e-out:cubic-bezier(.22,1,.36,1);
  --e-inout:cubic-bezier(.22,1,.36,1);
  --e-spring:cubic-bezier(.22,1,.36,1);
  --e-enter:cubic-bezier(.22,1,.36,1);
  --e-glide:cubic-bezier(.22,1,.36,1);

  /* Layers */
  --z-canvas:-1; --z-pane:1; --z-panel:2; --z-chrome:30; --z-pop:40;
  --z-dialog:60; --z-toast:70;

  --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",
         Arial,"Noto Sans",sans-serif;
  --mono:ui-monospace,SFMono-Regular,"SF Mono","JetBrains Mono","Cascadia Mono",
         Menlo,Consolas,"Liberation Mono",monospace;

  --rail:224px;            /* navigation column, desktop */
  --pane-max:1180px;       /* readable measure inside the workspace */
}

/* Dark. Deep neutral surfaces, not an inverted palette: the paper becomes
   charcoal, the hairlines stay visible, and the accent lightens so text on it
   still clears AA. */
:root[data-theme="dark"]{
  color-scheme:dark;
  --bg:#0e1013;
  --bg-top:#121418;
  --bg-bottom:#0b0d10;
  --fg:#e9ebe8; --fg-2:#a7aeab; --fg-3:#8a928e;
  --accent:#7fc9e0; --accent-fill:#8ed2e6; --accent-hover:#9adcee; --accent-fg:#08222b;
  --accent-soft:rgba(127,201,224,.10);
  --ok:#6fcf9b; --ok-fill:#6fcf9b;
  --warn:#d9ad63; --warn-fill:#d9ad63;
  --bad:#f0928f; --bad-fill:#f0928f;
  --info:#7fc9e0;

  --glass-1:#15181c;
  --glass-2:#181b1f;
  --glass-3:#16191d;
  --glass-4:#1b1f24;
  --glass-5:#1d2126;
  --glass-6:#22262b;
  --pane:transparent;
  --pane-stuck:#121418;
  --fill-1:rgba(255,255,255,.05);
  --fill-2:rgba(255,255,255,.08);
  --fill-3:rgba(255,255,255,.12);
  --field:#15181c;
  --field-hover:#191d21;
  --field-focus:#1b1f24;
  --code-bg:rgba(0,0,0,.30);
  --scrim:rgba(4,6,7,.66);
  --surface-solid:20,23,27;

  --line-1:rgba(255,255,255,.10);
  --line-2:rgba(255,255,255,.17);
  --line-3:rgba(255,255,255,.26);
  --edge-hi:rgba(255,255,255,.14);
  --edge-lo:rgba(255,255,255,.05);
  --edge-hi-strong:rgba(255,255,255,.16);
  --fill-on-tint:rgba(255,255,255,.16);
  --on-accent:#08222b;
  --knob:#d7dcda;
  --grid:rgba(255,255,255,.06);

  --acc-bg:rgba(127,201,224,.10); --acc-line:rgba(127,201,224,.30); --acc-ring:rgba(127,201,224,.30);
  --ok-bg:rgba(111,207,155,.10);  --ok-line:rgba(111,207,155,.30);
  --warn-bg:rgba(217,173,99,.11); --warn-line:rgba(217,173,99,.30);
  --bad-bg:rgba(240,146,143,.10); --bad-line:rgba(240,146,143,.30);
  --neu-bg:rgba(233,235,232,.05); --neu-line:rgba(233,235,232,.16);
  --tint-a:rgba(0,0,0,0);
  --tint-b:rgba(0,0,0,0);
  --tint-c:rgba(0,0,0,0);

  --sh-1:0 1px 2px rgba(0,0,0,.35);
  --sh-2:0 2px 6px rgba(0,0,0,.38);
  --sh-3:0 10px 24px -12px rgba(0,0,0,.60);
  --sh-4:0 18px 40px -18px rgba(0,0,0,.66);
  --sh-5:0 24px 56px -22px rgba(0,0,0,.70);
  --sh-inset:inset 0 1px 0 rgba(255,255,255,.05);
  --sh-lift-1:0 1px 1px rgba(0,0,0,.30);
  --sh-lift-2:0 1px 3px rgba(0,0,0,.36);
  --sh-knob:0 1px 2px rgba(0,0,0,.55);
  --sh-knob-press:0 1px 2px rgba(0,0,0,.65);
  --sh-well:inset 0 1px 2px rgba(0,0,0,.24);
  --sh-press:inset 0 1px 2px rgba(0,0,0,.40);
}
@media (prefers-color-scheme:dark){
:root:not([data-theme="light"]){  color-scheme:dark;
  --bg:#0e1013;
  --bg-top:#121418;
  --bg-bottom:#0b0d10;
  --fg:#e9ebe8; --fg-2:#a7aeab; --fg-3:#8a928e;
  --accent:#7fc9e0; --accent-fill:#8ed2e6; --accent-hover:#9adcee; --accent-fg:#08222b;
  --accent-soft:rgba(127,201,224,.10);
  --ok:#6fcf9b; --ok-fill:#6fcf9b;
  --warn:#d9ad63; --warn-fill:#d9ad63;
  --bad:#f0928f; --bad-fill:#f0928f;
  --info:#7fc9e0;

  --glass-1:#15181c;
  --glass-2:#181b1f;
  --glass-3:#16191d;
  --glass-4:#1b1f24;
  --glass-5:#1d2126;
  --glass-6:#22262b;
  --pane:transparent;
  --pane-stuck:#121418;
  --fill-1:rgba(255,255,255,.05);
  --fill-2:rgba(255,255,255,.08);
  --fill-3:rgba(255,255,255,.12);
  --field:#15181c;
  --field-hover:#191d21;
  --field-focus:#1b1f24;
  --code-bg:rgba(0,0,0,.30);
  --scrim:rgba(4,6,7,.66);
  --surface-solid:20,23,27;

  --line-1:rgba(255,255,255,.10);
  --line-2:rgba(255,255,255,.17);
  --line-3:rgba(255,255,255,.26);
  --edge-hi:rgba(255,255,255,.14);
  --edge-lo:rgba(255,255,255,.05);
  --edge-hi-strong:rgba(255,255,255,.16);
  --fill-on-tint:rgba(255,255,255,.16);
  --on-accent:#08222b;
  --knob:#d7dcda;
  --grid:rgba(255,255,255,.06);

  --acc-bg:rgba(127,201,224,.10); --acc-line:rgba(127,201,224,.30); --acc-ring:rgba(127,201,224,.30);
  --ok-bg:rgba(111,207,155,.10);  --ok-line:rgba(111,207,155,.30);
  --warn-bg:rgba(217,173,99,.11); --warn-line:rgba(217,173,99,.30);
  --bad-bg:rgba(240,146,143,.10); --bad-line:rgba(240,146,143,.30);
  --neu-bg:rgba(233,235,232,.05); --neu-line:rgba(233,235,232,.16);
  --tint-a:rgba(0,0,0,0);
  --tint-b:rgba(0,0,0,0);
  --tint-c:rgba(0,0,0,0);

  --sh-1:0 1px 2px rgba(0,0,0,.35);
  --sh-2:0 2px 6px rgba(0,0,0,.38);
  --sh-3:0 10px 24px -12px rgba(0,0,0,.60);
  --sh-4:0 18px 40px -18px rgba(0,0,0,.66);
  --sh-5:0 24px 56px -22px rgba(0,0,0,.70);
  --sh-inset:inset 0 1px 0 rgba(255,255,255,.05);
  --sh-lift-1:0 1px 1px rgba(0,0,0,.30);
  --sh-lift-2:0 1px 3px rgba(0,0,0,.36);
  --sh-knob:0 1px 2px rgba(0,0,0,.55);
  --sh-knob-press:0 1px 2px rgba(0,0,0,.65);
  --sh-well:inset 0 1px 2px rgba(0,0,0,.24);
  --sh-press:inset 0 1px 2px rgba(0,0,0,.40);
}
}

/* ── 2. Base ─────────────────────────────────────────────────────────────── */
*,*::before,*::after{box-sizing:border-box}
html{height:100%;-webkit-text-size-adjust:100%}
html,body{min-height:100%}
/* One viewport tall; the workspace scrolls inside it, so the chrome never
   leaves the screen and a long transcript stays readable. */
body{
  margin:0;height:100%;overflow:hidden;
  color:var(--fg);background:var(--bg);
  font:400 var(--fs-body)/var(--lh-body) var(--sans);
  -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;
  text-rendering:optimizeLegibility;
}
body.no-scroll{overflow:hidden}
h1,h2,h3,h4,p,figure{margin:0}
button,input,select,textarea{font:inherit;color:inherit}
a{color:var(--accent);text-underline-offset:2px}
a:hover{text-decoration:underline}
svg{flex:none}
b,strong{font-weight:600}
::selection{background:var(--acc-bg);color:var(--fg)}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
  clip:rect(0 0 0 0);white-space:nowrap;border:0}
[hidden]{display:none !important}

/* Focus: one ring for the whole product, never removed. Fields swap it for a
   ring drawn by the well itself. */
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:var(--r-1)}
input:focus-visible,select:focus-visible,textarea:focus-visible{outline:none}
.skip{
  position:fixed;left:-9999px;top:0;z-index:var(--z-toast);
  background:var(--glass-5);border:1px solid var(--line-2);border-radius:var(--r-2);
  padding:10px 14px;font-size:var(--fs-sm);font-weight:500;box-shadow:var(--sh-3);
}
.skip:focus{left:16px;top:16px}

/* The canvas is paper: one flat fill. Nothing floats behind the content. */
.canvas{position:fixed;inset:0;z-index:var(--z-canvas);pointer-events:none;background:var(--bg)}
.canvas-glow{display:none}

/* ── 3. Surfaces ─────────────────────────────────────────────────────────── */
/* `.glass-*` is retained as the elevation scale (the markup and the generated
   rows name it), but every level is a solid surface with a hairline. Depth is
   a shadow only where something genuinely floats. */
[class*="glass-"]{position:relative;border:1px solid var(--line-1);box-shadow:none}
.glass-1{background:var(--glass-1)}
.glass-2{background:var(--glass-2)}
.glass-3{background:var(--glass-3)}
.glass-4{background:var(--glass-4);border-color:var(--line-2);box-shadow:var(--sh-4)}
.glass-5{background:var(--glass-5);border-color:var(--line-2);box-shadow:var(--sh-5)}
.glass-6{background:var(--glass-6);border-color:var(--line-2);box-shadow:var(--sh-3)}
.tint-acc{background:var(--acc-bg);border-color:var(--acc-line)}
.tint-ok{background:var(--ok-bg);border-color:var(--ok-line)}
.tint-warn{background:var(--warn-bg);border-color:var(--warn-line)}
.tint-bad{background:var(--bad-bg);border-color:var(--bad-line)}
.tint-neu{background:var(--neu-bg);border-color:var(--neu-line)}

/* ── 4. Shell ────────────────────────────────────────────────────────────── */
/* A framed application: the topbar and the navigation column are chrome, the
   workspace is the page. Nothing floats in mid-air. */
.app{position:relative;z-index:var(--z-pane);height:100%;display:flex;flex-direction:column}

.topbar{
  position:relative;z-index:var(--z-chrome);flex:none;
  display:flex;align-items:center;gap:var(--sp-4);min-width:0;
  height:56px;padding:0 20px;
  background:var(--glass-3);border:0;border-bottom:1px solid var(--line-1);
  border-radius:0;box-shadow:none;
}
.topbar.is-scrolled{background:var(--glass-3);box-shadow:var(--sh-1)}
.brand{
  display:flex;align-items:center;gap:10px;min-width:0;
  appearance:none;background:none;border:0;padding:4px;margin-left:-4px;
  cursor:pointer;text-align:left;border-radius:var(--r-2);color:var(--fg);
}
.brand:hover{text-decoration:none}
.mark{
  width:22px;height:22px;flex:0 0 22px;display:grid;place-items:center;
  color:var(--accent);background:none;border:0;box-shadow:none;
}
.mark svg{width:21px;height:21px;display:block}
.brand-text{min-width:0;display:flex;align-items:baseline;gap:0}
.brand-text h1{
  font-size:var(--fs-h3);font-weight:600;letter-spacing:var(--track-tight);line-height:1.2;
  white-space:nowrap;color:var(--fg);
}
.brand-text p{
  font-size:var(--fs-meta);color:var(--fg-3);line-height:1.2;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  padding-left:10px;border-left:1px solid var(--line-1);
}
/* Section context: the workspace says which pane you are in; the topbar
   echoes it once the pane's own heading has scrolled away. */
.tb-context{
  display:flex;align-items:center;gap:8px;min-width:0;
  padding-left:14px;border-left:1px solid var(--line-1);
  opacity:0;transform:translateX(-4px);pointer-events:none;
  transition:opacity var(--d-3) var(--e-out),transform var(--d-3) var(--e-out);
}
.topbar.is-scrolled .tb-context{opacity:1;transform:none}
.tb-context .tb-title{font-size:var(--fs-sm);font-weight:500;color:var(--fg-2);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.topbar-end{display:flex;align-items:center;gap:var(--sp-3);margin-left:auto;min-width:0}

/* Health: a square lamp and the same fact in words. The lamp is static — a
   healthy service is not an animation. */
.health{
  display:inline-flex;align-items:center;gap:8px;padding:0;
  font-size:var(--fs-meta);font-weight:500;color:var(--fg-2);
  background:none;border:0;white-space:nowrap;
}
.health.is-ok{color:var(--ok)}
.health.is-warn{color:var(--warn)}
.health.is-bad{color:var(--bad)}
.dot{
  width:7px;height:7px;flex:0 0 7px;border-radius:1px;background:var(--fg-3);
  box-shadow:inset 0 0 0 1px rgba(0,0,0,.16);
  transition:background-color var(--d-2) var(--e-out);
}
.dot.ok{background:var(--ok-fill)}
.dot.warn{background:var(--warn-fill)}
.dot.bad{background:var(--bad-fill)}
.dot.ok::after,.dot.warn::after,.dot.bad::after{content:none}

.pill--meta{
  display:inline-flex;align-items:center;gap:6px;
  font-family:var(--mono);font-size:var(--fs-micro);font-weight:400;color:var(--fg-3);
  background:none;border:0;padding:0;white-space:nowrap;
  font-variant-numeric:tabular-nums;
}
.pill--meta .dot-sep{width:3px;height:3px;border-radius:50%;background:var(--line-3)}

/* Workspace: navigation column and content pane share the leftover height. */
.body{flex:1;display:grid;grid-template-columns:var(--rail) minmax(0,1fr);min-height:0}
.railwrap{position:relative;min-height:0;min-width:0;
  background:var(--glass-3);border-right:1px solid var(--line-1)}

/* Navigation — dense rows, one column of icons, labels on one line. The active
   row is marked with an accent rule, not a travelling pill. */
.rail{
  position:absolute;inset:0;z-index:var(--z-chrome);
  display:flex;flex-direction:column;gap:var(--sp-3);
  padding:16px 10px 12px;border:0;border-radius:0;background:none;box-shadow:none;
}
.rail.is-scrolled{background:none;box-shadow:none}
.rail-group{display:none}
nav.tabs{position:relative;display:flex;flex-direction:column;gap:2px;min-width:0;padding:0}
nav.tabs .glider{display:none}
.tab-btn{
  position:relative;z-index:1;display:flex;align-items:center;gap:10px;width:100%;
  appearance:none;background:none;border:0;cursor:pointer;text-align:left;
  height:32px;padding:0 10px;border-radius:var(--r-2);color:var(--fg-2);
  font-size:var(--fs-sm);font-weight:450;white-space:nowrap;
  transition:color var(--d-2) var(--e-out),background-color var(--d-2) var(--e-out);
}
.tab-btn .ico{width:17px;height:17px;display:block;opacity:.8;transition:opacity var(--d-2) var(--e-out)}
.tab-btn .lbl{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis}
.tab-btn .navkbd{
  font-family:var(--mono);font-size:10px;color:var(--fg-3);letter-spacing:0;
  padding:0 4px;border-radius:3px;background:none;border:1px solid transparent;
  opacity:0;transition:opacity var(--d-2) var(--e-out);
}
.tab-btn:hover{color:var(--fg);background:var(--fill-1)}
.tab-btn:hover .ico{opacity:1}
.tab-btn:hover .navkbd{opacity:.8}
.tab-btn[aria-selected="true"]{color:var(--fg);background:var(--fill-1);font-weight:550}
.tab-btn[aria-selected="true"] .ico{opacity:1}
.tab-btn[aria-selected="true"] .navkbd{opacity:.5}
.tab-btn[aria-selected="true"]::before{
  content:"";position:absolute;left:-10px;top:8px;bottom:8px;width:2px;
  background:var(--accent);border-radius:0 2px 2px 0;
}
.tab-btn:focus-visible{outline-offset:-2px}
.rail-foot{
  margin-top:auto;display:flex;flex-direction:column;gap:6px;
  padding-top:10px;border-top:1px solid var(--line-1);
}
.rail-live{
  display:flex;align-items:center;gap:8px;padding:0 10px;min-width:0;
  font-size:var(--fs-meta);font-weight:450;color:var(--fg-2);
}
.pulse{
  width:7px;height:7px;flex:0 0 7px;border-radius:1px;background:var(--fg-3);
  box-shadow:inset 0 0 0 1px rgba(0,0,0,.16);
  transition:background-color var(--d-2) var(--e-out);
}
.pulse.ok{background:var(--ok-fill)}
.pulse.warn{background:var(--warn-fill)}
.pulse.bad{background:var(--bad-fill)}
.pulse.ok::after,.pulse.warn::after,.pulse.bad::after{content:none}
.rail-live .rl-t{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rail-note{display:none}

/* Content pane — the page. Scrolls inside the frame. */
main.content{
  position:relative;min-width:0;min-height:0;
  overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;
  scrollbar-gutter:stable;
  padding:24px 28px 40px;border:0;border-radius:0;background:var(--bg);
  scroll-padding-top:16px;
}
main.content.is-scrolled{background:var(--bg)}
main.content>section,main.content>footer.foot{max-width:var(--pane-max);margin-inline:auto}

section.tab{display:none;flex-direction:column;gap:var(--sp-5);min-width:0;flex:0 0 auto}
section.tab.active{display:flex}
section.tab.active[data-enter]{animation:panelIn var(--d-4) var(--e-out)}
@keyframes panelIn{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}

/* ── 5. Controls ─────────────────────────────────────────────────────────── */
.btn{
  position:relative;display:inline-flex;align-items:center;justify-content:center;gap:7px;
  appearance:none;cursor:pointer;user-select:none;
  min-height:32px;padding:0 12px;border-radius:var(--r-2);
  border:1px solid transparent;background:none;
  font-size:var(--fs-sm);font-weight:500;white-space:nowrap;
  transition:background-color var(--d-2) var(--e-out),border-color var(--d-2) var(--e-out),
             color var(--d-1) var(--e-out),opacity var(--d-2) var(--e-out);
}
.btn svg{width:15px;height:15px;display:block}
.btn .lbl{min-width:0;overflow:hidden;text-overflow:ellipsis}
.btn:active:not(:disabled){transform:translateY(1px)}
.btn:disabled,.btn[aria-disabled="true"]{opacity:.45;cursor:not-allowed;transform:none}

/* Primary: a solid signal fill, no gradient, no glow. */
.btn--primary{
  background:var(--accent-fill);color:var(--accent-fg);
  border-color:color-mix(in srgb,var(--accent-fill) 82%,#000);
}
.btn--primary:hover:not(:disabled){background:var(--accent-hover)}
.btn--primary:active:not(:disabled){background:var(--accent-hover);transform:translateY(1px)}

/* Secondary: the raised surface, a hairline, primary ink. */
.btn--glass{background:var(--glass-1);border-color:var(--line-1);color:var(--fg)}
.btn--glass:hover:not(:disabled){background:var(--glass-2);border-color:var(--line-2)}
.btn--ghost{background:var(--fill-1);border-color:var(--line-1);color:var(--fg)}
.btn--ghost:hover:not(:disabled){background:var(--fill-2);border-color:var(--line-2)}
.btn--quiet{background:none;color:var(--fg-2);border-color:transparent;min-height:30px;padding:0 8px}
.btn--quiet:hover:not(:disabled){background:var(--fill-1);color:var(--fg)}
.btn--quiet[aria-pressed="true"],.btn--quiet.is-on{background:var(--acc-bg);color:var(--accent)}
.btn--quiet-danger:hover:not(:disabled){background:var(--bad-bg);color:var(--bad)}
.btn--sm{min-height:28px;padding:0 10px;font-size:var(--fs-meta);gap:6px}
.btn--sm svg{width:14px;height:14px}
.btn--icon{width:32px;height:32px;min-width:32px;min-height:32px;padding:0}
.btn--icon.btn--sm{width:28px;height:28px;min-width:28px;min-height:28px}
.btn kbd{
  font-family:var(--mono);font-size:10px;padding:0 4px;border-radius:3px;
  background:var(--fill-1);border:1px solid var(--line-1);color:var(--fg-3);
}
.btn--primary kbd{background:var(--fill-on-tint);border-color:transparent;color:inherit}
.btn.is-busy svg{animation:spin 900ms linear infinite}
.btn .spin,.btn.is-busy svg.spin{animation:spin 900ms linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.slot{position:relative;display:inline-flex}
.slot>[hidden]{display:none !important}
.morph{animation:morphIn var(--d-2) var(--e-out)}
@keyframes morphIn{from{opacity:0}to{opacity:1}}

/* Fields */
.field{display:flex;flex-direction:column;gap:6px;min-width:0}
.field>label,.lbl{
  font-size:var(--fs-meta);font-weight:500;color:var(--fg-3);
  letter-spacing:0;text-transform:none;
}
input[type="text"],input[type="password"],input[type="search"],input[type="number"],select,textarea{
  width:100%;min-width:0;
  background:var(--field);color:var(--fg);
  border:1px solid var(--line-1);border-radius:var(--r-2);
  padding:6px 10px;font-size:var(--fs-sm);min-height:32px;
  box-shadow:none;
  transition:border-color var(--d-2) var(--e-out),box-shadow var(--d-2) var(--e-out),
             background-color var(--d-2) var(--e-out);
}
input::placeholder,textarea::placeholder{color:var(--fg-3)}
input:hover:not(:disabled),select:hover:not(:disabled),textarea:hover:not(:disabled){
  border-color:var(--line-2);
}
input:focus,select:focus,textarea:focus{
  outline:none;border-color:var(--accent);background:var(--field-focus);
  box-shadow:0 0 0 3px var(--acc-ring);
}
input:disabled,select:disabled,textarea:disabled{opacity:.55;cursor:not-allowed}
input[aria-invalid="true"]{border-color:var(--bad-fill);box-shadow:0 0 0 3px var(--bad-bg)}
select{
  appearance:none;-webkit-appearance:none;cursor:pointer;padding-right:30px;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='9' height='6' viewBox='0 0 9 6'%3E%3Cpath d='M1 1.2 4.5 4.7 8 1.2' fill='none' stroke='%235f6864' stroke-width='1.3' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 11px center;
}
:root[data-theme="dark"] select{
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='9' height='6' viewBox='0 0 9 6'%3E%3Cpath d='M1 1.2 4.5 4.7 8 1.2' fill='none' stroke='%238a928e' stroke-width='1.3' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]) select{
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='9' height='6' viewBox='0 0 9 6'%3E%3Cpath d='M1 1.2 4.5 4.7 8 1.2' fill='none' stroke='%238a928e' stroke-width='1.3' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
  }
}
.search{position:relative;display:flex;align-items:center;min-width:0}
.phead-actions .search{flex:0 1 220px}
.search svg{position:absolute;left:10px;width:14px;height:14px;color:var(--fg-3);pointer-events:none}
.search input{padding-left:28px}

/* Switch — a real checkbox, restyled. Track, thumb and check live on a span so
   the input keeps its semantics (focus, keyboard, label click). */
.switch{position:relative;display:inline-flex;align-items:center;gap:8px;cursor:pointer;
  user-select:none;font-size:var(--fs-meta);color:var(--fg-2);white-space:nowrap}
.switch:hover{color:var(--fg)}
.switch input{
  position:absolute;left:0;top:50%;width:1px;height:1px;margin:0;padding:0;border:0;
  opacity:0;pointer-events:none;
}
.switch .sw{
  --sw-w:32px; --sw-h:18px; --sw-pad:2px; --sw-travel:14px;
  position:relative;flex:0 0 auto;width:var(--sw-w);height:var(--sw-h);
  border-radius:var(--r-full);
  background:var(--fill-2);border:1px solid var(--line-2);
  transition:background-color var(--d-2) var(--e-out),border-color var(--d-2) var(--e-out);
}
.switch .sw::after{
  content:"";position:absolute;top:var(--sw-pad);left:var(--sw-pad);
  width:calc(var(--sw-h) - var(--sw-pad) * 2);height:calc(var(--sw-h) - var(--sw-pad) * 2);
  border-radius:50%;background:var(--knob);
  box-shadow:var(--sh-knob);
  transition:transform var(--d-3) var(--e-out);
}
.switch .sw::before{content:none}
.switch input:checked + .sw{background:var(--accent-fill);border-color:transparent}
.switch input:checked + .sw::after{transform:translateX(var(--sw-travel))}
.switch:hover input:not(:checked) + .sw{background:var(--fill-3)}
.switch input:focus-visible + .sw{outline:2px solid var(--accent);outline-offset:2px}
.switch input:disabled + .sw{opacity:.5}
.switch input:disabled + .sw,.switch input:disabled ~ *{cursor:not-allowed}
.switch.is-sm .sw{--sw-w:28px; --sw-h:16px; --sw-pad:2px; --sw-travel:12px}
.switch .sw-txt{color:inherit}

/* Segmented control — a small bordered group, one raised selection. */
.seg{
  display:inline-flex;align-items:center;gap:1px;position:relative;
  padding:2px;border-radius:var(--r-2);
  background:var(--fill-1);border:1px solid var(--line-1);
}
.seg-btn{
  position:relative;appearance:none;border:0;background:none;cursor:pointer;
  display:inline-flex;align-items:center;justify-content:center;gap:6px;
  min-width:26px;height:24px;padding:0 7px;border-radius:var(--r-1);
  font-size:var(--fs-micro);font-weight:500;color:var(--fg-3);
  transition:color var(--d-2) var(--e-out),background-color var(--d-2) var(--e-out),
             box-shadow var(--d-2) var(--e-out);
}
.seg-btn svg{width:14px;height:14px;display:block}
.seg-btn:hover{color:var(--fg)}
.seg-btn[aria-checked="true"]{color:var(--fg);background:var(--glass-4);box-shadow:var(--sh-lift-1)}
.seg-btn:active{transform:none}

/* Progress rule — work is shown without moving the layout. */
.hairline{
  position:relative;height:1px;border-radius:0;overflow:hidden;
  background:var(--line-1);opacity:0;transition:opacity var(--d-2) var(--e-out);
}
.hairline.on{opacity:1}
.hairline::after{
  content:"";position:absolute;inset:0;
  background:var(--accent);
  transform:translateX(-100%);
  animation:sweep 1.15s var(--e-inout) infinite;
}
@keyframes sweep{0%{transform:translateX(-100%)}100%{transform:translateX(100%)}}

/* Tag — a small square-cornered label. Text carries the meaning; colour only
   reinforces it. */
.tag{
  display:inline-flex;align-items:center;justify-content:center;gap:5px;
  max-width:100%;padding:1px 6px;border-radius:var(--r-1);
  font-size:var(--fs-micro);font-weight:500;letter-spacing:0;white-space:nowrap;
  overflow-wrap:anywhere;
  background:var(--neu-bg);border:1px solid var(--neu-line);color:var(--fg-2);
  font-variant-numeric:tabular-nums;
}
.tag .tdot{width:5px;height:5px;border-radius:1px;background:currentColor;flex:0 0 5px}
.tag.ok{color:var(--ok);background:var(--ok-bg);border-color:var(--ok-line)}
.tag.bad{color:var(--bad);background:var(--bad-bg);border-color:var(--bad-line)}
.tag.warn{color:var(--warn);background:var(--warn-bg);border-color:var(--warn-line)}
.tag.acc{color:var(--accent);background:var(--acc-bg);border-color:var(--acc-line)}
.tag.cookie{color:var(--warn);background:var(--warn-bg);border-color:var(--warn-line)}

/* Tooltip — a small solid chip owned by the control that shows it. */
[data-tip]{position:relative}
[data-tip]::after{
  content:attr(data-tip);position:absolute;left:50%;bottom:calc(100% + 8px);
  transform:translate(-50%,2px);transform-origin:bottom center;
  padding:5px 8px;border-radius:var(--r-1);max-width:260px;width:max-content;
  background:var(--glass-6);border:1px solid var(--line-2);color:var(--fg);
  font-size:var(--fs-micro);font-weight:400;line-height:1.45;white-space:normal;
  box-shadow:var(--sh-3);
  opacity:0;pointer-events:none;z-index:var(--z-pop);
  transition:opacity var(--d-2) var(--e-out),transform var(--d-2) var(--e-out);
}
[data-tip]:hover::after,[data-tip]:focus-visible::after{opacity:1;transform:translate(-50%,0)}
[data-tip-pos="below"]::after{bottom:auto;top:calc(100% + 8px);transform:translate(-50%,-2px);
  transform-origin:top center}
[data-tip-pos="below"]:hover::after,[data-tip-pos="below"]:focus-visible::after{transform:translate(-50%,0)}
[data-tip-pos="left"]::after{left:auto;right:calc(100% + 8px);bottom:auto;top:50%;
  transform:translate(2px,-50%);transform-origin:right center}
[data-tip-pos="left"]:hover::after,[data-tip-pos="left"]:focus-visible::after{transform:translate(0,-50%)}
[data-tip-pos="right"]::after{left:calc(100% + 8px);bottom:auto;top:50%;
  transform:translate(-2px,-50%);transform-origin:left center}
[data-tip-pos="right"]:hover::after,[data-tip-pos="right"]:focus-visible::after{transform:translate(0,-50%)}

/* ── 6. Content ──────────────────────────────────────────────────────────── */
/* Page head: a title, one line of context, and a silkscreen rule that closes
   the header. It is the same on every section, so the workspace always reads
   as the same document. */
.phead{
  display:flex;align-items:flex-end;gap:var(--sp-4);flex-wrap:wrap;
  padding:0 0 16px;border-bottom:1px solid var(--line-1);
}
.phead-text{min-width:0;max-width:68ch}
.phead h2{font-size:var(--fs-h1);font-weight:550;letter-spacing:var(--track-tight);line-height:var(--lh-tight)}
.phead p{color:var(--fg-2);font-size:var(--fs-sm);margin-top:6px;line-height:1.55}
.phead-actions{display:flex;align-items:center;gap:var(--sp-2);margin-left:auto;flex-wrap:wrap;justify-content:flex-end}

/* Section label — sentence case, one weight up, over a rule. */
.slabel{
  display:flex;align-items:center;gap:var(--sp-3);
  font-size:var(--fs-sm);font-weight:550;color:var(--fg-2);
  letter-spacing:0;text-transform:none;
  margin-top:var(--sp-2);
}
.slabel::before{content:none}
.slabel::after{content:"";flex:1;height:1px;background:var(--line-1)}
.slabel .count{font-weight:400;color:var(--fg-3);font-size:var(--fs-meta)}

.panel{
  border-radius:var(--r-4);padding:var(--sp-4);
  display:flex;flex-direction:column;gap:var(--sp-3);min-width:0;
}
.panel--flush{padding:0;overflow:hidden}
.panel-head{display:flex;align-items:center;gap:var(--sp-2);flex-wrap:wrap;
  padding:12px var(--sp-4);border-bottom:1px solid var(--line-1)}
.panel-head h3{font-size:var(--fs-h3);font-weight:550}
.panel-head .sub{font-size:var(--fs-meta);color:var(--fg-3)}
.panel-body{padding:var(--sp-4)}

/* Readouts — the numerical face of the console. One block, divided by
   hairlines, so figures compare without becoming nine equal cards. */
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(184px,1fr));gap:1px;
  background:var(--line-1);border:1px solid var(--line-1);border-radius:var(--r-4);
  overflow:hidden}
.tile{
  position:relative;min-width:0;padding:12px 16px;
  display:flex;flex-direction:column;gap:2px;
  background:var(--glass-2);border:0;border-radius:0;box-shadow:none;
}
.tile:hover{background:var(--glass-1)}
.tile .k{
  font-size:var(--fs-meta);font-weight:400;color:var(--fg-3);
  letter-spacing:0;text-transform:none;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.tile .v{
  font-family:var(--mono);font-size:17px;font-weight:450;letter-spacing:-.01em;
  line-height:1.35;color:var(--fg);
  font-variant-numeric:tabular-nums;overflow-wrap:anywhere;
}
.tile .v.mono{font-size:17px;font-weight:450}
.tile .hintline{font-size:var(--fs-micro);color:var(--fg-3);margin-top:1px}
.tile--accent{background:var(--glass-2)}
.tile--accent .v{color:var(--accent)}
.card{border-radius:var(--r-4);padding:var(--sp-4);min-width:0;background:var(--glass-2)}

/* Traffic strip — the signature detail. Each tick is one of the last requests:
   its height is the latency, its colour the outcome, so the shape of the last
   minute is legible before any number is read. It is the only place the accent
   appears in the workspace chrome. */
.strip{
  display:flex;align-items:center;gap:12px;height:40px;padding:0 2px;
  border-bottom:1px solid var(--line-1);margin-top:calc(var(--sp-3) * -1);
}
.strip[hidden]{display:none}
.strip-lbl{
  font-family:var(--mono);font-size:var(--fs-micro);color:var(--fg-3);
  white-space:nowrap;font-variant-numeric:tabular-nums;
}
.strip-ticks{flex:1;min-width:0;display:flex;align-items:flex-end;gap:2px;height:20px;overflow:hidden}
.strip-ticks .tk{
  flex:1 1 0;min-width:2px;max-width:12px;border-radius:1px;
  background:var(--accent-fill);opacity:.45;
}
.strip-ticks .tk.warn{background:var(--warn-fill);opacity:.85}
.strip-ticks .tk.bad{background:var(--bad-fill);opacity:.9}

/* A pair of tables that belong together: the wide one leads, the narrow one
   answers a secondary question beside it instead of stacking below. */
.duo{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(0,1fr);gap:var(--sp-5)}
.duo-col{display:flex;flex-direction:column;gap:var(--sp-3);min-width:0}
.duo-col .slabel{margin-top:0}

/* Facts — configuration read as a spec sheet: two columns of key/value rows
   separated by hairlines, so the eye scans labels, not boxes. */
.facts{
  display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));
  gap:1px;margin:0;padding:0;border-radius:var(--r-4);overflow:hidden;
  background:var(--line-1);border:1px solid var(--line-1);
}
.facts .fact{
  display:flex;align-items:baseline;gap:12px;padding:8px 12px;min-width:0;
  background:var(--glass-2);
}
.facts .fact:hover{background:var(--glass-1)}
.facts dt{flex:0 0 auto;font-size:var(--fs-meta);color:var(--fg-3);min-width:104px}
.facts dd{
  margin:0;flex:1;min-width:0;text-align:right;font-size:var(--fs-sm);color:var(--fg);
  overflow-wrap:anywhere;
}
.facts dd.mono{font-family:var(--mono);font-size:var(--fs-meta);color:var(--fg-2)}

/* Callouts — health findings, errors, disclaimers: a rule, an icon, a line. */
.callout{
  display:flex;gap:10px;align-items:flex-start;
  border-radius:var(--r-2);padding:10px 12px;font-size:var(--fs-sm);line-height:1.55;
  border:1px solid var(--line-1);border-left-width:2px;background:var(--glass-1);
}
.callout .ico{width:16px;height:16px;margin-top:2px;color:var(--fg-2);flex:0 0 16px}
.callout strong{font-weight:550}
.callout--ok{background:var(--ok-bg);border-color:var(--ok-line);border-left-color:var(--ok-fill)}
.callout--ok .ico{color:var(--ok)}
.callout--warn{background:var(--warn-bg);border-color:var(--warn-line);border-left-color:var(--warn-fill)}
.callout--warn .ico{color:var(--warn)}
.callout--bad{background:var(--bad-bg);border-color:var(--bad-line);border-left-color:var(--bad-fill)}
.callout--bad .ico{color:var(--bad)}
.callout--info{background:var(--acc-bg);border-color:var(--acc-line);border-left-color:var(--accent)}
.callout--info .ico{color:var(--accent)}
.callout .ctx-body{min-width:0;max-width:76ch}
.callout-list{display:grid;gap:var(--sp-2)}

code,kbd{
  font-family:var(--mono);font-size:.92em;
  background:var(--code-bg);border:1px solid var(--line-1);
  padding:0 4px;border-radius:3px;overflow-wrap:anywhere;
}
kbd{font-size:11px;padding:1px 5px;background:var(--glass-2);border-color:var(--line-2);
  box-shadow:var(--sh-lift-1);color:var(--fg-2)}
.ks,.pal-item .kbd{display:inline-flex;align-items:center;gap:4px;flex-wrap:wrap}
pre code{background:none;border:0;padding:0;font-size:inherit;overflow-wrap:normal}

/* Tables — one surface, hairline rows, a sticky header in the page's own fill. */
.tablewrap{
  border-radius:var(--r-4);overflow:hidden;
  display:flex;flex-direction:column;min-width:0;
  background:var(--glass-2);border:1px solid var(--line-1);
}
.scroller{overflow:auto;min-width:0;-webkit-overflow-scrolling:touch}
.scroller.grow{flex:1;min-height:180px}
table{width:100%;border-collapse:separate;border-spacing:0;font-size:var(--fs-sm)}
caption.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
th,td{text-align:left;padding:8px 12px;vertical-align:top;border-bottom:1px solid var(--line-1)}
thead th{
  position:sticky;top:0;z-index:1;
  background:var(--glass-1);
  font-size:var(--fs-meta);font-weight:500;color:var(--fg-3);
  letter-spacing:0;text-transform:none;
  padding:8px 12px;border-bottom:1px solid var(--line-2);
  white-space:nowrap;
}
tbody td{transition:background-color var(--d-1) var(--e-out)}
tbody tr:hover td{background:var(--fill-1)}
tbody tr:last-child td{border-bottom:0}
td.code{font-family:var(--mono);font-size:var(--fs-meta);color:var(--fg-2);white-space:nowrap;
  font-variant-numeric:tabular-nums}
td .sub{display:block;margin-top:2px;font-size:var(--fs-micro);color:var(--fg-3)}
/* Quantities sit right and in mono, so a column can be compared by eye without
   reading every cell. Text columns stay left; nothing is centred. */
th.num,td.num{
  text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums;
  color:var(--fg-2);white-space:nowrap;
}
tr.is-new td{animation:rowIn var(--d-5) var(--e-out)}
@keyframes rowIn{from{background-color:var(--acc-bg)}to{background-color:transparent}}
.tnote{padding:8px 12px;border-top:1px solid var(--line-1);font-size:var(--fs-meta);color:var(--fg-3);
  background:var(--glass-1);line-height:1.5}
.empty-cell{padding:20px 12px;color:var(--fg-3);font-size:var(--fs-sm)}

/* Meters — inline bars for cooldowns and distributions. */
.meter{display:flex;align-items:center;gap:8px;min-width:96px;margin-top:4px}
.meter .track{position:relative;height:3px;flex:1;border-radius:1px;background:var(--fill-2);overflow:hidden}
.meter .fill{position:absolute;inset:0 auto 0 0;border-radius:1px;background:currentColor;opacity:.7;
  transition:width var(--d-5) var(--e-out)}
.meter .val{font-size:var(--fs-micro);font-family:var(--mono);color:var(--fg-3);
  font-variant-numeric:tabular-nums;min-width:30px;text-align:right}

/* ── 7. Chat ─────────────────────────────────────────────────────────────── */
.chat{
  display:grid;grid-template-columns:220px minmax(0,1fr);gap:var(--sp-5);
  height:clamp(400px, calc(100dvh - 300px), 720px);min-height:0;
}
.crail{
  border-radius:0;padding:0;display:flex;flex-direction:column;gap:var(--sp-2);
  min-height:0;min-width:0;
  background:none;border:0;border-right:1px solid var(--line-1);
  box-shadow:none;padding-right:var(--sp-3);
}
.crail-head{display:flex;align-items:center;gap:var(--sp-2);padding:0 0 8px;border-bottom:1px solid var(--line-1)}
.crail-head .lbl{flex:1}
.crail-list{flex:1;min-height:0;overflow:auto;display:flex;flex-direction:column;gap:1px;
  padding:4px 0;scrollbar-width:thin}
.conv{
  position:relative;display:flex;align-items:center;gap:8px;height:32px;padding:0 8px;
  border-radius:var(--r-1);cursor:pointer;border:1px solid transparent;
  font-size:var(--fs-sm);min-width:0;
  transition:background-color var(--d-1) var(--e-out),border-color var(--d-1) var(--e-out);
}
.conv:hover{background:var(--fill-1)}
.conv .t{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:450}
.conv .n{font-size:var(--fs-micro);color:var(--fg-3);font-family:var(--mono);
  font-variant-numeric:tabular-nums;flex:0 0 auto}
.conv.sel{background:var(--fill-1);border-color:var(--line-1)}
.conv.sel .t{font-weight:500;color:var(--fg)}
.conv.sel::before{content:"";position:absolute;left:-1px;top:6px;bottom:6px;width:2px;
  border-radius:0 2px 2px 0;background:var(--accent)}
.conv .x{
  appearance:none;border:0;background:none;color:var(--fg-3);cursor:pointer;
  display:grid;place-items:center;width:20px;height:20px;border-radius:var(--r-1);flex:0 0 20px;
  opacity:0;transition:opacity var(--d-1) var(--e-out),color var(--d-1) var(--e-out),
             background-color var(--d-1) var(--e-out);
}
.conv .x svg{width:12px;height:12px}
.conv:hover .x,.conv:focus-within .x{opacity:1}
.conv .x:hover{color:var(--bad);background:var(--bad-bg)}
.rail-empty{padding:8px 4px;font-size:var(--fs-meta);color:var(--fg-3);line-height:1.5}
.crail-foot{padding:8px 0 0;border-top:1px solid var(--line-1);
  font-size:var(--fs-micro);color:var(--fg-3);line-height:1.45}
button.conv-open{
  appearance:none;border:0;background:none;padding:0;margin:0;cursor:pointer;
  font:inherit;font-size:var(--fs-sm);color:inherit;text-align:left;
  flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}
button.conv-open:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:var(--r-1)}

.thread{
  border-radius:var(--r-4);overflow:hidden;display:flex;flex-direction:column;
  min-width:0;min-height:0;background:var(--glass-2);border:1px solid var(--line-1);
  box-shadow:none;
}
.thread-bar{
  display:flex;align-items:center;gap:var(--sp-2);flex-wrap:wrap;
  padding:8px var(--sp-3);border-bottom:1px solid var(--line-1);
  background:var(--glass-1);
}
.thread-bar .spacer{flex:1}
.thread-bar select{width:auto;min-width:132px;max-width:240px;padding:5px 26px 5px 9px;
  font-size:var(--fs-meta);min-height:28px;background-color:var(--glass-2)}
.thread-bar .key-field{position:relative;display:flex;align-items:center}
.thread-bar .key-field svg{position:absolute;left:9px;width:12px;height:12px;color:var(--fg-3)}
.thread-bar input[type="password"]{width:196px;padding:5px 9px 5px 26px;font-size:var(--fs-meta);
  min-height:28px;background-color:var(--glass-2)}

.thread-body{position:relative;flex:1;display:flex;flex-direction:column;min-height:0}
.msgs{
  flex:1;overflow:auto;padding:var(--sp-5) clamp(14px,2vw,24px);display:flex;flex-direction:column;
  gap:var(--sp-5);
}
.msg{display:grid;grid-template-columns:24px minmax(0,1fr);gap:12px}
.msg-avatar{
  width:24px;height:24px;border-radius:var(--r-1);display:grid;place-items:center;
  background:var(--fill-1);border:1px solid var(--line-1);
  color:var(--fg-3);margin-top:1px;
}
.msg-avatar svg{width:13px;height:13px}
.msg.assistant .msg-avatar{color:var(--accent);background:var(--acc-bg);border-color:var(--acc-line)}
.msg.err .msg-avatar{color:var(--bad);background:var(--bad-bg);border-color:var(--bad-line)}
.msg-body{min-width:0;max-width:76ch}
.msg-head{display:flex;align-items:baseline;gap:8px;margin-bottom:4px}
.msg-who{font-size:var(--fs-meta);font-weight:550;color:var(--fg-2)}
.msg-time{font-size:var(--fs-micro);color:var(--fg-3);font-variant-numeric:tabular-nums;
  font-family:var(--mono)}
.msg .bub{font-size:var(--fs-body);line-height:1.62;overflow-wrap:anywhere;min-width:0}
.msg.user .bub{
  display:inline-block;max-width:min(100%,70ch);
  background:var(--acc-bg);color:var(--fg);
  border:1px solid var(--acc-line);
  border-radius:var(--r-3);
  padding:10px 12px;
}
.msg.user .bub code{background:var(--fill-1);border-color:transparent;color:inherit}
.msg.err .bub{
  background:var(--bad-bg);border:1px solid var(--bad-line);color:var(--bad);
  border-radius:var(--r-3);padding:10px 12px;
}
.msg .bub p{margin:0 0 var(--sp-2)}
.msg .bub p:last-child{margin-bottom:0}
.msg .bub .mdh{
  font-size:var(--fs-lead);font-weight:600;line-height:1.3;
  margin:var(--sp-4) 0 var(--sp-2);
}
.msg .bub .mdh:first-child{margin-top:0}
.msg .bub .mdh:last-child{margin-bottom:0}
.msg .bub pre{
  margin:var(--sp-3) 0;padding:var(--sp-3);border-radius:var(--r-2);overflow:auto;
  background:var(--code-bg);border:1px solid var(--line-1);
  font-family:var(--mono);font-size:var(--fs-meta);line-height:1.55;scrollbar-width:thin;
}
.msg .bub code{font-family:var(--mono);font-size:.92em;background:var(--fill-1);
  border-color:transparent;padding:0 4px;border-radius:var(--r-1)}
.msg .bub pre code{background:none;border:0;padding:0}
.msg .bub strong{font-weight:600}
.mmeta{
  display:flex;align-items:center;gap:6px;flex-wrap:wrap;
  margin-top:8px;font-size:var(--fs-micro);color:var(--fg-3);
  font-family:var(--mono);font-variant-numeric:tabular-nums;
}
.mmeta .mchip{padding:0 5px;border-radius:var(--r-1);background:var(--fill-1);
  border:1px solid var(--line-1)}
/* Streaming caret: a thin bar that marks where the next token lands. */
.cursor{
  display:inline-block;width:2px;height:1em;margin-left:2px;
  background:var(--accent);vertical-align:text-bottom;
  animation:blink 1.05s var(--e-inout) infinite;
}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.15}}
.thread-anchor{display:flex;align-items:center;gap:10px;font-size:var(--fs-micro);
  color:var(--fg-3);padding-top:2px}
.thread-anchor::before,.thread-anchor::after{content:"";height:1px;flex:1;background:var(--line-1)}

.tobottom{
  position:absolute;right:var(--sp-4);bottom:var(--sp-4);z-index:var(--z-panel);
  width:32px;height:32px;border-radius:var(--r-2);cursor:pointer;
  display:grid;place-items:center;color:var(--fg-2);
  background:var(--glass-4);border:1px solid var(--line-2);
  box-shadow:var(--sh-3);
  opacity:0;pointer-events:none;transform:translateY(4px);
  transition:opacity var(--d-2) var(--e-out),transform var(--d-2) var(--e-out),
             background-color var(--d-2) var(--e-out);
}
.tobottom svg{width:15px;height:15px}
.tobottom.show{opacity:1;pointer-events:auto;transform:none}
.tobottom:hover{background:var(--glass-5);color:var(--fg)}

/* Composer — one well holding the field, the meta line and the actions. */
.composer{padding:var(--sp-3) var(--sp-3) var(--sp-3);border-top:1px solid var(--line-1);
  display:flex;flex-direction:column;gap:6px;background:var(--glass-1)}
.composer .well{
  border-radius:var(--r-3);border:1px solid var(--line-2);background:var(--glass-2);
  padding:8px 10px;display:flex;flex-direction:column;gap:6px;
  box-shadow:none;
  transition:border-color var(--d-2) var(--e-out),box-shadow var(--d-2) var(--e-out);
}
.composer .well:focus-within{border-color:var(--accent);box-shadow:0 0 0 3px var(--acc-ring)}
.composer textarea{
  width:100%;min-height:22px;max-height:200px;resize:none;padding:0;border:0;background:none;
  font-size:var(--fs-body);line-height:1.6;box-shadow:none;
}
.composer textarea:focus{box-shadow:none;background:none}
.composer .well-foot{display:flex;align-items:center;gap:var(--sp-2);flex-wrap:wrap;min-width:0}
.composer .well-foot .spacer{flex:1}
.composer-under{display:flex;align-items:center;gap:var(--sp-3);flex-wrap:wrap;padding:0 1px}
.composer .hint{font-size:var(--fs-micro);color:var(--fg-3);
  display:flex;align-items:center;gap:5px}
.composer .hint kbd{font-size:10px}
.chat-footnote{font-size:var(--fs-meta);color:var(--fg-3);line-height:1.55;max-width:88ch;padding:0}

/* Empty states — an icon, a title, one line, one suggested next step. */
.empty{
  margin:auto;text-align:center;max-width:44ch;padding:var(--sp-7) var(--sp-4);
  display:flex;flex-direction:column;align-items:center;gap:4px;
}
.empty-icon{
  width:32px;height:32px;border-radius:var(--r-2);display:grid;place-items:center;
  color:var(--accent);background:var(--acc-bg);border:1px solid var(--acc-line);
  margin-bottom:var(--sp-2);
}
.empty-icon svg{width:16px;height:16px}
.empty h3{font-size:var(--fs-lead);font-weight:550;color:var(--fg)}
.empty p{font-size:var(--fs-sm);color:var(--fg-2);line-height:1.55;margin-top:2px}
.empty .btn{margin-top:var(--sp-3)}
.chips{display:flex;gap:6px;flex-wrap:wrap;justify-content:center;margin-top:var(--sp-3)}
.chip{
  position:relative;appearance:none;cursor:pointer;font-family:inherit;
  font-size:var(--fs-meta);font-weight:450;color:var(--fg-2);
  background:var(--glass-2);border:1px solid var(--line-1);border-radius:var(--r-2);
  padding:6px 10px;box-shadow:none;
  transition:color var(--d-1) var(--e-out),background-color var(--d-1) var(--e-out),
             border-color var(--d-1) var(--e-out);
}
.chip:hover{color:var(--fg);background:var(--glass-1);border-color:var(--line-2)}

/* ── 8. Data ─────────────────────────────────────────────────────────────── */
/* The strip chart is the one place the product is allowed to be rich: graph
   paper, a signal-blue series, mono axis labels. Everything around it stays
   quiet so the chart reads first. */
.chart{border-radius:var(--r-4);padding:var(--sp-4);display:flex;flex-direction:column;gap:var(--sp-3);
  background:var(--glass-2);border:1px solid var(--line-1)}
.chart-head{display:flex;align-items:baseline;gap:var(--sp-3);flex-wrap:wrap}
.chart-head h3{font-size:var(--fs-h3);font-weight:550}
.chart-head .sub{font-size:var(--fs-meta);color:var(--fg-3);font-family:var(--mono)}
.chart-head .spacer{flex:1}
.chart-legend{display:flex;align-items:center;gap:6px;font-size:var(--fs-micro);color:var(--fg-3)}
.chart-legend .swatch{width:8px;height:8px;border-radius:2px;background:var(--accent-fill);opacity:.9}
.plot{position:relative;padding-left:44px;padding-bottom:18px;min-height:150px}
.gridlines{position:absolute;inset:0 0 18px 44px;display:flex;flex-direction:column;
  justify-content:space-between;pointer-events:none}
.gridlines span{position:relative;border-top:1px solid var(--line-1);flex:0 0 auto}
.gridlines span i{
  position:absolute;left:-44px;top:-9px;width:38px;text-align:right;font-style:normal;
  font-family:var(--mono);font-size:var(--fs-micro);color:var(--fg-3);
}
.bars{
  position:relative;display:flex;align-items:flex-end;gap:clamp(4px,1.1vw,10px);height:132px;
  background-image:linear-gradient(to right,var(--grid) 1px,transparent 1px),
                   linear-gradient(to bottom,var(--grid) 1px,transparent 1px);
  background-size:16px 16px;background-position:0 100%;
}
.bar{
  position:relative;flex:1 1 0;min-width:0;height:100%;display:flex;align-items:flex-end;
  background:none;border:0;padding:0;cursor:default;
}
.bar .fill{
  width:100%;border-radius:2px 2px 0 0;
  background:var(--accent-fill);opacity:.85;
  height:max(2px,var(--h));
  transition:opacity var(--d-2) var(--e-out);
}
.bar:hover .fill,.bar:focus-visible .fill{opacity:1}
.bar .xlab{
  position:absolute;left:50%;transform:translateX(-50%);bottom:-19px;white-space:nowrap;
  font-family:var(--mono);font-size:var(--fs-micro);color:var(--fg-3);
}
.bar[data-tip]::after{bottom:calc(100% + 5px)}
/* Edge bars anchor their tooltip inward, so it never clips at the pane's edge. */
.bar:first-child[data-tip]::after,.bar:nth-child(2)[data-tip]::after{
  left:0;transform:translate(0,2px);transform-origin:bottom left;
}
.bar:first-child[data-tip]:hover::after,.bar:first-child[data-tip]:focus-visible::after,
.bar:nth-child(2)[data-tip]:hover::after,.bar:nth-child(2)[data-tip]:focus-visible::after{transform:translate(0,0)}
.bar:nth-last-child(-n+2)[data-tip]::after{left:auto;right:0;transform:translate(0,2px);
  transform-origin:bottom right}
.bar:nth-last-child(-n+2)[data-tip]:hover::after,
.bar:nth-last-child(-n+2)[data-tip]:focus-visible::after{transform:translate(0,0)}
/* The busiest bucket is labelled in place, so the chart answers its own
   question without a hover. */
.bar .peak{
  position:absolute;left:50%;transform:translateX(-50%);
  bottom:clamp(12px, calc(var(--h) + 6px), calc(100% - 20px));
  font-family:var(--mono);font-size:var(--fs-micro);color:var(--accent);white-space:nowrap;
}
.chart-x{display:flex;align-items:center;gap:6px;font-size:var(--fs-micro);color:var(--fg-3);
  padding-left:44px}

/* Models — a divided list, not a grid of identical cards. */
#models{display:flex;flex-direction:column;gap:0;background:var(--glass-2);
  border:1px solid var(--line-1);border-radius:var(--r-4);overflow:hidden}
.model-card{
  display:grid;grid-template-columns:minmax(190px,.85fr) minmax(0,1.7fr) auto;
  grid-template-areas:"name desc tags" "name hint tags";
  column-gap:20px;row-gap:2px;align-items:start;
  padding:12px 16px;min-width:0;background:none;border:0;border-bottom:1px solid var(--line-1);
  border-radius:0;
}
#models .model-card:last-child{border-bottom:0}
.model-card:hover{background:var(--glass-1)}
.model-card[hidden]{display:none}
.model-top{grid-area:name;display:flex;align-items:center;gap:8px;min-width:0;padding-top:1px}
.model-id{
  flex:1 1 auto;min-width:0;background:none;border:0;padding:0;
  font-family:var(--mono);font-size:var(--fs-sm);font-weight:500;letter-spacing:-.01em;
  overflow-wrap:break-word;color:var(--fg);
}
.model-tags{grid-area:tags;display:flex;align-items:center;gap:5px;flex-wrap:wrap;
  justify-content:flex-end;min-width:0;padding-top:1px}
.model-desc{grid-area:desc;font-size:var(--fs-sm);color:var(--fg-2);line-height:1.5}
.model-card .hintline{grid-area:hint;font-size:var(--fs-micro);color:var(--fg-3);font-family:var(--mono)}

/* Copy buttons — small, square, and they answer with a drawn check. */
button.copy{
  appearance:none;cursor:pointer;flex:0 0 auto;
  font-family:inherit;font-size:var(--fs-micro);font-weight:500;color:var(--fg-3);
  background:none;border:1px solid var(--line-1);border-radius:var(--r-1);
  padding:1px 6px;white-space:nowrap;
  transition:color var(--d-1) var(--e-out),background-color var(--d-1) var(--e-out),
             border-color var(--d-1) var(--e-out);
}
button.copy .lbl{display:inline-flex;align-items:center;gap:4px}
button.copy:hover{color:var(--accent);background:var(--acc-bg);border-color:var(--acc-line)}
button.copy.done{color:var(--ok);background:var(--ok-bg);border-color:var(--ok-line)}
button.copy.done .lbl::before{
  content:"";width:10px;height:10px;flex:0 0 auto;background:currentColor;
  -webkit-mask:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='M4.5 12.5 9.6 17.6 19.5 6.8' fill='none' stroke='%23000' stroke-width='2.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") center/contain no-repeat;
  mask:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='M4.5 12.5 9.6 17.6 19.5 6.8' fill='none' stroke='%23000' stroke-width='2.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") center/contain no-repeat;
}
td .copy{margin-top:1px}

/* Footer — a quiet sign-off inside the workspace. */
footer.foot{padding:var(--sp-6) 0 0;font-size:var(--fs-meta);color:var(--fg-3);
  border-top:1px solid var(--line-1);margin-top:var(--sp-2)}
footer.foot #foot{display:flex;align-items:center;gap:var(--sp-3);flex-wrap:wrap}
footer.foot #foot>span{min-width:0}
.dot-sep{flex:0 0 auto;display:inline-block;width:3px;height:3px;border-radius:50%;background:var(--line-3)}

/* ── 9. Overlays ─────────────────────────────────────────────────────────── */
.veil{
  position:fixed;inset:0;z-index:var(--z-dialog);
  display:flex;align-items:center;justify-content:center;
  padding:clamp(12px,3vw,32px);
  background:var(--scrim);
  animation:veilIn var(--d-3) var(--e-out) both;
}
.veil[hidden]{display:none}
.veil--top{align-items:flex-start;padding-top:clamp(48px,10vh,120px)}
.veil.is-closing{animation:veilOut var(--d-2) var(--e-out) both}
@keyframes veilIn{from{opacity:0}to{opacity:1}}
@keyframes veilOut{from{opacity:1}to{opacity:0}}
.sheet{
  width:min(540px,100%);max-height:min(86vh,760px);
  border-radius:var(--r-6);display:flex;flex-direction:column;min-height:0;
  animation:sheetIn var(--d-4) var(--e-out) both;outline:none;
}
.veil.is-closing .sheet{animation:sheetOut var(--d-2) var(--e-out) both}
@keyframes sheetIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@keyframes sheetOut{from{opacity:1;transform:none}to{opacity:0;transform:translateY(4px)}}
.sheet-head{
  display:flex;align-items:center;gap:var(--sp-3);padding:12px var(--sp-4);
  border-bottom:1px solid var(--line-1);
}
.sheet-head h3{font-size:var(--fs-h3);font-weight:550;flex:1;min-width:0;
  display:flex;flex-direction:column;gap:2px}
.sheet-head .sub{display:block;font-size:var(--fs-meta);color:var(--fg-3);font-weight:400}
.sheet-body{padding:var(--sp-3) var(--sp-4) var(--sp-4);overflow:auto;display:flex;
  flex-direction:column;gap:var(--sp-2);min-height:0}
.sheet--sm{width:min(420px,100%)}

/* Command palette — a floating search field, not a page. */
.palette{
  width:min(600px,100%);border-radius:var(--r-6);overflow:hidden;
  display:flex;flex-direction:column;
  animation:sheetIn var(--d-3) var(--e-out) both;
}
.pal-input{display:flex;align-items:center;gap:10px;padding:12px 16px;border-bottom:1px solid var(--line-1)}
.pal-input svg{width:16px;height:16px;color:var(--fg-3)}
.pal-input input{
  flex:1;min-width:0;border:0;background:none;box-shadow:none;padding:0;min-height:24px;
  font-size:var(--fs-lead);
}
.pal-input input:focus{box-shadow:none;background:none}
.pal-list{max-height:min(52vh,420px);overflow:auto;padding:8px;display:flex;flex-direction:column;gap:2px}
.pal-group{padding:8px 8px 4px;font-size:var(--fs-micro);font-weight:500;color:var(--fg-3);
  letter-spacing:0;text-transform:none}
.pal-item{
  position:relative;appearance:none;border:1px solid transparent;background:none;
  cursor:pointer;width:100%;
  display:flex;align-items:center;gap:8px;padding:8px;border-radius:var(--r-1);
  font-size:var(--fs-sm);color:var(--fg);text-align:left;
  transition:background-color var(--d-1) var(--e-out),border-color var(--d-1) var(--e-out);
}
.pal-item .ico{width:15px;height:15px;color:var(--fg-3);flex:0 0 15px}
.pal-item .t{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pal-item .kbd{font-size:10px;color:var(--fg-3)}
.pal-item[aria-selected="true"]{background:var(--fill-2);border-color:var(--line-1)}
.pal-item[aria-selected="true"] .ico{color:var(--accent)}
.pal-empty{padding:var(--sp-6) var(--sp-4);text-align:center;color:var(--fg-3);font-size:var(--fs-sm)}
.pal-foot{display:flex;align-items:center;gap:var(--sp-3);padding:8px var(--sp-4);border-top:1px solid var(--line-1);
  font-size:var(--fs-micro);color:var(--fg-3);background:var(--glass-1)}
.pal-foot span{display:inline-flex;align-items:center;gap:5px}

/* Shortcut list — rows, not cards. */
.keys{display:grid;gap:1px}
.key-row{display:flex;align-items:center;gap:var(--sp-3);padding:6px 8px;border-radius:var(--r-1)}
.key-row:nth-child(odd){background:var(--fill-1)}
.key-row .t{flex:1;font-size:var(--fs-sm);color:var(--fg-2)}
.key-row .ks{display:flex;gap:4px;flex-wrap:wrap;justify-content:flex-end}

/* Toasts — small solid cards with a timer you can read. */
.toasts{
  position:fixed;z-index:var(--z-toast);right:20px;bottom:20px;
  display:flex;flex-direction:column;gap:8px;align-items:flex-end;
  pointer-events:none;max-width:min(380px,calc(100vw - 32px));
}
.toast{
  pointer-events:auto;position:relative;overflow:hidden;
  display:flex;align-items:flex-start;gap:10px;
  padding:10px 12px;border-radius:var(--r-3);min-width:240px;max-width:100%;
  background:var(--glass-6);border:1px solid var(--line-2);box-shadow:var(--sh-3);
  animation:toastIn var(--d-3) var(--e-out) both;
}
.toast.is-closing{animation:toastOut var(--d-2) var(--e-out) both}
@keyframes toastIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@keyframes toastOut{to{opacity:0;transform:translateY(4px)}}
.toast .ico{width:16px;height:16px;margin-top:1px;color:var(--accent);flex:0 0 16px}
.toast--ok .ico{color:var(--ok)}
.toast--warn .ico{color:var(--warn)}
.toast--bad .ico{color:var(--bad)}
.toast .txt{flex:1;min-width:0;font-size:var(--fs-sm);line-height:1.5}
.toast .txt strong{display:block;font-weight:550;margin-bottom:1px}
.toast .txt .sub{color:var(--fg-2);font-size:var(--fs-meta)}
.toast .x{
  appearance:none;border:0;background:none;color:var(--fg-3);cursor:pointer;
  width:20px;height:20px;border-radius:3px;display:grid;place-items:center;flex:0 0 20px;
  transition:background-color var(--d-1) var(--e-out),color var(--d-1) var(--e-out);
}
.toast .x:hover{background:var(--fill-2);color:var(--fg)}
.toast .x svg{width:12px;height:12px}
.toast-timer{
  position:absolute;left:0;right:0;bottom:0;height:2px;transform-origin:left;
  background:currentColor;opacity:.28;
  animation:toastTimer var(--toast-ms,3200ms) linear forwards;
}
@keyframes toastTimer{from{transform:scaleX(1)}to{transform:scaleX(0)}}
.toast.is-paused .toast-timer{animation-play-state:paused}
.toast--info{color:var(--accent)}
.toast--ok{color:var(--ok)}
.toast--warn{color:var(--warn)}
.toast--bad{color:var(--bad)}
.toast .txt,.toast .x{color:var(--fg)}

/* ── 10. States ──────────────────────────────────────────────────────────── */
/* Skeletons hold the shape of what is coming at a steady tone. No sweep, no
   pulse: a placeholder is not an event. */
.sk{position:relative;overflow:hidden;border-radius:3px;background:var(--fill-1);color:transparent}
.sk::after{content:none}
.sk--line{height:10px}
.sk--val{height:15px;margin-top:4px;width:70%}
.sk-tile{pointer-events:none}
.sk-tile:hover{background:var(--glass-2)}
tr.sk-row td{padding:11px 14px}
tr.sk-row:hover td{background:none}

/* Utilities */
.spacer{flex:1}
.stack{display:flex;flex-direction:column;gap:var(--sp-4)}
.row{display:flex;align-items:center;gap:var(--sp-2);flex-wrap:wrap}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
.note{font-size:var(--fs-meta);color:var(--fg-3);line-height:1.55}
.note strong{color:var(--fg-2);font-weight:550}
.nowrap{white-space:nowrap}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--fill-2);border-radius:var(--r-full);
  border:3px solid transparent;background-clip:content-box}
::-webkit-scrollbar-thumb:hover{background:var(--fill-3);background-clip:content-box;
  border:3px solid transparent}
main.content, .scroller, .crail-list, .pal-list, .msgs{scrollbar-color:var(--fill-2) transparent}

/* ── 11. Responsive ──────────────────────────────────────────────────────── */
@media (max-width:1240px){
  :root{--rail:204px}
  .chat{grid-template-columns:200px minmax(0,1fr)}
}
@media (max-width:1080px){
  :root{--rail:188px}
  .brand-text p{display:none}
  .tb-context{display:none}
}

/* Tablet and below: the navigation column becomes a thumb-reachable bar at the
   bottom of the frame. Same DOM, repositioned. */
@media (max-width:980px){
  .topbar{height:52px;padding:0 14px;gap:var(--sp-3)}
  .tb-context{display:flex;opacity:1;transform:none}
  .body{grid-template-columns:minmax(0,1fr)}
  .railwrap{
    position:fixed;left:0;right:0;bottom:0;z-index:var(--z-chrome);height:auto;
    padding:0 0 env(safe-area-inset-bottom,0px);
    pointer-events:none;background:none}
  .railwrap{border-right:0}
  .rail{
    position:relative;inset:auto;height:auto;flex-direction:row;align-items:center;
    padding:6px 8px;max-width:none;margin:0;pointer-events:auto;
    background:var(--glass-3);border-top:1px solid var(--line-1);
  }
  .rail-foot{display:none}
  nav.tabs{flex:1;flex-direction:row;justify-content:space-between;gap:2px;padding:0}
  .tab-btn{
    flex:1 1 0;flex-direction:column;gap:3px;height:auto;padding:6px 2px 5px;
    font-size:var(--fs-micro);text-align:center;align-items:center;min-width:0;border-radius:var(--r-2);
  }
  .tab-btn .ico{width:18px;height:18px}
  .tab-btn .lbl{max-width:100%;overflow:hidden;text-overflow:ellipsis;flex:none}
  .tab-btn .navkbd{display:none}
  .tab-btn[aria-selected="true"]{background:var(--acc-bg)}
  .tab-btn[aria-selected="true"]::before{content:none}
  main.content{
    padding:20px 16px calc(88px + env(safe-area-inset-bottom,0px));
    border-radius:0;
  }
  .chat{grid-template-columns:minmax(0,1fr);gap:var(--sp-3);
    height:clamp(420px, calc(100dvh - 320px), 760px)}
  /* The conversation rail becomes a horizontal strip: no half-empty column. */
  .crail{padding:0;background:none;border:0;box-shadow:none;overflow:visible}
  .crail-head{display:flex;flex-direction:row-reverse;align-items:center;justify-content:flex-end;
    gap:var(--sp-2);padding:0;border:0}
  .crail-head .lbl{display:flex;align-items:center;min-height:32px;padding:0 9px;
    border-radius:var(--r-2);background:var(--fill-1);border:1px solid var(--line-1);
    font-size:var(--fs-meta);font-weight:500;color:var(--fg-2);white-space:nowrap}
  .crail-head .btn{width:32px;min-height:32px;border-radius:var(--r-2);border-color:var(--line-1)}
  .crail-list{flex-direction:row;gap:6px;overflow-x:auto;overflow-y:hidden;
    padding:0 0 2px;scroll-snap-type:x proximity}
  .crail-list:empty{display:none}
  .conv{flex:0 0 auto;max-width:230px;scroll-snap-align:start;height:32px;
    background:var(--fill-1);border-color:var(--line-1)}
  .conv.sel::before{display:none}
  .conv .x{opacity:1}
  .crail-foot,.rail-empty{display:none}
  .thread{min-height:0}
  .thread-bar{padding:8px var(--sp-3)}
  .msgs{padding:var(--sp-4) var(--sp-3)}
  .composer{padding:var(--sp-3)}
  .toasts{left:12px;right:12px;bottom:calc(84px + env(safe-area-inset-bottom,0px));
    align-items:stretch;max-width:none}
  .toast{width:100%;min-width:0}
}

@media (max-width:760px){
  #verPill{display:none}
  .topbar-end{gap:var(--sp-2)}
  .phead{align-items:flex-start;padding-bottom:12px}
  .phead h2{font-size:var(--fs-h2)}
  .phead-actions{width:100%;justify-content:flex-start;margin-left:0}
  #models .model-card{
    grid-template-columns:minmax(0,1fr);grid-template-areas:"name" "desc" "tags" "hint";
    row-gap:4px;padding:12px;
  }
  .model-tags{justify-content:flex-start}
  /* Tables reflow into labelled rows: label above value, no sideways scroll. */
  table.stack-rows thead{display:none}
  table.stack-rows,table.stack-rows tbody,table.stack-rows tr,table.stack-rows td{display:block;width:100%}
  table.stack-rows tr{padding:8px 12px;border-bottom:1px solid var(--line-1)}
  table.stack-rows tr:last-child{border-bottom:0}
  table.stack-rows td{border:0;padding:1px 0;display:flex;gap:12px;justify-content:space-between;
    align-items:baseline;text-align:right}
  table.stack-rows td::before{
    content:attr(data-label);flex:0 0 auto;font-size:var(--fs-micro);font-weight:400;
    color:var(--fg-3);letter-spacing:0;text-transform:none;text-align:left;
  }
  table.stack-rows td.code{white-space:normal;overflow-wrap:anywhere}
  table.stack-rows td:empty{display:none}
  .sheet,.palette{width:100%;border-radius:var(--r-6)}
  .veil{align-items:flex-end;padding:8px}
  .veil--top{align-items:flex-start}
  .plot{padding-left:40px}
  .gridlines{left:40px}
  .gridlines span i{left:-40px;width:34px}
  .chart-x{padding-left:40px}
  .tiles{grid-template-columns:repeat(auto-fit,minmax(140px,1fr))}
}
/* The paired tables stack once the wide one would be squeezed. */
@media (max-width:900px){
  .duo{grid-template-columns:minmax(0,1fr);gap:var(--sp-5)}
}
/* Short viewports: the rail gives up its status line before the nav loses items. */
@media (max-height:520px) and (min-width:981px){
  .rail-foot{display:none}
}
/* Landscape phones cannot afford a window-sized transcript; the pane scrolls
   instead, and the composer stays reachable. */
@media (max-height:560px) and (max-width:980px){
  .chat{height:auto;min-height:0}
  .thread{min-height:min(64vh,340px)}
}
@media (max-width:620px){
  /* Stack the chat toolbar and give the API-key field a full row. */
  .thread-bar{flex-wrap:wrap;gap:var(--sp-2);padding:8px var(--sp-3)}
  .thread-bar .spacer{display:none}
  .thread-bar select{flex:1 1 46%;min-width:0;max-width:calc(50% - 4px)}
  .thread-bar .switch{flex:0 0 auto;order:3}
  .thread-bar .key-field{order:2;flex:0 0 100%;width:100%}
  .thread-bar input[type="password"]{width:100%;min-width:0;font-size:var(--fs-body)}
}
@media (max-width:560px){
  :root{--fs-h1:20px}
  .health{font-size:var(--fs-micro)}
  .brand-text h1{font-size:var(--fs-body)}
  .topbar{gap:var(--sp-2);padding:0 12px}
  .tiles{grid-template-columns:repeat(auto-fit,minmax(132px,1fr))}
  .tile{padding:10px 11px}
  .tile .v{font-size:var(--fs-h3)}
  .seg-btn{padding:0 5px}
  .msg{grid-template-columns:20px minmax(0,1fr);gap:10px}
  .msg-avatar{width:20px;height:20px}
  .msg-avatar svg{width:12px;height:12px}
  .msg.user .bub{max-width:100%}
  .composer .hint{display:none}
  .bars{height:112px}
  main.content{padding:18px 14px calc(84px + env(safe-area-inset-bottom,0px))}
}
@media (max-width:470px){
  .tb-context{display:none}
  .searchbtn .lbl,.searchbtn kbd{display:none}
  .searchbtn{padding:0 8px}
  .phead p{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
  .sheet-head,.sheet-body{padding-left:var(--sp-3);padding-right:var(--sp-3)}
}
@media (max-width:400px){
  .brand-text{display:none}
  .tab-btn{padding:6px 1px 5px}
}

/* Coarse pointers get roomier targets, never smaller type. */
@media (pointer:coarse){
  .tab-btn{padding-top:8px;padding-bottom:7px}
  .btn{min-height:40px}
  .btn--sm{min-height:36px}
  button.copy{min-height:32px;padding:5px 9px}
  .switch .sw{--sw-w:36px; --sw-h:20px; --sw-pad:2px; --sw-travel:16px}
}

/* ── 12. Hierarchy ───────────────────────────────────────────────────────── */
/* One thing leads on every screen: the figures and the chart on Status, the
   transcript on Chat, the request table on Activity, the list on Models. The
   rules here make that ordering explicit in the flow, not just in the markup. */
#tab-status .chart{margin-top:var(--sp-1)}
#tab-status .tablewrap{margin-top:0}
#tab-activity .tablewrap{flex:1;min-height:0}
#tab-activity .scroller.grow{min-height:220px}
#tab-api .panel pre{margin:0;font-family:var(--mono);font-size:var(--fs-meta);line-height:1.6;
  white-space:pre;overflow:auto;color:var(--fg-2)}
#tab-api .panel-body{padding:12px var(--sp-4);background:var(--code-bg)}
#tab-models .chat-footnote{margin-top:2px}

/* ── 13. Preferences ─────────────────────────────────────────────────────── */
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{
    animation-duration:.01ms !important;
    animation-iteration-count:1 !important;
    transition-duration:.01ms !important;
    scroll-behavior:auto !important;
  }
  .tile,.msg,section.tab.active,.toast,.sheet,.veil{animation:none !important}
  .sk{animation:none !important}
}
/* Users who ask for less transparency get the same surfaces, made opaque.
   Nothing in this interface depends on translucency any more. */
@media (prefers-reduced-transparency:reduce){
  .veil{background:rgba(17,21,22,.72)}
}
/* Higher contrast: firmer hairlines and stronger secondary ink. The dark
   values are stated twice for the same reason the theme blocks are: an
   explicit `data-theme="dark"` must beat the light defaults, and a system
   preference must beat them when no theme is set. */
@media (prefers-contrast:more){
  :root{
    --line-1:rgba(21,25,26,.28);
    --line-2:rgba(21,25,26,.40);
    --line-3:rgba(21,25,26,.55);
    --fg-2:#3a4240;
    --fg-3:#4a5250;
  }
  :root[data-theme="dark"]{
    --line-1:rgba(255,255,255,.26);
    --line-2:rgba(255,255,255,.38);
    --line-3:rgba(255,255,255,.52);
    --fg-2:#c6ccc9;
    --fg-3:#b3bab7;
  }
}
@media (prefers-contrast:more) and (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --line-1:rgba(255,255,255,.26);
    --line-2:rgba(255,255,255,.38);
    --line-3:rgba(255,255,255,.52);
    --fg-2:#c6ccc9;
    --fg-3:#b3bab7;
  }
}
@media print{
  .topbar,.railwrap,.toasts,.canvas{display:none !important}
  body{height:auto;overflow:visible}
  main.content{overflow:visible;padding:0;background:none}
}

/* ── Last word ───────────────────────────────────────────────────────────── */
/* Touch devices get no hover tooltips — they would fire on every tap. */
@media (hover:none){
  [data-tip]::after{display:none !important}
}
/* Empty rows keep their own layout instead of showing an empty label column. */
table.stack-rows td[data-label=""]::before{display:none}

</style>
</head>
<body>
<div class="canvas" aria-hidden="true"><span class="canvas-glow"></span></div>
<a class="skip" href="#main">Skip to content</a>

<div class="app">
  <!-- ── Topbar (layer 3): identity, live health and global actions ──────── -->
  <header class="topbar glass-3" id="topbar">
    <a class="brand" href="#main" id="brandBtn">
      <span class="mark" aria-hidden="true">
        <svg viewBox="0 0 32 32" role="img">
          <path style="fill:var(--accent)" d="M16 2C17 10.2 21.8 15 30 16C21.8 17 17 21.8 16 30C15 21.8 10.2 17 2 16C10.2 15 15 10.2 16 2Z"/>
        </svg>
      </span>
      <span class="brand-text">
        <h1>gemini-web2api</h1>
        <p>Gemini Web &rarr; OpenAI-compatible API gateway</p>
      </span>
    </a>

    <span class="tb-context">
      <span class="tb-title" id="tbSection">Chat</span>
    </span>

    <div class="topbar-end">
      <span class="health" id="health" data-tip="Live from /health, refreshed every 15 s" data-tip-pos="below">
        <span class="dot" id="statusDot" aria-hidden="true"></span>
        <span id="statusText" aria-live="polite">checking&hellip;</span>
      </span>
      <span class="pill--meta" id="verPill" data-tip="Build and uptime" data-tip-pos="below">
        v<span id="version">&mdash;</span><span class="dot-sep" aria-hidden="true"></span><span id="uptime">&mdash;</span>
      </span>

      <button class="btn btn--glass btn--sm searchbtn" id="paletteBtn" type="button" aria-keyshortcuts="Meta+K Control+K"
              data-tip="Search sections, chats and actions" data-tip-pos="below">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <circle cx="11" cy="11" r="6.2"/><path d="m15.6 15.6 3.9 3.9"/>
        </svg>
        <span class="lbl">Search</span>
        <kbd>&#8984;K</kbd>
      </button>

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
              data-tip="Keyboard shortcuts (?)" data-tip-pos="left">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="8.6"/><path d="M9.7 9.5a2.4 2.4 0 1 1 3.2 2.2c-.7.3-1 .9-1 1.6v.5M12 16.8h.01"/>
        </svg>
      </button>
    </div>
  </header>

  <div class="body">
    <!-- ── Navigation rail (layer 3): floats beside the workspace ─────────── -->
    <aside class="railwrap">
      <div class="rail glass-3" id="dock">
        <nav class="tabs" id="tabs" role="tablist" aria-label="Console sections">
          <span class="glider" aria-hidden="true"></span>
          <button class="tab-btn" type="button" role="tab" id="tabbtn-chat" data-tab="chat" aria-keyshortcuts="Meta+1 Control+1"
                  aria-selected="true" aria-controls="tab-chat" tabindex="0">
            <svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M20.5 12.1c0 3.7-3.8 6.7-8.5 6.7-1 0-2-.14-2.9-.4L4.6 19.8l1.1-3.3c-1.3-1.2-2.2-2.7-2.2-4.4 0-3.7 3.8-6.7 8.5-6.7s8.5 3 8.5 6.7Z"/>
            </svg>
            <span class="lbl">Chat</span>
            <kbd class="navkbd" aria-hidden="true">1</kbd>
          </button>
          <button class="tab-btn" type="button" role="tab" id="tabbtn-status" data-tab="status" aria-keyshortcuts="Meta+2 Control+2"
                  aria-selected="false" aria-controls="tab-status" tabindex="-1"
                 >
            <svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M3 13h3.7l2-5.4 3.3 9.8 2.2-6.5 1.6 3.4H21"/>
            </svg>
            <span class="lbl">Status</span>
            <kbd class="navkbd" aria-hidden="true">2</kbd>
          </button>
          <button class="tab-btn" type="button" role="tab" id="tabbtn-activity" data-tab="activity" aria-keyshortcuts="Meta+3 Control+3"
                  aria-selected="false" aria-controls="tab-activity" tabindex="-1"
                 >
            <svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M4 6.6h16M4 12h11.5M4 17.4h7.5"/>
            </svg>
            <span class="lbl">Activity</span>
            <kbd class="navkbd" aria-hidden="true">3</kbd>
          </button>
          <button class="tab-btn" type="button" role="tab" id="tabbtn-models" data-tab="models" aria-keyshortcuts="Meta+4 Control+4"
                  aria-selected="false" aria-controls="tab-models" tabindex="-1"
                 >
            <svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <rect x="3.6" y="3.6" width="7.2" height="7.2" rx="2"/><rect x="13.2" y="3.6" width="7.2" height="7.2" rx="2"/>
              <rect x="3.6" y="13.2" width="7.2" height="7.2" rx="2"/><rect x="13.2" y="13.2" width="7.2" height="7.2" rx="2"/>
            </svg>
            <span class="lbl">Models</span>
            <kbd class="navkbd" aria-hidden="true">4</kbd>
          </button>
          <button class="tab-btn" type="button" role="tab" id="tabbtn-api" data-tab="api" aria-keyshortcuts="Meta+5 Control+5"
                  aria-selected="false" aria-controls="tab-api" tabindex="-1"
                 >
            <svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M9.2 8.4 4.9 12l4.3 3.6M14.8 8.4 19.1 12l-4.3 3.6"/>
            </svg>
            <span class="lbl">API</span>
            <kbd class="navkbd" aria-hidden="true">5</kbd>
          </button>
        </nav>
        <div class="rail-foot">
          <span class="rail-live">
            <span class="pulse" id="railDot" aria-hidden="true"></span>
            <span class="rl-t" id="railStatus">checking&hellip;</span>
          </span>
        </div>
      </div>
    </aside>

    <!-- ── Workspace (layer 1): the pane the panels sit on ─────────────────── -->
    <main class="content" id="main">
      <!-- ── Chat ──────────────────────────────────────────────────────────── -->
      <section class="tab active" id="tab-chat" role="tabpanel" aria-labelledby="tabbtn-chat" tabindex="-1">
        <div class="phead">
          <div class="phead-text">
            <h2>Chat</h2>
            <p>Test the gateway from the page that serves it. Each turn resends the transcript,
               because the upstream is single-turn.</p>
          </div>
        </div>

        <div class="chat">
          <aside class="crail glass-2" aria-label="Conversations">
            <div class="crail-head">
              <span class="lbl">Conversations</span>
              <button class="btn btn--ghost btn--icon btn--sm" id="newChat" type="button" aria-keyshortcuts="Meta+N Control+N"
                      aria-label="New chat" data-tip="New chat" data-tip-pos="below">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M12 5.5v13M5.5 12h13"/>
                </svg>
              </button>
            </div>
            <div class="crail-list" id="convList"></div>
          </aside>

          <div class="thread glass-2">
            <div class="thread-bar">
              <label class="sr-only" for="model">Model</label>
              <select id="model" data-tip="Model ID" data-tip-pos="below"></select>
              <label class="sr-only" for="think">Thinking depth</label>
              <select id="think" data-tip="Thinking depth" data-tip-pos="below">
                <option value="">Model default</option>
                <option value="0">Think 0 — deepest</option>
                <option value="1">Think 1</option>
                <option value="2">Think 2 — medium</option>
                <option value="3">Think 3</option>
                <option value="4">Think 4 — shallowest</option>
              </select>
              <label class="switch is-sm" data-tip="Stream tokens as they are generated" data-tip-pos="below">
                <input type="checkbox" id="stream" checked>
                <span class="sw" aria-hidden="true"></span><span class="sw-txt">Stream</span>
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
                <div class="well-foot">
                  <button class="btn btn--quiet-danger btn--sm" id="clearConv" type="button"
                          data-tip="Clear this chat" data-tip-pos="right">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                      <path d="M5 7.6h14M9.6 7.6V5.9c0-.7.6-1.3 1.3-1.3h2.2c.7 0 1.3.6 1.3 1.3v1.7M6.9 7.6l.8 10.2c.06.8.73 1.4 1.53 1.4h5.54c.8 0 1.47-.6 1.53-1.4l.8-10.2"/>
                    </svg>
                    <span class="lbl">Clear</span>
                  </button>
                  <span class="hint"><kbd>Enter</kbd> send &middot; <kbd>Shift</kbd>+<kbd>Enter</kbd> new line</span>
                  <div class="spacer"></div>
                  <span class="slot">
                    <button class="btn btn--primary" id="send" type="button" data-tip="Send — Enter" data-tip-pos="left">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                        <path d="M12 19V5.6M12 5.6 6.2 11.4M12 5.6l5.8 5.8"/>
                      </svg>
                      <span class="lbl">Send</span>
                    </button>
                    <button class="btn btn--glass" id="stop" type="button" hidden>
                      <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                        <rect x="7.5" y="7.5" width="9" height="9" rx="2.2"/>
                      </svg>
                      <span class="lbl">Stop</span>
                    </button>
                  </span>
                </div>
              </div>
              <div class="composer-under">
                <span class="note mono" id="chatMeta" aria-live="polite"></span>
              </div>
            </div>
          </div>
        </div>

        <p class="chat-footnote">Transcripts stay in this browser. Every turn resends the whole thread,
          so clear it when it gets long.</p>
      </section>

      <!-- ── Status ────────────────────────────────────────────────────────── -->
      <section class="tab" id="tab-status" role="tabpanel" aria-labelledby="tabbtn-status" tabindex="-1">
        <div class="phead">
          <div class="phead-text">
            <h2>Status</h2>
            <p>What the server is doing now: startup checks, live upstream metrics, the cookie
               pool and the configuration it loaded.</p>
          </div>
          <div class="phead-actions">
            <span class="note" id="statusMeta" aria-live="polite"></span>
            <label class="switch is-sm"><input type="checkbox" id="autoStatus" checked>
              <span class="sw" aria-hidden="true"></span><span class="sw-txt">Auto 10s</span></label>
            <button class="btn btn--glass btn--sm" id="refreshStatus" type="button">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M4.6 12a7.4 7.4 0 0 1 12.6-5.2L20 9.4M20 4.6v4.8h-4.8M19.4 12a7.4 7.4 0 0 1-12.6 5.2L4 14.6M4 19.4v-4.8h4.8"/>
              </svg>
              <span class="lbl">Refresh</span>
            </button>
          </div>
        </div>

        <div class="hairline" id="statusProgress" aria-hidden="true"></div>
        <div class="strip" id="trafficStrip" hidden></div>
        <div class="callout-list" id="statusWarn"></div>
        <div class="callout-list" id="checks"></div>

        <h3 class="slabel">Right now</h3>
        <div class="tiles" id="latency"></div>
        <div class="chart glass-2" id="latencyChart"></div>

        <h3 class="slabel">Counters</h3>
        <div class="tiles" id="counters"></div>

        <h3 class="slabel">Accounts</h3>
        <div id="accountsNote"></div>
        <div class="tablewrap glass-2 panel--flush">
          <div class="scroller">
            <table class="stack-rows">
              <caption class="sr-only">Configured cookie files and their current state</caption>
              <thead><tr><th scope="col">Account</th><th scope="col">Google index</th><th scope="col">SAPISID</th><th scope="col" class="num">Uses</th><th scope="col">State</th></tr></thead>
              <tbody id="accounts"></tbody>
            </table>
          </div>
        </div>

        <div class="duo">
          <div class="duo-col">
            <h3 class="slabel">Upstream by model</h3>
            <div class="tablewrap glass-2 panel--flush">
              <div class="scroller">
                <table class="stack-rows">
                  <caption class="sr-only">Upstream calls per model</caption>
                  <thead><tr><th scope="col">Model</th><th scope="col" class="num">Requests</th><th scope="col" class="num">Average</th><th scope="col" class="num">Slowest</th></tr></thead>
                  <tbody id="byModel"></tbody>
                </table>
              </div>
            </div>
          </div>
          <div class="duo-col">
            <h3 class="slabel">Status codes</h3>
            <div class="tablewrap glass-2 panel--flush">
              <div class="scroller">
                <table class="stack-rows">
                  <caption class="sr-only">Responses by status code</caption>
                  <thead><tr><th scope="col">Code</th><th scope="col" class="num">Count</th></tr></thead>
                  <tbody id="byStatus"></tbody>
                </table>
              </div>
            </div>
          </div>
        </div>

        <h3 class="slabel">Runtime</h3>
        <dl class="facts glass-1" id="runtime"></dl>

        <h3 class="slabel">Configuration</h3>
        <div class="tablewrap glass-2 panel--flush">
          <div class="scroller">
            <table class="stack-rows">
              <caption class="sr-only">Effective configuration with secrets redacted</caption>
              <thead><tr><th scope="col">Key</th><th scope="col">Value</th></tr></thead>
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
            <p>Recent requests, newest first. Operational facts only: no prompts, response bodies
               or credentials.</p>
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
              <span class="lbl">Refresh</span>
            </button>
          </div>
        </div>

        <div class="hairline" id="actProgress" aria-hidden="true"></div>
        <div class="callout-list" id="actNote"></div>

        <div class="tablewrap glass-2 panel--flush">
          <div class="scroller grow">
            <table class="stack-rows">
              <caption class="sr-only">Recent requests</caption>
              <thead><tr><th scope="col">Time</th><th scope="col">Method</th><th scope="col">Path</th><th scope="col">Status</th>
                <th scope="col">Model</th><th scope="col" class="num">Latency</th><th scope="col">Client</th><th scope="col">Request ID</th></tr></thead>
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
            <p>Every ID accepted by <code>/v1/chat/completions</code>. Append <code>@think=N</code> to
               override thinking depth, 0 deepest to 4 shallowest.</p>
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
          <h3>No model matches that filter</h3>
          <p>Try a shorter query, or clear the filter to see every model ID.</p>
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
            <p>OpenAI-compatible endpoints plus the Google-native surface Gemini CLI expects.
               The full reference is <code>docs/API.md</code>.</p>
          </div>
        </div>

        <h3 class="slabel">Endpoints</h3>
        <div class="tablewrap glass-2 panel--flush">
          <div class="scroller">
            <table class="stack-rows">
              <caption class="sr-only">HTTP endpoints</caption>
              <thead><tr><th scope="col">Method</th><th scope="col">Path</th><th scope="col">Purpose</th></tr></thead>
              <tbody id="endpoints"></tbody>
            </table>
          </div>
        </div>

        <h3 class="slabel">Client configuration</h3>
        <div class="tablewrap glass-2 panel--flush">
          <div class="scroller">
            <table class="stack-rows">
              <caption class="sr-only">Values to configure in a client</caption>
              <thead><tr><th scope="col">Field</th><th scope="col">Value</th><th scope="col"><span class="sr-only">Copy</span></th></tr></thead>
              <tbody id="clientCfg"></tbody>
            </table>
          </div>
        </div>

        <h3 class="slabel">curl</h3>
        <div class="panel glass-2 panel--flush">
          <div class="panel-head">
            <span class="sub">Replace <code>YOUR_KEY</code> when authentication is enabled.</span>
            <span class="spacer"></span>
            <button class="copy btn btn--ghost btn--sm" id="copyCurl" type="button" data-copy-target="curlBox">
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
  <div class="sheet sheet--sm glass-5" role="dialog" aria-modal="true" aria-labelledby="helpLabel">
    <div class="sheet-head">
      <h3><span id="helpLabel">Keyboard shortcuts</span><span class="sub">A keyboard is optional — every one of these has a button.</span></h3>
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
/* The console can be shown inside another page (a LAN dashboard panel, a
   preview pane) where some browsers deny storage outright. The API key is the
   only value read and written outside the get/set pair, so it gets its own
   guarded accessors rather than a boot-time crash. */
const readKey = () => { try { return localStorage.getItem(LS.key) || ''; } catch (_) { return ''; } };
const writeKey = (value) => {
  try { value ? localStorage.setItem(LS.key, value) : localStorage.removeItem(LS.key); }
  catch (_) {}
};
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
/* Metric keys arrive snake_cased from the server; the interface reads sentence
   case, so one helper owns the translation. */
const human = (k) => {
  const t = String(k ?? '').replace(/[_-]+/g, ' ').trim().replace(/ total$/i, '');
  return t ? t.charAt(0).toUpperCase() + t.slice(1) : '';
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
      '<td data-label="Uses" class="num">' + esc(Number(a.uses) || 0) + '</td>' +
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

function toast(message, kind, opts){
  const region = $('toasts');
  if (!region) return;
  const level = TOAST_ICON[kind] ? kind : 'info';
  const o = opts || {};
  const ms = o.ms || TOAST_MS[level];
  const el = document.createElement('div');
  el.className = 'toast glass-6 toast--' + level;
  el.innerHTML =
    '<span class="ico">' + TOAST_ICON[level] + '</span>' +
    '<span class="txt">' + (o.title ? '<strong>' + esc(o.title) + '</strong>' : '') +
    '<span class="' + (o.title ? 'sub' : '') + '">' + esc(message) + '</span></span>' +
    '<button class="x" type="button" aria-label="Dismiss">' + ICON.close + '</button>' +
    /* The timer is drawn, not guessed: it shows exactly how long the toast has
       left, and it stops while the pointer is resting on it. */
    '<span class="toast-timer" style="--toast-ms:' + ms + 'ms" aria-hidden="true"></span>';
  // Cap the stack: the oldest leaves first so the newest is always readable.
  while (region.children.length >= 4) region.removeChild(region.firstChild);
  let remaining = ms, startedAt = 0, timer = 0;
  const go = () => {
    if (el._gone) return;
    el._gone = true;
    clearTimeout(timer);
    el.classList.add('is-closing');
    setTimeout(() => { if (el.parentNode) el.parentNode.removeChild(el); }, 200);
  };
  const arm = () => {
    startedAt = performance.now();
    timer = setTimeout(go, remaining);
  };
  el.querySelector('.x').addEventListener('click', go);
  /* Hover pauses the countdown *and* the bar, so the two never disagree. */
  el.addEventListener('mouseenter', () => {
    if (el._gone) return;
    clearTimeout(timer);
    remaining = Math.max(400, remaining - (performance.now() - startedAt));
    el.classList.add('is-paused');
  });
  el.addEventListener('mouseleave', () => {
    if (el._gone) return;
    el.classList.remove('is-paused');
    arm();
  });
  region.appendChild(el);
  el._dismiss = go;
  arm();
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

/* Navigation: one roving tabindex, a glider that travels to the active item,
   and a pane that enters from the direction of travel. The glider is measured
   with rects rather than offsets, so the same code drives the vertical rail on
   desktop and the horizontal bar on phones without a media query of its own. */
const tabButtons = $$('nav.tabs .tab-btn');
const glider = document.querySelector('nav.tabs .glider');
const navEl = document.querySelector('nav.tabs');
const TAB_TITLES = {chat: 'Chat', status: 'Status', activity: 'Activity', models: 'Models', api: 'API'};
const TAB_ORDER = Object.keys(TAB_TITLES);
let activeTab = 'chat';
let activeIndex = 0;

function moveGlider(instant){
  const btn = tabButtons.find((b) => b.dataset.tab === activeTab);
  if (!glider || !btn || !navEl) return;
  const nr = navEl.getBoundingClientRect(), br = btn.getBoundingClientRect();
  if (instant) glider.style.transition = 'none';
  glider.style.width = br.width + 'px';
  glider.style.height = br.height + 'px';
  glider.style.transform = 'translate(' + (br.left - nr.left) + 'px,' + (br.top - nr.top) + 'px)';
  if (instant) { void glider.offsetWidth; glider.style.transition = ''; }
  glider.classList.add('ready');
}
/* The rail runs down the side on desktop and across the bottom on phones, and
   the tablist's orientation has to follow: it is not a visual detail. */
function syncNavOrientation(){
  const stacked = !!(window.matchMedia && window.matchMedia('(max-width:980px)').matches);
  if (navEl) navEl.setAttribute('aria-orientation', stacked ? 'horizontal' : 'vertical');
  moveGlider(true);
}
function selectTab(name, opts){
  const o = opts || {};
  if (!TAB_ORDER.includes(name)) return;
  const next = TAB_ORDER.indexOf(name), prev = activeIndex;
  activeTab = name; activeIndex = next;
  tabButtons.forEach((b) => {
    const on = b.dataset.tab === name;
    b.setAttribute('aria-selected', String(on));
    b.tabIndex = on ? 0 : -1;
  });
  $$('section.tab').forEach((s) => {
    const on = s.id === 'tab-' + name;
    s.classList.toggle('active', on);
    /* Set on every switch: display:none → flex restarts the animation, so the
       pane always enters from the side the reader is travelling towards. */
    if (on) s.dataset.enter = next > prev ? 'fwd' : (next < prev ? 'back' : 'none');
  });
  document.title = (TAB_TITLES[name] || 'Console') + ' · gemini-web2api';
  const ctx = $('tbSection');
  if (ctx) ctx.textContent = TAB_TITLES[name] || 'Console';
  moveGlider(false);
  if (name === 'status') refreshStatus(o.force);
  if (name === 'activity') refreshActivity(o.force);
  if (name === 'chat' && !coarse()) $('prompt').focus();
  resetPaneScroll(o.keepScroll);
}
/* The hint follows the platform: a Mac user reads ⌘, everyone else Ctrl. */
$$('#paletteBtn kbd').forEach((k) => { k.textContent = modKey + ' K'; });
$$('nav.tabs .navkbd').forEach((k, i) => {
  k.textContent = (modKey === 'Ctrl' ? 'Ctrl ' : modKey) + (i + 1);
});
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
window.addEventListener('resize', syncNavOrientation);
window.addEventListener('orientationchange', syncNavOrientation);
if (window.matchMedia) {
  const mq = window.matchMedia('(max-width:980px)');
  if (mq.addEventListener) mq.addEventListener('change', syncNavOrientation);
}
// The glider must follow the labels, not just the layout: fonts land late and
// a webfont-free page still reflows once the CSS is fully applied.
if (window.ResizeObserver) new ResizeObserver(() => moveGlider(true)).observe(navEl);
if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => moveGlider(true));

/* Switching sections returns the reader to the top of the new one. */
function resetPaneScroll(keep){
  if (keep) return;
  const pane = $('main');
  if (!pane || pane.scrollTop <= 140) return;
  if (motionOK()) pane.scrollTo({top: 0, behavior: 'smooth'});
  else pane.scrollTop = 0;
}

/* Scroll is answered by the chrome, not by the content. Past a few pixels the
   topbar compacts and keeps the section name, the rail and the pane firm up,
   and the ambient halo drifts a little: the interface reads as layered planes
   moving at different speeds. Only transform and background-color are touched,
   and only inside one rAF per burst of scroll events. */
const dock = $('dock');
const topbar = $('topbar');
const scroller = $('main');
const halo = document.querySelector('.canvas-glow');
let scrollRaf = 0;
function applyScrollState(){
  scrollRaf = 0;
  const y = Math.max(window.scrollY || 0, scroller ? scroller.scrollTop : 0);
  const stuck = y > 6;
  if (topbar) topbar.classList.toggle('is-scrolled', stuck);
  if (dock) dock.classList.toggle('is-scrolled', stuck);
  if (scroller) scroller.classList.toggle('is-scrolled', stuck);
  if (halo && motionOK()) halo.style.transform = 'translate3d(0,' + Math.round(-Math.min(y, 700) * 0.045) + 'px,0)';
}
const onScroll = () => { if (!scrollRaf) scrollRaf = requestAnimationFrame(applyScrollState); };
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
    group: 'Actions', label: 'Switch appearance', hint: 'Light, dark or follow the system',
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
   Pointer light — surfaces answer the cursor
   ───────────────────────────────────────────────────────────────────────── */
/* One delegated, rAF-throttled listener for the whole page: a soft highlight
   follows the pointer across buttons, tabs, tiles and chips. It is skipped
   entirely on touch and under reduced motion, where it would be noise, and it
   only ever writes two custom properties, so nothing re-lays out. */
(function (){
  if (coarse() || !window.matchMedia || !window.matchMedia('(hover:hover)').matches) return;
  const SEL = '.btn,.tab-btn,.tile,.chip,.pal-item,.seg-btn,button.copy';
  let x = 0, y = 0, raf = 0, node = null;
  const paint = () => {
    raf = 0;
    if (!node) return;
    const r = node.getBoundingClientRect();
    node.style.setProperty('--mx', Math.round(x - r.left) + 'px');
    node.style.setProperty('--my', Math.round(y - r.top) + 'px');
  };
  document.addEventListener('pointermove', (ev) => {
    if (ev.pointerType && ev.pointerType !== 'mouse') return;
    const hit = ev.target && ev.target.closest ? ev.target.closest(SEL) : null;
    if (hit !== node) node = hit;
    if (!node) return;
    x = ev.clientX; y = ev.clientY;
    if (!raf) raf = requestAnimationFrame(paint);
  }, {passive: true});
})();

/* ─────────────────────────────────────────────────────────────────────────
   Layer 1–2 — the console itself
   ───────────────────────────────────────────────────────────────────────── */
/* One switch for "work is happening": the hairline sweeps, the button that
   asked for it spins its glyph, and the pane tells assistive tech it is busy.
   The spinner turns in place, so a long refresh never reflows the toolbar. */
function setProgress(id, on, opts){
  const el = $(id);
  if (el) el.classList.toggle('on', !!on);
  const o = opts || {};
  if (o.btn) {
    const btn = $(o.btn);
    if (btn) {
      btn.classList.toggle('is-busy', !!on);
      btn.setAttribute('aria-busy', String(!!on));
    }
  }
  if (o.busy) {
    const pane = $(o.busy);
    if (pane) pane.setAttribute('aria-busy', String(!!on));
  }
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
  $('runtime').innerHTML = cards.map(([k, v, mono]) =>
    '<div class="fact"><dt>' + esc(k) + '</dt><dd' + (mono ? ' class="mono"' : '') + '>' +
    esc(v == null ? '\u2014' : v) + '</dd></div>').join('');

  const models = STATE.models || [];
  $('modelCount').textContent = models.length + (models.length === 1 ? ' model' : ' models');
  $('models').innerHTML = models.map((m, i) =>
    '<article class="model-card" data-i="' + i + '" data-search="' +
      esc([m.id, m.category, m.desc, m.output, m.think].join(' ').toLowerCase()) + '">' +
      '<div class="model-top"><code class="model-id">' + esc(m.id) + '</code>' +
      '<button class="copy" type="button" data-copy="' + esc(m.id) + '" data-copy-label="Model ID"' +
      ' aria-label="Copy model ID ' + esc(m.id) + '"><span class="lbl">Copy</span></button></div>' +
      '<p class="model-desc">' + esc(m.desc) + '</p>' +
      '<p class="hintline">output ' + esc(m.output || 'varies') + '</p>' +
      '<div class="model-tags">' + pill(m.category) +
        (m.needs_cookie ? ' ' + pill('cookie', 'cookie') : '') +
        (m.think == null ? '' : ' ' + pill('think ' + m.think, 'acc')) + '</div>' +
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
    '" data-copy-label="' + esc(k) + '" aria-label="Copy ' + esc(k) + '"><span class="lbl">Copy</span></button></td></tr>').join('');

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
  const savedKey = readKey();
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
  setProgress('statusProgress', true, {btn: 'refreshStatus', busy: 'tab-status'});
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
        ? callout('ok', 'All startup checks passed.')
        : '');

    $('counters').innerHTML = Object.keys(c).map((k, i) =>
      tile(human(k), c[k], {num: true, i: i})).join('');

    const hist = m.latency_histogram_ms || {};
    const cap = s.concurrency || {};
    const capText = cap.max
      ? (cap.in_flight ?? 0) + ' / ' + cap.max
      : String(cap.in_flight ?? 0);
    $('latency').innerHTML = [
      ['Average', fmtMs(m.latency_ms_avg)],
      ['Samples', m.latency_ms_samples ?? 0],
      ['In flight', capText],
      ['Uptime', fmtUptime(m.uptime_sec)],
    ].map(([k, v], i) => tile(k, v, {i: i, mono: true})).join('');
    renderChart(hist, m.latency_ms_samples ?? 0);

    const models = m.models || {};
    const names = Object.keys(models);
    $('byModel').innerHTML = names.length ? names.map((n) =>
      '<tr><td data-label="Model"><code>' + esc(n) + '</code></td>' +
      '<td data-label="Requests" class="num">' + esc(models[n].requests) + '</td>' +
      '<td data-label="Average" class="num">' + esc(fmtMs(models[n].avg_ms)) + '</td>' +
      '<td data-label="Slowest" class="num">' + esc(fmtMs(models[n].max_ms)) + '</td></tr>').join('')
      : '<tr><td colspan="4" class="empty-cell" data-label=""><span class="note">No upstream calls yet — ' +
        'this table fills in as traffic arrives.</span></td></tr>';

    const codes = m.status_codes || {};
    const keys = Object.keys(codes);
    $('byStatus').innerHTML = keys.length ? keys.map((k) =>
      '<tr><td data-label="Code">' + statusPill(Number(k)) + '</td>' +
      '<td data-label="Count" class="num">' + esc(codes[k]) + '</td></tr>').join('')
      : '<tr><td colspan="2" class="empty-cell" data-label=""><span class="note">No responses recorded yet.</span></td></tr>';

    $('accountsNote').innerHTML = accountsNote(s.credentials);
    $('accounts').innerHTML = accountRows(s.credentials);
    renderStrip(s.history);

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
    setProgress('statusProgress', false, {btn: 'refreshStatus', busy: 'tab-status'});
  }
}
function fmtCfg(v){
  if (v === null || v === undefined) return '\u2014';
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
      '<h3>No latency samples yet</h3>' +
      '<p>Only upstream calls are timed, so the chart stays empty until a model answers.</p>' +
      '</div>';
    return;
  }
  const values = buckets.map((k) => Number(hist[k]) || 0);
  const sum = values.reduce((a, b) => a + b, 0);
  const total = sum || 1;
  const max = Math.max.apply(null, values);
  /* The median bucket, from the histogram itself: a comparison the reader can
     hold on to, where "busiest bucket" only repeated the peak label. */
  let seen = 0, median = buckets[0];
  for (let i = 0; i < values.length; i += 1) {
    seen += values[i];
    if (seen * 2 >= sum) { median = buckets[i]; break; }
  }
  const label = (k) => k === 'inf' ? '\u221e' : (Number(k) >= 1000 ? (Number(k) / 1000) + 's' : k + 'ms');
  // Buckets are upper bounds, so the last one is "above the one before it".
  const speak = (k, i) => {
    const size = (v) => {
      const n = Number(v);
      return n >= 1000 ? (n / 1000) + (n === 1000 ? ' second' : ' seconds')
                       : n + ' milliseconds';
    };
    if (k !== 'inf') return 'Up to ' + size(k);
    const prev = Number(buckets[i - 1]);
    return prev ? 'Above ' + size(prev) : 'Without an upper bound';
  };
  const summary = buckets.map((k, i) =>
    speak(k, i) + ': ' + values[i] + (values[i] === 1 ? ' request' : ' requests')).join('; ');
  box.setAttribute('role', 'img');
  box.setAttribute('aria-label',
    'Upstream latency histogram over ' + (samples || total) + ' samples. ' + summary + '.');
  const grid = [max, max * 0.5, 0].map((v) =>
    '<span><i>' + esc(fmtCount(Math.round(v))) + '</i></span>').join('');
  // The busiest bucket is labelled in place: the chart answers its own
  // question, and the tooltip still carries every exact count.
  const peak = values.indexOf(max);
  box.innerHTML =
    '<div class="chart-head"><h3>Upstream latency</h3>' +
    '<span class="sub">' + esc((samples || sum)) + ' samples &middot; median &le; ' + esc(label(median)) + '</span>' +
    '<span class="spacer"></span>' +
    '<span class="chart-legend"><span class="swatch"></span>Requests per bucket</span></div>' +
    '<div class="plot"><div class="gridlines">' + grid + '</div><div class="bars">' +
    buckets.map((k, i) => {
      const h = Math.max(2, Math.round(values[i] / max * 100));
      const chip = (i === peak && values.length > 1)
        ? '<span class="peak" aria-hidden="true">' + esc(values[i]) + '</span>' : '';
      return '<div class="bar" style="--i:' + i + ';--h:' + h + '%" data-tip="' + esc(label(k)) +
        ' &mdash; ' + esc(values[i]) + ' request' + (values[i] === 1 ? '' : 's') +
        ' (' + esc(Math.round(values[i] / total * 100)) + '%)">' + chip +
        '<span class="fill"></span>' +
        '<span class="xlab">' + esc(label(k)) + '</span></div>';
    }).join('') +
    '</div></div>' +
    '<div class="chart-x">Bucket upper bound &middot; hover a bar for exact counts</div>';
}

/* ── traffic strip ───────────────────────────────────────────────────────── */
/* Height carries latency, colour carries outcome. Bars are drawn from the
   request history the panel already fetched, so nothing new is queried; with
   history disabled the element stays hidden rather than showing an empty
   frame. */
function renderStrip(history){
  const box = $('trafficStrip');
  if (!box) return;
  const rows = Array.isArray(history) ? history.slice(0, 60).slice().reverse() : [];
  if (!rows.length) { box.hidden = true; box.innerHTML = ''; return; }
  const slowest = Math.max.apply(null, rows.map((r) => Number(r.ms) || 0)) || 1;
  const bad = rows.filter((r) => Number(r.status) >= 400).length;
  box.hidden = false;
  box.setAttribute('role', 'img');
  // Spoken, not printed: a listener gets the count, the peak and the outcome in
  // sentences, never axis shorthand like "250ms" or "∞".
  box.setAttribute('aria-label', 'Last ' + rows.length + ' requests, oldest first. Highest latency ' +
    Math.round(slowest) + ' milliseconds. ' +
    (bad ? bad + (bad === 1 ? ' request ended' : ' requests ended') + ' in an error status.'
         : 'None ended in an error status.'));
  box.innerHTML =
    '<span class="strip-lbl">last ' + rows.length + ' request' + (rows.length === 1 ? '' : 's') + '</span>' +
    '<span class="strip-ticks">' + rows.map((r) => {
      const ms = Number(r.ms) || 0;
      const h = Math.max(3, Math.round(ms / slowest * 20));
      const level = Number(r.status) >= 400 ? (Number(r.status) >= 500 ? 'bad' : 'warn') : '';
      return '<i class="tk ' + level + '" style="height:' + h + 'px"></i>';
    }).join('') + '</span>' +
    '<span class="strip-lbl">' + (bad ? bad + ' error' + (bad === 1 ? '' : 's') : 'no errors') + '</span>';
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
  setProgress('actProgress', true, {btn: 'refreshAct', busy: 'tab-activity'});
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
    setProgress('actProgress', false, {btn: 'refreshAct', busy: 'tab-activity'});
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
      '<td class="code num" data-label="Latency">' + esc(fmtMs(r.ms)) + '</td>' +
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
      '<h3>Start a conversation</h3>' +
      '<p>Pick a model and ask anything. Replies stream in as they are generated.</p>' +
      '<div class="chips">' +
      ['Explain what this server does', 'Write a haiku about the sea',
       'Compare Python and Go for CLI tools', 'Give me a JSON schema for a user record']
        .map((s) => '<button class="chip" type="button" data-sug="' + esc(s) + '">' + esc(s) + '</button>').join('') +
      '</div></div>';
    return;
  }
  /* A restored transcript is easier to read with a marker for when it began:
     it is the one thing the messages themselves do not say. */
  box.innerHTML = '<p class="thread-anchor">Started ' + esc(fmtAgo(c.created || c.messages[0].t)) +
    '</p>' + c.messages.map((m, i) => msgHtml(m, i)).join('');
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
  if (key) writeKey(key);
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
  writeKey(k);
  if (k) toast('Key kept in this browser and sent as a bearer token', 'ok', {title: 'API key saved'});
});

/* ── liveness + timers ───────────────────────────────────────────────────── */
function setHealth(level, text){
  const dot = $('statusDot'), pill = $('health'), label = $('statusText');
  if (dot) dot.className = 'dot ' + (level || '');
  if (pill) pill.className = 'health' + (level ? ' is-' + level : '');
  // Only touch text that actually changed: identical writes would re-announce.
  if (label && label.textContent !== text) label.textContent = text;
  // The rail carries the same fact without a second live region, so a screen
  // reader hears the state once, from the topbar.
  const railDot = $('railDot'), railText = $('railStatus');
  if (railDot) railDot.className = 'pulse ' + (level || '');
  if (railText && railText.textContent !== text) railText.textContent = text;
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
skeletonTiles(4, 'latency');
skeletonRows('accounts', 5, 2);
skeletonRows('actRows', 8, 5);
$('checks').innerHTML = callout('info', 'Reading startup checks&hellip;');

renderStatic();
/* The same empty state the chart draws for itself, so the panel does not
   change its wording the moment the first read lands. */
$('latencyChart').innerHTML = '<div class="empty"><span class="empty-icon">' + ICON.pulse + '</span>' +
  '<h3>No latency samples yet</h3>' +
  '<p>Only upstream calls are timed, so the chart stays empty until a model answers.</p></div>';
renderConvs();
renderMsgs();
growPrompt();
ping();
/* The nav settles before the first paint: orientation decides which axis the
   glider travels on, so it is measured once here and on every resize after. */
syncNavOrientation();
requestAnimationFrame(() => moveGlider(true));
window.addEventListener('load', () => { syncNavOrientation(); applyScrollState(); });
setInterval(ping, 15000);
setInterval(() => { if ($('autoStatus').checked && $('tab-status').classList.contains('active')) refreshStatus(false); }, 10000);
setInterval(() => { if ($('autoAct').checked && $('tab-activity').classList.contains('active')) refreshActivity(false); }, 5000);

</script>
</body>
</html>
"""


# The page is a 180 KB constant with one small hole in it. Splitting and
# encoding the two constant halves once, at import, means a render only
# encodes the (tiny) state payload and joins three byte strings, instead of
# scanning and re-encoding the whole document to insert a few hundred bytes.
_DASHBOARD_HEAD, _DASHBOARD_TAIL = (
    part.encode("utf-8") for part in _DASHBOARD_HTML.split(_STATE_TOKEN, 1))


def render_dashboard(state):
    """Return the dashboard HTML with the live state injected."""
    payload = json.dumps(state, ensure_ascii=False, default=str)
    # Prevent an early </script> in any interpolated value from breaking out.
    payload = payload.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return b"".join((_DASHBOARD_HEAD, payload.encode("utf-8"), _DASHBOARD_TAIL))
