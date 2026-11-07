# PyInstaller spec for the Fexor Kiosk desktop app. Build with: uv run python packaging/build.py
# ruff: noqa
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

HERE = Path(SPECPATH)
MODELS = HERE / "build" / "models"
VERSION = os.environ.get("FEXOR_VERSION", "0.1.0")
APP = "Fexor Kiosk"

datas = collect_data_files("fexor_kiosk") + [(str(MODELS), "models")]
binaries = collect_dynamic_libs("onnxruntime")
hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("fexor_kiosk")
    + collect_submodules("fexor_core")
)
if sys.platform != "linux":
    hiddenimports += collect_submodules("webview")

a = Analysis(
    [str(HERE / "kiosk_entry.py")],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide6", "IPython", "pytest",
              "fexor_server", "sqlalchemy", "asyncpg", "alembic", "openpyxl"],
    noarchive=False,
)
if sys.platform == "win32":
    # PyInstaller copies the C++ runtime that came with Python, which can be older than what
    # ONNX Runtime needs (it then crashes on import). Ship the build machine's current one.
    sys32 = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32"
    current = {n: str(sys32 / n) for n in ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
                                           "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll")
               if (sys32 / n).exists()}
    a.binaries = [(dest, current.get(dest.lower(), src), kind) for dest, src, kind in a.binaries]

pyz = PYZ(a.pure)
icon = {"win32": HERE / "windows" / "icon.ico", "darwin": HERE / "macos" / "icon.icns"}.get(sys.platform)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP,
    # Windows/macOS: no console window behind the kiosk. FEXOR_CONSOLE=1 builds a debug console.
    console=sys.platform == "linux" or bool(os.environ.get("FEXOR_CONSOLE")),
    icon=str(icon) if icon and icon.exists() else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name=APP, upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP}.app",
        icon=str(icon) if icon and icon.exists() else None,
        bundle_identifier="com.fexor.kiosk",
        version=VERSION,
        info_plist={
            "NSCameraUsageDescription": "The kiosk uses the camera to check faces for attendance.",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
        },
    )
