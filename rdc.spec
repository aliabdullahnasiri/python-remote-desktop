# Shared native build for Windows and Linux. Run on the target operating system.
import sys

from PyInstaller.utils.hooks import collect_submodules

input_backend = (
    "win32"
    if sys.platform == "win32"
    else "darwin" if sys.platform == "darwin" else "xorg"
)

a = Analysis(
    ["src/rdesktop/main.py"],
    pathex=["src"],
    # These packages select platform backends or protocols dynamically.
    hiddenimports=(
        collect_submodules("mss")
        + collect_submodules("uvicorn")
        + collect_submodules("av")
        + collect_submodules("aiortc")
        + [f"pynput.keyboard._{input_backend}", f"pynput.mouse._{input_backend}"]
        + ["tkinter", "PIL.ImageTk"]
    ),
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="rdc",
    console=True,
    upx=False,
)
