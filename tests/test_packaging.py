"""Packaging and repository structure.

These guard the two structural defects that made the project unusable as a
distributed package: setuptools could not build it at all, and the same server
existed twice in divergent copies.
"""
import ast
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE_DIR = os.path.join(REPO_ROOT, "gemini_web2api")
SHIM_PATH = os.path.join(REPO_ROOT, "gemini_web2api.py")

EXPECTED_MODULES = ["config", "gemini", "metrics", "models", "multimodal",
                    "ratelimit", "server", "tools", "webui"]


class PyprojectTests(unittest.TestCase):
    def setUp(self):
        if tomllib is None:
            self.skipTest("tomllib requires Python 3.11+")
        with open(os.path.join(REPO_ROOT, "pyproject.toml"), "rb") as handle:
            self.pyproject = tomllib.load(handle)

    def test_packages_are_declared_explicitly(self):
        """Regression: flat-layout auto-discovery failed on cloudflare/ + gemini_web2api/."""
        packages = self.pyproject["tool"]["setuptools"]["packages"]
        self.assertEqual(packages, ["gemini_web2api"])

    def test_console_script_target_exists(self):
        script = self.pyproject["project"]["scripts"]["gemini-web2api"]
        module, _, function = script.partition(":")
        self.assertEqual(module, "gemini_web2api.__main__")
        imported = importlib.import_module(module)
        self.assertTrue(callable(getattr(imported, function)))

    def test_core_has_no_hard_dependencies(self):
        self.assertEqual(self.pyproject["project"]["dependencies"], [])

    def test_httpx_is_an_optional_extra(self):
        extras = self.pyproject["project"]["optional-dependencies"]
        self.assertIn("streaming", extras)
        self.assertTrue(any("httpx" in item for item in extras["streaming"]))

    def test_python_floor_matches_the_documented_one(self):
        self.assertEqual(self.pyproject["project"]["requires-python"], ">=3.8")

    def test_readme_and_license_are_declared(self):
        self.assertEqual(self.pyproject["project"]["readme"], "README.md")
        self.assertTrue(os.path.exists(os.path.join(REPO_ROOT, "README.md")))
        self.assertTrue(os.path.exists(os.path.join(REPO_ROOT, "LICENSE")))


class VersionConsistencyTests(unittest.TestCase):
    def read(self, *parts):
        with open(os.path.join(REPO_ROOT, *parts), encoding="utf-8") as handle:
            return handle.read()

    def test_package_and_pyproject_versions_match(self):
        from gemini_web2api import __version__
        if tomllib is None:
            self.skipTest("tomllib requires Python 3.11+")
        with open(os.path.join(REPO_ROOT, "pyproject.toml"), "rb") as handle:
            declared = tomllib.load(handle)["project"]["version"]
        self.assertEqual(__version__, declared)

    def test_version_is_semver_shaped(self):
        from gemini_web2api import __version__
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+")

    def test_documented_config_example_matches_the_defaults(self):
        """config.example.json must not advertise options that do not exist."""
        import json

        from gemini_web2api.config import DEFAULT_CONFIG
        with open(os.path.join(REPO_ROOT, "config.example.json"), encoding="utf-8") as handle:
            example = json.load(handle)
        unknown = set(example) - set(DEFAULT_CONFIG)
        self.assertEqual(unknown, set(), f"config.example.json has unknown keys: {unknown}")


