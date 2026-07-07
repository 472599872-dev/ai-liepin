# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


project_root = Path(SPECPATH).parent.parent
src_root = project_root / "src"

datas = []
for filename in (
    project_root / ".env",
    project_root / ".env.example",
    project_root / "data" / "liepin_search_schema.json",
    project_root / "data" / "liepin_search_schema_light.json",
):
    if filename.exists():
        target = "." if filename.name in {".env", ".env.example"} else "data"
        datas.append((str(filename), target))

hiddenimports = []
for package in ("cryptography", "openai", "pydantic", "pydantic_settings", "dotenv", "yaml"):
    hiddenimports += collect_submodules(package)


a = Analysis(
    [str(src_root / "liepin_agent" / "desktop.py")],
    pathex=[str(src_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["playwright"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LiepinRecruitingAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LiepinRecruitingAgent",
)
