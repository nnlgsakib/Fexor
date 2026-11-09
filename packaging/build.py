"""Build the Fexor Kiosk desktop app and its installer for the platform this runs on.

    uv sync --extra desktop --group build
    uv run python packaging/build.py

Output in packaging/dist/:
  Windows  FexorKiosk-Setup-<ver>-windows-x64.exe   (needs Inno Setup 6; else a .zip)
  macOS    FexorKiosk-<ver>-macos-<arch>.dmg
  Linux    fexor-kiosk_<ver>_<arch>.deb               (Raspberry Pi OS 64-bit, Debian, Ubuntu)
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
DIST = HERE / "dist"
APP = "Fexor Kiosk"
VERSION = os.environ.get("FEXOR_VERSION", "0.1.0").lstrip("v")


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)  # noqa: S603 - build tool with fixed arguments


def arch() -> str:
    m = platform.machine().lower()
    return {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64"}.get(m, m)


def fetch_models() -> None:
    from fexor_core.models import ensure_all

    for name, path in ensure_all(BUILD / "models").items():
        print(f"model {name}: {path.name}")


def pyinstaller() -> Path:
    os.environ["FEXOR_VERSION"] = VERSION
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"),
         str(HERE / "fexor_kiosk.spec")])
    return DIST / APP


def windows(app_dir: Path) -> Path:
    iscc = shutil.which("iscc") or r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    if Path(iscc).exists():
        run([iscc, f"/DVersion={VERSION}", f"/DSourceDir={app_dir}", f"/DOutputDir={DIST}",
             str(HERE / "windows" / "installer.iss")])
        return DIST / f"FexorKiosk-Setup-{VERSION}-windows-x64.exe"
    print("Inno Setup not found: making a zip instead of an installer")
    out = DIST / f"FexorKiosk-{VERSION}-windows-{arch()}"
    return Path(shutil.make_archive(str(out), "zip", app_dir.parent, app_dir.name))


def macos(_: Path) -> Path:
    out = DIST / f"FexorKiosk-{VERSION}-macos-{arch()}.dmg"
    out.unlink(missing_ok=True)
    run(["hdiutil", "create", "-volname", APP, "-srcfolder", str(DIST / f"{APP}.app"),
         "-ov", "-format", "UDZO", str(out)])
    return out


def linux(app_dir: Path) -> Path:
    debarch = subprocess.run(["dpkg", "--print-architecture"], capture_output=True, text=True,  # noqa: S607
                             check=True).stdout.strip()
    root = BUILD / "deb"
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(app_dir, root / "opt" / "fexor-kiosk")
    src = HERE / "linux"
    files = {  # installed path -> file in packaging/linux
        "usr/bin/fexor-kiosk": "fexor-kiosk",
        "usr/share/applications/fexor-kiosk.desktop": "fexor-kiosk.desktop",
        "etc/xdg/autostart/fexor-kiosk.desktop": "fexor-kiosk.desktop",  # start on sign-in
        "etc/default/fexor-kiosk": "fexor-kiosk.cfg",
    }
    for rel, name in files.items():
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src / name, dest)
    (root / "usr/bin/fexor-kiosk").chmod(0o755)
    debian = root / "DEBIAN"
    debian.mkdir()
    control = (src / "control").read_text().replace("@VERSION@", VERSION).replace("@ARCH@", debarch)
    (debian / "control").write_text(control)
    (debian / "conffiles").write_text("/etc/default/fexor-kiosk\n")
    shutil.copy(src / "postinst", debian / "postinst")  # turns off screen blanking on a Pi
    (debian / "postinst").chmod(0o755)
    out = DIST / f"fexor-kiosk_{VERSION}_{debarch}.deb"
    run(["dpkg-deb", "--build", "--root-owner-group", str(root), str(out)])
    return out


def main() -> None:
    fetch_models()
    app_dir = pyinstaller()
    package = {"win32": windows, "darwin": macos}.get(sys.platform, linux)
    out = package(app_dir)
    print(f"\nBuilt {out} ({out.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
