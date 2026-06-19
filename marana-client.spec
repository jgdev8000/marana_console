# PyInstaller spec for the Marana client GUI.
#   pyinstaller marana-client.spec
# Produces dist/marana-client.exe (one-file, windowed). Build ON Windows.
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# pyqtgraph imports many submodules lazily; pull them all in so nothing is
# missing at runtime. PyQt6 / numpy / pyzmq have built-in PyInstaller hooks.
hiddenimports = collect_submodules("pyqtgraph")

a = Analysis(
    ["run_client.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["marana_server"],   # server code isn't needed in the client exe
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="marana-client",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,        # GUI app: no console window. Set True to see tracebacks.
    disable_windowed_traceback=False,
)
