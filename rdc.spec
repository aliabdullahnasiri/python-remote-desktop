# Shared native build for Windows and Linux. Run on the target operating system.
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ["src/rdesktop/main.py"],
    pathex=["src"],
    # These packages select platform backends or protocols dynamically.
    hiddenimports=(
        collect_submodules("mss")
        + collect_submodules("uvicorn")
        + collect_submodules("av")
        + collect_submodules("aiortc")
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
