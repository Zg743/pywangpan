"""pywangpan GUI 启动入口（供 PyInstaller 打包 / 双击运行）。

本文件位于 `pywangpan` 包根目录内；把其父目录加入 sys.path，
使 `from pywangpan.gui ...` 能按包路径解析。
"""
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_parent = os.path.dirname(_here)
for p in (_parent, _here):
    if p not in sys.path:
        sys.path.insert(0, p)

from pywangpan.gui import launch_gui  # noqa: E402


def _self_test() -> int:
    """打包后自检 Playwright 驱动链路（console 版用；PYWANGPAN_SELFTEST=1 触发）。"""
    from pywangpan import webview

    print("playwright_available:", webview.playwright_available())
    try:
        header = webview.browser_login(
            "https://pan.quark.cn/",
            ("ctoken",),
            lambda: "ok",
            user_agent=webview.DEFAULT_UA,
            max_wait_s=60,
        )
        print("browser_login returned:", repr(header)[:120])
        print("SELFTEST_OK")
        return 0
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print("SELFTEST_FAIL")
        return 1


if __name__ == "__main__":
    if os.environ.get("PYWANGPAN_SELFTEST") == "1":
        raise SystemExit(_self_test())
    raise SystemExit(launch_gui())
