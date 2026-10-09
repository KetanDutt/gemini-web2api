"""The Windows application: build script, launcher and first-run behaviour.

PyInstaller cannot run on the Linux CI runners, so these tests check the parts
that are decidable without it: the command the build would run, the artifact
layout, the platform guard, the launcher's file handling, and the server hook
it depends on. A separate CI job builds the real executable on windows-latest.
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_build_script():
    """Load scripts/build_windows.py by path: it is a script, not a package."""
    spec = importlib.util.spec_from_file_location(
        "build_windows", os.path.join(REPO_ROOT, "scripts", "build_windows.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bw = _load_build_script()

from gemini_web2api import __version__, windows  # noqa: E402

# Repository metadata (.github, .gitattributes, .gitignore) is absent from an
# sdist. These guards skip there and still run inside a git checkout.
from .test_packaging import (  # noqa: E402
    HAS_CI_WORKFLOW,
    HAS_GITATTRIBUTES,
    HAS_GITIGNORE,
    NO_REPO_METADATA,
)


class PlatformGuardTests(unittest.TestCase):
    def test_refuses_to_build_on_other_systems(self):
        for system in ("Linux", "Darwin"):
            with self.subTest(system=system), self.assertRaises(bw.BuildError) as ctx:
                bw.check_platform(system)
            self.assertIn("must run on Windows", str(ctx.exception))

    def test_builds_on_windows(self):
        bw.check_platform("Windows")  # no exception

    def test_force_overrides_the_guard_for_testing(self):
        bw.check_platform("Linux", force=True)


class PyInstallerCommandTests(unittest.TestCase):
    def setUp(self):
        self.root = REPO_ROOT

    def test_onedir_is_the_default_layout(self):
        args = bw.pyinstaller_args(self.root, onefile=False)
        self.assertIn("--onedir", args)
        self.assertNotIn("--onefile", args)

    def test_onefile_switches_the_layout(self):
        args = bw.pyinstaller_args(self.root, onefile=True)
        self.assertIn("--onefile", args)
        self.assertNotIn("--onedir", args)

    def test_entry_point_is_the_windows_launcher(self):
        args = bw.pyinstaller_args(self.root, onefile=False)
        self.assertEqual(os.path.join(self.root, "scripts", "windows_entry.py"), args[-1])

    def test_package_submodules_are_collected(self):
        args = bw.pyinstaller_args(self.root, onefile=False)
        index = args.index("--collect-submodules")
        self.assertEqual("gemini_web2api", args[index + 1])

    def test_console_app_named_after_the_project(self):
        args = bw.pyinstaller_args(self.root, onefile=False)
        self.assertIn("--console", args)
        self.assertEqual("gemini-web2api", args[args.index("--name") + 1])

    def test_icon_is_passed_only_when_it_exists(self):
        missing = os.path.join(self.root, "build", "windows", "does-not-exist.ico")
        self.assertNotIn("--icon", bw.pyinstaller_args(self.root, onefile=False, icon_path=missing))
        with tempfile.TemporaryDirectory() as tmp:
            icon = os.path.join(tmp, "icon.ico")
            with open(icon, "wb") as handle:
                handle.write(b"x")
            args = bw.pyinstaller_args(self.root, onefile=False, icon_path=icon)
            self.assertEqual(icon, args[args.index("--icon") + 1])

    def test_build_output_stays_inside_the_build_and_dist_folders(self):
        args = bw.pyinstaller_args(self.root, onefile=False)
        self.assertEqual(os.path.join(self.root, "dist", "windows"),
                         args[args.index("--distpath") + 1])
        self.assertEqual(os.path.join(self.root, "build", "pyinstaller"),
                         args[args.index("--workpath") + 1])
        self.assertEqual(os.path.join(self.root, "build", "pyinstaller"),
                         args[args.index("--specpath") + 1])


class ArtifactLayoutTests(unittest.TestCase):
    def test_onedir_executable_sits_in_a_named_folder(self):
        paths = bw.artifact_paths(REPO_ROOT, "1.2.3", onefile=False, system="Windows")
        self.assertEqual(os.path.join(REPO_ROOT, "dist", "windows", "gemini-web2api"),
                         paths["folder"])
        self.assertEqual(os.path.join(paths["folder"], "gemini-web2api.exe"), paths["exe"])

    def test_onefile_executable_sits_directly_in_dist(self):
        paths = bw.artifact_paths(REPO_ROOT, "1.2.3", onefile=True, system="Windows")
        self.assertIsNone(paths["folder"])
        self.assertEqual(os.path.join(REPO_ROOT, "dist", "windows", "gemini-web2api.exe"),
                         paths["exe"])

    def test_archive_name_carries_the_package_version(self):
        paths = bw.artifact_paths(REPO_ROOT, "1.2.3", onefile=False, system="Windows")
        self.assertEqual("gemini-web2api-1.2.3-windows-x64.zip",
                         os.path.basename(paths["zip"]))

    def test_script_reads_the_same_version_as_the_package(self):
        self.assertEqual(__version__, bw.read_version(REPO_ROOT))

    def test_venv_python_uses_the_windows_layout(self):
        self.assertTrue(bw.venv_python(REPO_ROOT, "Windows").endswith(
            os.path.join(".venv-build", "Scripts", "python.exe")))
        self.assertTrue(bw.venv_python(REPO_ROOT, "Linux").endswith(
            os.path.join(".venv-build", "bin", "python")))

    def test_bootstrap_installs_runtime_then_build_requirements(self):
        commands = bw.bootstrap_commands(REPO_ROOT, "Windows")
        installs = [cmd for cmd in commands if "install" in cmd and "-r" in cmd]
        self.assertEqual(2, len(installs))
        self.assertTrue(installs[0][-1].endswith("requirements.txt"))
        self.assertTrue(installs[1][-1].endswith("requirements-windows-build.txt"))


class ArchiveTests(unittest.TestCase):
    def test_archive_contains_the_folder_and_the_license_but_no_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "dist", "windows", "gemini-web2api")
            os.makedirs(os.path.join(folder, "_internal"))
            with open(os.path.join(folder, "gemini-web2api.exe"), "wb") as handle:
                handle.write(b"MZ")
            with open(os.path.join(folder, "_internal", "base.dll"), "wb") as handle:
                handle.write(b"dll")
            # A stray config.json with secrets must never reach the archive.
            with open(os.path.join(folder, "config.json"), "w") as handle:
                handle.write('{"api_keys": ["sk-secret"]}')
            with open(os.path.join(tmp, "LICENSE"), "w") as handle:
                handle.write("MIT")
            with open(os.path.join(tmp, "config.example.json"), "w") as handle:
                handle.write("{}")

            paths = {
                "dist": os.path.join(tmp, "dist", "windows"),
                "folder": folder,
                "exe": os.path.join(folder, "gemini-web2api.exe"),
                "zip": os.path.join(tmp, "dist", "windows", "out.zip"),
            }
            import zipfile

            archive = bw.make_archive(tmp, paths, onefile=False, version="9.9.9",
                                      system="Windows")
            with zipfile.ZipFile(archive) as bundle:
                names = set(bundle.namelist())
                self.assertIn("gemini-web2api/gemini-web2api.exe", names)
                self.assertIn("gemini-web2api/_internal/base.dll", names)
                self.assertIn("gemini-web2api/LICENSE", names)
                self.assertIn("gemini-web2api/config.example.json", names)
                self.assertFalse([n for n in names if n.endswith("config.json")],
                                 "config.json must not ship")


class BatchLauncherTests(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(REPO_ROOT, "build_windows.bat"), "rb") as handle:
            self.raw = handle.read()

    def test_uses_crlf_endings_for_cmd_exe(self):
        # cmd.exe finds labels by scanning CR-terminated lines.
        self.assertIn(b"\r\n", self.raw)
        self.assertEqual(0, len(re.findall(rb"(?<!\r)\n", self.raw)),
                         "every line break must be CRLF")

    def test_hands_off_to_the_python_build_script(self):
        text = self.raw.decode("ascii")
        self.assertIn('scripts\\build_windows.py', text)
        self.assertIn("goto :fail", text)

    def test_gitattributes_keeps_the_bat_bytes_verbatim(self):
        if not HAS_GITATTRIBUTES:
            self.skipTest(NO_REPO_METADATA)
        with open(os.path.join(REPO_ROOT, ".gitattributes"), encoding="utf-8") as handle:
            self.assertRegex(handle.read(), r"\*\.bat\s+-text")

    def test_the_build_venv_is_git_ignored(self):
        if not HAS_GITIGNORE:
            self.skipTest(NO_REPO_METADATA)
        with open(os.path.join(REPO_ROOT, ".gitignore"), encoding="utf-8") as handle:
            self.assertIn(".venv-build/", handle.read().splitlines())


class BuildRequirementsTests(unittest.TestCase):
    def test_pins_pyinstaller_and_pillow(self):
        with open(os.path.join(REPO_ROOT, "requirements-windows-build.txt"),
                  encoding="utf-8") as handle:
            lines = [line.strip() for line in handle if line.strip() and not line.startswith("#")]
        names = {re.split(r"[<>=]", line)[0] for line in lines}
        self.assertEqual({"pyinstaller", "pillow"}, names)


class LauncherArgumentTests(unittest.TestCase):
    def test_no_browser_is_consumed_and_everything_else_passes_through(self):
        open_browser, server = windows.split_launcher_args(
            ["--port", "9000", "--no-browser", "--api-key", "sk-x"])
        self.assertFalse(open_browser)
        self.assertEqual(["--port", "9000", "--api-key", "sk-x"], server)

    def test_browser_opens_by_default(self):
        open_browser, server = windows.split_launcher_args([])
        self.assertTrue(open_browser)
        self.assertEqual([], server)


class FirstRunConfigTests(unittest.TestCase):
    def test_first_run_config_is_bound_to_localhost_with_no_keys(self):
        config = json.loads(windows.first_run_config_text())
        self.assertEqual("127.0.0.1", config["host"])
        self.assertEqual([], config["api_keys"])

    def test_first_run_config_comes_from_the_package_defaults(self):
        from gemini_web2api.config import DEFAULT_CONFIG

        config = json.loads(windows.first_run_config_text())
        self.assertEqual(DEFAULT_CONFIG["port"], config["port"])

    def test_creates_config_when_absent_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            self.assertTrue(windows.ensure_config(tmp))
            self.assertTrue(os.path.exists(path))
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('{"port": 9999}')
            self.assertFalse(windows.ensure_config(tmp))
            with open(path, encoding="utf-8") as handle:
                self.assertEqual('{"port": 9999}', handle.read())


@contextlib.contextmanager
def _frozen_in(directory):
    """Pretend to be the frozen executable living in ``directory``."""
    original = os.getcwd()
    try:
        with mock.patch.object(windows, "is_frozen", return_value=True), \
                mock.patch.object(windows, "app_dir", return_value=directory), \
                contextlib.redirect_stdout(io.StringIO()):
            yield
    finally:
        os.chdir(original)


class LauncherRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _capture_main(self):
        from gemini_web2api import __main__ as server_main

        captured = {}

        def fake_main(argv=None, on_ready=None):
            captured["argv"] = argv
            captured["on_ready"] = on_ready
            return 0

        return server_main, captured, mock.patch.object(server_main, "main", fake_main)

    def test_frozen_run_works_from_the_executable_folder(self):
        server_main, captured, patch = self._capture_main()
        with _frozen_in(self.tmp), patch, \
                mock.patch.object(windows, "open_dashboard_later"):
            windows.run(["--no-browser", "--port", "9000"])
            self.assertEqual(os.path.realpath(self.tmp), os.path.realpath(os.getcwd()))
        self.assertEqual(["--port", "9000"], captured["argv"])
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "config.json")))

    def test_version_does_not_leave_a_config_behind(self):
        _, _, patch = self._capture_main()
        with _frozen_in(self.tmp), patch:
            windows.run(["--version"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "config.json")))

    def test_dashboard_opens_only_when_the_port_is_bound(self):
        server_main, captured, patch = self._capture_main()
        with _frozen_in(self.tmp), patch, \
                mock.patch.object(windows, "open_dashboard_later") as opener:
            windows.run([])
            self.assertFalse(opener.called, "nothing opens before the server is ready")
            captured["on_ready"](9123)
            opener.assert_called_once_with(9123)

    def test_no_browser_suppresses_the_dashboard(self):
        server_main, captured, patch = self._capture_main()
        with _frozen_in(self.tmp), patch, \
                mock.patch.object(windows, "open_dashboard_later") as opener:
            windows.run(["--no-browser"])
            captured["on_ready"](9123)
            self.assertFalse(opener.called)

    def test_source_run_never_writes_a_config(self):
        _, captured, patch = self._capture_main()
        before = os.path.exists(os.path.join(REPO_ROOT, "config.json"))
        with patch:
            windows.run(["--no-browser"])
        self.assertEqual(before, os.path.exists(os.path.join(REPO_ROOT, "config.json")))


class DashboardOpenerTests(unittest.TestCase):
    def test_opens_localhost_once_after_the_delay(self):
        with mock.patch.object(windows.webbrowser, "open") as opened:
            timer = windows.open_dashboard_later(9123, delay=0)
            timer.join(5)
            opened.assert_called_once_with("http://localhost:9123/")

    def test_a_browser_failure_is_not_fatal(self):
        with mock.patch.object(windows.webbrowser, "open", side_effect=OSError("no browser")), \
                contextlib.redirect_stdout(io.StringIO()):
            timer = windows.open_dashboard_later(9123, delay=0)
            timer.join(5)
        self.assertFalse(timer.is_alive())

    def test_timer_is_a_daemon_so_it_never_blocks_exit(self):
        timer = windows.open_dashboard_later(9123, delay=60)
        try:
            self.assertTrue(timer.daemon)
        finally:
            timer.cancel()


class ServerReadyHookTests(unittest.TestCase):
    """``gemini_web2api.__main__.main`` reports the bound port through ``on_ready``."""

    def setUp(self):
        # main() applies CLI flags to the global CONFIG; never leak them.
        from gemini_web2api.config import reset_config

        reset_config()
        self.addCleanup(reset_config)

    def _run_main(self, argv, on_ready):
        from gemini_web2api import __main__ as server_main

        server = mock.MagicMock()
        server.server_address = ("127.0.0.1", 9321)
        server.serve_forever.side_effect = KeyboardInterrupt
        with mock.patch.object(server_main, "build_server", return_value=server), \
                mock.patch.object(server_main, "warm_up"), \
                mock.patch.object(server_main, "print_banner"), \
                mock.patch.object(server_main, "preconnect"), \
                mock.patch("gemini_web2api.gemini.close_client"), \
                mock.patch("signal.signal"), \
                contextlib.redirect_stdout(io.StringIO()):
            return server_main.main(argv, on_ready=on_ready), server

    def test_on_ready_receives_the_bound_port(self):
        seen = []
        result, _ = self._run_main(["--port", "9321", "--no-auto-bl", "--quiet"], seen.append)
        self.assertEqual(0, result)
        self.assertEqual([9321], seen)

    def test_main_still_works_without_the_hook(self):
        result, server = self._run_main(["--port", "9321", "--no-auto-bl", "--quiet"], None)
        self.assertEqual(0, result)
        self.assertTrue(server.serve_forever.called)

    def test_hook_is_not_called_when_the_bind_fails(self):
        from gemini_web2api import __main__ as server_main

        seen = []
        with mock.patch.object(server_main, "build_server", side_effect=OSError("in use")), \
                contextlib.redirect_stderr(io.StringIO()):
            result = server_main.main(["--port", "9321"], on_ready=seen.append)
        self.assertEqual(1, result)
        self.assertEqual([], seen)


class WorkflowTests(unittest.TestCase):
    def test_ci_builds_the_windows_executable_on_a_windows_runner(self):
        if not HAS_CI_WORKFLOW:
            self.skipTest(NO_REPO_METADATA)
        path = os.path.join(REPO_ROOT, ".github", "workflows", "ci.yml")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("build-windows:", text)
        self.assertIn("runs-on: windows-latest", text)
        self.assertIn("scripts/build_windows.py", text)
        self.assertIn("--version", text)


if __name__ == "__main__":
    unittest.main()
