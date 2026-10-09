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
in the ``:root`` design tokens. Six glass strengths are built from one recipe —
a translucent fill, a backdrop blur with a little extra saturation, a hairline,
a masked 1 px edge light and a shadow from the ramp — and elevation is what
chooses the strength: the workspace pane is the quietest layer, panels and
cards sit on it, the floating topbar and navigation rail sit above those,
popovers above that, then dialogs, then toasts. A glass topbar holds identity
and live health, a navigation rail floats beside the workspace with a glider
that travels to the active item, and under 980 px that same rail becomes a
thumb-reachable bottom bar. Light and dark are first-class schemes,
``prefers-reduced-motion``, ``prefers-reduced-transparency`` and
``prefers-contrast`` are all honoured, and the page still falls back to opaque
materials where ``backdrop-filter`` is unavailable.

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
<meta name="theme-color" content="#eef0f5" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0d0f15" media="(prefers-color-scheme: dark)">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0' stop-color='%234285f4'/%3E%3Cstop offset='.5' stop-color='%239b72cb'/%3E%3Cstop offset='1' stop-color='%23d96570'/%3E%3C/linearGradient%3E%3C/defs%3E%3Cpath fill='url(%23g)' d='M16 2C17 10.2 21.8 15 30 16C21.8 17 17 21.8 16 30C15 21.8 10.2 17 2 16C10.2 15 15 10.2 16 2Z'/%3E%3C/svg%3E">
<style>
/* ═══════════════════════════════════════════════════════════════════════════
   gemini-web2api console — Liquid Glass design system (v2)

   1  Tokens      colour, material, light, depth, space, type, motion, layers
   2  Base        reset, canvas, typography, focus, scrollbars
   3  Materials   six strengths of glass, tints, edge light
   4  Shell       topbar, navigation rail, bottom bar, workspace
   5  Controls    buttons, fields, switches, segments, tags, tooltips
   6  Surfaces    panels, tiles, tables, lists, meters, callouts
   7  Chat        rail, transcript, composer, empty states
   8  Data        charts, model cards, copy pills
   9  Overlays    veils, dialogs, palette, toasts
   10 States      skeletons, utilities
   11 Responsive  intentional layouts per breakpoint
   12 Prefs       reduced motion, reduced transparency, contrast, print

   Layer order (z-index): canvas -1 · workspace pane 1 · panels 2 ·
   chrome (topbar, rail, bottom bar) 30 · popovers 40 · dialogs 60 · toasts 70.
   Nothing invents a value: a component picks a token or the scale grows.
   ═══════════════════════════════════════════════════════════════════════════ */

/* ── 1. Tokens ───────────────────────────────────────────────────────────── */
:root{
  color-scheme:light;

  /* Ink. Text is never translucent; only surfaces are. */
  --bg:#eef0f5;
  --bg-top:#f5f6fa;
  --bg-bottom:#e2e5ee;
  --fg:#16181f;
  --fg-2:#5d626d;
  --fg-3:#5f6470;
  /* Text accents are a half-step deeper than the fills so small labels clear
     4.5:1 on the tinted canvas; the fills carry the brighter GlassGem shades. */
  --accent:#3a52d4;
  --accent-fill:#4a63e7;
  --accent-hover:#4058d9;
  --accent-fg:#ffffff;
  --accent-soft:rgba(74,99,231,.10);
  --ok:#17704a;  --ok-fill:#2f9e63;
  --warn:#855600; --warn-fill:#c98a1e;
  --bad:#b5262f; --bad-fill:#d9434b;
  --info:#1d6896;

  /* Materials — one system, six strengths. Elevation picks the strength. */
  --glass-1:rgba(255,255,255,.38);   /* inset tiles, cards, list surfaces   */
  --glass-2:rgba(255,255,255,.55);   /* panels, transcript, tables          */
  --glass-3:rgba(255,255,255,.66);   /* chrome: topbar, rail, sticky heads  */
  --glass-4:rgba(255,255,255,.80);   /* popovers, menus, tooltips           */
  --glass-5:rgba(255,255,255,.87);   /* dialogs and sheets                  */
  --glass-6:rgba(255,255,255,.92);   /* toasts, highest layer               */
  --pane:rgba(255,255,255,.28);      /* the workspace the panels sit on     */
  --pane-stuck:rgba(255,255,255,.46);
  --fill-1:rgba(22,24,31,.045);      /* quiet fills: hover, rails, meters   */
  --fill-2:rgba(22,24,31,.080);
  --fill-3:rgba(22,24,31,.130);
  --field:rgba(255,255,255,.46);     /* input wells                         */
  --field-hover:rgba(255,255,255,.62);
  --field-focus:rgba(255,255,255,.90);
  --code-bg:rgba(22,24,31,.050);
  --scrim:rgba(20,22,30,.28);
  --surface-solid:247,248,251;      /* rgb triplet: opaque fallback surface  */

  /* Hairlines and edge light. A border defines; it never decorates. */
  --line-1:rgba(22,24,31,.075);
  --line-2:rgba(22,24,31,.130);
  --line-3:rgba(22,24,31,.220);
  --edge-hi:rgba(255,255,255,.88);
  --edge-lo:rgba(255,255,255,.18);
  --edge-hi-strong:rgba(255,255,255,.22);  /* highlight on a tinted fill */
  --sheen:rgba(255,255,255,.30);            /* pointer light, specular    */
  --sheen-fade:rgba(255,255,255,0);
  --fill-on-tint:rgba(255,255,255,.16);     /* chips sitting on a tint    */
  --on-accent:#ffffff;                      /* content on an accent fill  */

  /* Tints — opaque text on tinted glass. */
  --acc-bg:rgba(74,99,231,.10);   --acc-line:rgba(74,99,231,.28);   --acc-ring:rgba(74,99,231,.30);
  --ok-bg:rgba(47,158,99,.11);    --ok-line:rgba(47,158,99,.28);
  --warn-bg:rgba(201,138,30,.13); --warn-line:rgba(201,138,30,.32);
  --bad-bg:rgba(217,67,75,.10);   --bad-line:rgba(217,67,75,.28);
  --neu-bg:rgba(67,76,96,.09);    --neu-line:rgba(67,76,96,.20);

  /* Ambient colour behind the glass: three soft fields on the canvas. */
  --tint-a:rgba(120,140,220,.15);
  --tint-b:rgba(190,160,230,.11);
  --tint-c:rgba(140,200,230,.11);

  /* Depth — ambient, never dramatic. */
  --sh-1:0 1px 1px rgba(20,24,40,.03), 0 2px 8px -2px rgba(20,24,40,.05);
  --sh-2:0 1px 1px rgba(20,24,40,.03), 0 12px 32px -16px rgba(20,24,40,.13);
  --sh-3:0 1px 2px rgba(20,24,40,.03), 0 24px 56px -24px rgba(20,24,40,.20);
  --sh-4:0 1px 2px rgba(20,24,40,.04), 0 36px 80px -32px rgba(20,24,40,.28);
  --sh-5:0 2px 4px rgba(20,24,40,.05), 0 44px 96px -32px rgba(20,24,40,.34);
  --sh-inset:inset 0 1px 0 var(--edge-hi);
  --sh-lift-1:0 1px 1.5px rgba(20,24,40,.10);
  --sh-lift-2:0 2px 5px rgba(20,24,40,.12);
  --sh-knob:0 1px 2px rgba(20,24,40,.24), 0 2px 6px rgba(20,24,40,.12);
  --sh-knob-press:0 1px 3px rgba(20,24,40,.30), 0 2px 6px rgba(20,24,40,.16);
  --sh-well:inset 0 1px 2px rgba(20,24,40,.05);
  --sh-press:inset 0 1.5px 3px rgba(20,24,40,.10);

  /* Blur and saturation */
  --blur-1:10px; --blur-2:16px; --blur-3:26px; --blur-4:36px; --blur-5:48px;
  --blur-veil:9px;
  --sat-1:140%; --sat-2:160%; --sat-3:180%;

  /* Radii */
  --r-1:8px; --r-2:12px; --r-3:16px; --r-4:22px; --r-5:28px; --r-6:34px;
  --r-full:999px;

  /* Space */
  --sp-1:4px; --sp-2:8px; --sp-3:12px; --sp-4:16px; --sp-5:20px; --sp-6:24px;
  --sp-7:32px; --sp-8:44px; --sp-9:60px; --sp-10:80px;

  /* Type */
  --fs-micro:11px; --fs-meta:12.5px; --fs-sm:13.5px; --fs-body:15px;
  --fs-lead:17px; --fs-h3:19px; --fs-h2:24px; --fs-h1:clamp(29px,2.9vw,38px);
  --track-tight:-.022em; --track-wide:.085em;
  --lh-tight:1.2; --lh-body:1.62;

  /* Motion — fast in response, smooth in motion. */
  --d-1:130ms; --d-2:190ms; --d-3:260ms; --d-4:380ms; --d-5:520ms;
  --e-out:cubic-bezier(.22,.61,.21,1);
  --e-inout:cubic-bezier(.4,0,.2,1);
  --e-spring:cubic-bezier(.34,1.3,.42,1);
  --e-enter:cubic-bezier(.16,.84,.3,1);
  --e-glide:cubic-bezier(.26,.94,.34,1);

  /* Layers */
  --z-canvas:-1; --z-pane:1; --z-panel:2; --z-chrome:30; --z-pop:40;
  --z-dialog:60; --z-toast:70;

  --sans:-apple-system,BlinkMacSystemFont,"SF Pro Display","SF Pro Text","Inter",
         system-ui,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
  --mono:ui-monospace,"SF Mono",SFMono-Regular,"JetBrains Mono",Menlo,Consolas,monospace;

  --rail:236px;              /* navigation rail width, desktop          */
  --pane-max:1180px;         /* readable measure inside the workspace   */
}

/* Palette — dark. A designed scheme, not an inversion: deep neutrals, low
   border contrast, surfaces that stay visible without turning white. */
