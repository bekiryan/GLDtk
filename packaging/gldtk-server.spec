# PyInstaller spec for the GLDtk backend (one-folder). Build: scripts/build_mac_app.sh
# Output: packaging/dist/gldtk-server/gldtk-server
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT))  # so collect_submodules can import the repo's packages

# Packages that load providers / modules dynamically, plus the web stack.
hidden = []
for pkg in ("llm", "art", "aesthetic", "layout", "spec", "symbolic", "game", "api", "memory", "mission",
            "patterns", "pipeline", "fidelity", "edit",
            "uvicorn", "fastapi", "starlette", "pydantic", "anyio"):
    hidden += collect_submodules(pkg)

a = Analysis(
    [str(ROOT / "server.py")],
    pathex=[str(ROOT)],
    # aesthetic/library.py reads <repo>/assets relative to __file__, i.e. <bundle>/assets.
    datas=[(str(ROOT / "assets"), "assets")],
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "pytest", "IPython"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="gldtk-server", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="gldtk-server")
