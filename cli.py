"""命令行入口：解析分享链接并高速下载。

交互式 TUI（推荐）：
  python -m pywangpan.cli

一次性参数（夸克单平台，供脚本使用）：
  python -m pywangpan.cli --cookie "..." --url "https://pan.quark.cn/s/xxxx" --pwd 1234
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .downloader.chunk_downloader import ChunkDownloader
from .downloader.downloader import DownloadManager, DownloadProgress
from .pan.models import ShareFile
from .pan.parser import ShareLinkParser, SharePlatform
from .pan.quark import QuarkApi, QuarkConstants
from .pan.resolver import QuarkResolver


def _load_cookie(args) -> str:
    cookie = args.cookie
    if not cookie and args.cookie_file:
        cookie = Path(args.cookie_file).read_text(encoding="utf-8").strip()
    if not cookie:
        print("错误：需要 --cookie 或 --cookie-file 提供夸克登录 Cookie", file=sys.stderr)
        sys.exit(2)
    return cookie


def _pick_file(files: list[ShareFile], index: int | None) -> ShareFile:
    """让用户选择要下载的文件（目录递归示意：此处仅列一层）。"""
    files = [f for f in files if not f.isdir]
    if not files:
        print("该目录下没有可下载的文件", file=sys.stderr)
        sys.exit(1)
    if index is not None and 0 <= index < len(files):
        return files[index]
    print("可下载文件：")
    for i, f in enumerate(files):
        size = f"{f.fsize / 1024 / 1024:.1f} MB" if f.fsize else "未知"
        print(f"  [{i}] {f.fname} ({size})")
    while True:
        try:
            choice = int(input("选择文件序号: "))
            return files[choice]
        except (ValueError, IndexError):
            print("无效序号，请重试", file=sys.stderr)


def _progress_cb(p: DownloadProgress):
    if p.total > 0:
        percent = p.downloaded * 100 / p.total
        speed = f"{p.speed / 1024 / 1024:.2f} MB/s" if p.speed else ".."
        print(f"\r  {percent:5.1f}%  {p.downloaded / 1024 / 1024:.1f}/{p.total / 1024 / 1024:.1f} MB  {speed}", end="")
    else:
        print(f"\r  已下载 {p.downloaded / 1024 / 1024:.1f} MB", end="")
    if p.done:
        print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="网盘分享链接解析与高速下载")
    parser.add_argument("--cookie", help="夸克登录 Cookie 字符串")
    parser.add_argument("--cookie-file", help="存放 Cookie 的文件路径")
    parser.add_argument("--url", help="分享链接（缺省进入交互式 TUI）")
    parser.add_argument("--pwd", help="提取码（可空）")
    parser.add_argument("--index", type=int, help="选择文件序号（缺省则交互选择）")
    parser.add_argument("--threads", type=int, default=16, help="分片并发线程数")
    parser.add_argument("--out", default=".", help="输出目录")
    parser.add_argument("-v", "--verbose", action="store_true", help="打印网络日志")
    parser.add_argument("--gui", action="store_true", help="启动 tkinter 桌面窗口（GUI）")
    return parser


def _run_one_shot(args) -> int:
    import logging

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s %(message)s")
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    parsed = ShareLinkParser.parse(args.url)
    if not parsed:
        print("无法识别分享链接", file=sys.stderr)
        return 1
    if parsed.platform is not SharePlatform.QUARK:
        print(f"一次性模式仅支持夸克平台，遇到的是 {parsed.platform.value}。请改用 TUI（python -m pywangpan.cli）", file=sys.stderr)
        return 1

    cookie = _load_cookie(args)
    api = QuarkApi(cookie=cookie, cookie_sink=lambda c: print("  (Cookie 已刷新)", file=sys.stderr))
    resolver = QuarkResolver(api)
    dl = DownloadManager(ChunkDownloader(), thread_count=args.threads, out_dir=args.out)

    session = resolver.create_session(parsed.share_id, parsed.pwd or args.pwd)
    print(f"分享标题: {session.title or '(未知)'}")

    files = resolver.list_files(parsed.share_id, session.stoken, "0")
    file = _pick_file(files, args.index)

    link = resolver.get_share_download_link(parsed.share_id, session.stoken, file)
    print(f"获取直链: {link.filename}")

    headers = {
        "User-Agent": QuarkConstants.API_USER_AGENT,
        "Referer": "https://pan.quark.cn/",
        "Cookie": cookie,
    }
    try:
        result = dl.download(
            link.download_url,
            link.filename,
            headers=headers,
            known_size=link.size,
            on_progress=_progress_cb,
        )
        print(f"\n已保存到: {result}")
    finally:
        if link.cleanup_dir_fid:
            resolver.cleanup_temp_dir(link.cleanup_dir_fid)
            print("已清理临时转存", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.gui:
        from .gui import launch_gui

        return launch_gui(
            argv_config={"threads": args.threads, "out": args.out, "verbose": args.verbose}
        )

    if not args.url:
        from .tui import launch_tui

        return launch_tui(
            argv_config={"threads": args.threads, "out": args.out, "verbose": args.verbose}
        )
    return _run_one_shot(args)


if __name__ == "__main__":
    raise SystemExit(main())

