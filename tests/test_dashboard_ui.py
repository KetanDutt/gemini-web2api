"""Structural checks on the web console's own document.

The other dashboard tests cover *behaviour*: escaping (``DashboardScriptTests``
runs the pure renderers under Node) and the public state contract
(``tests/test_endpoints.py``). Neither notices a page that has quietly stopped
working as a document — a script that looks up an id no element defines, a
stylesheet with an unbalanced brace that silently drops every rule after it, or
a light theme that lost the token the dark theme still defines.

Those are the failure modes of a single-file interface that is edited in place,
and they are invisible to a reader skimming a diff. Everything here is derived
from the rendered page, so it checks what a browser would actually receive.
"""
import html.parser
import re
import unittest

from gemini_web2api import webui

# Attributes that exist to be measured, not to be looked up by id.
RUNTIME_CUSTOM_PROPERTIES = {"--h", "--i", "--mx", "--my", "--toast-ms"}

# Tokens whose value is a colour: every one of these has to exist in the dark
# scheme as well, or the theme switch produces a half-styled page.
COLOURISH = re.compile(
    r"^--(bg|bg-top|bg-bottom|fg|fg-2|fg-3|accent|accent-fill|accent-fg|accent-soft|"
    r"ok|ok-fill|warn|warn-fill|bad|bad-fill|info|"
    r"glass-\d|pane|pane-stuck|fill-\d|field|field-hover|field-focus|code-bg|scrim|"
    r"surface-solid|line-\d|edge-hi|edge-hi-strong|edge-lo|fill-on-tint|on-accent|"
    r"sh-lift-\d|sh-knob|sh-knob-press|"
    r"acc-bg|acc-line|acc-ring|ok-bg|ok-line|warn-bg|warn-line|bad-bg|bad-line|"
    r"neu-bg|neu-line|tint-[abc]|sh-\d|sh-inset|sh-well|sh-press)$"
)


def _state():
    return {
        "version": "1.2.0", "uptime_sec": 1.0, "base_url": "http://localhost:8081/v1",
        "streaming": "httpx", "api_keys": "1 configured", "cookie": "anonymous",
        "default_model": "gemini-3.6-flash", "gemini_bl": "boq_x", "proxy": None,
        "rate_limit": "disabled", "temporary_chats": False, "requests_served": 0,
        "auth_enabled": False, "history_enabled": True, "python": "3.11.2",
        "warnings": [], "models": [], "endpoints": [],
    }


def _page():
    return webui.render_dashboard(_state()).decode("utf-8")


def _style(page):
    return page[page.index("<style>") + len("<style>"):page.index("</style>")]


def _script(page):
    return re.findall(r"<script>(.*?)</script>", page, re.DOTALL)[0]


class _Balanced(html.parser.HTMLParser):
    """Report tags that do not nest, ignoring the void elements in use."""

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
            "meta", "param", "source", "track", "wbr", "path", "circle", "rect",
            "stop", "use", "polyline", "line"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if tag not in self.stack:
            self.errors.append(f"stray </{tag}>")
            return
        while self.stack and self.stack[-1] != tag:
            self.errors.append(f"unclosed <{self.stack.pop()}>")
        if self.stack:
            self.stack.pop()


class DocumentStructureTests(unittest.TestCase):
    """The page has to be a document before it can be an interface."""

    def test_tags_nest(self):
        page = _page()
        parser = _Balanced()
        parser.feed(page)
        self.assertEqual([], parser.errors)
        self.assertEqual([], parser.stack, "unclosed tags survive to the end")

    def test_ids_are_unique(self):
        page = _page()
        ids = re.findall(r'\bid="([^"]+)"', page)
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        self.assertEqual([], duplicates)

    def test_every_id_the_script_looks_up_exists(self):
        """`$('x')` with no such element is a feature that silently does nothing."""
        page = _page()
        ids = set(re.findall(r'\bid="([^"]+)"', page))
        looked_up = set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", _script(page)))
        self.assertEqual(set(), looked_up - ids)

    def test_generated_markup_only_uses_ids_that_exist(self):
        """Ids assembled in strings (palette options, tab panels) still resolve."""
        page = _page()
        ids = set(re.findall(r'\bid="([^"]+)"', page))
        # Every nav item must have a panel to switch to, and every panel a nav
        # item: either half missing is a dead control.
        panels = {i[4:] for i in ids if i.startswith("tab-")}
        tabs = set(re.findall(r'data-tab="([a-z]+)"', page))
        self.assertEqual({"chat", "status", "activity", "models", "api"}, panels | tabs)
        self.assertEqual(panels, tabs)

    def test_one_inline_script_and_nothing_to_fetch(self):
        """The escaping harness extracts the first bare <script> and needs it to
        be the only one; the page must also stay air-gapped."""
        page = _page()
        self.assertEqual(1, len(re.findall(r"<script", page)))
        self.assertEqual([], re.findall(r"<(?:script|link)[^>]+(?:src|href)=\"(?!#|data:)", page))
        self.assertNotIn("@import", page)
        external = [u for u in re.findall(r"https?://[^\s\"')]+", page)
                    if "w3.org" not in u and "localhost" not in u and "127.0.0.1" not in u]
        self.assertEqual([], external)


