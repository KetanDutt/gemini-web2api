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


def _git_repo_available():
    """True when REPO_ROOT sits inside a working git repository.

    An sdist or a `git archive` checkout has no `.git`. Git-based guards must
    *skip* there rather than fail — but they must not pass vacuously either,
    which is the trap: `git ls-files` outside a repo prints its fatal error to
    stderr and leaves stdout empty, so an assertion of "stdout is empty" is
    satisfied by git being broken or absent. Every git test here therefore
    skips when there is no repository and asserts a zero exit code when there
    is, so a green run means git actually answered.
    """
    try:
        result = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                                cwd=REPO_ROOT, capture_output=True, text=True)
    except OSError:
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


HAS_GIT = _git_repo_available()
NO_GIT = "not a git working tree (sdist or git archive checkout)"


def _repo_metadata_guarded(*parts):
    """True when a guard on repository metadata should run.

    `.gitignore`, `.gitattributes` and `.github/` are VCS and CI metadata. They
    are deliberately absent from a source distribution, where they would mean
    nothing, so guards on them skip there. But inside a git working tree their
    absence is a defect, so the guard still runs and fails loudly. Skipping
    whenever the file is merely missing would turn a deleted `.gitattributes`
    into a silent pass - the same vacuous-green trap as a piped test runner.

    Note this differs from `HAS_GIT`: a `git archive` tree has no `.git` yet
    still carries every tracked file, so these guards run there too.
    """
    return os.path.exists(os.path.join(REPO_ROOT, *parts)) or HAS_GIT


# Files setuptools puts in every sdist whatever MANIFEST.in says.
_SDIST_ALWAYS_INCLUDED = {"pyproject.toml", "MANIFEST.in", "LICENSE", "README.md"}

# Files that must never be in a distribution: VCS/CI metadata is meaningless
# outside a repository, and the rest are local secrets MANIFEST.in excludes.
_SDIST_NEVER_INCLUDED = {".gitignore", ".gitattributes", "config.json",
                         "cookie.txt", "cookie.json", "gemini-auth.json", ".env"}