:root[data-theme="dark"]{
  color-scheme:dark;
  --bg:#0d0f15;
  --bg-top:#12141c;
  --bg-bottom:#0a0c11;
  --fg:#eef0f6; --fg-2:#9aa2b1; --fg-3:#8a92a3;
  --accent:#7f92ff; --accent-fill:#4a63e7; --accent-hover:#4f66ea; --accent-fg:#ffffff;
  --accent-soft:rgba(127,146,255,.14);
  --ok:#55c98a; --ok-fill:#55c98a;
  --warn:#e2b25a; --warn-fill:#e2b25a;
  --bad:#f0676d; --bad-fill:#f0676d;
  --info:#7fc8ef;

  --glass-1:rgba(255,255,255,.045);
  --glass-2:rgba(255,255,255,.065);
  --glass-3:rgba(255,255,255,.085);
  --glass-4:rgba(24,27,38,.72);
  --glass-5:rgba(26,29,40,.86);
  --glass-6:rgba(30,34,46,.92);
  --pane:rgba(255,255,255,.030);
  --pane-stuck:rgba(255,255,255,.055);
  --fill-1:rgba(255,255,255,.045);
  --fill-2:rgba(255,255,255,.070);
  --fill-3:rgba(255,255,255,.120);
  --field:rgba(255,255,255,.050);
  --field-hover:rgba(255,255,255,.070);
  --field-focus:rgba(255,255,255,.100);
  --code-bg:rgba(0,0,0,.32);
  --scrim:rgba(4,5,10,.55);
  --surface-solid:18,20,28;

  --line-1:rgba(238,240,246,.080);
  --line-2:rgba(238,240,246,.140);
  --line-3:rgba(238,240,246,.220);
  --edge-hi:rgba(255,255,255,.12);
  --edge-lo:rgba(255,255,255,.040);
  --edge-hi-strong:rgba(255,255,255,.20);
  --fill-on-tint:rgba(255,255,255,.16);
  --on-accent:#ffffff;

  --acc-bg:rgba(127,146,255,.14); --acc-line:rgba(127,146,255,.32); --acc-ring:rgba(127,146,255,.35);
  --ok-bg:rgba(85,201,138,.10);   --ok-line:rgba(85,201,138,.30);
  --warn-bg:rgba(226,178,90,.12); --warn-line:rgba(226,178,90,.30);
  --bad-bg:rgba(240,103,109,.10); --bad-line:rgba(240,103,109,.30);
  --neu-bg:rgba(238,240,246,.08);  --neu-line:rgba(238,240,246,.16);
  --tint-a:rgba(90,110,220,.12);
  --tint-b:rgba(150,110,220,.09);
  --tint-c:rgba(80,170,230,.08);

  --sh-1:0 1px 1px rgba(0,0,0,.18), 0 2px 8px -2px rgba(0,0,0,.22);
  --sh-2:0 1px 1px rgba(0,0,0,.18), 0 12px 32px -16px rgba(0,0,0,.48);
  --sh-3:0 1px 2px rgba(0,0,0,.22), 0 24px 56px -24px rgba(0,0,0,.58);
  --sh-4:0 1px 2px rgba(0,0,0,.26), 0 36px 80px -32px rgba(0,0,0,.68);
  --sh-5:0 2px 4px rgba(0,0,0,.30), 0 44px 96px -32px rgba(0,0,0,.74);
  --sh-inset:inset 0 1px 0 var(--edge-hi);
  --sh-lift-1:0 1px 1.5px rgba(0,0,0,.30);
  --sh-lift-2:0 2px 6px rgba(0,0,0,.34);
  --sh-knob:0 1px 2px rgba(0,0,0,.45), 0 2px 6px rgba(0,0,0,.28);
  --sh-knob-press:0 1px 3px rgba(0,0,0,.55), 0 2px 7px rgba(0,0,0,.32);
  --sh-well:inset 0 1px 2px rgba(0,0,0,.34);
  --sh-press:inset 0 1.5px 3px rgba(0,0,0,.45);
}
@media (prefers-color-scheme:dark){
:root:not([data-theme="light"]){  color-scheme:dark;
  --bg:#0d0f15;
  --bg-top:#12141c;
  --bg-bottom:#0a0c11;
  --fg:#eef0f6; --fg-2:#9aa2b1; --fg-3:#8a92a3;
  --accent:#7f92ff; --accent-fill:#4a63e7; --accent-hover:#4f66ea; --accent-fg:#ffffff;
  --accent-soft:rgba(127,146,255,.14);
  --ok:#55c98a; --ok-fill:#55c98a;
  --warn:#e2b25a; --warn-fill:#e2b25a;
  --bad:#f0676d; --bad-fill:#f0676d;
  --info:#7fc8ef;

  --glass-1:rgba(255,255,255,.045);
  --glass-2:rgba(255,255,255,.065);
  --glass-3:rgba(255,255,255,.085);
  --glass-4:rgba(24,27,38,.72);
  --glass-5:rgba(26,29,40,.86);
  --glass-6:rgba(30,34,46,.92);
  --pane:rgba(255,255,255,.030);
  --pane-stuck:rgba(255,255,255,.055);
  --fill-1:rgba(255,255,255,.045);
  --fill-2:rgba(255,255,255,.070);
  --fill-3:rgba(255,255,255,.120);
  --field:rgba(255,255,255,.050);
  --field-hover:rgba(255,255,255,.070);
  --field-focus:rgba(255,255,255,.100);
  --code-bg:rgba(0,0,0,.32);
  --scrim:rgba(4,5,10,.55);
  --surface-solid:18,20,28;

  --line-1:rgba(238,240,246,.080);
  --line-2:rgba(238,240,246,.140);
  --line-3:rgba(238,240,246,.220);
  --edge-hi:rgba(255,255,255,.12);
  --edge-lo:rgba(255,255,255,.040);
  --edge-hi-strong:rgba(255,255,255,.20);
  --fill-on-tint:rgba(255,255,255,.16);
  --on-accent:#ffffff;

  --acc-bg:rgba(127,146,255,.14); --acc-line:rgba(127,146,255,.32); --acc-ring:rgba(127,146,255,.35);
  --ok-bg:rgba(85,201,138,.10);   --ok-line:rgba(85,201,138,.30);
  --warn-bg:rgba(226,178,90,.12); --warn-line:rgba(226,178,90,.30);
  --bad-bg:rgba(240,103,109,.10); --bad-line:rgba(240,103,109,.30);
  --neu-bg:rgba(238,240,246,.08);  --neu-line:rgba(238,240,246,.16);
  --tint-a:rgba(90,110,220,.12);
  --tint-b:rgba(150,110,220,.09);
  --tint-c:rgba(80,170,230,.08);

  --sh-1:0 1px 1px rgba(0,0,0,.18), 0 2px 8px -2px rgba(0,0,0,.22);
  --sh-2:0 1px 1px rgba(0,0,0,.18), 0 12px 32px -16px rgba(0,0,0,.48);
  --sh-3:0 1px 2px rgba(0,0,0,.22), 0 24px 56px -24px rgba(0,0,0,.58);
  --sh-4:0 1px 2px rgba(0,0,0,.26), 0 36px 80px -32px rgba(0,0,0,.68);
  --sh-5:0 2px 4px rgba(0,0,0,.30), 0 44px 96px -32px rgba(0,0,0,.74);
  --sh-inset:inset 0 1px 0 var(--edge-hi);
  --sh-lift-1:0 1px 1.5px rgba(0,0,0,.30);
  --sh-lift-2:0 2px 6px rgba(0,0,0,.34);
  --sh-knob:0 1px 2px rgba(0,0,0,.45), 0 2px 6px rgba(0,0,0,.28);
  --sh-knob-press:0 1px 3px rgba(0,0,0,.55), 0 2px 7px rgba(0,0,0,.32);
  --sh-well:inset 0 1px 2px rgba(0,0,0,.34);
  --sh-press:inset 0 1.5px 3px rgba(0,0,0,.45);}
}

/* No blur available → same geometry, near-solid materials. */
/* No backdrop blur available → same geometry, near-solid materials. */
@supports not ((backdrop-filter:blur(4px)) or (-webkit-backdrop-filter:blur(4px))){
  :root{
    --glass-1:rgba(var(--surface-solid),.93); --glass-2:rgba(var(--surface-solid),.96);
    --glass-3:rgba(var(--surface-solid),.97); --glass-4:rgba(var(--surface-solid),.98);
    --glass-5:rgba(var(--surface-solid),.99); --glass-6:rgb(var(--surface-solid));
    --pane:rgba(var(--surface-solid),.86); --pane-stuck:rgba(var(--surface-solid),.94);
    --field:rgba(var(--surface-solid),.80); --field-focus:rgb(var(--surface-solid));
  }
}

/* ── 2. Base ─────────────────────────────────────────────────────────────── */
*,*::before,*::after{box-sizing:border-box}
html{height:100%;-webkit-text-size-adjust:100%}
html,body{min-height:100%}
/* The app is exactly one viewport tall and the workspace scrolls inside it, so
   the chrome never leaves the screen and a long transcript stays readable. */
body{
  margin:0;height:100%;overflow:hidden;
  color:var(--fg);background:var(--bg);
  font:400 var(--fs-body)/var(--lh-body) var(--sans);
  font-optical-sizing:auto;
  font-feature-settings:"kern" 1,"liga" 1,"cv11" 1;
  -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;
  text-rendering:optimizeLegibility;
}
body.no-scroll{overflow:hidden}
h1,h2,h3,h4,p,figure{margin:0}
button,input,select,textarea{font:inherit;color:inherit}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
svg{flex:none}
b,strong{font-weight:640}
::selection{background:var(--acc-bg);color:var(--fg)}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
  clip:rect(0 0 0 0);white-space:nowrap;border:0}
[hidden]{display:none !important}

/* Focus: one ring for the whole product, always visible on keyboard use. */
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:6px}
input:focus-visible,select:focus-visible,textarea:focus-visible{outline:none}
.skip{
  position:fixed;left:-9999px;top:0;z-index:var(--z-toast);
  background:var(--glass-5);border:1px solid var(--line-2);border-radius:var(--r-2);
  padding:11px 17px;font-size:var(--fs-sm);font-weight:580;
  -webkit-backdrop-filter:blur(var(--blur-4)) saturate(var(--sat-3));
  backdrop-filter:blur(var(--blur-4)) saturate(var(--sat-3));
  box-shadow:var(--sh-3), var(--sh-inset);
}
.skip:focus{left:16px;top:16px}

/* Canvas — the ambient light the glass refracts. Three tinted fields come
   from tokens, so both schemes share one recipe; every translucent surface
   picks them up as the colour behind it. Almost invisible until a surface
   moves over it; the halo drifts on scroll only. */
.canvas{
  position:fixed;inset:0;z-index:var(--z-canvas);pointer-events:none;overflow:hidden;
  background:
    radial-gradient(60% 46% at 84% 0%, var(--tint-a), transparent 64%),
    radial-gradient(46% 38% at 4% 12%, var(--tint-b), transparent 66%),
    radial-gradient(64% 48% at 42% 108%, var(--tint-c), transparent 68%),
    linear-gradient(178deg, var(--bg-top) 0%, var(--bg) 46%, var(--bg-bottom) 100%);
}
.canvas-glow{
  position:absolute;inset:-24% -12% -8%;display:block;
  background:
    radial-gradient(38% 30% at 62% 22%, rgba(255,255,255,.40), transparent 70%),
    radial-gradient(34% 26% at 22% 62%, rgba(120,140,220,.10), transparent 72%);
  transform:translate3d(0,0,0);
  will-change:transform;
}
.canvas::after{ /* fine grain keeps the soft fields from banding */
  content:"";position:absolute;inset:0;opacity:.030;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.82' numOctaves='2' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3C/filter%3E%3Crect width='200' height='200' filter='url(%23n)'/%3E%3C/svg%3E");
}
:root[data-theme="dark"] .canvas-glow{
  background:
    radial-gradient(38% 30% at 62% 22%, rgba(140,160,255,.08), transparent 70%),
    radial-gradient(34% 26% at 22% 62%, rgba(120,140,220,.07), transparent 72%);
}
:root[data-theme="dark"] .canvas::after{opacity:.045}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]) .canvas-glow{
    background:
      radial-gradient(38% 30% at 62% 22%, rgba(140,160,255,.08), transparent 70%),
      radial-gradient(34% 26% at 22% 62%, rgba(120,140,220,.07), transparent 72%);
  }
  :root:not([data-theme="light"]) .canvas::after{opacity:.045}
}

/* ── 3. Materials ────────────────────────────────────────────────────────── */
/* Every glass surface shares one recipe: a translucent fill, a backdrop blur
   with a little extra saturation, a hairline, an inner top highlight and a
   shadow from the ramp. Only the strength changes with elevation. */
[class*="glass-"]{position:relative;border:1px solid var(--line-1)}
/* Edge light: a 1px inner gradient drawn with a mask, so it reads as light
   catching an edge rather than as a second border. */
[class*="glass-"]::before{
  content:"";position:absolute;inset:0;border-radius:inherit;pointer-events:none;
  padding:1px;
  background:linear-gradient(158deg, var(--edge-hi), transparent 34%, transparent 66%, var(--edge-lo));
  -webkit-mask:linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  mask:linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite:xor;mask-composite:exclude;
  opacity:.9;
}
.glass-1{
  background:var(--glass-1);
  -webkit-backdrop-filter:blur(var(--blur-1)) saturate(var(--sat-1));
  backdrop-filter:blur(var(--blur-1)) saturate(var(--sat-1));
  box-shadow:var(--sh-1), var(--sh-inset);
}
.glass-2{
  background:var(--glass-2);
  -webkit-backdrop-filter:blur(var(--blur-2)) saturate(var(--sat-2));
  backdrop-filter:blur(var(--blur-2)) saturate(var(--sat-2));
  box-shadow:var(--sh-2), var(--sh-inset);
}
.glass-3{
  background:var(--glass-3);
  -webkit-backdrop-filter:blur(var(--blur-3)) saturate(var(--sat-2));
  backdrop-filter:blur(var(--blur-3)) saturate(var(--sat-2));
  box-shadow:var(--sh-3), var(--sh-inset);
}
.glass-4{
  background:var(--glass-4);
  -webkit-backdrop-filter:blur(var(--blur-4)) saturate(var(--sat-3));
  backdrop-filter:blur(var(--blur-4)) saturate(var(--sat-3));
  box-shadow:var(--sh-4), var(--sh-inset);
}
.glass-5{
  background:var(--glass-5);
  -webkit-backdrop-filter:blur(var(--blur-4)) saturate(var(--sat-3));
  backdrop-filter:blur(var(--blur-4)) saturate(var(--sat-3));
  box-shadow:var(--sh-4), var(--sh-inset);
}
.glass-6{
  background:var(--glass-6);
  -webkit-backdrop-filter:blur(var(--blur-3)) saturate(var(--sat-3));
  backdrop-filter:blur(var(--blur-3)) saturate(var(--sat-3));
  box-shadow:var(--sh-4), var(--sh-inset);
}
/* Tinted glass — contextual states only, never behind body copy. */
.tint-acc{background:var(--acc-bg);border-color:var(--acc-line)}
.tint-ok{background:var(--ok-bg);border-color:var(--ok-line)}
.tint-warn{background:var(--warn-bg);border-color:var(--warn-line)}
.tint-bad{background:var(--bad-bg);border-color:var(--bad-line)}
/* A quiet sheet of light used behind generated numbers and sticky headers. */
.tint-neu{background:var(--neu-bg);border-color:var(--neu-line)}
/* ── 4. Shell ────────────────────────────────────────────────────────────── */
/* A padded grid: chrome and workspace float inside the viewport instead of
   being framed by the window, which is what makes the surfaces read as
   separate planes rather than one long page. */
