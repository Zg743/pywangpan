"""pywangpan GUI 启动入口（供 PyInstaller 打包 / 双击运行）。

本文件位于项目根目录，`pywangpan` 包是其子目录；把本文件所在目录加入
sys.path，使 `from pywangpan.gui ...` 能按包路径解析（打包后为解包目录，
同样成立）。
"""
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

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