class ShimTests(unittest.TestCase):
    """gemini_web2api.py must stay a thin entry point, not a second implementation."""

    def setUp(self):
        with open(SHIM_PATH, encoding="utf-8") as handle:
            self.source = handle.read()

    def test_shim_is_small(self):
        self.assertLess(len(self.source.splitlines()), 120)

    def test_shim_contains_no_server_implementation(self):
        for marker in ("class GeminiHandler", "def do_POST", "StreamGenerate",
                       "MODELS =", "DEFAULT_CONFIG ="):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, self.source)

    def test_shim_delegates_to_the_package(self):
        self.assertIn("from gemini_web2api.__main__ import main", self.source)
        self.assertIn("sys.exit(main())", self.source)

    def test_shim_reports_a_clear_error_when_the_package_is_missing(self):
        self.assertIn("could not start", self.source)
        self.assertIn("pip install", self.source)

    def test_shim_executes_and_reports_the_version(self):
        result = subprocess.run(
            [sys.executable, SHIM_PATH, "--version"],
            capture_output=True, text=True, timeout=60, cwd=REPO_ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        from gemini_web2api import __version__
        self.assertIn(__version__, result.stdout)

    def test_shim_help_lists_the_documented_flags(self):
        result = subprocess.run(
            [sys.executable, SHIM_PATH, "--help"],
            capture_output=True, text=True, timeout=60, cwd=REPO_ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        for flag in ("--port", "--config", "--cookie-file", "--proxy", "--api-key"):
            self.assertIn(flag, result.stdout)

    def test_shim_works_from_another_working_directory(self):
        result = subprocess.run(
            [sys.executable, SHIM_PATH, "--version"],
            capture_output=True, text=True, timeout=60, cwd=os.path.dirname(REPO_ROOT))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_package_wins_over_the_shim_on_import(self):
        """`import gemini_web2api` must resolve to the package, not the shim."""
        import gemini_web2api
        self.assertTrue(gemini_web2api.__file__.endswith(os.path.join("gemini_web2api", "__init__.py")))


class PackageStructureTests(unittest.TestCase):
    def test_all_expected_modules_exist(self):
        for name in EXPECTED_MODULES:
            with self.subTest(module=name):
                self.assertTrue(os.path.exists(os.path.join(PACKAGE_DIR, f"{name}.py")))

    def test_all_expected_modules_import(self):
        for name in EXPECTED_MODULES:
            with self.subTest(module=name):
                importlib.import_module(f"gemini_web2api.{name}")

    def test_no_stale_bytecode_is_committed(self):
        result = subprocess.run(["git", "ls-files", "*/__pycache__/*", "*.pyc"],
                                capture_output=True, text=True, cwd=REPO_ROOT)
        self.assertEqual(result.stdout.strip(), "")

    def test_secrets_are_gitignored(self):
        with open(os.path.join(REPO_ROOT, ".gitignore"), encoding="utf-8") as handle:
            ignored = handle.read()
        for pattern in ("config.json", "cookie.txt", "gemini-auth.json", ".env"):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, ignored)

    def test_no_secret_files_are_tracked(self):
        result = subprocess.run(
            ["git", "ls-files", "config.json", "cookie.txt", "cookie.json", "gemini-auth.json"],
            capture_output=True, text=True, cwd=REPO_ROOT)
        self.assertEqual(result.stdout.strip(), "")

    def test_no_dead_code_left_behind(self):
        """Helpers that were defined but never called have been removed."""
        with open(os.path.join(PACKAGE_DIR, "tools.py"), encoding="utf-8") as handle:
            tools = handle.read()
        self.assertNotIn("_compress_b64_if_needed", tools)
        self.assertNotIn("MAX_IMAGE_B64_SIZE", tools)

    def test_dockerfile_uses_the_package(self):
        with open(os.path.join(REPO_ROOT, "Dockerfile"), encoding="utf-8") as handle:
            dockerfile = handle.read()
        self.assertIn("COPY gemini_web2api/", dockerfile)
        self.assertIn('"python", "-m", "gemini_web2api"', dockerfile)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn("USER", dockerfile)

    def test_dockerfile_does_not_bake_in_a_config(self):
        """Shipping config.example.json as config.json baked in a public API key."""
        with open(os.path.join(REPO_ROOT, "Dockerfile"), encoding="utf-8") as handle:
            dockerfile = handle.read()
        self.assertNotIn("COPY config.example.json ./config.json", dockerfile)

    def test_requirements_matches_the_optional_extra(self):
        with open(os.path.join(REPO_ROOT, "requirements.txt"), encoding="utf-8") as handle:
            requirements = handle.read()
        self.assertIn("httpx", requirements)


class DocumentationPresenceTests(unittest.TestCase):
    def test_docs_folder_exists_with_the_core_pages(self):
        docs = os.path.join(REPO_ROOT, "docs")
        self.assertTrue(os.path.isdir(docs))
        expected = ["README.md", "CONFIGURATION.md", "API.md", "ARCHITECTURE.md",
                    "DEPLOYMENT.md", "TROUBLESHOOTING.md", "AUTHENTICATION.md",
                    "DEVELOPMENT.md", "SECURITY.md", "CHANGELOG.md", "AUDIT.md"]
        for name in expected:
            with self.subTest(doc=name):
                self.assertTrue(os.path.exists(os.path.join(docs, name)),
                                f"docs/{name} is missing")

    def test_readme_links_to_the_docs(self):
        with open(os.path.join(REPO_ROOT, "README.md"), encoding="utf-8") as handle:
            readme = handle.read()
        self.assertIn("docs/", readme)

    def test_documented_models_exist(self):
        """Every model ID in the README's model table must be a real model.

        Only table rows are scanned: a backticked `gemini-web2api` elsewhere in
        the document is the project name, not a model.
        """
        from gemini_web2api.models import MODELS
        with open(os.path.join(REPO_ROOT, "README.md"), encoding="utf-8") as handle:
            readme = handle.read()
        documented = set(re.findall(r"^\|\s*`(gemini-[^`@]+)`", readme, re.MULTILINE))
        self.assertTrue(documented, "no model table found in README.md")
        unknown = {name.strip() for name in documented if name.strip() not in MODELS}
        self.assertEqual(unknown, set(), f"README documents unknown models: {unknown}")

    def test_undocumented_models_are_listed(self):
        from gemini_web2api.models import MODELS
        with open(os.path.join(REPO_ROOT, "README.md"), encoding="utf-8") as handle:
            readme = handle.read()
        for name in MODELS:
            with self.subTest(model=name):
                self.assertIn(name, readme)


class DocumentationConsistencyTests(unittest.TestCase):
    """Docs must describe what the code actually does.

    Stale documentation was one of the defects this release fixed — the README
    claimed a "single file" implementation that did not exist and listed a model
    table missing two shipped models. These tests make that class of drift fail
    the build instead of misleading a reader.
    """

    def _read(self, *parts):
        with open(os.path.join(REPO_ROOT, *parts), encoding="utf-8") as handle:
            return handle.read()

    def test_documented_endpoints_are_routed(self):
        """Every endpoint in API.md must correspond to a route in server.py."""
        server = self._read("gemini_web2api", "server.py")
        api_doc = self._read("docs", "API.md")
        documented = set(re.findall(r"`(/[a-z0-9/v{}:_.*\-]+)`", api_doc))
        self.assertTrue(documented, "no endpoints found in docs/API.md")
        missing = []
        for path in sorted(documented):
            # Strip {placeholders} and :verb suffixes to get the routed prefix.
            core = path.split("{")[0].split(":")[0].rstrip("/")
            if not core:
                continue
            if core not in server and core.replace("/v1beta", "") not in server:
                missing.append(path)
        self.assertEqual(missing, [], f"API.md documents unrouted endpoints: {missing}")

    def test_every_config_key_is_documented(self):
        from gemini_web2api.config import DEFAULT_CONFIG
        conf_doc = self._read("docs", "CONFIGURATION.md")
        missing = [key for key in DEFAULT_CONFIG if key not in conf_doc]
        self.assertEqual(missing, [], f"undocumented config keys: {missing}")

    def test_every_doc_page_is_in_the_index(self):
        docs_dir = os.path.join(REPO_ROOT, "docs")
        index = self._read("docs", "README.md")
        pages = [f for f in sorted(os.listdir(docs_dir))
                 if f.endswith(".md") and f != "README.md"]
        self.assertTrue(pages, "docs/ contains no pages")
        missing = [page for page in pages if page not in index]
        self.assertEqual(missing, [], f"docs pages not linked from docs/README.md: {missing}")

    def test_every_doc_page_is_reachable_from_the_readme(self):
        """AUDIT.md is an internal working document, so it is exempt."""
        docs_dir = os.path.join(REPO_ROOT, "docs")
        readme = self._read("README.md")
        pages = [f for f in sorted(os.listdir(docs_dir))
                 if f.endswith(".md") and f not in ("README.md", "AUDIT.md")]
        missing = [page for page in pages if f"docs/{page}" not in readme]
        self.assertEqual(missing, [], f"docs pages not linked from README.md: {missing}")

    def test_current_version_appears_in_the_changelog(self):
        import gemini_web2api
        changelog = self._read("docs", "CHANGELOG.md")
        self.assertIn(f"## [{gemini_web2api.__version__}]", changelog)

    def test_both_readmes_claim_the_same_test_count(self):
        """The advertised test count must match the suite that actually runs."""
        import unittest as _unittest

        loader = _unittest.TestLoader()
        suite = loader.discover(os.path.join(REPO_ROOT, "tests"), top_level_dir=REPO_ROOT)
        actual = suite.countTestCases()
        for name in ("README.md", "README_CN.md"):
            text = self._read(name)
            claimed = re.search(r"(\d{2,4})\s*(?:tests|个测试)", text)
            self.assertIsNotNone(claimed, f"no test count found in {name}")
            self.assertEqual(
                int(claimed.group(1)), actual,
                f"{name} claims {claimed.group(1)} tests but the suite has {actual}",
            )


class Python38CompatibilityTests(unittest.TestCase):
    """`requires-python = ">=3.8"` must be true, not aspirational.

    `compile()` under a newer interpreter happily accepts newer syntax, so these
    parse the AST and look for constructs that would raise at import time on 3.8.
    The AST is used rather than a text scan because a regex cannot tell an
    annotation from a `|` inside a regex string literal — scanning gemini.py for
    `x | y` matches its code-fence pattern, which is not an annotation at all.
    """

    _BUILTIN_GENERICS = {"list", "dict", "tuple", "set", "frozenset", "type"}
    _TREES = None

    def _trees(self):
        if Python38CompatibilityTests._TREES is None:
            trees = {}
            package = os.path.join(REPO_ROOT, "gemini_web2api")
            for directory, subdirs, files in os.walk(package):
                subdirs[:] = [d for d in subdirs if d != "__pycache__"]
                for name in files:
                    if name.endswith(".py"):
                        path = os.path.join(directory, name)
                        with open(path, encoding="utf-8") as handle:
                            trees[name] = ast.parse(handle.read(), filename=path)
            Python38CompatibilityTests._TREES = trees
        return Python38CompatibilityTests._TREES

    def _annotations(self, tree):
        """Yield only nodes in annotation position."""
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.returns is not None:
                    yield node.returns
                groups = (node.args.args, node.args.kwonlyargs,
                          getattr(node.args, "posonlyargs", []))
                for group in groups:
                    for arg in group:
                        if arg.annotation is not None:
                            yield arg.annotation
                for extra in (node.args.vararg, node.args.kwarg):
                    if extra is not None and extra.annotation is not None:
                        yield extra.annotation
            elif isinstance(node, ast.AnnAssign) and node.annotation is not None:
                yield node.annotation

    def test_no_pep585_builtin_generics_in_annotations(self):
        """`list[str]` annotations need 3.9+."""
        offenders = []
        for name, tree in self._trees().items():
            for annotation in self._annotations(tree):
                for node in ast.walk(annotation):
                    if (isinstance(node, ast.Subscript)
                            and isinstance(node.value, ast.Name)
                            and node.value.id in self._BUILTIN_GENERICS):
                        offenders.append(f"{name}:{node.lineno} {node.value.id}[...]")
        self.assertEqual(offenders, [], f"PEP 585 generics need Python 3.9+: {offenders}")

    def test_no_pep604_union_annotations(self):
        """`str | None` annotations need 3.10+."""
        offenders = []
        for name, tree in self._trees().items():
            for annotation in self._annotations(tree):
                for node in ast.walk(annotation):
                    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
                        offenders.append(f"{name}:{node.lineno}")
        self.assertEqual(offenders, [], f"PEP 604 unions need Python 3.10+: {offenders}")

    def test_no_match_statements(self):
        """`match`/`case` needs 3.10+. `match = re.search(...)` is just a name."""
        if not hasattr(ast, "Match"):
            self.skipTest("this interpreter cannot parse match statements")
        offenders = []
        for name, tree in self._trees().items():
            for node in ast.walk(tree):
                if isinstance(node, ast.Match):
                    offenders.append(f"{name}:{node.lineno}")
        self.assertEqual(offenders, [], f"match statements need Python 3.10+: {offenders}")

    def test_no_dict_merge_operator(self):
        """`{} | {}` needs 3.9+. Only literal dicts are flagged, so a bitwise
        `or` on ints or sets is not a false positive."""
        offenders = []
        for name, tree in self._trees().items():
            for node in ast.walk(tree):
                if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
                        and isinstance(node.left, ast.Dict)
                        and isinstance(node.right, ast.Dict)):
                    offenders.append(f"{name}:{node.lineno}")
        self.assertEqual(offenders, [], f"dict | merge needs Python 3.9+: {offenders}")

    def test_no_39_plus_stdlib_apis(self):
        """Distinctive spellings, safe to scan as text."""
        banned = ("removeprefix(", "removesuffix(", "functools.cache",
                  "import zoneinfo", "import graphlib", "asyncio.to_thread")
        offenders = {}
        package = os.path.join(REPO_ROOT, "gemini_web2api")
        for directory, subdirs, files in os.walk(package):
            subdirs[:] = [d for d in subdirs if d != "__pycache__"]
            for name in files:
                if not name.endswith(".py"):
                    continue
                with open(os.path.join(directory, name), encoding="utf-8") as handle:
                    source = handle.read()
                hits = [token for token in banned if token in source]
                if hits:
                    offenders[name] = hits
        self.assertEqual(offenders, {}, f"3.9+ stdlib APIs used: {offenders}")


