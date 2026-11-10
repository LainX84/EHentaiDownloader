"""Build the ready-to-use app for the current system with PyInstaller.

    pip install -r requirements.txt pyinstaller
    python tools/build.py

The result goes to release/ (one archive per system, users don't need Python):

    Windows   EHentaiDownloader-<version>-windows-x64.zip       EHentaiDownloader.exe
    macOS     EHentaiDownloader-<version>-macos-<arch>.zip      EHentaiDownloader.app
    Linux     EHentaiDownloader-<version>-linux-x64.tar.gz      EHentaiDownloader

Each archive also holds README.md, LICENSE and cookies.example.json.
The GitHub workflow .github/workflows/release.yml runs this script on Windows, macOS
(Apple Silicon and Intel) and Linux when a tag v<version> is pushed.

The version is APP_VERSION from src/config.py, or the APP_VERSION environment variable
(the workflow sets it from the tag).
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "EHentaiDownloader"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
RELEASE = ROOT / "release"
EXTRA_FILES = ("README.md", "LICENSE", "cookies.example.json")


def app_version() -> str:
    """Version for the archive names."""
    env = os.environ.get("APP_VERSION", "").strip().lstrip("v")
    if env:
        return env
    match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', (ROOT / "src" / "config.py").read_text(encoding="utf-8"), re.M)
    return match.group(1) if match else "dev"


def system_tag() -> tuple[str, str]:
    """('windows'|'macos'|'linux', architecture)."""
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)
    if sys.platform.startswith("win"):
        return "windows", arch
    if sys.platform == "darwin":
        return "macos", arch
    return "linux", arch


def run_pyinstaller(system: str) -> None:
    """Call PyInstaller (as a command, so this script needs no PyInstaller import)."""
    sep = os.pathsep  # separator of --add-data SOURCE<sep>DEST
    icon = ROOT / "assets" / ("icon.ico" if system == "windows" else "icon.png")
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--windowed",
        "--name", NAME,
        "--icon", str(icon),
        "--distpath", str(DIST), "--workpath", str(BUILD), "--specpath", str(BUILD),
        "--add-data", f"{ROOT / 'LICENSE'}{sep}.",
        *[arg for name in ("icon.png", "icon_16.png", "icon_32.png", "icon_small.png", "logo_small.png")
          for arg in ("--add-data", f"{ROOT / 'assets' / name}{sep}assets")],
        "--collect-submodules", "src",
    ]
    if system == "macos":
        args += ["--osx-bundle-identifier", "io.github.lainx84.ehentaidownloader"]
    else:
        args.append("--onefile")  # a single executable on Windows and Linux
    args.append(str(ROOT / "EHentaiDownloader.pyw"))
    print("Running:", " ".join(args), flush=True)
    subprocess.run(args, check=True, cwd=ROOT)


def package(system: str, arch: str, version: str) -> Path:
    """Put the built app and the extra files in one archive in release/."""
    RELEASE.mkdir(exist_ok=True)
    base = f"{NAME}-{version}-{system}-{arch}"
    if system == "windows":
        target = RELEASE / f"{base}.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(DIST / f"{NAME}.exe", f"{NAME}/{NAME}.exe")
            for extra in EXTRA_FILES:
                archive.write(ROOT / extra, f"{NAME}/{extra}")
        return target
    if system == "macos":
        # ditto keeps the symbolic links and permissions an .app bundle needs
        staging = BUILD / "package" / NAME
        shutil.rmtree(staging.parent, ignore_errors=True)
        staging.mkdir(parents=True)
        subprocess.run(["ditto", str(DIST / f"{NAME}.app"), str(staging / f"{NAME}.app")], check=True)
        for extra in EXTRA_FILES:
            shutil.copy2(ROOT / extra, staging / extra)
        target = RELEASE / f"{base}.zip"
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(staging), str(target)], check=True)
        return target
    target = RELEASE / f"{base}.tar.gz"
    with tarfile.open(target, "w:gz") as archive:
        archive.add(DIST / NAME, f"{NAME}/{NAME}")  # keeps the executable bit
        for extra in EXTRA_FILES:
            archive.add(ROOT / extra, f"{NAME}/{extra}")
    return target


def main() -> None:
    """Build and package for the system this script runs on."""
    system, arch = system_tag()
    version = app_version()
    print(f"Building {NAME} {version} for {system}-{arch}", flush=True)
    for folder in (DIST, BUILD):
        shutil.rmtree(folder, ignore_errors=True)
    run_pyinstaller(system)
    target = package(system, arch, version)
    print(f"Done: {target} ({target.stat().st_size / 1_048_576:.1f} MB)")


if __name__ == "__main__":
    main()
