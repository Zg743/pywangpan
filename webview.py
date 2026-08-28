"""Playwright 内嵌浏览器登录：在工具内打开 Chromium 登录页，用户登录后自动读取 Cookie。

依赖：pip install playwright && python -m playwright install chromium

设计：Playwright 用自己的 headful Chromium 开一个独立浏览器窗口（登录所需真实 JS 环境，
无法内嵌进 tkinter）。登录窗口保持打开，`wait_fn` 阻塞调用线程直到用户完成登录并确认，
随后从浏览器会话读取 Cookie 拼成 `Cookie: k=v; k=v` 头，供 requests 侧复用。
"""
from __future__ import annotations

import os
from typing import Callable

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover - playwright 未安装
    sync_playwright = None

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def playwright_available() -> bool:
    return sync_playwright is not None


def build_cookie_header(cookies: list[dict]) -> str:
    return "; ".join(f"{c.get('name')}={c.get('value')}" for c in cookies if c.get("value"))


def _has_any(cookies: list[dict], names: tuple[str, ...]) -> bool:
    present = {c.get("name") for c in cookies}
    return any(n in present for n in names)


def browser_login(
    login_url: str,
    required_cookies: tuple[str, ...],
    wait_fn: Callable[[], str],
    user_agent: str = DEFAULT_UA,
    extra_headers: dict | None = None,
    max_wait_s: int = 600,
) -> str:
    """启动 Chromium 打开 `login_url`，调用 `wait_fn()` 阻塞等待用户确认登录，
    然后读取 Cookie 拼成 header 返回；未取得关键 Cookie 或取消则返回空串。

    `wait_fn` 返回 "ok" 表示用户已完成登录，其他值（如 "cancel"）表示放弃。
    """
    if sync_playwright is None:  # pragma: no cover
        raise RuntimeError("未安装 playwright，无法在工具内登录。请运行: pip install playwright && python -m playwright install chromium")
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH") and not _browser_installed():
        import subprocess
        import sys

        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)

    # 冻结（PyInstaller）模式下 Playwright 默认在解包目录里找 .local-browsers，
    # 浏览器本体是外置安装的，须显式指回标准 ms-playwright 目录。
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        _browsers = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ms-playwright"
        )
        if os.path.isdir(_browsers):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _browsers

    headers = dict(extra_headers or {})
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        try:
            ctx = browser.new_context(user_agent=user_agent, viewport={"width": 1280, "height": 800})
            page = ctx.new_page()
            page.goto(login_url, wait_until="domcontentloaded", timeout=max_wait_s * 1000)
            if headers:
                page.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                )
            result = wait_fn()
            if result != "ok":
                return ""
            cookies = ctx.cookies()
        finally:
            browser.close()
    if not _has_any(cookies, required_cookies):
        return ""
    return build_cookie_header(cookies)


def _browser_installed() -> bool:
    base = os.environ.get(
        "PLAYWRIGHT_BROWSERS_PATH",
        os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ms-playwright"),
    )
    try:
        return any(
            os.path.isdir(os.path.join(base, d))
            for d in os.listdir(base)
            if d.startswith("chromium-")
        )
    except OSError:
        return False
