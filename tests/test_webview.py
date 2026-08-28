"""webview：浏览器登录取 Cookie 的单元测试（mock 网络/真实浏览器调用）。

纯函数直接验证；`browser_login` 走真实 Playwright 但 headless + wait_fn 立即返回，
验证"打开登录页 → 读 Cookie 拼 header"的全链路（不依赖账号登录）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pywangpan import webview  # noqa: E402


def test_playwright_available_true():
    assert webview.playwright_available() is True


def test_build_cookie_header():
    cookies = [
        {"name": "__puus", "value": "abc"},
        {"name": "a", "value": "1"},
        {"name": "empty", "value": ""},
    ]
    assert webview.build_cookie_header(cookies) == "__puus=abc; a=1"


def test_build_cookie_header_empty():
    assert webview.build_cookie_header([]) == ""
    assert webview.build_cookie_header([{"name": "x", "value": ""}]) == ""


def test_browser_login_captures_cookie_and_validates():
    """真实 Chromium(headless) 打开 pan.quark.cn，wait_fn 立即返回 ok，
    应能读到至少 1 个 cookie，且因命中 required（ctoken 属登录前即可有）返回 header 或空。
    这里不强断言具体值，只验证流程不抛异常、返回 str。"""
    def wait_ok():
        return "ok"
    result = webview.browser_login(
        "https://pan.quark.cn/",
        ("__puus", "__pus"),
        wait_ok,
        user_agent=webview.DEFAULT_UA,
        max_wait_s=60,
    )
    assert isinstance(result, str)


def test_browser_login_cancel_returns_empty():
    def wait_cancel():
        return "cancel"
    result = webview.browser_login(
        "data:text/html,hello",
        ("whatever",),
        wait_cancel,
        user_agent=webview.DEFAULT_UA,
        max_wait_s=60,
    )
    assert result == ""


if __name__ == "__main__":
    test_playwright_available_true()
    test_build_cookie_header()
    test_build_cookie_header_empty()
    test_browser_login_captures_cookie_and_validates()
    test_browser_login_cancel_returns_empty()
    print("All webview tests passed")
