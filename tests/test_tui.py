"""TUI 非交互单元测试：菜单选择、目录导航、配置存储、平台注册表。

通过 patch rich.prompt.Prompt.ask 提供输入，无需真实 stdin / 网络。
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from rich.console import Console  # noqa: E402

from pywangpan.config import ConfigStore  # noqa: E402
from pywangpan.pan.models import ShareFile, ShareSession  # noqa: E402
from pywangpan.pan.parser import SharePlatform  # noqa: E402
from pywangpan.tui import (  # noqa: E402
    EmptyFilesError,
    TuiApp,
    _HANDLERS,
    handler_for,
)


def _console():
    return Console(file=open(os.devnull, "w", encoding="utf-8"))


class _FakeDl:
    def __init__(self):
        self.calls = []

    def download(self, url, filename, headers=None, known_size=-1, on_progress=None):
        self.calls.append((url, filename, headers, known_size))
        return Path("fake_out") / filename


# ---------- 配置存储 ----------

def test_config_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(Path(tmp) / "cfg.json")
        store.load()
        store.set_cookie("quark", "abc;def")
        store.set_token("xunlei", {"access_token": "tok", "refresh_token": "rt"})
        assert store.get_cookie("quark") == "abc;def"
        assert store.get_token("xunlei")["access_token"] == "tok"
        # 重新加载验证持久化
        store2 = ConfigStore(Path(tmp) / "cfg.json")
        store2.load()
        assert store2.get_cookie("quark") == "abc;def"
        assert store2.get_token("xunlei")["refresh_token"] == "rt"
        # 置空删除
        store2.set_cookie("quark", None)
        assert store2.get_cookie("quark") is None


# ---------- 菜单选择 ----------

def test_choose_valid_and_invalid():
    app = TuiApp(console=_console(), dl=_FakeDl())
    with patch("pywangpan.ui.Prompt.ask", side_effect=["42", "2"]) as ask:
        result = app.choose("测试菜单", [("a", "A"), ("b", "B")])
        assert result == "b"
        assert ask.call_count == 2


def test_choose_quit():
    app = TuiApp(console=_console(), dl=_FakeDl())
    with patch("pywangpan.ui.Prompt.ask", return_value="1"):
        result = app.choose("测试菜单", [("x", "X")])
        assert result == "x"


# ---------- 目录导航 ----------

def _make_dir(name):
    return ShareFile(fid=f"d-{name}", fname=name, fsize=0, isdir=True,
                     pdir_fid="0", fid_token="")


def _make_file(name, size):
    return ShareFile(fid=f"f-{name}", fname=name, fsize=size, isdir=False,
                     pdir_fid="0", fid_token="")


def test_browse_select_file():
    app = TuiApp(console=_console(), dl=_FakeDl())
    files = {"root": [_make_dir("电影"), _make_file("a.txt", 1024)],
             "d-电影": [_make_file("b.mp4", 5 * 1024 * 1024)]}

    def list_fn(dir_fid):
        return files.get(dir_fid or "root", [])

    # 进入 "电影" 目录（序号1），再选文件 b.mp4（序号1）
    with patch("pywangpan.ui.Prompt.ask", side_effect=["1", "1"]) as ask:
        chosen = app.ui.browse_files(list_fn, root_fid="root", title="测试")
    assert chosen.fname == "b.mp4"
    assert ask.call_count == 2


def test_browse_back_navigation():
    app = TuiApp(console=_console(), dl=_FakeDl())
    files = {"root": [_make_dir("dir1")], "d-dir1": [_make_file("x.dat", 99)]}

    def list_fn(dir_fid):
        return files.get(dir_fid or "root", [])

    # 进 dir1 → 返回 b → 根目录只有 dir1（无法再选文件）
    with patch("pywangpan.ui.Prompt.ask", side_effect=["1", "b", "q"]):
        try:
            app.ui.browse_files(list_fn, root_fid="root", title="t")
            assert False, "应因 q 取消抛出 EmptyFilesError"
        except EmptyFilesError:
            pass


def test_browse_quit_raises():
    app = TuiApp(console=_console(), dl=_FakeDl())
    with patch("pywangpan.ui.Prompt.ask", return_value="q"):
        try:
            app.ui.browse_files(lambda d: [_make_file("f", 1)], root_fid="0", title="t")
            assert False, "应抛出 EmptyFilesError"
        except EmptyFilesError:
            pass


# ---------- 平台注册表 ----------

def test_handler_registry_all_platforms():
    names = [c.platform for c in _HANDLERS]
    assert set(names) == {
        SharePlatform.QUARK, SharePlatform.UC, SharePlatform.BAIDU,
        SharePlatform.C139, SharePlatform.PAN123, SharePlatform.XUNLEI,
    }
    app = TuiApp(console=_console(), dl=_FakeDl())
    for platform in names:
        h = handler_for(platform, app.ui, app.config)
        assert h.name and h.platform is platform
        assert isinstance(h.root_fid, str)


# ---------- Cookie 类平台：工具内浏览器登录 ----------

def test_quark_browser_login_saves_cookie():
    with tempfile.TemporaryDirectory() as tmp:
        app = TuiApp(console=_console(), dl=_FakeDl())
        app.config = ConfigStore(Path(tmp) / "cfg.json")
        app.config.load()
        handler = handler_for(SharePlatform.QUARK, app.ui, app.config)
        # 无已保存 cookie → 选择方式选 1(浏览器登录)
        with patch("pywangpan.tui.webview.browser_login", return_value="__puus=x; a=1") as bl:
            with patch("pywangpan.ui.Prompt.ask", side_effect=["1"]):
                cookie = handler.collect_auth()
        assert cookie == "__puus=x; a=1"
        assert app.config.get_cookie("quark") == "__puus=x; a=1"
        assert bl.call_count == 1


def test_cookie_platforms_browser_login_all():
    cases = [
        (SharePlatform.QUARK, "quark"),
        (SharePlatform.UC, "uc"),
        (SharePlatform.BAIDU, "baidu"),
        (SharePlatform.C139, "c139"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        app = TuiApp(console=_console(), dl=_FakeDl())
        app.config = ConfigStore(Path(tmp) / "cfg.json")
        app.config.load()
        for platform, cookie in cases:
            handler = handler_for(platform, app.ui, app.config)
            assert handler.login_url and handler.required_cookies
            with patch("pywangpan.tui.webview.browser_login", return_value="k=v"):
                with patch("pywangpan.ui.Prompt.ask", side_effect=["1"]):
                    got = handler.collect_auth()
            assert got == "k=v"
            assert app.config.get_cookie(cookie) == "k=v"


def test_cookie_platform_cancel_raises():
    with tempfile.TemporaryDirectory() as tmp:
        app = TuiApp(console=_console(), dl=_FakeDl())
        app.config = ConfigStore(Path(tmp) / "cfg.json")
        app.config.load()
        handler = handler_for(SharePlatform.QUARK, app.ui, app.config)
        with patch("pywangpan.ui.Prompt.ask", side_effect=["3"]):  # 取消
            try:
                handler.collect_auth()
                assert False, "应抛出 EmptyFilesError"
            except EmptyFilesError:
                pass


def test_cookie_platform_browser_fail_falls_back_to_paste():
    with tempfile.TemporaryDirectory() as tmp:
        app = TuiApp(console=_console(), dl=_FakeDl())
        app.config = ConfigStore(Path(tmp) / "cfg.json")
        app.config.load()
        handler = handler_for(SharePlatform.QUARK, app.ui, app.config)
        # 浏览器登录返回空（未取到登录态）→ 回退手动粘贴；ask 依次: 1(浏览器), 粘贴内容
        with patch("pywangpan.tui.webview.browser_login", return_value=""):
            with patch("pywangpan.ui.Prompt.ask", side_effect=["1", "__pus=pasted"]):
                got = handler.collect_auth()
        assert got == "__pus=pasted"
        assert app.config.get_cookie("quark") == "__pus=pasted"


if __name__ == "__main__":
    test_config_roundtrip()
    test_choose_valid_and_invalid()
    test_choose_quit()
    test_browse_select_file()
    test_browse_back_navigation()
    test_browse_quit_raises()
    test_handler_registry_all_platforms()
    test_quark_browser_login_saves_cookie()
    test_cookie_platform_cancel_raises()
    test_cookie_platform_browser_fail_falls_back_to_paste()
    print("All TUI tests passed")