class WorkerParityTests(unittest.TestCase):
    """The Cloudflare Worker is a separate implementation, but its model table
    should not silently drift from the Python one.

    The Worker shipped 7 models while Python shipped 9, with nothing recording
    the gap. These tests parse cloudflare/worker.js directly so a model added on
    one side and forgotten on the other fails the build. They assert parity of
    *shared* models only — the Worker legitimately lacks one, and that exception
    is required to be documented rather than merely tolerated.
    """

    WORKER = os.path.join(REPO_ROOT, "cloudflare", "worker.js")
    # Models the Worker genuinely cannot support, and why.
    KNOWN_GAPS = {"gemini-3.1-pro-enhanced"}

    def _worker_models(self):
        """Parse the Worker's MODELS table: name -> (mode, think)."""
        with open(self.WORKER, encoding="utf-8") as handle:
            source = handle.read()
        start = source.index("var MODELS = {")
        end = source.index("\n};", start)
        block = source[start:end]
        found = {}
        for entry in re.finditer(
            r"'([\w.\-]+)'\s*:\s*\{(.*?)\}", block, re.DOTALL
        ):
            name, body = entry.group(1), entry.group(2)
            mode = re.search(r"\bmode\s*:\s*(\d+)", body)
            think = re.search(r"\bthink\s*:\s*(\d+)", body)
            found[name] = (
                int(mode.group(1)) if mode else None,
                int(think.group(1)) if think else None,
            )
        return found

    def setUp(self):
        if not os.path.exists(self.WORKER):
            self.skipTest("cloudflare/worker.js is not present")

    def test_worker_models_parsed(self):
        models = self._worker_models()
        self.assertGreaterEqual(len(models), 8, f"parsed only {models}")
        for name, (mode, think) in models.items():
            with self.subTest(model=name):
                self.assertIsNotNone(mode, f"{name} has no mode")
                self.assertIsNotNone(think, f"{name} has no think")

    def test_no_phantom_worker_models(self):
        """Every model the Worker advertises must exist in Python's table."""
        from gemini_web2api.models import MODELS
        phantom = set(self._worker_models()) - set(MODELS)
        self.assertEqual(
            phantom, set(),
            f"worker.js advertises models absent from gemini_web2api: {phantom}",
        )

    def test_shared_models_agree_on_mode_and_think(self):
        """A shared model must select the same MODE_CATEGORY and think level."""
        from gemini_web2api.models import MODELS
        mismatched = {}
        for name, (mode, think) in self._worker_models().items():
            py = MODELS.get(name)
            if not py:
                continue
            if py["mode"] != mode or py["think"] != think:
                mismatched[name] = {
                    "python": (py["mode"], py["think"]),
                    "worker": (mode, think),
                }
        self.assertEqual(
            mismatched, {},
            f"worker/python disagree on (mode, think): {mismatched}",
        )

    def test_known_gaps_are_documented(self):
        """Each unsupported model must be explained, not silently missing."""
        from gemini_web2api.models import MODELS
        with open(os.path.join(REPO_ROOT, "cloudflare", "README.MD"),
                  encoding="utf-8") as handle:
            doc = handle.read()
        for name in self.KNOWN_GAPS:
            with self.subTest(model=name):
                self.assertIn(name, MODELS, f"{name} is no longer a Python model")
                self.assertNotIn(
                    f"'{name}'", self._worker_models_source(),
                    f"{name} is now supported by the Worker; update KNOWN_GAPS",
                )
                self.assertIn(name, doc, f"{name} gap is not documented")

    def test_no_undocumented_gaps(self):
        """Any Python model the Worker lacks must be a declared known gap."""
        from gemini_web2api.models import MODELS
        gaps = set(MODELS) - set(self._worker_models())
        self.assertEqual(
            gaps, self.KNOWN_GAPS,
            f"undocumented worker gaps: {gaps - self.KNOWN_GAPS}; "
            f"stale KNOWN_GAPS entries: {self.KNOWN_GAPS - gaps}",
        )

    def _worker_models_source(self):
        with open(self.WORKER, encoding="utf-8") as handle:
            source = handle.read()
        start = source.index("var MODELS = {")
        return source[start:source.index("\n};", start)]

    def test_worker_javascript_parses(self):
        """A syntax error in the Worker would only surface at deploy time."""
        import shutil
        import subprocess

        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        result = subprocess.run(
            [node, "--check", self.WORKER],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(
            result.returncode, 0,
            f"node --check failed on cloudflare/worker.js:\n{result.stderr}",
        )


class WindowsLauncherTests(unittest.TestCase):
    """The one-click Windows launcher must be safe to double-click.

    These are content assertions: a .bat cannot be executed on the Linux CI
    runner, so the guards check the properties that make the difference between
    a launcher that works and one that silently opens a window and vanishes.
    """

    @classmethod
    def setUpClass(cls):
        cls.bat_path = os.path.join(REPO_ROOT, "start.bat")
        cls.setup_path = os.path.join(REPO_ROOT, "scripts", "win_setup.py")
        with open(cls.bat_path, encoding="utf-8") as handle:
            # Batch files are CRLF on Windows; normalise so assertions are stable.
            cls.bat = handle.read().replace("\r\n", "\n")
        with open(cls.setup_path, encoding="utf-8") as handle:
            cls.setup = handle.read()

    def test_launcher_exists_at_the_repo_root(self):
        self.assertTrue(os.path.exists(self.bat_path))

    def test_echo_is_off(self):
        """Without this, every command is echoed and the output is unreadable."""
        self.assertTrue(self.bat.lstrip().lower().startswith("@echo off"))

    def test_local_state_is_scoped(self):
        self.assertIn("setlocal", self.bat)

    def test_changes_to_its_own_directory(self):
        """Double-clicking sets cwd to System32; relative paths would break."""
        self.assertIn('cd /d "%~dp0"', self.bat)

    def test_python_is_verified_by_execution_not_by_path(self):
        """The Microsoft Store ships a python.exe stub that opens the Store.
        Detection must actually run the interpreter and check the version."""
        self.assertIn("sys.version_info>=(3,8)", self.bat)
        for candidate in ("py -3", "python", "python3"):
            self.assertIn(candidate, self.bat)

    def test_uses_a_virtual_environment(self):
        self.assertIn("-m venv", self.bat)
        self.assertIn('set "VENV=%~dp0.venv"', self.bat)
        # Built from VENV, so a path with spaces still resolves.
        self.assertIn(r'set "VPY=%VENV%\Scripts\python.exe"', self.bat)

    def test_installs_from_requirements(self):
        """One source of truth for dependencies, not a hardcoded package list."""
        self.assertIn("requirements.txt", self.bat)

    def test_dependency_failure_is_not_fatal(self):
        """httpx is optional: offline users must still get a running server."""
        self.assertIn("WARNING: dependency installation failed", self.bat)

    def test_paths_are_quoted(self):
        """Spaces in the install path (C:\\Program Files, user names) are common."""
        for fragment in ('"%VPY%"', '"%VENV%"', r'"%~dp0requirements.txt"',
                         r'"%~dp0scripts\win_setup.py"'):
            self.assertIn(fragment, self.bat, f"unquoted path: {fragment}")

    def test_broken_venv_is_recreated(self):
        self.assertIn("rmdir /s /q", self.bat)

    def test_window_stays_open_on_failure(self):
        """A launcher that exits immediately hides the reason it failed."""
        self.assertIn("pause", self.bat)
        self.assertIn(":fail", self.bat)

    def test_launches_the_server_module(self):
        self.assertIn("-m gemini_web2api", self.bat)

    def test_opens_the_dashboard_after_a_delay(self):
        """Opening the browser immediately races the socket bind."""
        self.assertIn("Start-Sleep", self.bat)
        self.assertIn("Start-Process", self.bat)
        self.assertIn("http://localhost:%PORT%/", self.bat)

    def test_extra_arguments_are_passed_through(self):
        self.assertIn("%*", self.bat)

    def test_arguments_are_captured_before_the_shift_loop(self):
        """Whether `shift` also empties `%*` is cmd.exe version-dependent.

        Where it does, the `:scanargs` loop consumes every argument and the
        launch line expands `%*` to nothing, silently dropping flags the user
        passed - `start.bat --api-key sk-x` would start an open server. The
        arguments must therefore be copied into a variable before any shift,
        and that variable must be what the launch line expands.
        """
        capture = self.bat.index('set "USERARGS=%*"')
        loop = self.bat.index(":scanargs")
        launch = self.bat.index("-m gemini_web2api --port")
        self.assertLess(capture, loop,
                        "arguments must be captured before :scanargs shifts them")
        self.assertLess(loop, launch)
        self.assertIn("--port %PORT% %USERARGS%", self.bat)
        # A bare %* on the launch line would reintroduce the bug.
        self.assertNotIn("--port %PORT% %*", self.bat)

    def test_shift_loop_terminates(self):
        """A `goto` loop with no exit is a hung window on double-click."""
        self.assertIn('if "%~1"=="" goto :argsdone', self.bat)
        self.assertIn(":argsdone", self.bat)
        self.assertGreater(self.bat.index(":argsdone"), self.bat.index(":scanargs"))

    def test_cli_port_overrides_the_config(self):
        self.assertIn("ARGPORT", self.bat)

    def test_setup_helper_is_valid_python(self):
        ast.parse(self.setup)

    def test_setup_helper_defaults_to_localhost_only(self):
        """A desktop launcher must not expose an open proxy to the LAN."""
        self.assertIn('SAFE_HOST = "127.0.0.1"', self.setup)
        self.assertIn('config["host"] = SAFE_HOST', self.setup)

    def test_setup_helper_leaves_an_existing_config_alone(self):
        self.assertIn("if os.path.exists(path)", self.setup)
        self.assertIn("leaving it untouched", self.setup)

    def test_setup_helper_reads_defaults_from_the_package(self):
        """Copying config.example.json would drift; DEFAULT_CONFIG cannot.

        The module docstring names config.example.json to explain why it is
        *not* read, so only the code is scanned here.
        """
        self.assertIn("DEFAULT_CONFIG", self.setup)
        self.assertIn("from gemini_web2api.config import DEFAULT_CONFIG", self.setup)

        # Docstrings at every level name the template to explain why it is not
        # read, so collect only string literals that are actual code.
        tree = ast.parse(self.setup)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
                body = getattr(node, "body", None)
                if body and isinstance(body[0], ast.Expr) and \
                        isinstance(body[0].value, ast.Constant) and \
                        isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))
        code_strings = [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings
        ]
        offenders = [s for s in code_strings if "config.example.json" in s]
        self.assertEqual(offenders, [],
                         f"setup helper reads the template instead of DEFAULT_CONFIG: {offenders}")

    def test_setup_helper_prints_a_parseable_contract(self):
        self.assertIn('print(f"PORT={port}")', self.setup)
        self.assertIn('print(f"CREATED={', self.setup)

    def test_setup_helper_keeps_prose_off_stdout(self):
        """stdout is parsed by the batch file, so messages must go to stderr."""
        self.assertIn("file=sys.stderr", self.setup)

    def test_launcher_is_git_tracked_and_not_ignored(self):
        """A launcher that .gitignore swallows never reaches the user."""
        result = subprocess.run(
            [sys.executable, "-c",
             "import subprocess,sys;"
             "sys.exit(subprocess.run(['git','check-ignore','-q','start.bat']).returncode)"],
            cwd=REPO_ROOT, capture_output=True)
        self.assertEqual(result.returncode, 1, "start.bat is ignored by git")

    def test_launcher_uses_crlf_line_endings(self):
        """cmd.exe locates `goto` labels by scanning for CR-terminated lines.

        A LF-only batch file can fail to find a label or mis-parse a
        parenthesised block, and this launcher depends on `goto :fail`, a
        `:scanargs` loop and several `if ... ( ... )` blocks. The bytes are
        checked rather than git's view of them, because what reaches the user's
        disk is what actually runs.
        """
        with open(self.bat_path, "rb") as handle:
            raw = handle.read()
        crlf = raw.count(b"\r\n")
        bare_lf = raw.count(b"\n") - crlf
        self.assertEqual(bare_lf, 0,
                         f"start.bat has {bare_lf} LF-only line(s); cmd.exe needs CRLF")
        self.assertGreater(crlf, 100, "start.bat looks truncated")

    def test_gitattributes_stores_the_launcher_as_crlf(self):
        """`text eol=crlf` only converts at checkout, so a GitHub ZIP download
        or `git archive` would still hand out LF-only bytes. `-text` stores the
        CRLF verbatim, making every retrieval path safe."""
        attrs_path = os.path.join(REPO_ROOT, ".gitattributes")
        self.assertTrue(os.path.exists(attrs_path), ".gitattributes is missing")
        with open(attrs_path, encoding="utf-8") as handle:
            lines = [line.strip() for line in handle
                     if line.strip() and not line.startswith("#")]
        # Last matching pattern wins in .gitattributes, so the generic rule must
        # precede the override or it silently undoes it.
        order = {pat.split()[0]: i for i, pat in enumerate(lines) if pat.split()}
        self.assertIn("*", order)
        self.assertIn("*.bat", order)
        self.assertLess(order["*"], order["*.bat"],
                        "`* text=auto` must come before `*.bat -text` or it overrides it")
        bat_rule = next(line for line in lines if line.startswith("*.bat"))
        self.assertIn("-text", bat_rule,
                      f"*.bat must disable conversion, got: {bat_rule}")

    def test_git_stores_the_launcher_blob_as_crlf(self):
        """Guards the retrieval paths that bypass a working-tree checkout."""
        result = subprocess.run(["git", "ls-files", "--eol", "start.bat"],
                                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("i/crlf", result.stdout,
                      f"the committed blob is not CRLF: {result.stdout.strip()}")

    def test_ci_lints_every_python_directory(self):
        """scripts/ holds real Python; a lint job that skips it lets rot in."""
        ci = os.path.join(REPO_ROOT, ".github", "workflows", "ci.yml")
        with open(ci, encoding="utf-8") as handle:
            text = handle.read()
        for directory in ("gemini_web2api", "tests", "scripts"):
            self.assertRegex(text, rf"ruff check[^\n]*\b{directory}\b",
                             f"CI does not lint {directory}/")
            self.assertRegex(text, rf"compileall[^\n]*\b{directory}\b",
                             f"CI does not compile-check {directory}/")

    def test_venv_is_ignored(self):
        """The launcher creates .venv; it must never be committed."""
        result = subprocess.run(["git", "check-ignore", "-q", ".venv/"],
                                cwd=REPO_ROOT, capture_output=True)
        self.assertEqual(result.returncode, 0, ".venv/ is not gitignored")


class WindowsSetupHelperTests(unittest.TestCase):
    """Behavioural tests for scripts/win_setup.py (the .bat itself cannot run)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
        self.addCleanup(sys.path.pop, 0)
        for name in list(sys.modules):
            if name == "win_setup":
                del sys.modules[name]
        self.mod = importlib.import_module("win_setup")
        # The helper narrates its progress on stderr for the batch file's user.
        # Swallow it here so stray chatter cannot mask a real failure in the
        # test output; test_main_prints_the_contract_on_stdout checks stdout,
        # which is the part the batch file actually parses.
        patcher = mock.patch.object(self.mod, "say", lambda *a, **k: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_creates_config_when_absent(self):
        path = os.path.join(self.tmp, "config.json")
        port, created = self.mod.ensure_config(path)
        self.assertTrue(created)
        self.assertEqual(port, 8081)
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["host"], "127.0.0.1")
        self.assertEqual(data["api_keys"], [])

    def test_generated_config_covers_every_default_key(self):
        from gemini_web2api.config import DEFAULT_CONFIG
        path = os.path.join(self.tmp, "config.json")
        self.mod.ensure_config(path)
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(set(data), set(DEFAULT_CONFIG))

    def test_is_idempotent(self):
        path = os.path.join(self.tmp, "config.json")
        self.mod.ensure_config(path)
        with open(path, encoding="utf-8") as handle:
            first = handle.read()
        _port, created = self.mod.ensure_config(path)
        self.assertFalse(created)
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), first)

    def test_preserves_a_user_port(self):
        path = os.path.join(self.tmp, "config.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"port": 9111, "host": "0.0.0.0"}, handle)
        port, created = self.mod.ensure_config(path)
        self.assertEqual(port, 9111)
        self.assertFalse(created)
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["host"], "0.0.0.0")

    def test_survives_a_corrupt_config(self):
        path = os.path.join(self.tmp, "config.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{ this is not json")
        port, created = self.mod.ensure_config(path)
        self.assertEqual(port, 8081)
        self.assertFalse(created)

    def test_rejects_an_out_of_range_port(self):
        path = os.path.join(self.tmp, "config.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"port": 99999}, handle)
        port, _created = self.mod.ensure_config(path)
        self.assertEqual(port, 8081)

    def test_main_prints_the_contract_on_stdout(self):
        import contextlib
        import io
        path = os.path.join(self.tmp, "config.json")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = self.mod.main([path])
        self.assertEqual(code, 0)
        lines = out.getvalue().strip().splitlines()
        self.assertEqual(lines, ["PORT=8081", "CREATED=1"])


class SyntaxTests(unittest.TestCase):
    def test_every_python_file_compiles(self):
        failures = []
        for directory, subdirs, files in os.walk(REPO_ROOT):
            subdirs[:] = [d for d in subdirs if d not in (".git", "__pycache__", ".venv")]
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(directory, name)
                with open(path, encoding="utf-8") as handle:
                    source = handle.read()
                try:
                    compile(source, path, "exec")
                except SyntaxError as exc:
                    failures.append(f"{path}: {exc}")
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
