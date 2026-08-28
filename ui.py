"""UI 抽象协议：handler 只依赖这套接口，rich TUI 与 tkinter GUI 各实现一份。

这样解析/登录/下载头/清理等核心逻辑单一来源，界面可切换而不改动 handler。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, Iterator, Protocol

from rich.prompt import Confirm, Prompt
from rich.table import Table

from .pan.models import ShareFile


def _fmt_size(n: int) -> str:
    if n <= 0:
        return "未知"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


class UI(Protocol):
    """handler 与界面的交互契约。"""

    def info(self, message: str) -> None: ...

    def ask(self, prompt: str, default: str = "", password: bool = False) -> str: ...

    def confirm(self, prompt: str, default: bool = True) -> bool: ...

    def choose(self, prompt: str, choices: list[tuple[str, str]]) -> str: ...

    @contextmanager
    def status(self, label: str) -> Iterator[None]: ...

    def browse_files(
        self,
        list_fn: Callable[[str], list[ShareFile]],
        root_fid: str,
        title: str = "",
    ) -> ShareFile: ...


class RichUI:
    """rich 终端的 UI 实现。"""

    def __init__(self, console):
        self.console = console

    def info(self, message: str) -> None:
        self.console.print(message)

    def ask(self, prompt: str, default: str = "", password: bool = False) -> str:
        return Prompt.ask(prompt, default=default or None, password=password) or default

    def confirm(self, prompt: str, default: bool = True) -> bool:
        return Confirm.ask(prompt, default=default)

    def choose(self, prompt: str, choices: list[tuple[str, str]]) -> str:
        table = Table(title=prompt, border_style="cyan", box=None)
        table.add_column("#", justify="right")
        table.add_column("选项", style="bold")
        table.add_column("说明", style="dim")
        for i, (label, desc) in enumerate(choices, 1):
            table.add_row(str(i), label, desc)
        self.console.print(table)
        while True:
            raw = Prompt.ask("输入序号")
            try:
                idx = int(raw)
                if 1 <= idx <= len(choices):
                    return choices[idx - 1][0]
            except ValueError:
                pass
            self.console.print("[red]无效序号，请重试[/red]")

    @contextmanager
    def status(self, label: str) -> Iterator[None]:
        with self.console.status(label):
            yield

    def _retry_or_quit(self, list_fn, dir_fid) -> ShareFile:
        if self.confirm("重试列目录？", default=True):
            return self.browse_files(list_fn, dir_fid)
        from .tui import EmptyFilesError

        raise EmptyFilesError("列目录失败")

    def browse_files(
        self,
        list_fn: Callable[[str], list[ShareFile]],
        root_fid: str,
        title: str = "",
    ) -> ShareFile:
        from .tui import EmptyFilesError

        dir_ = root_fid
        stack: list[str] = []
        while True:
            try:
                files = list_fn(dir_)
            except Exception as e:
                self.console.print(f"[red]列出目录失败: {e}[/red]")
                return self._retry_or_quit(list_fn, dir_)
            dirs = [f for f in files if f.isdir]
            plain = [f for f in files if not f.isdir]

            table = Table(
                title=f"📁 {title or ''}  [dim]当前目录: {'/'.join(stack) or '/'}[/dim]",
                box=None,
            )
            table.add_column("类型", justify="right", width=4)
            table.add_column("名称", style="bold")
            table.add_column("大小", justify="right")
            table.add_column("序号", justify="right")
            n = 0
            for d in dirs:
                n += 1
                table.add_row("DIR", d.fname, "", str(n))
            for f in plain:
                n += 1
                table.add_row("FILE", f.fname, _fmt_size(f.fsize), str(n))
            self.console.print(table)

            if not files:
                self.console.print("[yellow]该目录为空[/yellow]")
            prompt = "输入序号选择文件/目录 ([green]b[/green] 上级  [green]q[/green] 取消)"
            if stack:
                prompt += f"（共 {len(plain)} 个文件）"
            while True:
                raw = Prompt.ask(prompt)
                raw = raw.strip().lower()
                if raw in ("q", "quit", "exit"):
                    raise EmptyFilesError("已取消选择")
                if raw == "b":
                    if stack:
                        dir_ = stack.pop()
                        break
                    self.console.print("[yellow]已在根目录[/yellow]")
                    continue
                try:
                    idx = int(raw)
                    if 1 <= idx <= n:
                        target = files[idx - 1]
                        if target.isdir:
                            stack.append(dir_)
                            dir_ = target.fid
                        else:
                            return target
                        break
                except ValueError:
                    pass
                self.console.print("[red]无效序号，请重试[/red]")
