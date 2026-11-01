# The console's design system

The dashboard in `gemini_web2api/webui.py` is the only part of this project a
person looks at. It is one HTML string, one stylesheet, one script, served by
`server.py` on `Accept: text/html` — no build step, no dependencies. That
constraint is why it is worth documenting: every rule below lives in that file
and nothing enforces it except the tests in `tests/test_dashboard_ui.py`.

This page records the audit that came before the v4 redesign, the direction that
came out of it, and the tokens and rules that hold the result together. Read it
before changing the stylesheet; if a change contradicts a rule here, change the
rule here too, in the same commit.

## Method

The procedure is adapted from [Unslop](https://github.com/heydamir/unslop) by
Damir Sokolovsky ([@heydamir](https://github.com/heydamir)), licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). In short: find the
slop, set a direction from what the product actually is, then fix it screen by
screen instead of restyling wholesale. The catalogue of tells, the token scales
and the pass order come from that work; the words, the layout and every value
in this document are this project's own.

## Direction

Five lines, decided before any CSS was touched. Everything else follows from
them, and anything that does not serve them was cut.

**Product** — a signal path (an OpenAI-compatible client, a translator, Gemini's
web surface) watched by one operator who wants to know whether it is healthy.

**Character** — anodised, silkscreened, engineered. The page should read like
equipment that has a label plate, not a product site that has a hero.

**World** — a test bench: paper-grey panels, hairline rules, mono readouts,
square status lamps.

**Moments** — two. The latency strip chart on Status, and the chat transcript.
Visual richness is spent there and nowhere else.

**Signature** — every state is a square lamp plus its fact in words; every
figure is mono and tabular.

## What the audit found

Line references are to `gemini_web2api/webui.py` at commit `99175ae`, the last
version before the redesign.

| What the page did | Where | Why it read as generic | What it does now |
|---|---|---|---|
| Five stacked canvas gradients: three tinted radials, a linear ramp, two blue/purple radials on a masked 28px grid | 374–433 | Depth came from decoration, not hierarchy. A tinted wash under translucent surfaces also means no surface has a definite edge | One flat paper grey (`#f1f2ef`). Graph paper survives only inside the chart plot, where it measures something |
| `backdrop-filter: blur(8/14/22/30px) saturate(160–174%)` on the topbar, rail, panels, popovers and toasts | 466–522 | Frosted panels over a tinted wash is the stock "AI dashboard" material; it costs GPU time and buys no legibility | Solid fills. Elevation is a fill step plus a hairline; a shadow appears only where something floats over the page |
| Six near-identical translucent whites (`--glass-1` … `--glass-6`) | token block | Six materials that differ by 4% alpha read as one blurry material with no ordering | Two visible fills: `#ffffff` for panels and `#fafbf9` for quiet blocks. The six names stay as elevation levels so nothing reintroduces a material |
| A gradient edge-light hairline drawn inside every panel with a mask | 446–452 | A highlight on every edge is a material claim the flat page cannot keep, and it outlined twelve equally-important boxes | Removed. Hairlines define, they never decorate |
| User message bubble with an inner white highlight and a coloured glow (`inset 0 1px 0 rgba(255,255,255,.22)`, `0 8px 24px -12px var(--acc-ring)`) | 1193–1199 | The textbook glass card — and a glow on the transcript competed with the error state that needs to be seen | Solid accent fill, no glow, no inner highlight; errors own the red tint |
| Uppercase, wide-tracked section labels, each with a 5px dot and a 28px rule | 961–971 | Small caps plus tracking on every section is template punctuation. Applied everywhere it also shouts, which is the opposite of structure | Sentence case at 12.5px/600, the count in mono beside it, and a hairline that carries the label |
| `border-radius:999px` on tabs, health, inputs, tags and chips | 512, 571, 593, 641, 1237 | When everything is a pill, shape stops carrying meaning | Radius scale 4/6/8/10/12/14 sized by element. The only `999px` left is the switch knob, which really is a pill |
| Page title at `clamp(32px, 3vw, 42px)` | 175 | A display size borrowed from a marketing page, on a page whose job is a table | 26px/550. The subtitle is the only lede, the actions sit on the same baseline |
| Spring easing and a 1–1.5px lift plus scale on every hoverable thing | 183, 539–543, 661, 1008 | Motion as decoration: the page twitches as you move across it, and overshoot implies a physics nothing shares | One curve, `cubic-bezier(.22,1,.36,1)`, 120–160ms, colour and fill only. Press is a 1px inset shadow |
| A breathing halo behind the status dot (`@keyframes breathe`, scale 2.6) | 591, 676–678 | A halo says "live" while implying a liveness the page does not measure | A square lamp, static, with the state in words next to it |
| Skeletons with a sweeping shimmer | 1533–1545 | A light show for a 200ms wait, and it fakes progress that is not being measured | Static blocks shaped like the content that is coming; the boot placeholder reuses the chart's own empty-state wording |
| Twelve equally weighted panels on Status, each with its own footnote | Status tab | No ranking: the reader has to hunt for the two numbers that matter | Live figures first, then the chart, then counters and accounts, then two paired tables; configuration last, as a spec sheet |
| The same fact in two places (uptime in the topbar and in a tile; streaming and proxy restated as prose) | topbar, Runtime facts, Configuration | Repetition reads as padding, and two copies of one fact drift apart | One home per fact. A footnote appears only where a number needs a caveat |
| Models as a uniform card grid, every card with the same chrome | `#models` | A list pretending to be a gallery; identical boxes make nine different models look interchangeable | One divided column: name and copy action, one line of description, facts right-aligned, tags only where they are facts |

## Type

One UI family (system sans) plus the machine's own mono for everything numeric.
No display face, no letter-spaced headings; sentence case throughout.

| Role | Size / weight | Used for |
|---|---|---|
| `--fs-h1` | 26px / 550 | page title only |
| `--fs-h2` | 20px / 550 | dialog titles |
| `--fs-h3` | 15px / 550 | panel and chart titles |
| `--fs-lead` | 16px / 400 | the one sentence under a page title |
| `--fs-body` | 14px / 400 | prose, table cells |
| `--fs-sm` | 13.5px / 400–500 | controls, list rows |
| `--fs-meta` | 12.5px / 400–550 | labels, footnotes, table heads |
| `--fs-micro` | 11.5px / 400–550 | the smallest fact on screen — never body copy |

Numerals use `font-variant-numeric: tabular-nums` and the mono family wherever a
value can change: counters, latencies, timings, IDs, status codes.

## Colour

Paper grey with a trace of green, not the blue-grey every dashboard starts from.
One steel-blue accent, used for the active state, the chart and links — never for
decoration. States reuse the same three colours everywhere.

| Token | Light | Dark | Role |
|---|---|---|---|
| `--bg` | `#f1f2ef` | `#0e1013` | canvas |
| `--glass-2` | `#ffffff` | `#181b1f` | panels, tables, transcript |
| `--glass-1` | `#fafbf9` | `#15181c` | quiet blocks, inset rows |
| `--fg` | `#15191a` | `#e9ebe8` | primary text |
| `--fg-2` | `#4c5451` | `#a7aeab` | secondary prose |
| `--fg-3` | `#5f6864` | `#8a928e` | labels, footnotes |
| `--accent` | `#0d5d78` | `#7fc9e0` | accent as text |
| `--accent-fill` | `#0f6d8c` | `#8ed2e6` | accent as fill |
| `--ok` / `--warn` / `--bad` | `#1a6b45` / `#7a5200` / `#a92b31` | `#6fcf9b` / `#d9ad63` / `#f0928f` | state text and lamps |

Measured contrast, worst background first (canvas for light, panel for dark):

| Pair | Light | Dark |
|---|---|---|
| `--fg` on canvas | 15.8:1 | 14.4:1 |
| `--fg-2` on canvas | 6.9:1 | 7.6:1 |
| `--fg-3` on canvas | 5.1:1 | 5.4:1 |
| `--accent` on canvas | 6.6:1 | 9.3:1 |
| `--ok` / `--warn` / `--bad` on canvas | 5.8 / 6.2 / 6.1:1 | 9.1 / 8.3 / 7.6:1 |
| `--accent-fg` on `--accent-fill` | 5.9:1 (white on `#0f6d8c`) | 9.8:1 (`#08222b` on `#8ed2e6`) |

New colour tokens must be added to the light block, the dark block **and** the
`prefers-color-scheme: dark` duplicate — `test_the_theme_blocks_define_the_same_colours`
fails otherwise, and the two would drift.

## Space, radius, motion

- **Space** — 4px grid: 4, 8, 12, 16, 20, 24, 32, 40, 48, 64. The 2px step is
  reserved for nudges inside small controls.
- **Radius** — 4 (keycap, tag), 6 (input, row), 8 (button, cell), 10 (menu),
  12 (card), 14 (dialog). A child's radius stays inside its parent's padding.
- **Motion** — one curve, `cubic-bezier(.22,1,.36,1)`; hover and press 120–160ms,
  panel entrance 200–240ms, stagger 40ms. Only colour, fill, opacity, transform
  in the layout's own direction, and height of the chart bars. Nothing else
  animates; no element scales on hover.
- **Icons** — one stroked family, 16/18px at 1.5–1.6px stroke, `currentColor`.
  No emoji, no filled glyphs, no per-state icon sets.

## The two moments

**The latency strip chart** (Status). One bar per bucket, height is the sample
count, the busiest bucket is labelled in place, hovering any bar gives the exact
count and share, and the whole figure is one `role="img"` with a text summary of
every bucket for assistive tech. The plot carries the page's only graph paper.

**The transcript** (Chat). The user's turns are the single place the accent
fills a large area; the model's turns are plain panels. Everything else on the
page is a table, a list or a figure.

The traffic strip under the status rule is the smaller relative of the first: one
tick per recorded request, height for latency, colour for outcome, with "last N
requests" and the error count written beside it. It is hidden when history is
empty rather than shown as an empty frame.

## Screen by screen

- **Chat** — conversations rail, thread, composer. The composer is a well with
  one filled send action; the empty state offers four starting points.
- **Status** — status rule with the live health and the traffic strip; startup
  checks; four live figures; the latency chart; counters; the cookie pool;
  upstream by model and status codes side by side; runtime facts; configuration
  as a spec sheet.
- **Activity** — one table of operational facts, newest first, with a filter and
  a pause control. Query strings are stripped before anything is recorded.
- **Models** — one divided list, search on the page head, copy per row.
- **API** — endpoint table, then the three client snippets a caller actually
  pastes.
- **Overlays** — command palette, shortcuts sheet, toasts. Same fills and
  hairlines as the page, one step higher, and the only place a shadow is large.

The cookie-sync extension's popup (`gemini-cookie-sync-extension/popup.html`)
carries the same tokens at window scale: the window itself is the surface, so
there is no card floating inside it; controls are 36px; the status line is a
square lamp plus words. A popup cannot fetch the console's stylesheet, so it
keeps its own copy of the scale — change both, or the two surfaces drift apart.

## Accessibility and responsiveness

- Every state is a colour **and** a word; nothing is encoded in hue alone.
- Focus is a 2px `--accent` outline at 2px offset, visible in both schemes.
- Live regions: the health line, the toast stack and the status text
  (`aria-live="polite"`), so a refresh is announced without stealing focus.
- The chart and the strip expose text summaries; tables keep their headers;
  every icon-only control carries an `aria-label`.
- `prefers-reduced-motion` removes transitions and the caret blink;
  `prefers-contrast: more` strengthens hairlines and secondary text.
- Layout: rail 224px above 900px, collapsing to a horizontal strip below it;
  page padding 24px → 16px; tiles `auto-fit` from 184px down to 132px; the paired
  tables stack at 900px; the palette becomes a sheet on small screens; no
  horizontal scrolling at 320px.

## How this was validated

`python3 -m unittest discover -q` (the full suite, including
`tests/test_dashboard_ui.py`) plus a jsdom smoke run that boots the page, mocks
`/status` and `/v1/models`, refreshes, switches tabs and opens the palette and
the shortcuts sheet, asserting zero console errors. The stylesheet is parsed with
`css-tree` to catch unknown properties; contrast is measured, not eyeballed. No
headless browser exists in the build environment, so no screenshot is claimed
anywhere in this repository.

## What not to add back

Gradient text or gradient marks; `backdrop-filter`; glows and halos; a pill for
everything; uppercase letter-spaced labels; hover lift or scale; shimmer on
anything that is not actually loading; a new colour per state; a sparkline on
every figure; a badge that says `PRO` or `NEW`; a card per fact.

The project's own rule is the same one it applies to code: if it does not make
the instrument easier to read, it is not a feature.

---

Design procedure adapted from [Unslop](https://github.com/heydamir/unslop) by
Damir Sokolovsky, CC BY 4.0. See [CHANGELOG.md](CHANGELOG.md) for when the
redesign landed and what it touched.
