"""交互式 TUI：解析分享链接并高速下载（基于 rich，覆盖 6 平台）。

用法：
  python -m pywangpan.cli            # 进入交互菜单
  python -m pywangpan.cli --url ...  # 仍可用一次性参数（见 cli.py）

主流程：主菜单 → 粘贴/解析分享链接（自动识别平台）→ 收集登录态（Cookie/Token/迅雷登录）→
        目录导航 → 选择文件/目录 → 分片并发下载（rich 进度条）→ 清理临时转存。
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from .config import ConfigStore
from .downloader.chunk_downloader import ChunkDownloader
from .downloader.downloader import DownloadManager, DownloadProgress
from .ui import RichUI
from . import webview
from .pan import (
    baidu,
    c139,
    pan123,
    quark,
    uc,
    xunlei,
)
from .pan.baidu_resolver import BaiduResolver
from .pan.c139_resolver import C139Resolver
from .pan.models import DownloadLink, ShareSession
from .pan.pan123_resolver import Pan123Resolver
from .pan.parser import ParsedShare, ShareLinkParser, SharePlatform
from .pan.quark import QuarkConstants
from .pan.resolver import QuarkResolver
from .pan.uc_resolver import UCResolver
from .pan.xunlei import XunleiApi, XunleiLoginStep
from .pan.xunlei_fingerprint import XunleiDeviceFingerprint
from .pan.xunlei_resolver import XunleiResolver

logger = logging.getLogger("pywangpan.tui")

TEMP_DIR_NAME = "YunX临时转存"


def _fmt_size(n: int) -> str:
    if n <= 0:
        return "未知"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


class EmptyFilesError(Exception):
    pass


DEFAULT_COOKIE_CHOICES = [
    ("browser", "在工具内打开浏览器登录并自动取 Cookie"),
    ("paste", "手动粘贴 Cookie"),
    ("cancel", "取消"),
]


def _browser_cookie(
    ui,
    handler,
    platform_title: str,
    cookie_key: str,
) -> str:
    """在工具内打开浏览器登录，自动读取并保存 Cookie；取消/失败返回空串。"""
    try:
        ui.info(f"[cyan]正在打开浏览器窗口用于 {platform_title} 登录 ...[/cyan]")
        cookie = webview.browser_login(
            handler.login_url,
            handler.required_cookies,
            lambda: ui.wait_browser_login(handler.login_url, platform_title),
            user_agent=handler.login_ua,
        )
    except Exception as e:  # noqa: BLE001
        ui.info(f"[red]浏览器登录失败: {e}[/red]")
        return ""
    if cookie:
        handler.config.set_cookie(cookie_key, cookie)
        ui.info(f"[dim]已自动获取 {platform_title} Cookie（{len(cookie)} 字符）并保存[/dim]")
    else:
        ui.info(f"[red]未获取到有效的 {platform_title} 登录态，请重试或改用手动粘贴[/red]")
    return cookie


@dataclass
class ResolveOutcome:
    """一次解析结果：含下载所需上下文，供共享下载循环使用。"""
    link: DownloadLink
    session: ShareSession
    resolver: object
    headers: dict
    handler: "PlatformHandler"


class PlatformHandler:
    platform: SharePlatform
    name: str
    root_fid: str = "0"

    def __init__(self, ui, config: ConfigStore):
        self.ui = ui
        self.config = config

    # ---- 登录态 ----

    def collect_auth(self) -> str:
        """返回后续 resolve 需要的凭据摘要（内存中），并负责把持久的 Cookie/Token 写回 config。"""
        raise NotImplementedError

    # ---- 流程 ----

    def resolve(self, parsed: ParsedShare) -> ResolveOutcome:
        raise NotImplementedError

    # ---- 下载头 / 清理 ----

    def headers(self, resolver, session: ShareSession, link: DownloadLink) -> dict:
        raise NotImplementedError

    def cleanup(self, resolver, link: DownloadLink) -> None:
        pass


# ============================================================ QUARK

class QuarkHandler(PlatformHandler):
    platform = SharePlatform.QUARK
    name = "夸克网盘"
    root_fid = "0"
    login_url = "https://pan.quark.cn/"
    login_ua = webview.DEFAULT_UA
    required_cookies = ("__puus", "__pus")

    def collect_auth(self) -> str:
        saved = self.config.get_cookie("quark")
        if saved:
            self.ui.info(f"[dim]已读取本机保存的夸克 Cookie（{len(saved)} 字符）[/dim]")
            if self.ui.confirm("使用已保存的 Cookie 吗？", default=True):
                return saved
        method = self.ui.choose("夸克登录方式", DEFAULT_COOKIE_CHOICES)
        if method == "cancel":
            raise EmptyFilesError("已取消夸克登录")
        if method == "browser":
            cookie = _browser_cookie(self.ui, self, "夸克网盘", "quark")
            if cookie:
                return cookie
            method = "paste"
        cookie = self.ui.ask("粘贴夸克 Cookie（须含 __pus / __puus）")
        cookie = cookie.strip()
        if cookie:
            self.config.set_cookie("quark", cookie)
        return cookie

    def resolve(self, parsed: ParsedShare) -> ResolveOutcome:
        cookie = self.collect_auth()
        if not cookie:
            raise EmptyFilesError("未提供夸克 Cookie")
        api = quark.QuarkApi(
            cookie=cookie,
            cookie_sink=lambda c: self.config.set_cookie("quark", c),
        )
        resolver = QuarkResolver(api, temp_dir_name=TEMP_DIR_NAME)
        session = resolver.create_session(parsed.share_id, parsed.pwd)
        file = self.ui.browse_files(
            lambda dir_fid: resolver.list_files(parsed.share_id, session.stoken, dir_fid),
            root_fid=self.root_fid,
            title=session.title or parsed.share_id,
        )
        link = resolver.get_share_download_link(parsed.share_id, session.stoken, file)
        return ResolveOutcome(link, session, resolver, self.headers(resolver, session, link), self)

    def headers(self, resolver, session, link):
        h = {
            "User-Agent": QuarkConstants.API_USER_AGENT,
            "Referer": "https://pan.quark.cn/",
        }
        cookie = getattr(resolver.api, "_cookie", "") or ""
        if cookie:
            h["Cookie"] = cookie
        return h

    def cleanup(self, resolver, link: DownloadLink) -> None:
        if link.cleanup_dir_fid:
            try:
                resolver.cleanup_temp_dir(link.cleanup_dir_fid)
                self.ui.info("[dim]已清理临时转存[/dim]")
            except Exception as e:  # pragma: no cover
                logger.debug("清理临时转存失败: %s", e)


# ============================================================ UC

class UcHandler(PlatformHandler):
    platform = SharePlatform.UC
    name = "UC 网盘"
    root_fid = "0"
    login_url = "https://drive.uc.cn/"
    login_ua = webview.DEFAULT_UA
    required_cookies = ("__puus", "__pus")

    def collect_auth(self) -> str:
        saved = self.config.get_cookie("uc")
        if saved:
            self.ui.info(f"[dim]已读取本机保存的 UC Cookie（{len(saved)} 字符）[/dim]")
            if self.ui.confirm("使用已保存的 Cookie 吗？", default=True):
                return saved
        method = self.ui.choose("UC 登录方式", DEFAULT_COOKIE_CHOICES)
        if method == "cancel":
            raise EmptyFilesError("已取消 UC 登录")
        if method == "browser":
            cookie = _browser_cookie(self.ui, self, "UC 网盘", "uc")
            if cookie:
                return cookie
            method = "paste"
        cookie = self.ui.ask("粘贴 UC Cookie（须含 __pus / __puus）")
        cookie = cookie.strip()
        if cookie:
            self.config.set_cookie("uc", cookie)
        return cookie

    def resolve(self, parsed: ParsedShare) -> ResolveOutcome:
        cookie = self.collect_auth()
        if not cookie:
            raise EmptyFilesError("未提供 UC Cookie")
        api = uc.UCApi(
            cookie=cookie,
            cookie_sink=lambda c: self.config.set_cookie("uc", c),
        )
        resolver = UCResolver(api, temp_dir_name=TEMP_DIR_NAME)
        session = resolver.create_session(parsed.share_id, parsed.pwd)
        file = self.ui.browse_files(
            lambda dir_fid: resolver.list_files(session, dir_fid),
            root_fid=self.root_fid,
            title=session.title or parsed.share_id,
        )
        link = resolver.get_share_download_link(session, file)
        return ResolveOutcome(link, session, resolver, self.headers(resolver, session, link), self)

    def headers(self, resolver, session, link):
        h = {
            "User-Agent": uc.UCConstants.USER_AGENT,
            "Referer": "https://drive.uc.cn/",
            "Origin": "https://drive.uc.cn",
        }
        cookie = getattr(resolver.api, "_cookie", "") or ""
        if cookie:
            h["Cookie"] = cookie
        return h


# ============================================================ BAIDU

class BaiduHandler(PlatformHandler):
    platform = SharePlatform.BAIDU
    name = "百度网盘"
    root_fid = "/"
    login_url = "https://pan.baidu.com/"
    login_ua = webview.DEFAULT_UA
    required_cookies = ("BDUSS", "BDUSS_BFESS")

    def collect_auth(self) -> str:
        saved = self.config.get_cookie("baidu")
        if saved:
            self.ui.info(f"[dim]已读取本机保存的百度 Cookie（{len(saved)} 字符）[/dim]")
            if self.ui.confirm("使用已保存的 Cookie 吗？", default=True):
                return saved
        method = self.ui.choose("百度登录方式", DEFAULT_COOKIE_CHOICES)
        if method == "cancel":
            raise EmptyFilesError("已取消百度登录")
        if method == "browser":
            cookie = _browser_cookie(self.ui, self, "百度网盘", "baidu")
            if cookie:
                return cookie
            method = "paste"
        cookie = self.ui.ask("粘贴百度 Cookie（须含 BDUSS）")
        cookie = cookie.strip()
        if cookie:
            self.config.set_cookie("baidu", cookie)
        return cookie

    def resolve(self, parsed: ParsedShare) -> ResolveOutcome:
        cookie = self.collect_auth()
        if not cookie:
            raise EmptyFilesError("未提供百度 Cookie")
        api = baidu.BaiduApi()
        resolver = BaiduResolver(api, cookie, temp_dir_name=TEMP_DIR_NAME)
        session = resolver.create_session(parsed.share_id, parsed.pwd)
        file = self.ui.browse_files(
            lambda dir_fid: resolver.list_files(session, dir_fid or "/"),
            root_fid=self.root_fid,
            title=session.title or parsed.share_id,
        )
        link = resolver.get_share_download_link(session, file)
        return ResolveOutcome(link, session, resolver, self.headers(resolver, session, link), self)

    def headers(self, resolver, session, link):
        return {"Cookie": resolver.cookie, "User-Agent": baidu.BaiduConstants.UA_NETDISK}


# ============================================================ C139

class C139Handler(PlatformHandler):
    platform = SharePlatform.C139
    name = "139 和彩云"
    root_fid = "0"
    login_url = "https://yun.139.com/"
    login_ua = webview.DEFAULT_UA
    required_cookies = ("authorization", "Os_SSo_Sid")

    def collect_auth(self) -> str:
        saved = self.config.get_cookie("c139")
        if saved:
            self.ui.info(f"[dim]已读取本机保存的 139 Cookie（{len(saved)} 字符）[/dim]")
            if self.ui.confirm("使用已保存的 Cookie 吗？", default=True):
                return saved
        method = self.ui.choose("139 登录方式", DEFAULT_COOKIE_CHOICES)
        if method == "cancel":
            raise EmptyFilesError("已取消 139 登录")
        if method == "browser":
            cookie = _browser_cookie(self.ui, self, "139 和彩云", "c139")
            if cookie:
                return cookie
            method = "paste"
        cookie = self.ui.ask("粘贴 139 Cookie（须含 authorization 或 Os_SSo_Sid+RMKEY）")
        cookie = cookie.strip()
        if cookie:
            self.config.set_cookie("c139", cookie)
        return cookie

    def resolve(self, parsed: ParsedShare) -> ResolveOutcome:
        cookie = self.collect_auth()
        if not cookie:
            raise EmptyFilesError("未提供 139 Cookie")
        resolver = C139Resolver(c139.C139Api(), cookie)
        session = resolver.create_session(parsed.share_id, parsed.pwd)
        file = self.ui.browse_files(
            lambda dir_fid: resolver.list_files(session, dir_fid),
            root_fid=self.root_fid,
            title=session.title or parsed.share_id,
        )
        link = resolver.get_share_download_link(session, file)
        return ResolveOutcome(link, session, resolver, self.headers(resolver, session, link), self)

    def headers(self, resolver, session, link):
        return {
            "User-Agent": c139.C139Constants.PC_UA,
            "Referer": "https://yun.139.com/",
        }


# ============================================================ PAN123

class Pan123Handler(PlatformHandler):
    platform = SharePlatform.PAN123
    name = "123 云盘"
    root_fid = "0"

    def collect_auth(self) -> str:
        saved = self.config.get_token("pan123")
        if saved and saved.get("token"):
            self.ui.info("[dim]已读取本机保存的 123 Token[/dim]")
            if self.ui.confirm("使用已保存的 Token 吗？", default=True):
                return saved["token"]
        return self._login_to_token()

    def _api_with_login(self) -> tuple:
        passport = self.ui.ask("123 云盘手机号")
        password = self.ui.ask("123 云盘密码", password=True)
        api = pan123.Pan123Api()
        with self.ui.status("正在登录 123 云盘..."):
            token = api.login(passport, password)
        self.config.set_token("pan123", {"token": token})
        return api, token

    def _login_to_token(self) -> str:
        try:
            api, token = self._api_with_login()
            self._pan123_api = api
            return token
        except pan123.Pan123ApiError as e:
            self.ui.info(f"[red]{e}[/red]")
            raise EmptyFilesError(str(e)) from e

    def resolve(self, parsed: ParsedShare) -> ResolveOutcome:
        token = self.collect_auth()
        if not token:
            raise EmptyFilesError("未取得 123 Token")
        api = getattr(self, "_pan123_api", None) or pan123.Pan123Api()
        resolver = Pan123Resolver(api, token)
        session = resolver.create_session(parsed.share_id, parsed.pwd)
        file = self.ui.browse_files(
            lambda dir_fid: resolver.list_files(session, dir_fid),
            root_fid=self.root_fid,
            title=session.title or parsed.share_id,
        )
        link = resolver.get_share_download_link(session, file, token)
        return ResolveOutcome(link, session, resolver, self.headers(resolver, session, link), self)

    def headers(self, resolver, session, link):
        return {
            "User-Agent": pan123.Pan123Constants.WEB_UA,
            "Referer": "https://yun.123pan.cn/",
        }


# ============================================================ XUNLEI

class XunleiHandler(PlatformHandler):
    platform = SharePlatform.XUNLEI
    name = "迅雷网盘"
    root_fid = ""

    FP_PATH = str(Path(os.environ.get("TEMP", Path.home())) / "pywangpan" / "xunlei_device_fp.txt")

    def collect_auth(self) -> str:
        """返回 access_token；刷新 token 通过 refresh_provider 在内存/持久续期。"""
        saved = self.config.get_token("xunlei")
        if saved and saved.get("access_token"):
            self.ui.info("[dim]已读取本机保存的迅雷登录态[/dim]")
            if self.ui.confirm("使用已保存的登录态吗？", default=True):
                return self._restore_session()
        return self._login_interactive()

    def _restore_session(self) -> str:
        token = self.config.get_token("xunlei")
        return self._build_api_and_tokens(
            access=token.get("access_token", ""),
            refresh=token.get("refresh_token", ""),
            captcha=token.get("captcha_token", ""),
        )[0]

    def _build_api_and_tokens(self, access, refresh, captcha, user_id=""):
        fp = XunleiDeviceFingerprint(fp_path=self.FP_PATH)
        fp.init()
        device_id = fp.device_id
        api = XunleiApi(device_fp=fp)

        def refresh_provider():
            nonlocal access, refresh
            if not refresh:
                return None
            try:
                new = api.refresh_token(refresh, device_id)
            except Exception as e:  # pragma: no cover
                logger.debug("迅雷刷新失败: %s", e)
                return None
            if not new:
                return None
            access, refresh = new
            self.config.set_token("xunlei", {
                "access_token": access,
                "refresh_token": refresh,
                "device_id": device_id,
                "captcha_token": captcha,
            })
            return (access, refresh)

        return access, refresh, device_id, captcha, api, refresh_provider

    def _login_interactive(self) -> str:
        fp = XunleiDeviceFingerprint(fp_path=self.FP_PATH)
        fp.init()
        device_id = fp.device_id
        api = XunleiApi(device_fp=fp)

        mode = self.ui.choose(
            "迅雷登录方式",
            [("password", "密码登录"), ("sms", "短信验证码"), ("cancel", "取消")],
        )
        if mode == "cancel":
            raise EmptyFilesError("已取消迅雷登录")
        username = self.ui.ask("迅雷账号（手机号/邮箱）")
        step: XunleiLoginStep
        if mode == "password":
            password = self.ui.ask("迅雷密码", password=True)
            with self.ui.status("正在登录迅雷..."):
                step = api.login_with_password(username, password, device_id)
        else:
            with self.ui.status("正在发送短信验证码..."):
                sms_step = api.send_sms(username, device_id)
            sms_token = getattr(sms_step, "sms_token", "")
            credit_key = getattr(sms_step, "sms_credit_key", "")
            code = self.ui.ask("短信验证码")
            with self.ui.status("正在校验验证码..."):
                step = api.sms_login(username, code, credit_key, sms_token, device_id)

        if step.need_sms:
            if mode != "sms":
                with self.ui.status("发送短信验证码..."):
                    sms_step = api.send_sms(username, device_id)
                code = self.ui.ask("短信验证码（首次登录需二次验证）")
                with self.ui.status("校验验证码..."):
                    step = api.sms_login(
                        username, code,
                        sms_step.sms_credit_key, sms_step.sms_token, device_id,
                    )
            else:
                raise EmptyFilesError("短信登录未成功，请重试")

        if not getattr(step, "session_id", "") and not step.user_id:
            raise EmptyFilesError(step.message or "迅雷登录失败")

        # 交换 access/refresh token
        captcha_token = ""
        try:
            with self.ui.status("初始化验证环境..."):
                captcha_token = api.init_captcha(device_id, username) or ""
        except Exception:  # pragma: no cover
            captcha_token = ""
        with self.ui.status("交换 Token..."):
            tokens = api.exchange_token(step.session_id, device_id, captcha_token)
        if not tokens:
            raise EmptyFilesError("迅雷 Token 交换失败")
        access, refresh = tokens
        self.config.set_token("xunlei", {
            "access_token": access,
            "refresh_token": refresh,
            "device_id": device_id,
            "captcha_token": captcha_token,
        })
        self._xunlei_refresh = refresh
        self._xunlei_device = device_id
        return access

    def resolve(self, parsed: ParsedShare) -> ResolveOutcome:
        access = self.collect_auth()
        if not access:
            raise EmptyFilesError("未取得迅雷 Token")
        token = self.config.get_token("xunlei")
        captcha = (token or {}).get("captcha_token", "") or ""
        refresh = (token or {}).get("refresh_token", "") or getattr(self, "_xunlei_refresh", "")

        _, _, device_id, captcha, api, refresh_provider = self._build_api_and_tokens(
            access=access, refresh=refresh, captcha=captcha,
        )
        resolver = XunleiResolver(
            api,
            access_token=access,
            device_id=device_id,
            captcha_token=captcha,
            refresh_provider=refresh_provider,
            temp_dir_name=TEMP_DIR_NAME,
        )
        try:
            session = resolver.create_session(parsed.share_id, parsed.pwd)
            file = self.ui.browse_files(
                lambda dir_fid: resolver.list_files(session, dir_fid),
                root_fid=self.root_fid,
                title=session.title or parsed.share_id,
            )
            link = resolver.get_share_download_link(session, file)
        except Exception:
            raise
        return ResolveOutcome(link, session, resolver, self.headers(resolver, session, link), self)

    def headers(self, resolver, session, link):
        return {"User-Agent": xunlei.XunleiConstants.APP_UA}


# ============================================================ registry

_HANDLERS = [
    QuarkHandler,
    UcHandler,
    BaiduHandler,
    C139Handler,
    Pan123Handler,
    XunleiHandler,
]


def handler_for(platform: SharePlatform, ui, config: ConfigStore):
    for cls in _HANDLERS:
        if cls.platform is platform:
            return cls(ui, config)
    raise EmptyFilesError(f"暂不支持平台 {platform.value}")


# ============================================================ TUI App

class TuiApp:
    def __init__(self, console: Console | None = None, config: ConfigStore | None = None,
                 dl: DownloadManager | None = None, argv_config: dict | None = None):
        self.console = console or Console()
        self.ui = RichUI(self.console)
        self.config = config or ConfigStore()
        self.config.load()
        self.argv_config = argv_config or {}
        self._threads = max(1, int(self.argv_config.get("threads") or 16))
        self._out_dir = self.argv_config.get("out") or "."
        self.dl = dl or DownloadManager(
            ChunkDownloader(), thread_count=self._threads, out_dir=self._out_dir
        )

    # ---------------- 通用选择工具 ----------------

    def choose(self, prompt: str, choices: list[tuple[str, str]]) -> str:
        return self.ui.choose(prompt, choices)

    # ---------------- 主流程 ----------------

    def _collect_download_settings(self) -> None:
        self.console.print(Panel("[bold]下载设置[/bold]", border_style="cyan"))
        self._threads = int(self.ui.ask("分片并发线程数", default=str(self._threads)))
        out = self.ui.ask("输出目录", default=self._out_dir)
        self._out_dir = out
        self._rebuild_downloader()

    def _rebuild_downloader(self) -> None:
        self.dl = DownloadManager(
            ChunkDownloader(), thread_count=max(1, self._threads), out_dir=self._out_dir
        )

    def run_resolve(self) -> None:
        raw = self.ui.ask("粘贴分享链接 / 包含链接的文字")
        parsed = ShareLinkParser.parse(raw)
        if not parsed:
            self.console.print("[red]无法识别分享链接[/red]")
            return
        self.console.print(
            Panel(f"[bold]平台:[/bold] {_HANDLER_LABEL.get(parsed.platform, parsed.platform.value)}\n"
                  f"[bold]分享ID:[/bold] {parsed.share_id}\n"
                  f"[bold]提取码:[/bold] {parsed.pwd or '(无)'}", border_style="green")
        )
        handler = handler_for(parsed.platform, self.ui, self.config)
        try:
            outcome = handler.resolve(parsed)
        except EmptyFilesError as e:
            self.console.print(f"[yellow]{e}[/yellow]")
            return
        except Exception as e:
            self.console.print(f"[red]解析失败: {e}[/red]")
            return
        self._download(outcome)

    def _download(self, outcome: ResolveOutcome) -> None:
        link = outcome.link
        self.console.print(f"[green]已获取直链[/green]: {link.filename}（{_fmt_size(link.size)}）")
        if not self.ui.confirm("开始下载？", default=True):
            self._maybe_cleanup(outcome)
            return
        desc = link.filename
        total = link.size if link.size > 0 else None
        done = {"downloaded": 0}
        lock = threading.Lock()

        with Progress(
            TextColumn("[bold blue]{task.description}[/bold blue]"),
            BarColumn(),
            DownloadColumn(binary_units=True),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=self.console,
        ) as progress:
            task = progress.add_task(desc, total=total)

            def on_progress(p: DownloadProgress):
                with lock:
                    done["downloaded"] = p.downloaded
                    progress.update(task, completed=p.downloaded)

            try:
                result = self.dl.download(
                    link.download_url,
                    link.filename,
                    headers=outcome.headers,
                    known_size=link.size,
                    on_progress=on_progress,
                )
                progress.update(task, completed=total or done["downloaded"])
                self.console.print(f"[bold green]下载完成:[/bold green] {result}")
            except Exception as e:
                self.console.print(f"[red]下载失败: {e}[/red]")
            finally:
                self._maybe_cleanup(outcome)

    def _maybe_cleanup(self, outcome: ResolveOutcome) -> None:
        try:
            outcome.handler.cleanup(outcome.resolver, outcome.link)
        except Exception as e:  # pragma: no cover
            logger.debug("清理失败: %s", e)


_HANDLER_LABEL = {}


def _build_label_map():
    for cls in _HANDLERS:
        _HANDLER_LABEL[cls.platform] = cls.name


_build_label_map()


# ============================================================ entry

def launch_tui(argv_config: dict | None = None) -> int:
    tui = TuiApp(argv_config=argv_config)
    console = tui.console
    console.print(Panel("[bold cyan] 🚀 pywangpan — 网盘分享解析与高速下载 [/bold cyan]\n"
                        "[dim]夸克 / UC / 百度 / 139 / 123 / 迅雷[/dim]", border_style="cyan"))
    while True:
        choice = tui.choose(
            "主菜单",
            [
                ("resolve", "解析分享链接并下载"),
                ("settings", "下载设置（线程数 / 输出目录）"),
                ("quit", "退出"),
            ],
        )
        if choice == "quit":
            break
        if choice == "settings":
            tui._collect_download_settings()
            continue
        try:
            tui.run_resolve()
        except EmptyFilesError as e:
            console.print(f"[yellow]{e}[/yellow]")
        except KeyboardInterrupt:
            console.print("\n[yellow]已中断[/yellow]")
        except Exception as e:  # pragma: no cover
            console.print(f"[red]发生错误: {e}[/red]")
    console.print("[dim]再见[/dim]")
    return 0
