/* Assertions for the web console's rendering functions.
 *
 * This file is not standalone. tests/test_metrics.py extracts the DOM-free
 * portion of the dashboard's inline <script> (from `const esc` through
 * `renderMd`) and concatenates it ahead of this file, so the functions under
 * test are already in scope when these assertions run. `node --check` on this
 * file alone will fail on undefined names; that is expected.
 *
 * Why this exists: model output is attacker-influenced content that the console
 * assigns to innerHTML. Python can assert that the page *contains* an escaping
 * helper, but only executing it proves the helper actually works.
 *
 * Exit non-zero on the first summary of failures so the Python test can assert
 * on the return code and surface the detail.
 */

let fails = 0;
function check(name, cond, detail) {
  if (!cond) {
    console.log("FAIL " + name + (detail ? " :: " + detail : ""));
    fails++;
  }
}

/* ── esc(): every HTML-significant character, escaped exactly once ───────── */

const e = esc('<script>alert("x")</script> & \'q\'');
check("esc leaves no raw angle brackets", !/[<>]/.test(e), e);
check("esc leaves no raw quotes", !/["']/.test(e), e);
check("esc does not double-escape", e.indexOf("&lt;script&gt;") === 0, e);
check("esc coerces non-strings", esc(null) === "" && esc(undefined) === "", esc(null));

/* ── inlineMd(): escapes before formatting, so no payload yields live markup
 *
 * Only the tags inlineMd itself creates are stripped. Note that a closing tag
 * carries no attributes, so `</div>` has to be removed separately from the
 * opening `<div class="mdh">` — getting that wrong leaves a residue that looks
 * like an injection and reports a failure that is not one.
 * ────────────────────────────────────────────────────────────────────────── */

const payloads = [
  '<img src=x onerror=alert(1)>',
  '<script>alert(document.cookie)</script>',
  '`<script>alert(1)</script>`',            // inside inline code
  '**<img src=x onerror=alert(1)>**',       // inside bold
  '# <script>alert(1)</script>',            // inside a heading
  '<a href="javascript:alert(1)">click</a>',
  '" onmouseover="alert(1)',                // attribute breakout
  "';alert(1);//",                          // script-context breakout
];

for (const p of payloads) {
  const out = inlineMd(p);
  const stripped = out
    .replace(/<\/?code>/g, "")
    .replace(/<\/?strong>/g, "")
    .replace(/<div class="mdh">/g, "")
    .replace(/<\/div>/g, "")
    .replace(/<br>/g, "");
  check("no live markup for " + JSON.stringify(p), !/[<>]/.test(stripped), stripped);
}

/* ── renderMd(): fenced blocks are escaped, not formatted ────────────────── */

const fenced = renderMd("before\n```html\n<script>alert(1)</script>\n```\nafter");
check("fence content is escaped", fenced.indexOf("<script>alert") === -1, fenced);
check("fence is wrapped in pre/code", fenced.indexOf("<pre><code>") !== -1, fenced);

/* An unterminated fence must not swallow the rest of the reply or throw. */
const unterminated = renderMd("text\n```js\nlet x = 1;");
check("unterminated fence is inert", unterminated.indexOf("<script") === -1, unterminated);

/* ── escaping must not break legitimate formatting ───────────────────────── */

check("bold renders", inlineMd("a **b** c").indexOf("<strong>b</strong>") !== -1,
      inlineMd("a **b** c"));
check("inline code renders", inlineMd("use `x`").indexOf("<code>x</code>") !== -1,
      inlineMd("use `x`"));
check("heading renders", inlineMd("# Title").indexOf('<div class="mdh">Title</div>') !== -1,
      inlineMd("# Title"));
check("newline becomes br", inlineMd("a\nb").indexOf("<br>") !== -1, inlineMd("a\nb"));
check("code fence renders",
      renderMd("```py\nprint(1)\n```").indexOf("<pre><code>print(1)</code></pre>") !== -1,
      renderMd("```py\nprint(1)\n```"));

/* ── the server legitimately emits content: null for an empty reply ──────── */

for (const bad of [null, undefined, 123, {}, [], ""]) {
  try {
    inlineMd(bad);
    renderMd(bad);
  } catch (err) {
    check("handles " + JSON.stringify(bad), false, err.message);
  }
}

if (fails) {
  console.log(fails + " assertion(s) failed");
  process.exit(1);
}
console.log("all dashboard rendering assertions passed");