.app{
  position:relative;z-index:var(--z-pane);
  height:100%;display:flex;flex-direction:column;gap:clamp(10px,1.1vw,14px);
  padding:clamp(10px,1.2vw,16px) clamp(12px,1.8vw,22px) clamp(12px,1.5vw,18px);
  max-width:1560px;margin:0 auto;
}

/* Topbar — identity, live health and global actions. Compacts as the
   workspace scrolls, so the page gives back height exactly when it is needed. */
.topbar{
  position:relative;z-index:var(--z-chrome);flex:none;
  display:flex;align-items:center;gap:var(--sp-3);min-width:0;
  padding:8px 10px;border-radius:var(--r-4);
  transition:padding var(--d-3) var(--e-out),background-color var(--d-3) var(--e-out),
             border-color var(--d-3) var(--e-out),box-shadow var(--d-3) var(--e-out);
}
.topbar.is-scrolled{
  padding:5px 8px;background:var(--glass-4);border-color:var(--line-2);
  box-shadow:var(--sh-3), var(--sh-inset);
}
.brand{
  display:flex;align-items:center;gap:11px;min-width:0;
  appearance:none;background:none;border:0;padding:4px;margin:-4px;
  cursor:pointer;text-align:left;border-radius:var(--r-3);color:var(--fg);
}
.brand:hover{text-decoration:none}
.mark{
  width:36px;height:36px;flex:0 0 36px;border-radius:var(--r-2);
  display:grid;place-items:center;position:relative;
  background:var(--glass-4);border:1px solid var(--line-1);
  box-shadow:var(--sh-1), var(--sh-inset);
  transition:transform var(--d-2) var(--e-spring),box-shadow var(--d-2) var(--e-out);
}
.mark svg{width:20px;height:20px;display:block}
.brand:hover .mark{transform:translateY(-1px) scale(1.04);box-shadow:var(--sh-2), var(--sh-inset)}
.brand:active .mark{transform:scale(.97)}
.brand-text{min-width:0}
.brand-text h1{
  font-size:15.5px;font-weight:625;letter-spacing:var(--track-tight);line-height:1.15;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--fg);
}
.brand-text p{
  font-size:var(--fs-meta);color:var(--fg-3);margin-top:1px;line-height:1.3;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
/* Section context: the workspace says which pane you are in, the topbar
   echoes it once the pane's own heading has scrolled away. */
.tb-context{
  display:flex;align-items:center;gap:9px;min-width:0;
  padding-left:15px;margin-left:5px;border-left:1px solid var(--line-1);
  opacity:0;transform:translateX(-4px);pointer-events:none;
  transition:opacity var(--d-3) var(--e-out),transform var(--d-3) var(--e-glide);
}
.topbar.is-scrolled .tb-context{opacity:1;transform:none}
.tb-context .tb-title{
  font-size:var(--fs-sm);font-weight:600;letter-spacing:-.01em;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.topbar-end{display:flex;align-items:center;gap:var(--sp-2);margin-left:auto;min-width:0;flex-wrap:wrap;justify-content:flex-end}

/* Health pill — a breathing dot and the same fact in words. */
.health{
  display:inline-flex;align-items:center;gap:8px;
  padding:6px 12px 6px 10px;border-radius:var(--r-full);
  font-size:var(--fs-meta);font-weight:560;color:var(--fg-2);
  background:var(--fill-1);border:1px solid var(--line-1);
  font-variant-numeric:tabular-nums;white-space:nowrap;
  transition:background-color var(--d-2) var(--e-out),border-color var(--d-2) var(--e-out),
             color var(--d-2) var(--e-out);
}
.health.is-ok{color:var(--ok);background:var(--ok-bg);border-color:var(--ok-line)}
.health.is-warn{color:var(--warn);background:var(--warn-bg);border-color:var(--warn-line)}
.health.is-bad{color:var(--bad);background:var(--bad-bg);border-color:var(--bad-line)}
.dot{width:8px;height:8px;flex:0 0 8px;border-radius:50%;background:var(--fg-3);position:relative;
  transition:background-color var(--d-2) var(--e-out)}
.dot.ok{background:var(--ok-fill);box-shadow:0 0 0 3px var(--ok-bg)}
.dot.warn{background:var(--warn-fill);box-shadow:0 0 0 3px var(--warn-bg)}
.dot.bad{background:var(--bad-fill);box-shadow:0 0 0 3px var(--bad-bg)}
.dot.ok::after,.dot.warn::after,.dot.bad::after{
  content:"";position:absolute;inset:0;border-radius:50%;background:currentColor}
.dot.ok::after{background:var(--ok-fill);animation:breathe 3.1s var(--e-out) infinite}
.dot.warn::after{background:var(--warn-fill);animation:breathe 2.3s var(--e-out) infinite}
.dot.bad::after{background:var(--bad-fill);animation:breathe 1.7s var(--e-out) infinite}
@keyframes breathe{0%{transform:scale(1);opacity:.45}70%,100%{transform:scale(2.6);opacity:0}}
.pill--meta{
  display:inline-flex;align-items:center;gap:7px;padding:6px 12px;border-radius:var(--r-full);
  font-size:var(--fs-micro);font-weight:560;color:var(--fg-3);letter-spacing:.01em;
  background:var(--fill-1);border:1px solid var(--line-1);
  font-variant-numeric:tabular-nums;white-space:nowrap;
}
.pill--meta .dot-sep{width:3px;height:3px;border-radius:50%;background:var(--fill-3)}

/* Workspace: rail and content pane share the leftover height. */
.body{
  flex:1;display:grid;grid-template-columns:var(--rail) minmax(0,1fr);
  gap:clamp(10px,1.1vw,14px);min-height:0;
}
.railwrap{position:relative;min-height:0;min-width:0}

/* Navigation rail — a floating glass column, not a framed sidebar. It sits at
   the chrome layer (with the topbar): the workspace pane is a translucent
   stacking context, so a z-auto rail would paint underneath it and swallow
   the tab tooltips that travel across the workspace. */
.rail{
  position:absolute;inset:0;z-index:var(--z-chrome);
  display:flex;flex-direction:column;gap:var(--sp-2);
  padding:10px;border-radius:var(--r-5);
  transition:background-color var(--d-3) var(--e-out),box-shadow var(--d-3) var(--e-out),
             border-color var(--d-3) var(--e-out);
}
.rail.is-scrolled{border-color:var(--line-2);box-shadow:var(--sh-3), var(--sh-inset)}
.rail-group{
  padding:2px 10px 4px;font-size:var(--fs-micro);font-weight:640;color:var(--fg-3);
  text-transform:uppercase;letter-spacing:var(--track-wide);
}
nav.tabs{
  position:relative;display:flex;flex-direction:column;gap:2px;min-width:0;
  padding:2px;border-radius:var(--r-4);
}
nav.tabs .glider{
  position:absolute;left:0;top:0;width:0;height:0;border-radius:var(--r-3);
  background:var(--glass-4);border:1px solid var(--line-2);
  box-shadow:var(--sh-2), var(--sh-inset);
  opacity:0;pointer-events:none;
  transition:transform var(--d-3) var(--e-glide),width var(--d-3) var(--e-glide),
             height var(--d-3) var(--e-glide),opacity var(--d-2) var(--e-out);
  will-change:transform,width,height;
}
nav.tabs .glider.ready{opacity:1}
.tab-btn{
  position:relative;z-index:1;display:flex;align-items:center;gap:11px;width:100%;
  appearance:none;background:none;border:0;cursor:pointer;text-align:left;
  padding:9px 12px;border-radius:var(--r-3);color:var(--fg-2);
  font-size:var(--fs-sm);font-weight:560;letter-spacing:-.004em;white-space:nowrap;
  transition:color var(--d-2) var(--e-out),transform var(--d-1) var(--e-out),
             background-color var(--d-2) var(--e-out);
}
.tab-btn .ico{width:18px;height:18px;display:block;opacity:.78;
  transition:opacity var(--d-2) var(--e-out),transform var(--d-2) var(--e-spring)}
.tab-btn .lbl{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis}
.tab-btn .navkbd{
  font-family:var(--mono);font-size:10px;color:var(--fg-3);letter-spacing:0;
  padding:1px 5px;border-radius:5px;background:var(--fill-1);border:1px solid transparent;
  opacity:0;transform:translateX(3px);
  transition:opacity var(--d-2) var(--e-out),transform var(--d-2) var(--e-out);
}
.tab-btn:hover{color:var(--fg);background:var(--fill-1)}
.tab-btn:hover .ico{opacity:.95}
.tab-btn:hover .navkbd{opacity:.75;transform:none}
.tab-btn[aria-selected="true"]{color:var(--fg)}
.tab-btn[aria-selected="true"] .ico{opacity:1;transform:translateY(-.5px) scale(1.05)}
.tab-btn[aria-selected="true"] .navkbd{opacity:.55}
.tab-btn:active{transform:scale(.988)}
.tab-btn:focus-visible{outline-offset:-2px}
.rail-foot{
  margin-top:auto;display:flex;flex-direction:column;gap:7px;
  padding-top:var(--sp-2);border-top:1px solid var(--line-1);
}
.rail-live{
  display:flex;align-items:center;gap:9px;padding:8px 10px;border-radius:var(--r-2);
  background:var(--fill-1);border:1px solid var(--line-1);
  font-size:var(--fs-meta);font-weight:540;color:var(--fg-2);min-width:0;
}
.pulse{
  width:7px;height:7px;flex:0 0 7px;border-radius:50%;background:var(--fg-3);position:relative;
  transition:background-color var(--d-2) var(--e-out);
}
.pulse.ok{background:var(--ok-fill);box-shadow:0 0 0 3px var(--ok-bg)}
.pulse.warn{background:var(--warn-fill);box-shadow:0 0 0 3px var(--warn-bg)}
.pulse.bad{background:var(--bad-fill);box-shadow:0 0 0 3px var(--bad-bg)}
.pulse.ok::after,.pulse.warn::after,.pulse.bad::after{
  content:"";position:absolute;inset:0;border-radius:50%;background:inherit;
  animation:breathe 3.1s var(--e-out) infinite}
.rail-live .rl-t{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rail-note{font-size:var(--fs-micro);color:var(--fg-3);line-height:1.45;padding:0 10px}

/* Content pane — the quiet layer the panels sit on. It picks up the ambient
   background through its transparency, so the page never looks flat. */
main.content{
  position:relative;min-width:0;min-height:0;
  overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;
  scrollbar-gutter:stable;
  padding:clamp(16px,1.9vw,30px) clamp(14px,2.1vw,34px) var(--sp-8);
  border-radius:var(--r-5);border:1px solid var(--line-1);
  background:var(--pane);
  -webkit-backdrop-filter:blur(var(--blur-1)) saturate(var(--sat-1));
  backdrop-filter:blur(var(--blur-1)) saturate(var(--sat-1));
  box-shadow:var(--sh-1), var(--sh-inset);
  transition:background-color var(--d-3) var(--e-out);
}
main.content.is-scrolled{background:var(--pane-stuck)}
main.content>section,main.content>footer.foot{max-width:var(--pane-max);margin-inline:auto}

/* Pane transitions: the incoming view fades in from the direction of travel. */
section.tab{display:none;flex-direction:column;gap:var(--sp-5);min-width:0;flex:0 0 auto}
section.tab.active{display:flex}
section.tab.active[data-enter="fwd"]{animation:panelFwd var(--d-4) var(--e-enter)}
section.tab.active[data-enter="back"]{animation:panelBack var(--d-4) var(--e-enter)}
section.tab.active[data-enter="none"]{animation:panelIn var(--d-3) var(--e-enter)}
@keyframes panelIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
@keyframes panelFwd{from{opacity:0;transform:translateX(14px) scale(.995)}to{opacity:1;transform:none}}
@keyframes panelBack{from{opacity:0;transform:translateX(-14px) scale(.995)}to{opacity:1;transform:none}}

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
.btn .lbl{min-width:0;overflow:hidden;text-overflow:ellipsis}
.btn:active{transform:scale(.978)}
.btn:disabled,.btn[aria-disabled="true"]{opacity:.44;cursor:not-allowed;transform:none;filter:saturate(.45)}
/* A light that follows the pointer: it makes a surface feel like an object
   rather than a rectangle. Buttons and segments carry a tooltip, and a tooltip
   owns its ::after — so those take ::before and the rest keep ::after. One
   recipe, one delegated listener, nothing that re-lays out. */
.btn::before,.tab-btn::before,.seg-btn::before,
.tile::after,.chip::after,.pal-item::after,button.copy::after{
  content:"";position:absolute;inset:0;border-radius:inherit;pointer-events:none;opacity:0;
  background:radial-gradient(140px 92px at var(--mx,50%) var(--my,0%),
             var(--sheen), var(--sheen-fade) 72%);
  transition:opacity var(--d-3) var(--e-out);
}
@media (hover:hover){
  .btn:hover::before,.tab-btn:hover::before,.seg-btn:hover::before,.tile:hover::after,
  .chip:hover::after,.pal-item:hover::after,button.copy:hover::after{opacity:.72}
  /* The primary button is already bright: it needs the least light. */
  .btn--primary:hover::before{opacity:.34}
}
/* Primary — a top-lit accent object: the gradient is the light, the ring
   shadow is the depth, and hover moves the fill one step via --accent-hover
   (deeper in light, lifted in dark) instead of washing it out. */
.btn--primary{
  background:linear-gradient(180deg,color-mix(in srgb,var(--accent-fill) 86%,#fff) 0%,var(--accent-fill) 62%);
  color:var(--accent-fg);border-color:transparent;
  box-shadow:var(--sh-lift-1), 0 8px 20px -6px var(--acc-ring),
             inset 0 1px 0 var(--edge-hi-strong);
}
.btn--primary:hover:not(:disabled){
  background:linear-gradient(180deg,color-mix(in srgb,var(--accent-hover) 86%,#fff) 0%,var(--accent-hover) 62%);
  transform:translateY(-1px);
  box-shadow:var(--sh-lift-2), 0 14px 30px -8px var(--acc-ring),
             inset 0 1px 0 var(--edge-hi-strong)}
.btn--primary:active:not(:disabled){transform:translateY(0) scale(.978);
  box-shadow:var(--sh-lift-1), 0 3px 10px -4px var(--acc-ring),
             inset 0 1px 0 var(--edge-hi-strong)}
.btn--glass{
  background:var(--glass-3);color:var(--fg);border-color:var(--line-1);
  -webkit-backdrop-filter:blur(var(--blur-2)) saturate(var(--sat-2));
  backdrop-filter:blur(var(--blur-2)) saturate(var(--sat-2));
  box-shadow:var(--sh-1), var(--sh-inset);
}
.btn--glass:hover:not(:disabled){background:var(--glass-4);border-color:var(--line-2);
  box-shadow:var(--sh-2), var(--sh-inset)}
.btn--glass:active:not(:disabled){box-shadow:var(--sh-1), var(--sh-press)}
.btn--ghost{background:var(--fill-1);color:var(--fg);border-color:var(--line-1)}
.btn--ghost:hover:not(:disabled){background:var(--fill-2);border-color:var(--line-2)}
.btn--quiet{background:transparent;color:var(--fg-2);border-color:transparent;min-height:34px;padding:0 10px}
.btn--quiet:hover:not(:disabled){background:var(--fill-1);color:var(--fg)}
.btn--quiet[aria-pressed="true"],.btn--quiet.is-on{background:var(--acc-bg);color:var(--accent);border-color:var(--acc-line)}
.btn--quiet-danger:hover:not(:disabled){background:var(--bad-bg);color:var(--bad);border-color:transparent}
.btn--sm{min-height:32px;padding:0 12px;font-size:var(--fs-meta);border-radius:var(--r-2);gap:7px}
.btn--sm svg{width:15px;height:15px}
.btn--icon{width:38px;min-width:38px;padding:0;border-radius:var(--r-2)}
.btn--icon.btn--sm{width:32px;min-width:32px}
.btn kbd{
  font-family:var(--mono);font-size:10px;padding:1px 5px;border-radius:5px;
  background:var(--fill-1);border:1px solid var(--line-1);
}
.btn--glass kbd,.btn--primary kbd{background:var(--fill-on-tint);border-color:transparent;color:inherit}
.btn.is-busy svg{animation:spin 900ms linear infinite}
.btn .spin,.btn.is-busy svg.spin{animation:spin 900ms linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
/* Busy slot: send morphs into stop without moving a pixel. */
.slot{position:relative;display:inline-flex}
.slot>[hidden]{display:none !important}
.morph{animation:morphIn var(--d-2) var(--e-enter)}
@keyframes morphIn{from{opacity:0;transform:scale(.86)}to{opacity:1;transform:none}}

/* Fields */
.field{display:flex;flex-direction:column;gap:6px;min-width:0}
.field>label,.lbl{
  font-size:var(--fs-micro);font-weight:640;color:var(--fg-3);
  text-transform:uppercase;letter-spacing:var(--track-wide);
}
input[type="text"],input[type="password"],input[type="search"],input[type="number"],select,textarea{
  width:100%;min-width:0;
  background:var(--field);color:var(--fg);
  border:1px solid var(--line-1);border-radius:var(--r-2);
  padding:9px 12px;font-size:var(--fs-sm);
  box-shadow:var(--sh-well);
  transition:border-color var(--d-2) var(--e-out),box-shadow var(--d-2) var(--e-out),
             background-color var(--d-2) var(--e-out);
}
input::placeholder,textarea::placeholder{color:var(--fg-3)}
input:hover:not(:disabled),select:hover:not(:disabled),textarea:hover:not(:disabled){
  border-color:var(--line-2);background:var(--field-hover)}
input:focus,select:focus,textarea:focus{
  outline:none;border-color:var(--accent);background:var(--field-focus);
  box-shadow:0 0 0 3.5px var(--acc-ring),var(--sh-well);
}
input:disabled,select:disabled,textarea:disabled{opacity:.55;cursor:not-allowed}
input[aria-invalid="true"]{border-color:var(--bad-fill);box-shadow:0 0 0 3.5px var(--bad-bg)}
select{
  appearance:none;-webkit-appearance:none;cursor:pointer;padding-right:34px;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='11' height='7' viewBox='0 0 11 7'%3E%3Cpath d='M1.2 1.4 5.5 5.6l4.3-4.2' fill='none' stroke='%234b5462' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 13px center;
}
:root[data-theme="dark"] select{
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='11' height='7' viewBox='0 0 11 7'%3E%3Cpath d='M1.2 1.4 5.5 5.6l4.3-4.2' fill='none' stroke='%23a3adbc' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]) select{
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='11' height='7' viewBox='0 0 11 7'%3E%3Cpath d='M1.2 1.4 5.5 5.6l4.3-4.2' fill='none' stroke='%23a3adbc' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
  }
}
/* Search field with a leading glyph */
.search{position:relative;display:flex;align-items:center;min-width:0}
.search svg{position:absolute;left:12px;width:15px;height:15px;color:var(--fg-3);pointer-events:none}
.search input{padding-left:34px}

/* Switch — a real checkbox, restyled. Track, thumb and check live on a span so
   the input keeps its own semantics (focus, keyboard, label click). */
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
  box-shadow:var(--sh-well);
  transition:background-color var(--d-2) var(--e-out),border-color var(--d-2) var(--e-out);
}
.switch .sw::after{
  content:"";position:absolute;top:var(--sw-pad);left:var(--sw-pad);
  width:calc(var(--sw-h) - var(--sw-pad) * 2);height:calc(var(--sw-h) - var(--sw-pad) * 2);
  border-radius:50%;background:var(--on-accent);
  box-shadow:var(--sh-knob);
  transition:transform var(--d-3) var(--e-spring), box-shadow var(--d-2) var(--e-out);
}
.switch .sw::before{ /* the check draws itself in */
  content:"";position:absolute;inset:0;margin:auto;width:5px;height:9px;
  border-right:2px solid var(--on-accent);border-bottom:2px solid var(--on-accent);opacity:0;
  transform:translateX(calc(var(--sw-travel) / 2)) rotate(45deg) scaleY(0);
  transform-origin:center bottom;
  transition:transform var(--d-2) var(--e-out), opacity var(--d-1) var(--e-out);
}
.switch input:checked + .sw{background:var(--accent-fill);border-color:transparent;box-shadow:inset 0 1px 0 rgba(255,255,255,.20)}
.switch input:checked + .sw::after{transform:translateX(var(--sw-travel))}
.switch input:checked + .sw::before{opacity:1;transform:translateX(calc(var(--sw-travel) / 2)) rotate(45deg) scaleY(1);
  transition:transform var(--d-2) var(--e-out) 60ms, opacity var(--d-1) var(--e-out) 60ms}
.switch:hover input:not(:checked) + .sw{background:var(--fill-3)}
.switch input:active + .sw::after{box-shadow:var(--sh-knob-press)}
.switch input:focus-visible + .sw{outline:2px solid var(--accent);outline-offset:2px}
.switch input:disabled + .sw{opacity:.5}
.switch input:disabled + .sw,.switch input:disabled ~ *{cursor:not-allowed}
.switch.is-sm .sw{--sw-w:34px; --sw-h:20px; --sw-pad:2.5px; --sw-travel:14px}
.switch.is-sm .sw::before{width:4px;height:7.5px;border-width:0 1.8px 1.8px 0}
.switch .sw-txt{color:inherit}

/* Segmented control — one gliding highlight, no colour blocks. */
.seg{
  display:inline-flex;align-items:center;gap:2px;position:relative;
  padding:3px;border-radius:var(--r-full);
  background:var(--fill-1);border:1px solid var(--line-1);
}
.seg-btn{
  position:relative;appearance:none;border:0;background:none;cursor:pointer;
  display:inline-flex;align-items:center;justify-content:center;gap:6px;
  min-width:32px;height:28px;padding:0 9px;border-radius:var(--r-full);
  font-size:var(--fs-micro);font-weight:600;color:var(--fg-3);
  transition:color var(--d-2) var(--e-out),background-color var(--d-2) var(--e-out),
             box-shadow var(--d-2) var(--e-out),transform var(--d-1) var(--e-out);
}
.seg-btn svg{width:15px;height:15px;display:block}
.seg-btn:hover{color:var(--fg)}
.seg-btn[aria-checked="true"]{color:var(--fg);background:var(--glass-5);box-shadow:var(--sh-1), var(--sh-inset)}
.seg-btn:active{transform:scale(.96)}

/* Progress hairline — work is shown without moving the layout. */
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

/* Tooltip — a glass chip that belongs to the control that owns it. */
[data-tip]{position:relative}
[data-tip]::after{
  content:attr(data-tip);position:absolute;left:50%;bottom:calc(100% + 9px);
  transform:translate(-50%,3px) scale(.98);transform-origin:bottom center;
  padding:6px 10px;border-radius:var(--r-1);max-width:260px;width:max-content;
  background:var(--glass-4);border:1px solid var(--line-2);color:var(--fg);
  font-size:var(--fs-micro);font-weight:520;letter-spacing:.005em;line-height:1.4;white-space:normal;
  -webkit-backdrop-filter:blur(var(--blur-3)) saturate(var(--sat-3));
  backdrop-filter:blur(var(--blur-3)) saturate(var(--sat-3));
  box-shadow:var(--sh-3), var(--sh-inset);
  opacity:0;pointer-events:none;z-index:var(--z-pop);
  transition:opacity var(--d-2) var(--e-out),transform var(--d-2) var(--e-out);
}
[data-tip]:hover::after,[data-tip]:focus-visible::after{opacity:1;transform:translate(-50%,0) scale(1)}
[data-tip-pos="below"]::after{bottom:auto;top:calc(100% + 9px);transform:translate(-50%,-3px) scale(.98);
  transform-origin:top center}
[data-tip-pos="below"]:hover::after,[data-tip-pos="below"]:focus-visible::after{transform:translate(-50%,0) scale(1)}
[data-tip-pos="left"]::after{left:auto;right:calc(100% + 9px);bottom:auto;top:50%;
  transform:translate(3px,-50%) scale(.98);transform-origin:right center}
[data-tip-pos="left"]:hover::after,[data-tip-pos="left"]:focus-visible::after{transform:translate(0,-50%) scale(1)}
[data-tip-pos="right"]::after{left:calc(100% + 9px);bottom:auto;top:50%;
  transform:translate(-3px,-50%) scale(.98);transform-origin:left center}
[data-tip-pos="right"]:hover::after,[data-tip-pos="right"]:focus-visible::after{transform:translate(0,-50%) scale(1)}
/* ── 6. Surfaces ─────────────────────────────────────────────────────────── */
.phead{display:flex;align-items:flex-end;gap:var(--sp-4);flex-wrap:wrap;padding:2px 2px 0}
.phead-text{min-width:0;max-width:68ch}
.phead h2{
  font-size:var(--fs-h1);font-weight:640;letter-spacing:var(--track-tight);
  line-height:var(--lh-tight);
}
.phead p{color:var(--fg-2);font-size:var(--fs-sm);margin-top:8px;line-height:1.6}
.phead-actions{display:flex;align-items:center;gap:var(--sp-2);margin-left:auto;flex-wrap:wrap;justify-content:flex-end}

/* Section label — small caps over a hairline; the page's quiet structure. */
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
.panel-head{display:flex;align-items:center;gap:var(--sp-3);flex-wrap:wrap;
  padding:var(--sp-4) var(--sp-4) var(--sp-3)}
.panel-head h3{font-size:var(--fs-h3);font-weight:600;letter-spacing:-.016em}
.panel-head .sub{font-size:var(--fs-meta);color:var(--fg-3);margin-top:2px}
.panel-body{padding:0 var(--sp-4) var(--sp-4)}

/* Tiles — the numerical face of the console. */
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(196px,1fr));gap:var(--sp-3)}
.tile{
  position:relative;border-radius:var(--r-3);padding:13px 15px;min-width:0;
  display:flex;flex-direction:column;gap:3px;
  transition:transform var(--d-2) var(--e-out),box-shadow var(--d-2) var(--e-out),
             border-color var(--d-2) var(--e-out),background-color var(--d-2) var(--e-out);
  animation:riseIn var(--d-3) var(--e-enter) backwards;animation-delay:calc(var(--i,0) * 22ms);
}
@keyframes riseIn{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:none}}
/* Hover is a whisper: a touch of lift, a touch of light. */
.tile:hover{transform:translateY(-1.5px);border-color:var(--line-2);background:var(--glass-2);
  box-shadow:var(--sh-2), var(--sh-inset)}
.tile .k{
  font-size:var(--fs-micro);font-weight:640;color:var(--fg-3);
  text-transform:uppercase;letter-spacing:var(--track-wide);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.tile .v{
  font-size:var(--fs-lead);font-weight:545;letter-spacing:-.014em;line-height:1.4;
  font-variant-numeric:tabular-nums;overflow-wrap:anywhere;
}
.tile .v.mono{font-family:var(--mono);font-size:var(--fs-meta);font-weight:500;letter-spacing:-.01em}
.tile .hintline{font-size:var(--fs-micro);color:var(--fg-3);margin-top:2px}
.tile--accent{border-color:var(--acc-line)}
.tile--accent .v{color:var(--accent)}
.card{border-radius:var(--r-3);padding:var(--sp-4);min-width:0}

/* Callouts — health findings, errors, disclaimers. */
.callout{
  display:flex;gap:11px;align-items:flex-start;
  border-radius:var(--r-3);padding:12px 14px;font-size:var(--fs-sm);line-height:1.58;
  border:1px solid var(--line-1);background:var(--fill-1);
  animation:riseIn var(--d-3) var(--e-enter) backwards;
}
.callout .ico{width:18px;height:18px;margin-top:1px;color:var(--fg-2);flex:0 0 18px}
.callout strong{font-weight:640}
.callout--ok{background:var(--ok-bg);border-color:var(--ok-line)}
.callout--ok .ico{color:var(--ok)}
.callout--warn{background:var(--warn-bg);border-color:var(--warn-line)}
.callout--warn .ico{color:var(--warn)}
.callout--bad{background:var(--bad-bg);border-color:var(--bad-line)}
.callout--bad .ico{color:var(--bad)}
.callout--info{background:var(--acc-bg);border-color:var(--acc-line)}
.callout--info .ico{color:var(--accent)}
.callout .ctx-body{min-width:0;max-width:72ch}
.callout-list{display:grid;gap:var(--sp-2)}
code,kbd{
  font-family:var(--mono);font-size:.92em;
  background:var(--code-bg);border:1px solid var(--line-1);
  padding:1px 5px;border-radius:5px;overflow-wrap:anywhere;
}
kbd{font-size:11.5px;padding:2px 6px;box-shadow:var(--sh-inset)}
.ks,.pal-item .kbd{display:inline-flex;align-items:center;gap:4px;flex-wrap:wrap}
pre code{background:none;border:0;padding:0;font-size:inherit;overflow-wrap:normal}

/* Tables — one surface, hairline rows, quiet hover, sticky glass header. */
.tablewrap{
  border-radius:var(--r-4);overflow:hidden;
  display:flex;flex-direction:column;min-width:0;
}
.scroller{overflow:auto;min-width:0;-webkit-overflow-scrolling:touch}
.scroller.grow{flex:1;min-height:180px}
table{width:100%;border-collapse:separate;border-spacing:0;font-size:var(--fs-sm)}
caption.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
th,td{text-align:left;padding:11px 15px;vertical-align:top;border-bottom:1px solid var(--line-1)}
thead th{
  position:sticky;top:0;z-index:1;
  background:var(--glass-3);
  -webkit-backdrop-filter:blur(var(--blur-2)) saturate(var(--sat-2));
  backdrop-filter:blur(var(--blur-2)) saturate(var(--sat-2));
  font-size:var(--fs-micro);font-weight:640;color:var(--fg-3);
  text-transform:uppercase;letter-spacing:var(--track-wide);
  padding:9px 15px;border-bottom:1px solid var(--line-2);
  white-space:nowrap;
}
tbody td{transition:background-color var(--d-1) var(--e-out)}
tbody tr:hover td{background:var(--fill-1)}
tbody tr:last-child td{border-bottom:0}
td.code{font-family:var(--mono);font-size:var(--fs-meta);color:var(--fg-2);white-space:nowrap;
  font-variant-numeric:tabular-nums}
td .sub{display:block;margin-top:3px;font-size:var(--fs-meta);color:var(--fg-3)}
tr.is-new{animation:rowIn var(--d-5) var(--e-enter)}
@keyframes rowIn{from{background-color:var(--acc-bg)}to{background-color:transparent}}
.tnote{padding:11px 15px;border-top:1px solid var(--line-1);font-size:var(--fs-meta);color:var(--fg-3);
  background:var(--fill-1)}
.empty-cell{padding:24px 15px;color:var(--fg-3);font-size:var(--fs-sm)}
tr.sk-row:hover td{background:transparent}

/* Meters — inline bars for cooldowns and distributions. */
.meter{display:flex;align-items:center;gap:8px;min-width:96px}
.meter .track{position:relative;height:5px;flex:1;border-radius:var(--r-full);background:var(--fill-2);overflow:hidden}
.meter .fill{position:absolute;inset:0 auto 0 0;border-radius:var(--r-full);background:currentColor;opacity:.7;
  transition:width var(--d-5) var(--e-out)}
.meter .val{font-size:var(--fs-micro);font-family:var(--mono);color:var(--fg-3);
  font-variant-numeric:tabular-nums;min-width:34px;text-align:right}

/* ── 7. Chat ─────────────────────────────────────────────────────────────── */
/* A definite height (viewport-relative, clamped) lets the transcript scroll
   inside its panel while the composer stays put. */
.chat{
  display:grid;grid-template-columns:244px minmax(0,1fr);gap:var(--sp-4);
  height:clamp(380px, calc(100dvh - 320px), 700px);min-height:0;
}
.crail{
  border-radius:var(--r-4);padding:var(--sp-3);display:flex;flex-direction:column;gap:var(--sp-2);
  min-height:0;min-width:0;
}
.crail-head{display:flex;align-items:center;gap:var(--sp-2);padding:2px 4px 6px}
.crail-head .lbl{flex:1}
.crail-list{flex:1;min-height:0;overflow:auto;display:flex;flex-direction:column;gap:2px;
  padding-right:2px;scrollbar-width:thin}
.conv{
  display:flex;align-items:center;gap:9px;padding:9px 10px;border-radius:var(--r-2);
  cursor:pointer;border:1px solid transparent;font-size:var(--fs-sm);min-width:0;
  transition:background-color var(--d-1) var(--e-out),border-color var(--d-1) var(--e-out),
             transform var(--d-1) var(--e-out),box-shadow var(--d-2) var(--e-out);
}
.conv:hover{background:var(--fill-1)}
.conv:active{transform:scale(.994)}
.conv .t{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:520}
.conv .n{font-size:10.5px;color:var(--fg-3);font-family:var(--mono);
  font-variant-numeric:tabular-nums;flex:0 0 auto}
.conv.sel{background:var(--glass-3);border-color:var(--line-2);box-shadow:var(--sh-1), var(--sh-inset)}
.conv.sel .t{font-weight:600;color:var(--fg)}
.conv.sel::before{ /* the selected row keeps a quiet accent mark, not a block */
  content:"";position:absolute;left:-5px;top:50%;width:3px;height:17px;margin-top:-8.5px;
  border-radius:var(--r-full);background:var(--accent);
}
.conv{position:relative}
.conv .x{
  appearance:none;border:0;background:none;color:var(--fg-3);cursor:pointer;
  display:grid;place-items:center;width:22px;height:22px;border-radius:6px;flex:0 0 22px;
  opacity:0;transition:opacity var(--d-1) var(--e-out),color var(--d-1) var(--e-out),
             background-color var(--d-1) var(--e-out);
}
.conv .x svg{width:13px;height:13px}
.conv:hover .x,.conv:focus-within .x{opacity:1}
.conv .x:hover{color:var(--bad);background:var(--bad-bg)}
.rail-empty{padding:10px;font-size:var(--fs-meta);color:var(--fg-3);line-height:1.5}
.crail-foot{padding:4px 6px 2px;font-size:var(--fs-micro);color:var(--fg-3);line-height:1.45}
button.conv-open{
  appearance:none;border:0;background:none;padding:0;margin:0;cursor:pointer;
  font:inherit;font-size:var(--fs-sm);color:inherit;text-align:left;
  flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}
button.conv-open:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:6px}

.thread{
  border-radius:var(--r-4);overflow:hidden;display:flex;flex-direction:column;
  min-width:0;min-height:0;
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
.msgs{
  flex:1;overflow:auto;padding:var(--sp-6) clamp(14px,2vw,26px) var(--sp-6);
  display:flex;flex-direction:column;gap:var(--sp-5);scroll-behavior:smooth;
}
.msg{display:grid;grid-template-columns:30px minmax(0,1fr);gap:13px;animation:msgIn var(--d-4) var(--e-enter)}
@keyframes msgIn{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:none}}
.msg-avatar{
  width:30px;height:30px;border-radius:var(--r-1);display:grid;place-items:center;
  background:var(--glass-3);border:1px solid var(--line-1);box-shadow:var(--sh-1), var(--sh-inset);
  color:var(--accent);margin-top:1px;
}
.msg-avatar svg{width:15px;height:15px}
.msg.user .msg-avatar{color:var(--fg-2)}
.msg.err .msg-avatar{color:var(--bad)}
.msg-body{min-width:0;max-width:78ch}
.msg-head{display:flex;align-items:baseline;gap:9px;margin-bottom:6px}
.msg-who{font-size:var(--fs-meta);font-weight:620;letter-spacing:-.006em}
.msg-time{font-size:var(--fs-micro);color:var(--fg-3);font-variant-numeric:tabular-nums}
.msg .bub{font-size:var(--fs-body);line-height:1.7;overflow-wrap:anywhere;min-width:0}
.msg.assistant .bub{padding:0 2px}
/* The user's own words sit on solid accent glass, lit from the top edge, so
   the bubble reads as an object floating over the transcript rather than a
   tinted label. White on the accent fill holds ~5:1 in both schemes. */
.msg.user .bub{
  display:inline-block;max-width:min(100%,72ch);
  background:linear-gradient(180deg,color-mix(in srgb,var(--accent-fill) 88%,#fff),var(--accent-fill));
  color:var(--accent-fg);
  border:1px solid color-mix(in srgb,var(--accent-fill) 86%,#000);
  border-radius:var(--r-3);border-top-left-radius:var(--r-1);
  padding:11px 15px;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.22), 0 8px 24px -12px var(--acc-ring);
  -webkit-backdrop-filter:blur(var(--blur-1)) saturate(var(--sat-1));
  backdrop-filter:blur(var(--blur-1)) saturate(var(--sat-1));
}
.msg.user .bub ::selection{background:rgba(255,255,255,.32)}
.msg.user .bub code{background:rgba(255,255,255,.18);border-color:transparent;color:inherit}
.msg.err .bub{
  background:var(--bad-bg);border:1px solid var(--bad-line);color:var(--bad);
  border-radius:var(--r-3);border-top-left-radius:var(--r-1);padding:11px 15px;
}
.msg .bub p{margin:0 0 10px}
.msg .bub p:last-child{margin-bottom:0}
.msg .bub .mdh{font-weight:620;font-size:17.5px;letter-spacing:-.016em;margin:17px 0 7px;line-height:1.35}
.msg .bub .mdh:first-child{margin-top:0}
.msg .bub pre{
  margin:11px 0;padding:12px 14px;border-radius:var(--r-2);overflow:auto;
  background:var(--code-bg);border:1px solid var(--line-1);
  font-family:var(--mono);font-size:var(--fs-meta);line-height:1.6;scrollbar-width:thin;
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
/* Streaming caret: a soft bar that pulses while tokens arrive. */
.cursor{
  display:inline-block;width:7px;height:16px;margin-left:3px;border-radius:2px;
  background:var(--accent);background:linear-gradient(180deg,var(--accent),
    color-mix(in srgb,var(--accent) 55%,transparent));
  vertical-align:text-bottom;animation:blink 1.05s var(--e-inout) infinite;
}
@keyframes blink{0%,100%{opacity:.92}50%{opacity:.16}}
.thread-anchor{display:flex;align-items:center;gap:10px;font-size:var(--fs-micro);color:var(--fg-3);padding-top:2px}
.thread-anchor::before,.thread-anchor::after{content:"";height:1px;flex:1;background:var(--line-1)}

.tobottom{
  position:absolute;right:18px;bottom:16px;z-index:var(--z-panel);
  width:38px;height:38px;border-radius:var(--r-full);cursor:pointer;
  display:grid;place-items:center;color:var(--fg);
  background:var(--glass-5);border:1px solid var(--line-2);
  -webkit-backdrop-filter:blur(var(--blur-3)) saturate(var(--sat-3));
  backdrop-filter:blur(var(--blur-3)) saturate(var(--sat-3));
  box-shadow:var(--sh-3), var(--sh-inset);
  opacity:0;pointer-events:none;transform:translateY(6px) scale(.96);
  transition:opacity var(--d-2) var(--e-out),transform var(--d-3) var(--e-spring),
             background-color var(--d-2) var(--e-out);
}
.tobottom svg{width:16px;height:16px}
.tobottom.show{opacity:1;pointer-events:auto;transform:none}
.tobottom:hover{background:var(--glass-6);transform:translateY(-1px)}

/* Composer — one well holding the field, the meta line and the actions. */
.composer{padding:var(--sp-3) var(--sp-4) var(--sp-4);border-top:1px solid var(--line-1);
  display:flex;flex-direction:column;gap:8px;
  background:linear-gradient(180deg, transparent, var(--fill-1))}
.composer .well{
  border-radius:var(--r-3);border:1px solid var(--line-1);background:var(--field);
  padding:11px 12px 9px;display:flex;flex-direction:column;gap:8px;
  box-shadow:var(--sh-well);
  transition:border-color var(--d-2) var(--e-out),box-shadow var(--d-2) var(--e-out),
             background-color var(--d-2) var(--e-out);
}
.composer .well:focus-within{border-color:var(--accent);background:var(--field-focus);
  box-shadow:0 0 0 3.5px var(--acc-ring), var(--sh-well)}
.composer textarea{
  width:100%;min-height:26px;max-height:220px;resize:none;padding:0;border:0;background:none;
  font-size:var(--fs-body);line-height:1.6;box-shadow:none;
}
.composer textarea:focus{box-shadow:none;background:none}
.composer .well-foot{display:flex;align-items:center;gap:var(--sp-2);flex-wrap:wrap;min-width:0}
.composer .well-foot .spacer{flex:1}
.composer-under{display:flex;align-items:center;gap:var(--sp-3);flex-wrap:wrap;padding:0 2px}
.composer .hint{font-size:var(--fs-micro);color:var(--fg-3);
  display:flex;align-items:center;gap:6px}
.composer .hint kbd{font-size:10.5px}
.chat-footnote{font-size:var(--fs-meta);color:var(--fg-3);line-height:1.6;max-width:86ch;padding:0 2px}

/* Empty states — icon, title, one line, one action. */
.empty{
  margin:auto;text-align:center;max-width:46ch;padding:var(--sp-7) var(--sp-4);
  display:flex;flex-direction:column;align-items:center;gap:4px;
  animation:panelIn var(--d-3) var(--e-enter);
}
.empty-icon{
  width:54px;height:54px;border-radius:var(--r-4);display:grid;place-items:center;
  color:var(--accent);background:var(--glass-3);border:1px solid var(--line-1);
  box-shadow:var(--sh-2), var(--sh-inset);margin-bottom:var(--sp-3);
}
.empty-icon svg{width:25px;height:25px}
.empty h4{font-size:var(--fs-lead);font-weight:600;letter-spacing:-.014em;color:var(--fg)}
.empty p{font-size:var(--fs-sm);color:var(--fg-2);line-height:1.62;margin-top:2px}
.empty .btn{margin-top:var(--sp-4)}
.chips{display:flex;gap:var(--sp-2);flex-wrap:wrap;justify-content:center;margin-top:var(--sp-4)}
.chip{
  position:relative;appearance:none;cursor:pointer;font-family:inherit;
  font-size:var(--fs-meta);font-weight:520;color:var(--fg-2);
  background:var(--glass-2);border:1px solid var(--line-1);border-radius:var(--r-full);
  padding:8px 14px;box-shadow:var(--sh-1), var(--sh-inset);
  transition:color var(--d-1) var(--e-out),background-color var(--d-1) var(--e-out),
             border-color var(--d-1) var(--e-out),transform var(--d-2) var(--e-spring),
             box-shadow var(--d-2) var(--e-out);
}
.chip:hover{color:var(--accent);background:var(--glass-3);border-color:var(--acc-line);
  transform:translateY(-1.5px);box-shadow:var(--sh-2), var(--sh-inset)}
.chip:active{transform:translateY(0) scale(.98)}

/* ── 8. Data ─────────────────────────────────────────────────────────────── */
.chart{border-radius:var(--r-4);padding:var(--sp-4) var(--sp-4) var(--sp-3);display:flex;flex-direction:column;gap:var(--sp-3)}
.chart-head{display:flex;align-items:baseline;gap:var(--sp-3);flex-wrap:wrap}
.chart-head h3{font-size:var(--fs-sm);font-weight:620;letter-spacing:-.01em}
.chart-head .sub{font-size:var(--fs-meta);color:var(--fg-3)}
.chart-head .spacer{flex:1}
.chart-legend{display:flex;align-items:center;gap:6px;font-size:var(--fs-micro);color:var(--fg-3)}
.chart-legend .swatch{width:9px;height:9px;border-radius:3px;background:var(--accent-fill);opacity:.72}
.plot{position:relative;padding-left:44px;padding-bottom:20px;min-height:150px}
.gridlines{position:absolute;inset:0 0 20px 44px;display:flex;flex-direction:column;
  justify-content:space-between;pointer-events:none}
.gridlines span{position:relative;border-top:1px solid var(--line-1);flex:0 0 auto}
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
  width:100%;border-radius:7px 7px 3px 3px;
  background:var(--accent);opacity:.72;
  background:linear-gradient(180deg, color-mix(in srgb,var(--accent-fill) 80%,transparent),
             color-mix(in srgb,var(--accent-fill) 38%,transparent));
  opacity:1;border:1px solid var(--acc-line);border-bottom:0;
  height:max(3px,var(--h));transform-origin:bottom;
  animation:barGrow var(--d-5) var(--e-enter) backwards;animation-delay:calc(var(--i,0) * 34ms);
  transition:filter var(--d-2) var(--e-out),box-shadow var(--d-2) var(--e-out);
}
@keyframes barGrow{from{transform:scaleY(.02);opacity:.4}to{transform:scaleY(1);opacity:1}}
.bar:hover .fill,.bar:focus-visible .fill{filter:brightness(1.12);box-shadow:0 0 0 1px var(--acc-line)}
.bar .xlab{
  position:absolute;left:50%;transform:translateX(-50%);bottom:-20px;white-space:nowrap;
  font-family:var(--mono);font-size:10px;color:var(--fg-3);
}
.bar[data-tip]::after{bottom:calc(100% + 6px)}
/* Edge bars anchor their tooltip inward, so it never clips at the pane's
   edge (the pane is a scroll container with overflow-x hidden). */
.bar:first-child[data-tip]::after,.bar:nth-child(2)[data-tip]::after{
  left:0;transform:translate(0,3px) scale(.98);transform-origin:bottom left;
}
.bar:first-child[data-tip]:hover::after,.bar:first-child[data-tip]:focus-visible::after,
.bar:nth-child(2)[data-tip]:hover::after,.bar:nth-child(2)[data-tip]:focus-visible::after{transform:translate(0,0) scale(1)}
.bar:nth-last-child(-n+2)[data-tip]::after{left:auto;right:0;transform:translate(0,3px) scale(.98);transform-origin:bottom right}
.bar:nth-last-child(-n+2)[data-tip]:hover::after,.bar:nth-last-child(-n+2)[data-tip]:focus-visible::after{transform:translate(0,0) scale(1)}
/* The busiest bucket is labelled in place, so the chart answers its own
   question without a hover. */
.bar .peak{
  position:absolute;left:50%;transform:translateX(-50%);
  bottom:clamp(13px, calc(var(--h) + 8px), calc(100% - 24px));
  padding:1px 7px;border-radius:var(--r-full);white-space:nowrap;
  font-family:var(--mono);font-size:10px;color:var(--accent);
  background:var(--acc-bg);border:1px solid var(--acc-line);
  animation:peakIn var(--d-4) var(--e-enter) backwards;
  animation-delay:calc(var(--i,0) * 34ms + 120ms);
}
@keyframes peakIn{from{opacity:0;transform:translate(-50%,6px)}to{opacity:1;transform:translate(-50%,0)}}
.chart-x{display:flex;align-items:center;gap:6px;font-size:var(--fs-micro);color:var(--fg-3);padding-left:44px}

/* Model cards — one idea each, filterable, never generic boxes. */
.model-card{display:flex;flex-direction:column;gap:9px;padding:15px 15px 14px;grid-column:span 1;min-width:0}
.model-card[hidden]{display:none}
.model-top{display:flex;align-items:flex-start;gap:8px;min-width:0}
.model-id{
  flex:1 1 auto;min-width:0;background:none;border:0;padding:0;
  font-family:var(--mono);font-size:var(--fs-sm);font-weight:560;letter-spacing:-.012em;
  overflow-wrap:break-word;
}
.model-tags{display:flex;align-items:center;gap:6px;flex-wrap:wrap;min-width:0}
.model-tags .tag{white-space:normal;line-height:1.35;padding-top:3px;padding-bottom:3px}
.model-desc{font-size:var(--fs-meta);color:var(--fg-2);line-height:1.58}
.model-card .hintline{font-size:var(--fs-micro);color:var(--fg-3);margin-top:2px}

/* Copy chips — small, tactile, and they answer with a drawn check. */
button.copy{
  appearance:none;cursor:pointer;flex:0 0 auto;
  font-family:inherit;font-size:var(--fs-micro);font-weight:600;color:var(--fg-2);
  background:var(--fill-1);border:1px solid var(--line-1);border-radius:var(--r-full);
  padding:3px 10px;white-space:nowrap;
  transition:color var(--d-1) var(--e-out),background-color var(--d-1) var(--e-out),
             border-color var(--d-1) var(--e-out),transform var(--d-1) var(--e-out);
}
button.copy .lbl{display:inline-flex;align-items:center;gap:5px}
button.copy:hover{color:var(--accent);background:var(--acc-bg);border-color:var(--acc-line)}
button.copy:active{transform:scale(.95)}
button.copy.done{color:var(--ok);background:var(--ok-bg);border-color:var(--ok-line)}
button.copy.done .lbl::before{
  content:"";width:11px;height:11px;flex:0 0 auto;background:currentColor;
  -webkit-mask:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='M4.5 12.5 9.6 17.6 19.5 6.8' fill='none' stroke='%23000' stroke-width='2.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") center/contain no-repeat;
  mask:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='M4.5 12.5 9.6 17.6 19.5 6.8' fill='none' stroke='%23000' stroke-width='2.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") center/contain no-repeat;
  animation:tickDraw var(--d-3) var(--e-out) both;
}
@keyframes tickDraw{
  from{clip-path:inset(0 100% 0 0);transform:scale(.86)}
  to{clip-path:inset(0 0 0 0);transform:none}
}
td .copy{margin-top:1px}

/* Footer — a quiet sign-off inside the workspace. */
footer.foot{padding:var(--sp-6) 2px 0;font-size:var(--fs-meta);color:var(--fg-3)}
footer.foot #foot{display:flex;align-items:center;gap:var(--sp-3);flex-wrap:wrap}
footer.foot #foot>span{min-width:0}
.dot-sep{flex:0 0 auto;display:inline-block;width:3px;height:3px;border-radius:50%;background:var(--fill-3)}
/* ── 9. Overlays ─────────────────────────────────────────────────────────── */
/* Dialog 1 · backdrop: the app recedes — blur, dim, no colour. */
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
/* Dialog 2 · the object: rises, settles, and the content fades in behind it. */
.sheet{
  width:min(560px,100%);max-height:min(86vh,760px);
  border-radius:var(--r-6);display:flex;flex-direction:column;min-height:0;
  animation:sheetIn var(--d-4) var(--e-spring) both;outline:none;
}
.veil.is-closing .sheet{animation:sheetOut var(--d-2) var(--e-inout) both}
@keyframes sheetIn{from{opacity:0;transform:translateY(16px) scale(.962)}to{opacity:1;transform:none}}
@keyframes sheetOut{from{opacity:1;transform:none}to{opacity:0;transform:translateY(6px) scale(.986)}}
.sheet-head{
  display:flex;align-items:center;gap:var(--sp-3);padding:var(--sp-4) var(--sp-5) var(--sp-3);
  border-bottom:1px solid var(--line-1);
}
.sheet-head h3{font-size:var(--fs-h3);font-weight:600;letter-spacing:-.016em;flex:1;min-width:0}
.sheet-head .sub{display:block;font-size:var(--fs-meta);color:var(--fg-3);font-weight:440;margin-top:3px}
.sheet-body{padding:var(--sp-4) var(--sp-5);overflow:auto;display:flex;flex-direction:column;
  gap:var(--sp-3);min-height:0;animation:contentIn var(--d-4) var(--e-out) both;animation-delay:60ms}
@keyframes contentIn{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}
.sheet--sm{width:min(430px,100%)}

/* Command palette — a floating search object, not a page. */
.palette{
  width:min(620px,100%);border-radius:var(--r-5);overflow:hidden;
  display:flex;flex-direction:column;
  animation:sheetIn var(--d-3) var(--e-spring) both;
}
.pal-input{display:flex;align-items:center;gap:11px;padding:15px 18px;border-bottom:1px solid var(--line-1)}
.pal-input svg{width:18px;height:18px;color:var(--fg-3)}
.pal-input input{
  flex:1;min-width:0;border:0;background:none;box-shadow:none;padding:0;
  font-size:var(--fs-lead);letter-spacing:-.014em;
}
.pal-input input:focus{box-shadow:none;background:none}
.pal-list{max-height:min(52vh,420px);overflow:auto;padding:8px;display:flex;flex-direction:column;gap:2px}
.pal-group{padding:10px 10px 5px;font-size:var(--fs-micro);font-weight:640;color:var(--fg-3);
  text-transform:uppercase;letter-spacing:var(--track-wide)}
.pal-item{
  position:relative;appearance:none;border:1px solid transparent;background:none;
  cursor:pointer;width:100%;
  display:flex;align-items:center;gap:11px;padding:9px 11px;border-radius:var(--r-2);
  font-size:var(--fs-sm);color:var(--fg);text-align:left;
  transition:background-color var(--d-1) var(--e-out),border-color var(--d-1) var(--e-out),
             transform var(--d-2) var(--e-glide);
}
.pal-item .ico{width:17px;height:17px;color:var(--fg-3);flex:0 0 17px}
.pal-item .t{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pal-item .kbd{font-size:10.5px;color:var(--fg-3)}
.pal-item[aria-selected="true"]{background:var(--acc-bg);border-color:var(--acc-line);transform:translateX(2px)}
.pal-item[aria-selected="true"] .ico{color:var(--accent)}
.pal-empty{padding:28px 16px;text-align:center;color:var(--fg-3);font-size:var(--fs-sm)}
.pal-foot{display:flex;align-items:center;gap:14px;padding:9px 18px;border-top:1px solid var(--line-1);
  font-size:var(--fs-micro);color:var(--fg-3);background:var(--fill-1)}
.pal-foot span{display:inline-flex;align-items:center;gap:5px}

/* Shortcut list (help dialog) — rows, not cards. */
.keys{display:grid;gap:2px}
.key-row{display:flex;align-items:center;gap:var(--sp-3);padding:8px 10px;border-radius:var(--r-2)}
.key-row:nth-child(odd){background:var(--fill-1)}
.key-row .t{flex:1;font-size:var(--fs-sm);color:var(--fg-2)}
.key-row .ks{display:flex;gap:5px;flex-wrap:wrap;justify-content:flex-end}

/* Toasts — small floating objects with a timer you can read. */
.toasts{
  position:fixed;z-index:var(--z-toast);right:22px;bottom:22px;
  display:flex;flex-direction:column;gap:10px;align-items:flex-end;
  pointer-events:none;max-width:min(400px,calc(100vw - 32px));
}
.toast{
  pointer-events:auto;position:relative;overflow:hidden;
  display:flex;align-items:flex-start;gap:11px;
  padding:12px 14px 13px;border-radius:var(--r-3);min-width:250px;max-width:100%;
  animation:toastIn var(--d-4) var(--e-spring) both;
}
.toast.is-closing{animation:toastOut var(--d-2) var(--e-out) both}
@keyframes toastIn{from{opacity:0;transform:translateY(14px) scale(.96)}to{opacity:1;transform:none}}
@keyframes toastOut{to{opacity:0;transform:translateY(6px) scale(.98)}}
.toast .ico{width:18px;height:18px;margin-top:1px;color:var(--accent);flex:0 0 18px}
.toast--ok .ico{color:var(--ok)}
.toast--warn .ico{color:var(--warn)}
.toast--bad .ico{color:var(--bad)}
.toast .txt{flex:1;min-width:0;font-size:var(--fs-sm);line-height:1.5}
.toast .txt strong{display:block;font-weight:620;margin-bottom:1px}
.toast .txt .sub{color:var(--fg-2);font-size:var(--fs-meta)}
.toast .x{
  appearance:none;border:0;background:none;color:var(--fg-3);cursor:pointer;
  width:24px;height:24px;border-radius:6px;display:grid;place-items:center;flex:0 0 24px;
  transition:background-color var(--d-1) var(--e-out),color var(--d-1) var(--e-out);
}
.toast .x:hover{background:var(--fill-2);color:var(--fg)}
.toast .x svg{width:13px;height:13px}
.toast-timer{
  position:absolute;left:0;right:0;bottom:0;height:2px;transform-origin:left;
  background:currentColor;opacity:.30;
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
/* Skeletons breathe instead of shimmering: enough motion to read as "working",
   never a light show. Shapes match the content they stand in for. */
.sk{position:relative;overflow:hidden;border-radius:6px;background:var(--fill-2);color:transparent;
  animation:skBreathe 1.9s var(--e-inout) infinite}
.sk::after{
  content:"";position:absolute;inset:0;
  background:linear-gradient(90deg,transparent,var(--fill-1),transparent);
  transform:translateX(-100%);animation:skSweep 2.4s var(--e-inout) infinite;
}
@keyframes skBreathe{0%,100%{opacity:.72}50%{opacity:1}}
@keyframes skSweep{0%{transform:translateX(-100%)}60%,100%{transform:translateX(100%)}}
.sk--line{height:11px}
.sk--val{height:16px;margin-top:4px;width:70%}
.sk-tile{pointer-events:none}
.sk-tile:hover{transform:none;background:var(--glass-1);box-shadow:var(--sh-1), var(--sh-inset)}

/* Utilities */
.spacer{flex:1}
.stack{display:flex;flex-direction:column;gap:var(--sp-4)}
.row{display:flex;align-items:center;gap:var(--sp-2);flex-wrap:wrap}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
.note{font-size:var(--fs-meta);color:var(--fg-3);line-height:1.58}
.note strong{color:var(--fg-2);font-weight:600}
.nowrap{white-space:nowrap}
::-webkit-scrollbar{width:11px;height:11px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--fill-2);border-radius:var(--r-full);
  border:3px solid transparent;background-clip:content-box}
::-webkit-scrollbar-thumb:hover{background:var(--fill-3);background-clip:content-box;border:3px solid transparent}
main.content, .scroller, .crail-list, .pal-list, .msgs{scrollbar-color:var(--fill-2) transparent}

/* ── 11. Responsive ──────────────────────────────────────────────────────── */
@media (max-width:1240px){
  :root{--rail:214px}
  .chat{grid-template-columns:220px minmax(0,1fr)}
}
@media (max-width:1080px){
  :root{--rail:196px}
  .tb-context{display:none}
}

/* Tablet and below: navigation becomes a floating, thumb-reachable bar and the
   rail's own furniture moves into the topbar. Same DOM, repositioned. */
@media (max-width:980px){
  .app{padding:10px 12px 0;gap:10px}
  .topbar{padding:7px 9px}
  .topbar.is-scrolled{padding:6px 9px}
  .tb-context{display:flex;opacity:1;transform:none;pointer-events:auto;
    padding-left:12px;margin-left:2px}
  .body{grid-template-columns:minmax(0,1fr);gap:0}
  .railwrap{
    position:fixed;left:0;right:0;bottom:0;z-index:var(--z-chrome);height:auto;
    padding:0 10px calc(10px + env(safe-area-inset-bottom,0px));
    pointer-events:none;background:none;
  }
  .rail{
    position:relative;inset:auto;height:auto;flex-direction:row;align-items:center;
    padding:6px;border-radius:var(--r-5);max-width:560px;margin:0 auto;
    pointer-events:auto;
  }
  .rail-group,.rail-foot{display:none}
  nav.tabs{flex:1;flex-direction:row;justify-content:space-between;gap:1px;padding:2px}
  .tab-btn{
    flex:1 1 0;flex-direction:column;gap:4px;padding:7px 4px 6px;
    font-size:10.5px;text-align:center;align-items:center;min-width:0;
  }
  .tab-btn .ico{width:19px;height:19px;opacity:.8}
  .tab-btn .lbl{max-width:100%;overflow:hidden;text-overflow:ellipsis}
  .tab-btn .navkbd{display:none}
  main.content{
    padding:clamp(14px,3vw,22px) clamp(12px,3vw,20px) calc(104px + env(safe-area-inset-bottom,0px));
    border-radius:var(--r-4) var(--r-4) 0 0;
  }
  .chat{grid-template-columns:minmax(0,1fr);gap:var(--sp-3);
    height:clamp(420px, calc(100dvh - 340px), 780px)}
  /* The conversation rail becomes a horizontal strip: no half-empty column. */
  .crail{padding:0;background:none;border:0;box-shadow:none;
    -webkit-backdrop-filter:none;backdrop-filter:none;overflow:visible}
  .crail-head{display:flex;flex-direction:row-reverse;align-items:center;justify-content:flex-end;
    gap:var(--sp-2);padding:0}
  .crail-head .lbl{display:flex;align-items:center;gap:7px;min-height:36px;padding:0 13px;
    border-radius:var(--r-full);background:var(--fill-1);border:1px solid var(--line-1);
    font-size:var(--fs-meta);font-weight:560;color:var(--fg-2);white-space:nowrap;
    text-transform:none;letter-spacing:0}
  .crail-head .btn{width:36px;min-height:36px;border-radius:var(--r-full);border-color:var(--line-1)}
  .crail-list{flex-direction:row;gap:var(--sp-2);overflow-x:auto;overflow-y:hidden;
    padding:0 2px 2px;scroll-snap-type:x proximity}
  .crail-list:empty{display:none}
  .conv{flex:0 0 auto;max-width:230px;scroll-snap-align:start;
    background:var(--fill-1);border-color:var(--line-1)}
  .conv.sel::before{display:none}
  .conv .x{opacity:1}
  .crail-foot,.rail-empty{display:none}
  .thread{min-height:0}
  .thread-bar{padding:9px var(--sp-3)}
  .msgs{padding:var(--sp-4) var(--sp-3) var(--sp-5);gap:var(--sp-4)}
  .composer{padding:var(--sp-3)}
  .toasts{left:12px;right:12px;bottom:calc(96px + env(safe-area-inset-bottom,0px));
    align-items:stretch;max-width:none}
  .toast{width:100%;min-width:0}
}

@media (max-width:760px){
  .phead{align-items:flex-start}
  .phead-actions{width:100%;justify-content:flex-start;margin-left:0}
  .tiles{grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}
  /* Tables reflow into labelled blocks: label above value, no sideways scroll. */
  table.stack-rows thead{display:none}
  table.stack-rows,table.stack-rows tbody,table.stack-rows tr,table.stack-rows td{display:block;width:100%}
  table.stack-rows tr{padding:12px 15px;border-bottom:1px solid var(--line-1)}
  table.stack-rows tr:last-child{border-bottom:0}
  table.stack-rows td{border:0;padding:2px 0;display:flex;gap:12px;justify-content:space-between;
    align-items:baseline;text-align:right}
  table.stack-rows td::before{
    content:attr(data-label);flex:0 0 auto;font-size:var(--fs-micro);font-weight:640;
    color:var(--fg-3);text-transform:uppercase;letter-spacing:var(--track-wide);text-align:left;
  }
  table.stack-rows td.code{white-space:normal;overflow-wrap:anywhere}
  table.stack-rows td:empty{display:none}
  .panel,.card{border-radius:var(--r-3)}
  .sheet,.palette{width:100%;border-radius:var(--r-5)}
  .veil{align-items:flex-end;padding:10px}
  .veil--top{align-items:flex-start}
  .plot{padding-left:34px}
  .gridlines{left:34px}
  .gridlines span i{left:-34px;width:30px}
  .chart-x{padding-left:34px}
}
/* Short viewports: the rail gives up its footnote before the nav loses items. */
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
  /* Stack the chat toolbar and give the API-key field a full row: a shrinking
     text field is worse than one extra line. */
  .thread-bar{flex-wrap:wrap;gap:var(--sp-2);padding:10px var(--sp-3)}
  .thread-bar .spacer{display:none}
  .thread-bar select{flex:1 1 46%;min-width:0;max-width:calc(50% - 4px)}
  .thread-bar .switch{flex:0 0 auto;order:3}
  .thread-bar .key-field{order:2;flex:0 0 100%;width:100%}
  .thread-bar input[type="password"]{width:100%;min-width:0;font-size:var(--fs-body);padding-top:9px;padding-bottom:9px}
}
@media (max-width:560px){
  :root{--fs-h1:26px}
  #verPill{display:none}
  .brand-text p{display:none}
  .brand-text h1{font-size:15px}
  .topbar{gap:var(--sp-2)}
  .tiles{grid-template-columns:repeat(auto-fill,minmax(138px,1fr));gap:var(--sp-2)}
  .tile{padding:11px 12px}
  .seg-btn{padding:0 8px}
  .msg{grid-template-columns:26px minmax(0,1fr);gap:10px}
  .msg-avatar{width:26px;height:26px}
  .msg-avatar svg{width:13px;height:13px}
  .msg.user .bub{max-width:100%}
  .composer .hint{display:none}
  .bars{height:110px}
  footer.foot{padding-bottom:0}
}
@media (max-width:470px){
  .tb-context{display:none}
  .searchbtn .lbl{display:none}
  .searchbtn{padding:0 10px}
  .phead p{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
  .sheet-head,.sheet-body{padding-left:var(--sp-4);padding-right:var(--sp-4)}
}
@media (max-width:400px){
  .health{padding:6px 10px 6px 9px}
  .brand-text{display:none}
  .tab-btn{font-size:9.5px;padding:7px 2px 6px}
}

/* Coarse pointers get roomier targets, never smaller type. */
@media (pointer:coarse){
  .tab-btn{padding-top:9px;padding-bottom:8px}
  .btn{min-height:40px}
  .btn--sm{min-height:36px}
  button.copy{min-height:34px;padding:6px 13px}
  .switch .sw{--sw-w:44px; --sw-h:26px; --sw-pad:3px; --sw-travel:18px}
}

/* Short viewports: the rail gives up its footnote before the nav loses items,
   and a landscape phone scrolls the workspace instead of squeezing the thread. */
@media (max-height:520px) and (min-width:981px){
  .rail-foot{display:none}
}
@media (max-height:560px) and (max-width:980px){
  .chat{height:auto}
  .thread{min-height:320px}
}

/* ── 12. Preferences ─────────────────────────────────────────────────────── */
/* Reduced motion: transitions collapse, but every state change stays visible
   (the glider still lands on the active item, counters still update). */
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{
    animation-duration:.01ms !important;
    animation-iteration-count:1 !important;
    transition-duration:.01ms !important;
    scroll-behavior:auto !important;
  }
  .tile,.msg,.bar .fill,.bar .peak,section.tab.active,.toast,.sheet,.veil,.callout{animation:none !important}
  .glider{transition:none !important}
  .dot.ok::after,.dot.warn::after,.dot.bad::after,.pulse::after{display:none}
  .sk,.sk::after{animation:none !important}
}
/* Users who ask for less transparency get calm, near-solid surfaces — one
   triplet per theme, so the rule is written once. */
@media (prefers-reduced-transparency:reduce){
  :root{
    --glass-1:rgba(var(--surface-solid),.95); --glass-2:rgba(var(--surface-solid),.97);
    --glass-3:rgba(var(--surface-solid),.98); --glass-4:rgba(var(--surface-solid),.99);
    --glass-5:rgba(var(--surface-solid),.995); --glass-6:rgb(var(--surface-solid));
    --pane:rgba(var(--surface-solid),.93); --pane-stuck:rgba(var(--surface-solid),.97);
    --field:rgba(var(--surface-solid),.88); --field-hover:rgba(var(--surface-solid),.94);
    --field-focus:rgb(var(--surface-solid));
  }
  .canvas-glow{display:none}
}
/* Higher contrast: firmer hairlines, stronger ink, opaque surfaces. Values are
   mixed from the theme's own ink so both schemes are covered by one rule; the
   plain declarations above each mix are the fallback where color-mix() is
   unsupported (the later declaration is dropped, the earlier one stands). */
@media (prefers-contrast:more){
  :root{
    --line-1:rgba(15,20,33,.26);
    --line-1:color-mix(in srgb, var(--fg) 22%, transparent);
    --line-2:rgba(15,20,33,.38);
    --line-2:color-mix(in srgb, var(--fg) 34%, transparent);
    --line-3:rgba(15,20,33,.52);
    --line-3:color-mix(in srgb, var(--fg) 48%, transparent);
    --fg-2:#3a424f;
    --fg-2:color-mix(in srgb, var(--fg) 74%, var(--bg));
    --fg-3:#4a5361;
    --fg-3:color-mix(in srgb, var(--fg) 62%, var(--bg));
    --glass-1:rgba(var(--surface-solid),.94); --glass-2:rgba(var(--surface-solid),.96);
    --glass-3:rgba(var(--surface-solid),.97); --glass-4:rgb(var(--surface-solid));
    --pane:rgba(var(--surface-solid),.9);
  }
}
@media print{
  .topbar,.railwrap,.toasts,.canvas{display:none !important}
  body{height:auto;overflow:visible}
  main.content{overflow:visible;border:0;box-shadow:none;background:none;padding:0}
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
        <span class="rail-group">Console</span>
        <nav class="tabs" id="tabs" role="tablist" aria-label="Console sections">
          <span class="glider" aria-hidden="true"></span>
          <button class="tab-btn" type="button" role="tab" id="tabbtn-chat" data-tab="chat" aria-keyshortcuts="Meta+1 Control+1"
                  aria-selected="true" aria-controls="tab-chat" tabindex="0"
                  data-tip="Talk to the models" data-tip-pos="right">
            <svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M20.5 12.1c0 3.7-3.8 6.7-8.5 6.7-1 0-2-.14-2.9-.4L4.6 19.8l1.1-3.3c-1.3-1.2-2.2-2.7-2.2-4.4 0-3.7 3.8-6.7 8.5-6.7s8.5 3 8.5 6.7Z"/>
            </svg>
            <span class="lbl">Chat</span>
            <kbd class="navkbd" aria-hidden="true">1</kbd>
          </button>
          <button class="tab-btn" type="button" role="tab" id="tabbtn-status" data-tab="status" aria-keyshortcuts="Meta+2 Control+2"
                  aria-selected="false" aria-controls="tab-status" tabindex="-1"
                  data-tip="Live status and metrics" data-tip-pos="right">
            <svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M3 13h3.7l2-5.4 3.3 9.8 2.2-6.5 1.6 3.4H21"/>
            </svg>
            <span class="lbl">Status</span>
            <kbd class="navkbd" aria-hidden="true">2</kbd>
          </button>
          <button class="tab-btn" type="button" role="tab" id="tabbtn-activity" data-tab="activity" aria-keyshortcuts="Meta+3 Control+3"
                  aria-selected="false" aria-controls="tab-activity" tabindex="-1"
                  data-tip="Recent requests" data-tip-pos="right">
            <svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M4 6.6h16M4 12h11.5M4 17.4h7.5"/>
            </svg>
            <span class="lbl">Activity</span>
            <kbd class="navkbd" aria-hidden="true">3</kbd>
          </button>
          <button class="tab-btn" type="button" role="tab" id="tabbtn-models" data-tab="models" aria-keyshortcuts="Meta+4 Control+4"
                  aria-selected="false" aria-controls="tab-models" tabindex="-1"
                  data-tip="Available models" data-tip-pos="right">
            <svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <rect x="3.6" y="3.6" width="7.2" height="7.2" rx="2"/><rect x="13.2" y="3.6" width="7.2" height="7.2" rx="2"/>
              <rect x="3.6" y="13.2" width="7.2" height="7.2" rx="2"/><rect x="13.2" y="13.2" width="7.2" height="7.2" rx="2"/>
            </svg>
            <span class="lbl">Models</span>
            <kbd class="navkbd" aria-hidden="true">4</kbd>
          </button>
          <button class="tab-btn" type="button" role="tab" id="tabbtn-api" data-tab="api" aria-keyshortcuts="Meta+5 Control+5"
                  aria-selected="false" aria-controls="tab-api" tabindex="-1"
                  data-tip="Endpoints and client setup" data-tip-pos="right">
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
          <span class="rail-note">Transcripts stay in this browser.</span>
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
            <p>Send a prompt straight through this server and watch it arrive token by token.
               Transcripts live in this browser; the upstream is single-turn, so every turn resends the conversation.</p>
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
            <p class="crail-foot">Saved in this browser only. Nothing is stored server-side.</p>
          </aside>

          <div class="thread glass-2">
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

        <p class="chat-footnote">Very long transcripts cost more tokens upstream and can eventually exceed what
          the upstream accepts — clearing or starting a new chat keeps things fast.</p>
      </section>

      <!-- ── Status ────────────────────────────────────────────────────────── -->
      <section class="tab" id="tab-status" role="tabpanel" aria-labelledby="tabbtn-status" tabindex="-1">
        <div class="phead">
          <div class="phead-text">
            <h2>Status</h2>
            <p>What the backend is doing right now: the configuration it loaded, the checks it ran
               at startup, live metrics and the behaviour of each cookie in the pool.</p>
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
        <div class="callout-list" id="statusWarn"></div>

        <h3 class="slabel">Runtime</h3>
        <div class="tiles" id="runtime"></div>

        <h3 class="slabel">Health checks</h3>
        <div class="callout-list" id="checks"></div>

        <h3 class="slabel">Accounts</h3>
        <div id="accountsNote"></div>
        <div class="tablewrap glass-2 panel--flush">
          <div class="scroller">
            <table class="stack-rows">
              <caption class="sr-only">Configured cookie files and their current state</caption>
              <thead><tr><th>Account</th><th>Google index</th><th>SAPISID</th><th>Uses</th><th>State</th></tr></thead>
              <tbody id="accounts"></tbody>
            </table>
          </div>
        </div>

        <h3 class="slabel">Counters</h3>
        <div class="tiles" id="counters"></div>

        <h3 class="slabel">Latency</h3>
        <div class="tiles" id="latency"></div>
        <div class="chart glass-2" id="latencyChart"></div>

        <h3 class="slabel">By model</h3>
        <div class="tablewrap glass-2 panel--flush">
          <div class="scroller">
            <table class="stack-rows">
              <caption class="sr-only">Upstream calls per model</caption>
              <thead><tr><th>Model</th><th>Requests</th><th>Average</th><th>Slowest</th></tr></thead>
              <tbody id="byModel"></tbody>
            </table>
          </div>
        </div>

        <h3 class="slabel">Status codes</h3>
        <div class="tablewrap glass-2 panel--flush">
          <div class="scroller">
            <table class="stack-rows">
              <caption class="sr-only">Responses by status code</caption>
              <thead><tr><th>Code</th><th>Count</th></tr></thead>
              <tbody id="byStatus"></tbody>
            </table>
          </div>
        </div>

        <h3 class="slabel">Configuration (redacted)</h3>
        <div class="tablewrap glass-2 panel--flush">
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
        <div class="tablewrap glass-2 panel--flush">
          <div class="scroller">
            <table class="stack-rows">
              <caption class="sr-only">HTTP endpoints</caption>
              <thead><tr><th>Method</th><th>Path</th><th>Purpose</th></tr></thead>
              <tbody id="endpoints"></tbody>
            </table>
          </div>
        </div>

        <h3 class="slabel">Client configuration</h3>
        <div class="tablewrap glass-2 panel--flush">
          <div class="scroller">
            <table class="stack-rows">
              <caption class="sr-only">Values to configure in a client</caption>
              <thead><tr><th>Field</th><th>Value</th><th><span class="sr-only">Copy</span></th></tr></thead>
              <tbody id="clientCfg"></tbody>
            </table>
          </div>
        </div>

        <h3 class="slabel">curl</h3>
        <div class="panel glass-2 panel--flush">
          <div class="panel-head">
            <h3>Copy-ready examples</h3>
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
    setProgress('statusProgress', false, {btn: 'refreshStatus', busy: 'tab-status'});
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
  // The busiest bucket is labelled in place: the chart answers its own
  // question, and the tooltip still carries every exact count.
  const peak = values.indexOf(max);
  box.innerHTML =
    '<div class="chart-head"><h3>Upstream latency</h3>' +
    '<span class="sub">' + esc((samples || total)) + ' samples &middot; slowest bucket ' + esc(fmtCount(max)) + '</span>' +
    '<span class="spacer"></span>' +
    '<span class="chart-legend"><span class="swatch"></span>requests per upper bound</span></div>' +
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


def render_dashboard(state):
    """Return the dashboard HTML with the live state injected."""
    payload = json.dumps(state, ensure_ascii=False, default=str)
    # Prevent an early </script> in any interpolated value from breaking out.
    payload = payload.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return _DASHBOARD_HTML.replace(_STATE_TOKEN, payload).encode("utf-8")
