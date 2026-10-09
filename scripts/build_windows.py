"""Build the Windows application: ``gemini-web2api.exe`` plus a portable zip.

Run it on Windows (double-click ``build_windows.bat`` or ``python
scripts\\build_windows.py``). PyInstaller cannot cross-compile, so the script
refuses to build on another system. ``--dry-run`` prints the whole plan on any
system without building anything.

What it does:

1. Creates ``.venv-build`` (kept out of the runtime ``.venv``) and installs
   ``requirements.txt`` plus ``requirements-windows-build.txt`` into it. httpx
   is bundled so the executable streams incrementally.
2. Re-runs itself inside that venv, which makes Pillow and PyInstaller
   available, and:
   a. converts ``logo.png`` to ``build/windows/icon.ico``;
   b. runs PyInstaller on ``scripts/windows_entry.py``;
   c. runs the built ``--version`` as a smoke test;
   d. zips the folder into ``dist/windows/``.

Output (both are git-ignored)::

    dist/windows/gemini-web2api/gemini-web2api.exe        onedir (default)
    dist/windows/gemini-web2api-<version>-windows-x64.zip  portable archive

``--onefile`` produces a single ``dist/windows/gemini-web2api.exe`` instead.
The onedir layout is the default because it starts faster and triggers fewer
antivirus false positives than one-file archives.

Usage::

    python scripts/build_windows.py              # onedir build + zip
    python scripts/build_windows.py --onefile    # single executable
    python scripts/build_windows.py --clean      # remove previous outputs first
    python scripts/build_windows.py --dry-run    # print the plan, build nothing
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import zipfile

APP_NAME = "gemini-web2api"
ENTRY_SCRIPT = os.path.join("scripts", "windows_entry.py")
BUILD_VENV = ".venv-build"
BUILD_REQUIREMENTS = os.path.join("requirements-windows-build.txt")
RUNTIME_REQUIREMENTS = "requirements.txt"
LOGO = "logo.png"
ICON_OUT = os.path.join("build", "windows", "icon.ico")
DIST_DIR = os.path.join("dist", "windows")
WORK_DIR = os.path.join("build", "pyinstaller")
ICON_SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]

# Files shipped next to the onedir executable.
ARCHIVE_EXTRAS = ["LICENSE", "config.example.json"]

# Local secrets and state that must never enter an archive, even if a previous
# run left them in the output folder. config.json is created on the user's
# machine at first run instead.
SECRET_NAMES = {"config.json", "cookie.txt", "cookie.json", "gemini-auth.json", ".env"}

SMOKE_TIMEOUT_SEC = 120


class BuildError(RuntimeError):
    """A build step failed. The message is shown to the user as-is."""


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_version(root):
    """The package version, read from source so the build needs no import."""
    path = os.path.join(root, "gemini_web2api", "__init__.py")
    with open(path, encoding="utf-8") as handle:
        match = re.search(r'^__version__\s*=\s*"([^"]+)"', handle.read(), re.MULTILINE)
    if not match:
        raise BuildError(f"no __version__ found in {path}")
    return match.group(1)


def check_platform(system, force=False):
    """Raise unless PyInstaller can produce a Windows executable on ``system``."""
    if system == "Windows" or force:
        return
    raise BuildError(
        f"this builds a Windows executable and must run on Windows (this is {system}).\n"
        "PyInstaller cannot cross-compile. Run build_windows.bat on Windows, or use\n"
        "--dry-run to print the plan here."
    )


def venv_python(root, system):
    """Interpreter inside the build venv for the given platform."""
    if system == "Windows":
        return os.path.join(root, BUILD_VENV, "Scripts", "python.exe")
    return os.path.join(root, BUILD_VENV, "bin", "python")


def executable_name(system):
    return APP_NAME + (".exe" if system == "Windows" else "")


def artifact_paths(root, version, onefile, system="Windows"):
    """Where the finished outputs land. Returned as a dict for tests and logging."""
    dist = os.path.join(root, DIST_DIR)
    if onefile:
        exe = os.path.join(dist, executable_name(system))
        folder = None
    else:
        folder = os.path.join(dist, APP_NAME)
        exe = os.path.join(folder, executable_name(system))
    return {
        "dist": dist,
        "folder": folder,
        "exe": exe,
        "zip": os.path.join(dist, f"{APP_NAME}-{version}-windows-x64.zip"),
        "icon": os.path.join(root, ICON_OUT),
    }


def bootstrap_commands(root, system="Windows"):
    """Commands that create the build venv and install its dependencies."""
    py = venv_python(root, system)
    return [
        [sys.executable, "-m", "venv", os.path.join(root, BUILD_VENV)],
        [py, "-m", "pip", "install", "--upgrade", "pip"],
        [py, "-m", "pip", "install", "-r", os.path.join(root, RUNTIME_REQUIREMENTS)],
        [py, "-m", "pip", "install", "-r", os.path.join(root, BUILD_REQUIREMENTS)],
    ]


def pyinstaller_args(root, onefile, icon_path=None):
    """Arguments for ``python -m PyInstaller``. Pure, so tests can inspect them."""
    args = [
        "--noconfirm",
        "--clean",
        "--onefile" if onefile else "--onedir",
        "--console",  # the server prints its log here; Ctrl+C stops it
        "--name", APP_NAME,
        "--paths", root,
        # Every module of the package, including any imported indirectly.
        "--collect-submodules", "gemini_web2api",
        "--distpath", os.path.join(root, DIST_DIR),
        "--workpath", os.path.join(root, WORK_DIR),
        "--specpath", os.path.join(root, WORK_DIR),
    ]
    if icon_path and os.path.exists(icon_path):
        args += ["--icon", icon_path]
    args.append(os.path.join(root, ENTRY_SCRIPT))
    return args


def make_icon(root, dst):
    """Convert ``logo.png`` to a multi-size ``.ico``. Needs Pillow.

    ``logo.png`` is a JPEG with a ``.png`` extension, so this relies on
    Pillow's format sniffing rather than the file name.
    """
    from PIL import Image  # imported late: only the build venv has Pillow

    os.makedirs(os.path.dirname(dst), exist_ok=True)
    image = Image.open(os.path.join(root, LOGO)).convert("RGBA")
    side = max(image.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(image, ((side - image.size[0]) // 2, (side - image.size[1]) // 2))
    square.save(dst, format="ICO", sizes=ICON_SIZES)
    return dst


def run(cmd, cwd):
    """Run a command, streaming its output, and fail the build if it fails."""
    print("  $ " + " ".join(_quote(part) for part in cmd), flush=True)
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        raise BuildError(f"command failed with exit code {result.returncode}: {cmd[0]}")


def _quote(part):
    return f'"{part}"' if " " in part else part


def bootstrap(root, system):
    """Create the build venv and install its dependencies. Runs outside the venv.

    An existing venv is reused, so repeat builds do not re-download PyInstaller.
    Delete ``.venv-build`` to force a fresh install.
    """
    py = venv_python(root, system)
    if os.path.exists(py):
        print(f"  reusing {BUILD_VENV} (delete it to reinstall the build tools)")
        return py
    for cmd in bootstrap_commands(root, system):
        run(cmd, root)
    return py


def build(root, system, onefile, clean, zip_it, force):
    """The build itself. Runs inside the build venv, so Pillow and PyInstaller import."""
    version = read_version(root)
    paths = artifact_paths(root, version, onefile, system)

    if clean:
        for stale in (paths["dist"], os.path.join(root, WORK_DIR)):
            if os.path.isdir(stale):
                shutil.rmtree(stale)

    print(f"  [icon] converting {LOGO} -> {os.path.relpath(paths['icon'], root)}")
    icon = paths["icon"]
    try:
        make_icon(root, icon)
    except Exception as exc:  # Pillow missing or logo unreadable: build without an icon
        print(f"  ! icon skipped ({exc}); the executable will use the default icon")
        icon = None

    print(f"  [pyinstaller] building {APP_NAME} ({'onefile' if onefile else 'onedir'})")
    run([sys.executable, "-m", "PyInstaller"] + pyinstaller_args(root, onefile, icon), root)

    exe = paths["exe"]
    if not os.path.exists(exe):
        raise BuildError(f"PyInstaller finished but {exe} is missing")

    print("  [smoke] running the built executable: --version")
    smoke = subprocess.run([exe, "--version"], cwd=root, capture_output=True, text=True,
                           timeout=SMOKE_TIMEOUT_SEC)
    output = (smoke.stdout + smoke.stderr).strip()
    if smoke.returncode != 0 or f"{APP_NAME} {version}" not in output:
        raise BuildError(f"smoke test failed (exit {smoke.returncode}): {output!r}")
    print(f"         {output}")

    artifacts = [exe]
    if zip_it:
        artifacts.append(make_archive(root, paths, onefile, version, system))
    return artifacts


def make_archive(root, paths, onefile, version, system):
    """Zip the onedir folder (or a onefile exe) with the license alongside."""
    archive = paths["zip"]
    if os.path.exists(archive):
        os.remove(archive)
    # Zip member names always use "/", whatever the host separator is.
    top = APP_NAME + "/"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        if onefile:
            bundle.write(paths["exe"], top + executable_name(system))
        else:
            folder = paths["folder"]
            for dirpath, _dirnames, filenames in os.walk(folder):
                for name in filenames:
                    if name in SECRET_NAMES:
                        continue
                    full = os.path.join(dirpath, name)
                    rel = os.path.relpath(full, os.path.dirname(folder)).replace(os.sep, "/")
                    bundle.write(full, rel)
        for extra in ARCHIVE_EXTRAS:
            source = os.path.join(root, extra)
            if os.path.exists(source):
                bundle.write(source, top + extra)
    return archive


def print_plan(root, system, onefile, zip_it):
    version = read_version(root)
    paths = artifact_paths(root, version, onefile, system)
    print("Plan (nothing is built with --dry-run):")
    print(f"  platform     {system}")
    print(f"  version      {version}")
    print(f"  build venv   {os.path.join(root, BUILD_VENV)}")
    for cmd in bootstrap_commands(root, system):
        print("    bootstrap  " + " ".join(_quote(part) for part in cmd))
    print("  icon         " + os.path.relpath(paths["icon"], root))
    print("  pyinstaller  " + " ".join(
        _quote(part) for part in ["python", "-m", "PyInstaller"]
        + pyinstaller_args(root, onefile, paths["icon"])))
    print("  executable   " + os.path.relpath(paths["exe"], root))
    if zip_it:
        print("  archive      " + os.path.relpath(paths["zip"], root))


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="build_windows.py",
        description="Build gemini-web2api.exe for Windows and a portable zip.")
    parser.add_argument("--onefile", action="store_true",
                        help="a single executable instead of a folder")
    parser.add_argument("--clean", action="store_true",
                        help="delete the previous build and dist outputs first")
    parser.add_argument("--no-zip", action="store_true", help="skip the portable zip")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan without installing or building anything")
    parser.add_argument("--force", action="store_true",
                        help="allow a build on a non-Windows system (testing only; "
                             "the result will not run on Windows)")
    parser.add_argument("--stage", choices=["bootstrap", "build"], default="bootstrap",
                        help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    root = repo_root()
    system = _system()
    zip_it = not args.no_zip

    if args.stage == "build":
        # Inside the build venv: Pillow and PyInstaller are importable here.
        try:
            artifacts = build(root, system, args.onefile, args.clean, zip_it, args.force)
        except BuildError as exc:
            print(f"\n[ERROR] {exc}", file=sys.stderr)
            return 1
        print("\n[OK] Build complete:")
        for item in artifacts:
            print("     " + os.path.relpath(item, root))
        return 0

    if args.dry_run:
        print_plan(root, system, args.onefile, zip_it)
        return 0

    try:
        check_platform(system, force=args.force)
        bootstrap(root, system)
        forwarded = ["--stage", "build"]
        for flag, enabled in (("--onefile", args.onefile), ("--clean", args.clean),
                              ("--no-zip", not zip_it), ("--force", args.force)):
            if enabled:
                forwarded.append(flag)
        run([venv_python(root, system), os.path.abspath(__file__)] + forwarded, root)
    except BuildError as exc:
        print(f"\n[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


def _system():
    """The platform name in the same vocabulary as ``platform.system()``."""
    if sys.platform == "win32":
        return "Windows"
    if sys.platform == "darwin":
        return "Darwin"
    if sys.platform.startswith("linux"):
        return "Linux"
    return sys.platform


if __name__ == "__main__":
    sys.exit(main())
