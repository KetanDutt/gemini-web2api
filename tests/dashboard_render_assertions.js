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

/* ── accounts: the cookie pool card ──────────────────────────────────────────
 *
 * The account source is a path the operator configured and the last error is a
 * string that came from upstream; both are interpolated into innerHTML. The
 * hostile cases below are the reason these two renderers are pure functions
 * outside refreshStatus — a guard that only reads the template would pass on a
 * renderer that forgets to escape.
 * ────────────────────────────────────────────────────────────────────────── */

const pool = {
  size: 2, rotating: true, available: 1, cooldown_sec: 60,
  entries: [
    {source: "/data/primary.json", has_sapisid: true, auth_user: "0", usable: false,
     cooldown_remaining_sec: 41.2, uses: 128, last_error: "429"},
    {source: "/data/second.json", has_sapisid: true, auth_user: null, usable: true,
     cooldown_remaining_sec: 0, uses: 3, last_error: null},
  ],
};
const poolRows = accountRows(pool);
check("one row per account", (poolRows.match(/<tr>/g) || []).length === 2, poolRows);
check("a resting account is marked and names its status",
      /cooling 42s/.test(poolRows) && /HTTP 429/.test(poolRows), poolRows);
check("a ready account is marked ready", /ready/.test(poolRows), poolRows);
check("the source path is shown", poolRows.indexOf("/data/primary.json") !== -1, poolRows);
check("a missing Google index renders as a dash, not 'null'",
      poolRows.indexOf("null") === -1 && poolRows.indexOf("\u2014") !== -1, poolRows);
check("use counts are shown", poolRows.indexOf("128") !== -1, poolRows);

const singleNote = accountsNote({size: 1, rotating: false, available: 1, cooldown_sec: 60});
check("a single account says rotation is off", /no rotation/.test(singleNote), singleNote);
const poolNote = accountsNote(pool);
check("a pool counts what is available", /2 accounts/.test(poolNote) && /1 available/.test(poolNote), poolNote);
check("the pool note names the cooldown", /cooldown 60s/.test(poolNote), poolNote);
check("the separator survives escaping", poolNote.indexOf("&middot;") !== -1 &&
      poolNote.indexOf("&amp;middot;") === -1, poolNote);
check("an anonymous deployment is explained",
      /anonymously/.test(accountsNote(undefined)) && /cookie_files/.test(accountsNote({})),
      accountsNote({}));
check("an empty pool still renders a table row, not a broken table",
      /colspan/.test(accountRows(undefined)) && /colspan/.test(accountRows({entries: []})),
      accountRows({}));

/* Nothing outside what the renderer itself creates may reach the page. */
const own = /<\/?(tr|td|p|code|span)>|<(td|span)[^>]*>|&[a-z]+;/g;
const hostile = {
  size: 1, rotating: false, available: 1, cooldown_sec: 60,
  entries: [{source: "</script><img src=x onerror=alert(1)>", has_sapisid: true,
             auth_user: "<b>0</b>", usable: false, cooldown_remaining_sec: 5,
             uses: 0, last_error: "<svg onload=alert(2)>"}],
};
const hostileRows = accountRows(hostile);
check("a hostile source path is escaped", !/[<>]/.test(hostileRows.replace(own, "")), hostileRows);
check("a hostile last_error is escaped", hostileRows.indexOf("<svg") === -1, hostileRows);
check("a hostile Google index is escaped", hostileRows.indexOf("<b>0</b>") === -1, hostileRows);
/* Escaped *exactly* once. "No raw angle bracket" passes for a value escaped twice
 * as well, and escaping twice is a real defect: the page shows `&lt;svg` to the
 * reader as literal text. These two checks fail in both directions - not enough
 * escaping and too much. */
check("the source path is escaped exactly once",
      hostileRows.indexOf("&lt;/script&gt;") !== -1 && hostileRows.indexOf("&amp;lt;") === -1,
      hostileRows);
check("the last error is escaped exactly once",
      hostileRows.indexOf("&lt;svg") !== -1 && hostileRows.indexOf("&amp;lt;") === -1,
      hostileRows);

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
