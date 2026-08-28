# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：onefile 窗口程序 pywangpan。

- 打包 GUI 入口 pywangpan_gui.py（含 Playwright 内嵌浏览器登录）。
- 必须通过 datas 收集 playwright.driver（node.exe + package，约 100MB），
  否则打包后的 exe 无法找到 Playwright 驱动。Chromium 浏览器本体保持外置
  （%LOCALAPPDATA%\\ms-playwright），不随 exe 分发。
"""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules
from pathlib import Path

# 项目根目录（含 pywangpan 包与 pywangpan_gui.py 入口）。spec 执行时无 __file__，
# 用 PyInstaller 注入的 SPECPATH（spec 文件所在目录）。
_project_root = Path(SPECPATH)

datas = collect_data_files("playwright", includes=["driver/**"])

# collect_data_files 把 .exe 视为二进制而漏掉 node.exe；driver 是 Playwright 驱动的
# 真实节点运行时，必须打进包，解包后仍位于 playwright/driver/node.exe。
_playwright_dir = Path(__import__("playwright").__file__).parent
_driver_node = _playwright_dir / "driver" / "node.exe"
binaries = [(_driver_node, "playwright/driver")]

hiddenimports = ["playwright", "playwright.sync_api", "playwright.async_api"] + collect_submodules("playwright")

a = Analysis(
    ["pywangpan_gui.py"],
    pathex=[str(_project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="pywangpan",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
)
