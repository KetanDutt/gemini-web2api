"""Packaging and repository structure.

These guard the two structural defects that made the project unusable as a
distributed package: setuptools could not build it at all, and the same server
existed twice in divergent copies.
"""
import ast
import importlib
import os
import re
import subprocess
import sys
import unittest

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