def _manifest_include_names():
    """The root-level names MANIFEST.in really includes, comments ignored.

    Parsed rather than substring-matched: a comment mentioning a filename would
    otherwise satisfy the check, which is how the `pipefail` guard was defeated
    by its own explanatory prose.
    """
    names = set()
    with open(os.path.join(REPO_ROOT, "MANIFEST.in"), encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if parts[0] == "include":
                names.update(parts[1:])
    return names


HAS_GITIGNORE = _repo_metadata_guarded(".gitignore")
HAS_GITATTRIBUTES = _repo_metadata_guarded(".gitattributes")
HAS_CI_WORKFLOW = _repo_metadata_guarded(".github", "workflows", "ci.yml")
NO_REPO_METADATA = "repository metadata is not part of a source distribution"


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

    @unittest.skipUnless(HAS_GIT, NO_GIT)
    def test_no_stale_bytecode_is_committed(self):
        result = subprocess.run(["git", "ls-files", "*/__pycache__/*", "*.pyc"],
                                capture_output=True, text=True, cwd=REPO_ROOT)
        # Without this, an empty stdout caused by git failing would look like a
        # clean repository.
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    @unittest.skipUnless(HAS_GITIGNORE, NO_REPO_METADATA)
    def test_secrets_are_gitignored(self):
        with open(os.path.join(REPO_ROOT, ".gitignore"), encoding="utf-8") as handle:
            ignored = handle.read()
        for pattern in ("config.json", "cookie.txt", "gemini-auth.json", ".env"):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, ignored)

    @unittest.skipUnless(HAS_GIT, NO_GIT)
    def test_no_secret_files_are_tracked(self):
        result = subprocess.run(
            ["git", "ls-files", "config.json", "cookie.txt", "cookie.json", "gemini-auth.json"],
            capture_output=True, text=True, cwd=REPO_ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_no_dead_code_left_behind(self):
        """Helpers that were defined but never called have been removed."""
        with open(os.path.join(PACKAGE_DIR, "tools.py"), encoding="utf-8") as handle:
            tools = handle.read()
        self.assertNotIn("_compress_b64_if_needed", tools)
        self.assertNotIn("MAX_IMAGE_B64_SIZE", tools)

    def test_the_sdist_ships_every_file_the_docs_promise(self):
        """A distribution must not contradict the documentation inside it.

        The sdist shipped `docs/DEPLOYMENT.md`, which walks the reader through
        `docker-compose.yml` and the `Dockerfile`, and a README documenting
        `python gemini_web2api.py` and embedding `logo.png` — while MANIFEST.in
        omitted all four. Anyone installing from source got instructions
        pointing at files that were not there, and on PyPI the rendered README
        showed a broken image. Nothing failed loudly, because an sdist is only
        ever read, never imported.

        Only files that actually exist at the repository root are considered, so
        prose about a `config.json` the reader creates, or a
        `docker-compose.override.yml` they may write, is not a false positive.
        """
        pattern = re.compile(
            r"\b[\w.-]+\.(?:py|png|yml|yaml|json|txt|bat|md)\b"
            r"|\bDockerfile\b"
            r"|\.(?:dockerignore|gitignore|gitattributes|env)\b")
        doc_paths = ["README.md", "README_CN.md"]
        docs_dir = os.path.join(REPO_ROOT, "docs")
        doc_paths += [os.path.join("docs", n) for n in sorted(os.listdir(docs_dir))
                      if n.endswith(".md")]
        referenced = set()
        for relative in doc_paths:
            with open(os.path.join(REPO_ROOT, relative), encoding="utf-8") as handle:
                referenced.update(pattern.findall(handle.read()))
        existing = {name for name in os.listdir(REPO_ROOT)
                    if os.path.isfile(os.path.join(REPO_ROOT, name))}
        promised = sorted(referenced & existing)
        # Guards the guard: a regex that matched nothing would pass vacuously.
        self.assertGreater(len(promised), 5,
                           f"the doc scan found only {promised} - the pattern is broken")
        included = _manifest_include_names()
        missing = [name for name in promised
                   if name not in included
                   and name not in _SDIST_ALWAYS_INCLUDED
                   and name not in _SDIST_NEVER_INCLUDED]
        self.assertEqual(
            missing, [],
            f"the shipped documentation tells the reader to use these root "
            f"files, but MANIFEST.in leaves them out of the sdist: {missing}")
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
        """AUDIT.md is an internal working document, so it is exempt.

        Both READMEs are checked, not just the English one. CONTRIBUTING.md was
        added to README.md and the omission from README_CN.md went unnoticed
        precisely because this guard only read one file - a translation that
        silently lists fewer documents than the original is the kind of drift
        nobody reports, since each page still works when linked.
        """
        docs_dir = os.path.join(REPO_ROOT, "docs")
        pages = [f for f in sorted(os.listdir(docs_dir))
                 if f.endswith(".md") and f not in ("README.md", "AUDIT.md")]
        self.assertTrue(pages, "docs/ contains no pages")
        for readme_name in ("README.md", "README_CN.md"):
            readme = self._read(readme_name)
            missing = [page for page in pages if f"docs/{page}" not in readme]
            self.assertEqual(missing, [],
                             f"docs pages not linked from {readme_name}: {missing}")

    def test_both_readmes_link_the_same_doc_set(self):
        """The two READMEs are parallel documents; their doc indexes must agree.

        Catches a page linked from one and not the other even when both happen
        to satisfy the reachability rule above (e.g. an incidental mention).
        """
        pattern = re.compile(r"docs/([A-Z]+\.md)")
        sets = {}
        for readme_name in ("README.md", "README_CN.md"):
            sets[readme_name] = set(pattern.findall(self._read(readme_name)))
        self.assertEqual(sets["README.md"], sets["README_CN.md"],
                         f"README doc indexes diverge: "
                         f"only in EN={sorted(sets['README.md'] - sets['README_CN.md'])}, "
                         f"only in CN={sorted(sets['README_CN.md'] - sets['README.md'])}")

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


def _docker_pattern_matches(pattern, path):
    """Approximate Docker's .dockerignore matching.

    Docker uses Go's ``filepath.Match``, where ``*`` does **not** cross a path
    separator and ``**`` does. Python's ``fnmatch`` differs: its ``*`` becomes
    ``.*`` and happily crosses ``/``, which would make ``*.md`` match
    ``docs/API.md`` and report false exclusions. A directory pattern also
    excludes everything beneath it.
    """
    pattern = pattern.rstrip("/")
    if path == pattern or path.startswith(pattern + "/"):
        return True
    regex = ""
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if pattern[i:i + 2] == "**":
                regex += ".*"
                i += 2
                continue
            regex += "[^/]*"
        elif char == "?":
            regex += "[^/]"
        elif char in ".+^${}()|[]\\":
            regex += "\\" + char
        else:
            regex += char
        i += 1
    return re.fullmatch(regex, path) is not None


def _dockerignore_patterns(text):
    return [line.strip() for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")]


def _excluded_by_dockerignore(path, patterns):
    """Whether ``path`` is outside the build context. Last match wins."""
    excluded = False
    for pattern in patterns:
        negate = pattern.startswith("!")
        if negate:
            pattern = pattern[1:]
        if _docker_pattern_matches(pattern, path):
            excluded = not negate
    return excluded


def _dockerfile_copy_sources(text):
    """Every source path named by a COPY instruction, in order.

    Handles backslash continuations and ``--chown``/``--from`` flags. The final
    token of each instruction is the destination, not a source.
    """
    joined = []
    buffer = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.endswith("\\"):
            buffer += stripped[:-1].strip() + " "
            continue
        joined.append(buffer + stripped)
        buffer = ""
    if buffer:
        joined.append(buffer)

    sources = []
    for line in joined:
        if not line.upper().startswith("COPY "):
            continue
        tokens = [t for t in line[5:].split() if not t.startswith("--")]
        if len(tokens) < 2:
            continue
        sources.extend(tokens[:-1])
    return sources


class DockerfileTests(unittest.TestCase):
    """The Dockerfile and .dockerignore must agree.

    CI is the only place this repository's image gets built, so a conflict
    between the two files cannot be caught locally - and it cost a failed build
    to find this one. `COPY README.md LICENSE` had been added while `*.md` and
    `LICENSE` were added to .dockerignore, so neither file reached the build
    context. Both checks below are static, which is the point: they run
    anywhere, with no container runtime.
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO_ROOT, "Dockerfile"), encoding="utf-8") as handle:
            cls.dockerfile = handle.read()
        with open(os.path.join(REPO_ROOT, ".dockerignore"), encoding="utf-8") as handle:
            cls.dockerignore = handle.read()
        cls.patterns = _dockerignore_patterns(cls.dockerignore)
        cls.sources = _dockerfile_copy_sources(cls.dockerfile)

    def test_copy_instructions_were_found(self):
        self.assertGreaterEqual(len(self.sources), 3,
                                f"COPY parsing looks broken: {self.sources}")

    def test_no_copy_source_is_excluded_from_the_context(self):
        """The defect this class exists for: buildx fails with
        `"/README.md": not found` when a COPY source is dockerignored."""
        excluded = [s for s in self.sources
                    if _excluded_by_dockerignore(s.rstrip("/"), self.patterns)]
        self.assertEqual(excluded, [],
                         f"Dockerfile COPYs paths that .dockerignore excludes: {excluded}")

    def test_every_copy_source_exists_in_the_repository(self):
        """Catches a typo'd COPY source, which fails the same way."""
        missing = [s for s in self.sources
                   if "*" not in s and not os.path.exists(os.path.join(REPO_ROOT, s))]
        self.assertEqual(missing, [], f"Dockerfile COPYs nonexistent paths: {missing}")

    def test_secrets_are_still_excluded(self):
        """Tightening the ignore list to fix a COPY must not reopen the hole
        that made an earlier image ship a baked-in `config.json`."""
        for secret in ("config.json", "cookie.txt", "cookie.json",
                       "gemini-auth.json", ".env"):
            with self.subTest(secret=secret):
                self.assertTrue(_excluded_by_dockerignore(secret, self.patterns),
                                f"{secret} would be baked into the image")

    def test_the_dockerfile_does_not_bake_in_a_config(self):
        """Copying config.example.json to config.json used to ship the image
        with the public key "sk-gemini", which looked like authentication and
        was not.

        Asserted against the parsed COPY sources, not the raw file: the
        Dockerfile's own comment names config.example.json to explain why it is
        absent, and scanning text would flag that explanation as the defect.
        """
        self.assertNotIn("config.example.json", self.sources)
        self.assertEqual([s for s in self.sources if "config" in s], [],
                         f"a config file would be baked into the image: {self.sources}")

    def test_copy_destinations_do_not_write_a_config(self):
        """The other half: a COPY could fetch a template and *land* it as
        config.json, which is how the original defect actually worked."""
        destinations = []
        for line in self.dockerfile.splitlines():
            stripped = line.strip()
            if stripped.upper().startswith("COPY "):
                tokens = [t for t in stripped[5:].split() if not t.startswith("--")]
                if len(tokens) >= 2:
                    destinations.append(tokens[-1])
        self.assertTrue(destinations)
        self.assertEqual([d for d in destinations if "config.json" in d], [],
                         f"a COPY lands on config.json: {destinations}")

    def test_licence_negation_follows_the_pattern_it_undoes(self):
        """In .dockerignore the last match wins, so `!LICENSE` placed before
        `LICENSE` would silently do nothing."""
        lines = [line.strip() for line in self.dockerignore.splitlines()
                 if line.strip() and not line.strip().startswith("#")]
        self.assertIn("LICENSE", lines)
        self.assertIn("!LICENSE", lines)
        self.assertLess(lines.index("LICENSE"), lines.index("!LICENSE"),
                        "`!LICENSE` must come after `LICENSE` or it has no effect")
        self.assertFalse(_excluded_by_dockerignore("LICENSE", self.patterns))

    def test_image_runs_as_an_unprivileged_user(self):
        self.assertIn("USER gemini", self.dockerfile)
        self.assertRegex(self.dockerfile, r"useradd\s+--uid\s+10001")

    def test_image_has_a_healthcheck(self):
        self.assertIn("HEALTHCHECK", self.dockerfile)
        self.assertIn("_healthcheck.py", self.dockerfile)

    def test_pattern_matcher_does_not_cross_separators(self):
        """Guards the guard: `*.md` must match README.md but not docs/API.md,
        or this class reports false exclusions and gets ignored."""
        self.assertTrue(_docker_pattern_matches("*.md", "README.md"))
        self.assertFalse(_docker_pattern_matches("*.md", "docs/API.md"))
        self.assertTrue(_docker_pattern_matches("docs", "docs/API.md"))
        self.assertTrue(_docker_pattern_matches("**/*.md", "docs/API.md"))
        self.assertFalse(_docker_pattern_matches("tests", "latest.md"))


def _slugify(heading):
    """GitHub's heading -> anchor rules, as implemented by github-slugger.

    Lowercase, drop inline markup markers, drop anything that is not a word
    character, space or hyphen, then replace *each* space with one hyphen.
    Collapsing space runs instead would be wrong: an em dash between two words
    is removed and leaves two adjacent spaces, which GitHub turns into a double
    hyphen. Getting this wrong produces false failures on real anchors.
    """
    text = heading.strip().lower()
    text = re.sub(r"[`*_]", "", text)
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def _anchors_in(path):
    """Every anchor a Markdown file exposes, including -1/-2 duplicates."""
    with open(path, encoding="utf-8") as handle:
        body = handle.read()
    counts = {}
    for heading in re.findall(r"^#{1,6}\s+(.*)$", body, re.MULTILINE):
        slug = _slugify(heading)
        counts[slug] = counts.get(slug, 0) + 1
    anchors = set()
    for slug, seen in counts.items():
        anchors.add(slug)
        for i in range(1, seen):
            anchors.add(f"{slug}-{i}")
    return anchors


class MarkdownLinkTests(unittest.TestCase):
    """Every relative link and heading anchor in the corpus must resolve.

    Docs that exist but point nowhere were part of the original defect class
    this release fixed. Reachability of *pages* is asserted elsewhere; this
    covers link targets and in-page anchors, which is where drift hides: a
    heading gets reworded and every link to it silently dies.
    """

    @classmethod
    def setUpClass(cls):
        cls.files = []
        roots = ["docs", "README.md", "README_CN.md", "cloudflare",
                 "gemini-cookie-sync-extension"]
        for root in roots:
            full = os.path.join(REPO_ROOT, root)
            if os.path.isfile(full):
                cls.files.append(full)
                continue
            for dirpath, dirnames, filenames in os.walk(full):
                dirnames[:] = [d for d in dirnames
                               if d not in (".git", ".venv", "__pycache__", "node_modules")]
                cls.files += [os.path.join(dirpath, f) for f in sorted(filenames)
                              if f.lower().endswith(".md")]
        cls.files.sort()
        cls._anchors = {}

    def test_corpus_was_found(self):
        self.assertGreater(len(self.files), 10, "the link scan found no markdown")

    def test_relative_links_resolve(self):
        broken = []
        for path in self.files:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                location = target.partition("#")[0]
                if not location:
                    continue
                resolved = os.path.normpath(
                    os.path.join(os.path.dirname(path), location))
                if not os.path.exists(resolved):
                    broken.append(f"{os.path.relpath(path, REPO_ROOT)} -> {target}")
        self.assertEqual(broken, [], f"broken relative links: {broken}")

    def test_heading_anchors_resolve(self):
        """An anchor is only valid if the target file really has that heading."""
        broken = []
        for path in self.files:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                location, _, fragment = target.partition("#")
                if not fragment:
                    continue
                resolved = os.path.normpath(
                    os.path.join(os.path.dirname(path), location)) if location else path
                if not os.path.exists(resolved):
                    continue  # reported by test_relative_links_resolve
                if resolved not in self._anchors:
                    self._anchors[resolved] = _anchors_in(resolved)
                if fragment.lower() not in self._anchors[resolved]:
                    broken.append(f"{os.path.relpath(path, REPO_ROOT)} -> {target}")
        self.assertEqual(broken, [], f"broken heading anchors: {broken}")

    def test_slugifier_handles_an_em_dash_between_words(self):
        """Guards the guard: a space-collapsing slugifier would pass this test
        suite while reporting false breakages on real anchors."""
        self.assertEqual(
            _slugify("1.2 Two divergent implementations — `CRITICAL`"),
            "12-two-divergent-implementations--critical")

    def test_slugifier_strips_punctuation_and_inline_code(self):
        self.assertEqual(_slugify("Editing `start.bat`"), "editing-startbat")
        self.assertEqual(_slugify("Outbound: authenticating to Google"),
                         "outbound-authenticating-to-google")


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

    def test_every_module_parses_under_python_38_grammar(self):
        """Post-3.8 *syntax* is rejected by the parser itself, on any version.

        `ast.parse(..., feature_version=(3, 8))` applies 3.8 grammar whatever
        the host interpreter, so this catches `match`/`case` (3.10), `except*`
        (3.11) and parenthesised `with` (3.9/3.10) in one assertion. It
        replaces an `ast.Match` walk that had to skip when `ast.Match` did not
        exist - which meant the guard was absent on 3.8 and 3.9, the very
        interpreters it existed to protect. On 3.8 it now proves the real
        thing: the modules parse.

        PEP 585/604 annotations and `dict |=` are deliberately *not* covered
        here. They are valid 3.8 grammar and fail at runtime instead, which is
        what the AST-walk tests alongside this one are for.
        """
        offenders = []
        targets = [os.path.join(REPO_ROOT, "gemini_web2api.py")]
        for directory in ("gemini_web2api", "scripts", "tests"):
            for dirpath, subdirs, files in os.walk(os.path.join(REPO_ROOT, directory)):
                subdirs[:] = [d for d in subdirs if d != "__pycache__"]
                for name in sorted(files):
                    if name.endswith(".py"):
                        targets.append(os.path.join(dirpath, name))
        self.assertGreater(len(targets), 20, "the scan found almost nothing")
        for path in targets:
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            try:
                ast.parse(source, filename=path, feature_version=(3, 8))
            except SyntaxError as exc:
                offenders.append(f"{os.path.relpath(path, REPO_ROOT)}:{exc.lineno}: {exc.msg}")
        self.assertEqual(
            offenders, [],
            f"this is syntax newer than Python 3.8, which requires-python "
            f"claims to support: {offenders}")

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

    @unittest.skipUnless(HAS_GIT, NO_GIT)
    def test_launcher_is_git_tracked_and_not_ignored(self):
        """A launcher that .gitignore swallows never reaches the user."""
        result = subprocess.run(["git", "check-ignore", "-q", "start.bat"],
                                cwd=REPO_ROOT, capture_output=True, text=True)
        # check-ignore exits 1 when the path is NOT ignored, 0 when it is.
        self.assertEqual(result.returncode, 1, f"start.bat is ignored by git: {result.stdout}")
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "start.bat"],
                                 cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(tracked.returncode, 0,
                         f"start.bat is not tracked by git: {tracked.stderr}")

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

    @unittest.skipUnless(HAS_GITATTRIBUTES, NO_REPO_METADATA)
    def test_gitattributes_stores_the_launcher_as_crlf(self):
        """`text eol=crlf` only converts at checkout, so a GitHub ZIP download
        or `git archive` would still hand out LF-only bytes. `-text` stores the
        CRLF verbatim, making every retrieval path safe."""
        attrs_path = os.path.join(REPO_ROOT, ".gitattributes")
        # Reached only when the file exists or this is a git tree, where its
        # absence is a defect rather than a reason to skip.
        self.assertTrue(os.path.exists(attrs_path),
                        ".gitattributes is missing from the repository")
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

    @unittest.skipUnless(HAS_GIT, NO_GIT)
    def test_git_stores_the_launcher_blob_as_crlf(self):
        """Guards the retrieval paths that bypass a working-tree checkout."""
        result = subprocess.run(["git", "ls-files", "--eol", "start.bat"],
                                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("i/crlf", result.stdout,
                      f"the committed blob is not CRLF: {result.stdout.strip()}")

    @unittest.skipUnless(HAS_GIT, NO_GIT)
    def test_venv_is_ignored(self):
        """The launcher creates .venv; it must never be committed."""
        result = subprocess.run(["git", "check-ignore", "-q", ".venv/"],
                                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
                         f".venv/ is not gitignored: {result.stdout}")


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


def _workflow_run_blocks(text):
    """Return ``[(step_name, script)]`` for every ``run:`` block in a workflow.

    A hand-rolled scan rather than a YAML parse: this suite has to run with the
    standard library alone (the stdlib-only CI job proves that), and pyyaml is
    deliberately not a dependency.
    """
    blocks = []
    lines = text.splitlines()
    name = ""
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("- name:"):
            name = stripped.split("- name:", 1)[1].strip()
        elif stripped.startswith("name:"):
            name = stripped.split("name:", 1)[1].strip()
        match = (re.match(r"^(\s*)-\s+run:\s*(.*)$", line)
                 or re.match(r"^(\s*)run:\s*(.*)$", line))
        if match:
            indent = len(match.group(1))
            rest = match.group(2).strip()
            if rest in ("|", "|-", ">", ">-", "|+", ">+"):
                body = []
                index += 1
                while index < len(lines):
                    following = lines[index]
                    if not following.strip():
                        body.append("")
                        index += 1
                        continue
                    if len(following) - len(following.lstrip()) <= indent:
                        break
                    body.append(following)
                    index += 1
                blocks.append((name, "\n".join(body)))
                continue
            if rest:
                blocks.append((name, rest))
        index += 1
    return blocks


def _suite_lines(script):
    """The lines of ``script`` that actually invoke the test suite."""
    return [line for line in _shell_commands(script)
            if re.search(r"python\s+-m\s+unittest", line)]


def _shell_commands(script):
    """The executable lines of a shell block, with comments dropped.

    Scanning raw text lets prose defeat the assertion: this block's own
    comment says "WITHOUT pipefail", so a check for that word passes on a
    script that never enables it. Only real commands should count.
    """
    return [line for line in script.splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def _enables_pipefail(script):
    """True only if the block turns pipefail on with an actual ``set``."""
    return any(re.search(r"\bset\b.*\bpipefail\b", line)
               for line in _shell_commands(script))


def _pipes_status_away(line):
    """True when the line hands its exit status to the command on its right.

    ``||`` and ``2>&1`` are a short-circuit and a redirection, not pipelines;
    only a bare ``|`` makes the exit status belong to whatever follows it.
    """
    return "|" in line.replace("||", "")


@unittest.skipUnless(HAS_CI_WORKFLOW, NO_REPO_METADATA)
class CIWorkflowTests(unittest.TestCase):
    """CI must fail when the suite fails.

    These guards exist because this branch shipped exactly that defect. The
    test step was rewritten as ``python -m unittest ... | tee out.txt`` so the
    skip audit could be logged. Actions' default shell on Linux is
    ``bash -e {0}`` - note the missing ``pipefail`` - so a pipeline reports the
    status of its *last* command. ``tee`` always succeeds, which means all four
    matrix jobs would have gone green no matter what the suite did. The
    checkmark stayed green while the thing it verified stopped being verified.

    Piping a test runner into a logger is a natural edit to make, so the
    invariant is asserted here rather than left to code review.
    """

    @classmethod
    def setUpClass(cls):
        cls.path = os.path.join(REPO_ROOT, ".github", "workflows", "ci.yml")
        with open(cls.path, encoding="utf-8") as handle:
            cls.text = handle.read()
        cls.run_blocks = _workflow_run_blocks(cls.text)
        cls.suite_scripts = [script for _, script in cls.run_blocks
                             if _suite_lines(script)]

    def test_the_run_blocks_are_actually_parsed(self):
        """Guards the guard: an empty parse makes every check below vacuous."""
        self.assertTrue(self.run_blocks, "no `run:` blocks found in ci.yml")
        self.assertGreaterEqual(
            len(self.suite_scripts), 2,
            "expected at least the matrix job and the stdlib-only job to run "
            f"the suite, found {len(self.suite_scripts)} - the parser is "
            "probably broken, which would make the guards below pass vacuously")

    def test_a_piped_suite_run_cannot_swallow_the_exit_status(self):
        offenders = []
        for name, script in self.run_blocks:
            if not _suite_lines(script):
                continue
            for line in _suite_lines(script):
                if _pipes_status_away(line) and not _enables_pipefail(script):
                    offenders.append(f"{name}: {line.strip()}")
        self.assertEqual(
            offenders, [],
            "the suite's exit status is piped into a command that always "
            "succeeds and the block never sets `pipefail`. Actions' default "
            "shell is `bash -e` without it, so a failing suite would still be "
            "reported as a pass: " + "; ".join(offenders))

    def test_a_captured_exit_status_is_re_raised(self):
        for name, script in self.run_blocks:
            if not _suite_lines(script):
                continue
            commands = "\n".join(_shell_commands(script))
            for variable in re.findall(r"\|\|\s*(\w+)=\$\?", commands):
                self.assertRegex(
                    commands, r"exit\s+\$\{?" + re.escape(variable),
                    f"{name} captures ${variable} from the suite but never "
                    "exits with it, so the step ends on the status of its last "
                    "command and the failure is discarded")

    def test_the_suite_step_reports_what_it_skipped(self):
        """A guard that silently skips is indistinguishable from one that ran.

        Two families of guards here are skip-gated: the Node-backed dashboard
        checks and the git-backed launcher checks. If the tool they need is
        missing they skip, the step still exits 0, and CI stays green while
        covering less than it claims. Surfacing the count costs one grep.
        """
        reporting = [script for script in self.suite_scripts
                     if any("skipped" in line for line in _shell_commands(script))]
        self.assertTrue(
            reporting,
            "no test job reports its skipped tests, so a missing node or git "
            "would quietly remove guards from CI with no visible signal")

    def test_ci_lints_every_python_directory(self):
        """scripts/ holds real Python; a lint job that skips it lets rot in."""
        for directory in ("gemini_web2api", "tests", "scripts"):
            self.assertRegex(self.text, rf"ruff check[^\n]*\b{directory}\b",
                             f"CI does not lint {directory}/")
            self.assertRegex(self.text, rf"compileall[^\n]*\b{directory}\b",
                             f"CI does not compile-check {directory}/")


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