class TabContractTests(unittest.TestCase):
    """Tabs and panels are generated in two places and must agree."""

    def test_every_tab_controls_an_existing_panel(self):
        page = _page()
        ids = set(re.findall(r'\bid="([^"]+)"', page))
        tabs = re.findall(r'role="tab" id="([^"]+)"[^>]*aria-controls="([^"]+)"', page,
                          re.DOTALL)
        self.assertGreaterEqual(len(tabs), 5)
        for _tab, panel in tabs:
            self.assertIn(panel, ids)

    def test_the_script_agrees_with_the_markup_about_the_sections(self):
        """TAB_ORDER drives the glider, the shortcuts and the topbar title."""
        script = _script(_page())
        order = re.search(r"const TAB_TITLES = \{(.*?)\};", script).group(1)
        names = re.findall(r"(\w+):", order)
        page = _page()
        for name in names:
            self.assertIn(f'data-tab="{name}"', page,
                          f"{name} is in the script but has no nav item")
            self.assertIn(f'id="tab-{name}"', page)
        self.assertEqual(5, len(names))


class StylesheetTests(unittest.TestCase):
    """A stylesheet fails silently: one stray brace drops everything after it."""

    def test_braces_balance(self):
        css = _style(_page())
        depth = 0
        for index, char in enumerate(css):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            self.assertGreaterEqual(depth, 0, "} before { at offset " + str(index))
        self.assertEqual(0, depth)

    def test_every_variable_used_is_defined(self):
        css = _style(_page())
        defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", css))
        used = set(re.findall(r"var\((--[a-z0-9-]+)", css))
        self.assertEqual(set(), used - defined - RUNTIME_CUSTOM_PROPERTIES,
                         "a var() with no definition renders nothing")

    def test_the_theme_blocks_define_the_same_colours(self):
        """The dark scheme is written as a second block; a token added to one and
        forgotten in the other shows up as a light patch in the dark theme."""
        css = _style(_page())
        start = css.index(':root[data-theme="dark"]{')
        depth, i = 0, css.index("{", start)
        while True:
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        light = css[:start]
        dark = css[start:i + 1]
        missing = sorted(
            name for name in set(re.findall(r"(--[a-z0-9-]+)\s*:", light))
            if COLOURISH.match(name) and (name + ":") not in dark
        )
        self.assertEqual([], missing, "defined for light but not for dark")

    def test_text_colours_meet_wcag_aa_in_both_schemes(self):
        """Every colour used as text, or as a label on a fill, must reach 4.5:1
        against the canvas it sits on, in light and dark. Glass is translucent,
        so the check uses the opaque canvas stops: the worst case behind a
        surface. A palette change that trades legibility for shade fails here."""
        css = _style(_page())
        start = css.index(':root[data-theme="dark"]{')
        light_css, dark_css = css[:start], css[start:]

        def tokens(block):
            # First definition wins: the base palette comes before the
            # prefers-contrast overrides, which restate the same names.
            found = {}
            for name, value in re.findall(r"(--[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})\b", block):
                found.setdefault(name, value.lower())
            return found

        def luminance(hex_colour):
            channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                      for c in channels]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        def ratio(a, b):
            hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
            return (hi + 0.05) / (lo + 0.05)

        text_tokens = ["--fg", "--fg-2", "--fg-3", "--accent", "--ok", "--warn", "--bad", "--info"]
        failures = []
        for scheme, block in (("light", light_css), ("dark", dark_css)):
            values = tokens(block)
            canvas = [values[name] for name in ("--bg-top", "--bg", "--bg-bottom")]
            for name in text_tokens:
                worst = min(ratio(values[name], backdrop) for backdrop in canvas)
                if worst < 4.5:
                    failures.append(f"{scheme} {name} {worst:.2f}:1")
            worst = ratio(values["--accent-fg"], values["--accent-fill"])
            if worst < 4.5:
                failures.append(f"{scheme} accent-fg on accent-fill {worst:.2f}:1")
        self.assertEqual([], failures, "below WCAG AA 4.5:1")

    def test_every_control_has_an_accessible_name(self):
        """A control with no name is unusable with a screen reader, and an
        icon-only button is the usual way one slips in.

        A name counts if it is an ``aria-label``/``aria-labelledby``, a
        ``<label for=...>`` somewhere in the page, or a wrapping ``<label>``
        (the pattern the switches use).
        """
        page = _page()
        body = page[page.index("<body>"):page.index("<script>", page.index("<body>"))]
        labelled = set(re.findall(r'<label[^>]*\bfor="([^"]+)"', body))

        unnamed = []
        for button in re.findall(r"<button\b.*?</button>", body, re.DOTALL):
            opens = button[:button.index(">") + 1]
            if re.search(r"aria-label(?:ledby)?=", opens):
                continue
            visible = re.sub(r"<svg\b.*?</svg>", "", button, flags=re.DOTALL)
            if not re.sub(r"<[^>]+>", "", visible).strip():
                unnamed.append(opens[:70])
        self.assertEqual([], unnamed, "buttons with no accessible name")

        unlabelled = []
        for field in re.findall(r"<(?:input|select|textarea)\b[^>]*>", body):
            ident = re.search(r'\bid="([^"]+)"', field)
            if re.search(r"aria-label(?:ledby)?=", field):
                continue
            if ident and ident.group(1) in labelled:
                continue
            if re.search(r'type="(?:hidden|submit|button)"', field):
                continue
            # A checkbox inside <label>…</label> takes its name from the label.
            if ident and re.search(r"<label\b[^>]*>(?:(?!</label>).)*?" + re.escape(field),
                                   body, re.DOTALL):
                continue
            unlabelled.append(field[:70])
        self.assertEqual([], unlabelled, "form fields with no accessible name")

    def test_animations_stay_on_the_compositor(self):
        """Keyframes that touch layout force a reflow every frame.

        Opacity, transform, colour and filters are cheap; width, height, inset
        and margin are not. Entrance animations are also checked against the
        motion scale the interface promises (120–520ms), ignoring the ambient
        loops (a status dot breathing) that are meant to run forever.
        """
        page = _page()
        style = _style(page)
        allowed = {"opacity", "transform", "background-color", "color", "clip-path",
                   "backdrop-filter", "-webkit-backdrop-filter", "filter", "box-shadow"}
        offenders = {}
        for name, block in re.findall(
                r"@keyframes\s+([\w-]+)\s*\{((?:[^{}]*\{[^}]*\})*)\s*\}", style):
            bad = sorted(set(re.findall(r"([a-z-]+)\s*:", block)) - allowed)
            if bad:
                offenders[name] = bad
        self.assertEqual({}, offenders)

        scale = [float(v) / 1000 if unit == "ms" else float(v)
                 for v, unit in re.findall(r"--d-\d:\s*(\d+(?:\.\d+)?)(ms|s)", style)]
        self.assertTrue(scale, "the motion scale tokens disappeared")
        self.assertGreaterEqual(min(scale), 0.12)
        self.assertLessEqual(max(scale), 0.52)

    def test_keyboard_focus_is_visible(self):
        """One ring for the whole product, and fields that swap it for a ring
        of their own rather than losing focus styling altogether."""
        style = _style(_page())
        self.assertRegex(style, r"\n:focus-visible\{[^}]*outline:\s*2px")
        self.assertRegex(style, r":focus-visible\{[^}]*outline-offset")
        # The fields opt out of the outline because the well draws the ring.
        self.assertRegex(style, r"input:focus-visible[^{]*\{outline:none\}")
        self.assertRegex(style, r":focus-within\{[^}]*border-color:var\(--accent\)")

    def test_the_accessibility_overrides_are_present(self):
        css = _style(_page())
        for query in ("prefers-reduced-motion", "prefers-reduced-transparency",
                      "prefers-contrast:more", "prefers-color-scheme"):
            self.assertIn(query, css)


if __name__ == "__main__":
    unittest.main()
