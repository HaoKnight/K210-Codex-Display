# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

project = Path(SPECPATH)
datas = [
    (str(project / "bridge" / "web"), "bridge/web"),
    (str(project / "bridge" / "device_support" / "font.json"), "bridge/device_support"),
    (str(project / "config.example.json"), "."),
    (str(project / "K210" / "CanMV_Preview.py"), "."),
    (str(project / "K210" / "font.bin"), "."),
    (str(project / "icons" / "menuTemplate.png"), "icons"),
]

a = Analysis(
    [str(project / "tray_app.py")],
    pathex=[str(project)],
    binaries=[],
    datas=datas,
    hiddenimports=["AppKit", "Foundation", "PyObjCTools.AppHelper"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="K210 Codex Display Hub",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
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
    upx=True,
    upx_exclude=[],
    name="K210 Codex Display Hub",
)
app = BUNDLE(
    coll,
    name="K210 Codex Display Hub.app",
    icon=str(project / "icons" / "AppIcon.icns"),
    bundle_identifier="com.knight.k210-codex-display-hub",
    info_plist={"CFBundleShortVersionString": "1.2.2", "CFBundleVersion": "1.2.2",
                "CFBundleIconName": "AppIcon",
                "LSUIElement": True, "NSHighResolutionCapable": True,
                "NSBluetoothAlwaysUsageDescription": "查找、配对并连接 K210 外接蓝牙串口模块",
                "NSHumanReadableCopyright": "K210 Codex Display Hub"},
)
