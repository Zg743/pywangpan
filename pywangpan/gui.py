"""tkinter 桌面 GUI：复用 tui.py 的 6 平台 handler 与 ui.py 的 UI 协议。

所有网络/解析/下载都在后台线程执行；TkUI 把交互（登录态收集、目录导航）桥接回主
线程以弹窗完成；下载进度经 queue 回流主线程刷新进度条。
"""
from __future__ import annotations

import os
import queue
import threading
from contextlib import contextmanager

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from .config import ConfigStore
from .downloader.chunk_downloader import ChunkDownloader
from .downloader.downloader import DownloadManager
from .pan.models import ShareFile
from .pan.parser import ShareLinkParser
from .tui import EmptyFilesError, handler_for, _HANDLER_LABEL


def _fmt_size(n: int) -> str:
    if n <= 0:
        return "未知"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.0f} {unit}"


class TkUI:
    """UI 协议：以 tkinter 弹窗实现交互。

    后台线程通过 `_sync` 把调用请求放入队列后等待；主线程的轮询器（运行于 mainloop）
    取出请求并在主线程执行（tkinter 只能在主线程操作），完成后再唤醒等待线程。
    """

    def __init__(self, root: tk.Tk, log_cb=None):
        self.root = root
        self._log = log_cb or (lambda m: None)
        self._req_q = queue.Queue()
        self._pending: dict[int, dict] = {}
        self._n = 0
        self.root.after(0, self._poll_requests)

    def _poll_requests(self):
        while True:
            try:
                rid, fn = self._req_q.get_nowait()
            except queue.Empty:
                break
            if rid is None:
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    pass
                continue
            slot = self._pending.get(rid)
            if slot is None:
                continue
            try:
                slot["reply"]["v"] = fn()
            except Exception as e:  # noqa: BLE001
                slot["reply"]["e"] = e
            finally:
                slot["event"].set()
        self.root.after(30, self._poll_requests)

    def _sync(self, fn):
        """提交调用到主线程执行并等待结果/异常。仅后台线程调用。"""
        with threading.Lock():
            self._n += 1
            rid = self._n
            slot = {"event": threading.Event(), "reply": {}}
            self._pending[rid] = slot
        self._req_q.put((rid, fn))
        slot["event"].wait()
        reply = slot["reply"]
        self._pending.pop(rid, None)
        if "e" in reply:
            raise reply["e"]
        return reply.get("v")

    def _call_async(self, fn):
        """把调用投递到主线程执行，不等待（fire-and-forget）。仅后台线程调用。"""
        self._req_q.put((None, fn))

    def info(self, message: str) -> None:
        self._log(str(message))
        return None

    def ask(self, prompt, default="", password=False):
        def _do():
            return simpledialog.askstring(
                "pywangpan",
                prompt,
                initialvalue=default or None,
                show="*" if password else None,
            )
        return self._sync(_do) or default

    def confirm(self, prompt, default=True):
        def _do():
            if default:
                return bool(messagebox.askyesno("pywangpan", prompt))
            return bool(messagebox.askyesno("pywangpan", prompt))
        return self._sync(_do)

    def choose(self, prompt, choices):
        text = f"{prompt}\n\n" + "\n".join(f"{i+1}. {label} — {desc}" for i, (label, desc) in enumerate(choices))
        def _do():
            idx = simpledialog.askinteger("pywangpan", text + "\n\n输入序号", minvalue=1, maxvalue=len(choices))
            if idx is None:
                raise EmptyFilesError("已取消")
            return choices[idx - 1][0]
        return self._sync(_do)

    @contextmanager
    def status(self, label):
        self._log(label)
        yield
        self._log("完成")

    def browse_files(self, list_fn, root_fid, title=""):
        def _do():
            return _FileBrowser(self.root, list_fn, root_fid, title).run()
        return self._sync(_do)

    def wait_browser_login(self, url, title=""):
        """阻塞后台线程，弹出\"完成登录\"对话框；用户点击后返回 ok / cancel。"""
        ev = threading.Event()
        holder = {"r": "cancel"}

        def show():
            win = tk.Toplevel(self.root)
            win.title("浏览器登录 — " + (title or "网盘"))
            win.geometry("420x200")
            win.transient(self.root)
            win.resizable(False, False)
            win.protocol("WM_DELETE_WINDOW", lambda: _finish("cancel"))

            def _finish(r):
                holder["r"] = r
                win.destroy()
                ev.set()

            ttk.Label(
                win,
                text="已在独立浏览器窗口打开登录页。\n\n"
                     "请在浏览器里完成登录后，回到这里点击“完成登录”。\n"
                     "工具将自动读取 Cookie。",
                justify="left",
                wraplength=380,
                padding=14,
            ).pack(anchor="w")
            url_lbl = ttk.Label(win, text=url, foreground="#1a6fd4", padding=(14, 0))
            url_lbl.pack(anchor="w")
            btns = ttk.Frame(win, padding=(14, 12))
            btns.pack(fill="x")
            ttk.Button(btns, text="已登录，读取 Cookie", command=lambda: _finish("ok")).pack(side="left")
            ttk.Button(btns, text="取消", command=lambda: _finish("cancel")).pack(side="right")

        self._call_async(show)
        ev.wait()
        return holder["r"]


class _FileBrowser:
    """模态目录/文件浏览器（树形列表，双击进目录，选中即返回）。"""

    def __init__(self, root, list_fn, root_fid, title):
        self.root = root
        self.list_fn = list_fn
        self.dir = root_fid
        self.stack = []
        self._item_map = {}
        self._result = None
        self.win = tk.Toplevel(root)
        self.win.title(f"选择文件 — {title or ''}")
        self.win.geometry("760x480")
        self.win.transient(root)
        self.win.grab_set()

        top = ttk.Frame(self.win, padding=6)
        top.pack(side="top", fill="x")
        self.path_var = tk.StringVar(value="/")
        self.path_lbl = ttk.Label(top, textvariable=self.path_var, font=("TkDefaultFont", 9, "bold"))
        self.path_lbl.pack(side="left", fill="x", expand=True)
        ttk.Button(top, text="上级", command=self._go_up).pack(side="right")

        self.tree = ttk.Treeview(self.win, columns=("size",), selectmode="browse")
        self.tree.heading("#0", text="名称", anchor="w")
        self.tree.heading("size", text="大小", anchor="e")
        self.tree.column("#0", width=520, anchor="w")
        self.tree.column("size", width=180, anchor="e")
        vsb = ttk.Scrollbar(self.win, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Return>", self._on_double)

        bottom = ttk.Frame(self.win, padding=6)
        bottom.pack(side="bottom", fill="x")
        ttk.Button(bottom, text="重试", command=lambda: self._load(self.dir)).pack(side="left")
        ttk.Button(bottom, text="取消", command=lambda: self._finish(None)).pack(side="right")
        ttk.Button(bottom, text="选择", command=self._select).pack(side="right")

        self._load(self.dir)

    def _load(self, dir_fid):
        self.dir = dir_fid
        self.tree.delete(*self.tree.get_children())
        self.path_var.set("/".join(self.stack) or "/")
        files = self.list_fn(dir_fid)
        for f in files:
            tag = "dir" if f.isdir else "file"
            size = "" if f.isdir else _fmt_size(f.fsize)
            self.tree.insert("", "end", iid=f.fid, text=("📁 " if f.isdir else "📄 ") + f.fname,
                             values=(size,), tags=(tag,))
            self._item_map[f.fid] = f
        self.tree.tag_configure("dir", foreground="#1a6fd4")

    def _on_double(self, _evt):
        sel = self.tree.selection()
        if not sel:
            return
        f = self._item_map.get(sel[0])
        if f and f.isdir:
            self.stack.append(self.dir)
            self._load(f.fid)
        elif f:
            self._finish(f)

    def _go_up(self):
        if self.stack:
            parent = self.stack.pop()
            self._load(parent)

    def _select(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("pywangpan", "请先选择一个文件", parent=self.win)
            return
        f = self._item_map.get(sel[0])
        if f and not f.isdir:
            self._finish(f)
        elif f:
            self._on_double(None)

    def _finish(self, f):
        self._result = f
        self.win.destroy()

    def run(self):
        self.win.wait_window()
        self.root.deiconify()
        return self._result


class GuiApp:
    def __init__(self, root, config=None, argv_config=None):
        self.root = root
        self.root.title("pywangpan — 网盘分享解析与高速下载")
        self.root.geometry("860x600")
        self.log_q = queue.Queue()
        self.prog_q = queue.Queue()
        self._out_dir = "."
        self._threads = 16
        self.config = config or ConfigStore()
        self.config.load()
        self.argv_config = argv_config or {}
        self._threads = max(1, int(self.argv_config.get("threads") or 16))
        self._out_dir = self.argv_config.get("out") or "."
        self.ui = TkUI(root, log_cb=self._log)
        self._busy = False
        self._build_widgets()
        self.root.after(100, self._poll)

    # ---------------- 界面 ----------------

    def _build_widgets(self):
        frm = ttk.Frame(self.root, padding=8)
        frm.pack(fill="x")
        ttk.Label(frm, text="分享链接:").pack(side="left")
        self.link_var = tk.StringVar()
        self.link_entry = ttk.Entry(frm, textvariable=self.link_var, width=72)
        self.link_entry.pack(side="left", fill="x", expand=True, padx=4)
        self.resolve_btn = ttk.Button(frm, text="解析", command=self._on_resolve)
        self.resolve_btn.pack(side="left")

        st = ttk.Frame(self.root, padding=(8, 0))
        st.pack(fill="x")
        self.thread_var = tk.StringVar(value=str(self._threads))
        ttk.Label(st, text="并发线程:").pack(side="left")
        ttk.Spinbox(st, from_=1, to=64, width=5, textvariable=self.thread_var).pack(side="left", padx=(0, 12))
        ttk.Label(st, text="输出目录:").pack(side="left")
        self.out_var = tk.StringVar(value=self._out_dir)
        ttk.Entry(st, textvariable=self.out_var, width=44).pack(side="left", padx=4)
        ttk.Button(st, text="浏览", command=self._pick_out_dir).pack(side="left")

        body = ttk.PanedWindow(self.root, orient="vertical")
        body.pack(fill="both", expand=True, padx=8, pady=8)

        logf = ttk.Frame(body)
        body.add(logf, weight=2)
        ttk.Label(logf, text="日志").pack(anchor="w")
        self.log_text = tk.Text(logf, wrap="word", state="disabled", font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)

        dlf = ttk.Frame(body)
        body.add(dlf, weight=1)
        ttk.Label(dlf, text="下载").pack(anchor="w")
        self.dl_label = ttk.Label(dlf, text="就绪", anchor="w")
        self.dl_label.pack(fill="x")
        self.prog_var = tk.DoubleVar(value=0)
        self.prog = ttk.Progressbar(dlf, variable=self.prog_var, maximum=100)
        self.prog.pack(fill="x")

    def _pick_out_dir(self):
        d = filedialog.askdirectory(initialdir=self.out_var.get())
        if d:
            self.out_var.set(d)

    # ---------------- 日志 / 进度回流 ----------------

    def _log(self, msg):
        self.log_q.put(f"[{threading.current_thread().name}] {msg}")

    def _poll(self):
        try:
            while True:
                m = self.log_q.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", str(m) + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        try:
            while True:
                p = self.prog_q.get_nowait()
                cur = p.downloaded
                total = p.total if p.total > 0 else 0
                if total:
                    self.prog_var.set(min(100, cur * 100.0 / total))
                    self.dl_label.configure(
                        text=f"{p.filename}  {_fmt_size(cur)} / {_fmt_size(total)}"
                             + (f"  {_fmt_size(p.speed)}/s" if p.speed else ""))
                else:
                    self.dl_label.configure(text=f"{p.filename}  {_fmt_size(cur)}")
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    # ---------------- 解析 + 下载 ----------------

    def _on_resolve(self, *_):
        if self._busy:
            messagebox.showwarning("pywangpan", "已有任务进行中")
            return
        raw = self.link_var.get().strip()
        if not raw:
            messagebox.showinfo("pywangpan", "请先粘贴分享链接")
            return
        parsed = ShareLinkParser.parse(raw)
        if not parsed:
            messagebox.showerror("pywangpan", "无法识别分享链接")
            return
        label = _HANDLER_LABEL.get(parsed.platform, parsed.platform.value)
        self._log(f"识别平台: {label}  分享ID: {parsed.share_id}"
                  + (f"  提取码: {parsed.pwd}" if parsed.pwd else ""))
        threads = max(1, int(self.thread_var.get() or 16))
        out_dir = self.out_var.get().strip() or "."
        self._busy = True
        self.resolve_btn.configure(state="disabled")
        self._log(f"== 开始解析（{label}） ==")
        threading.Thread(target=self._worker, args=(parsed, threads, out_dir), daemon=True).start()

    def _worker(self, parsed, threads, out_dir):
        try:
            handler = handler_for(parsed.platform, self.ui, self.config)
            outcome = handler.resolve(parsed)
            self._log(f"已获取直链: {outcome.link.filename}（{_fmt_size(outcome.link.size)}）")
            proceed = outcome.link
            if not proceed:
                return
            self._log("开始下载 ...")
            self._download(outcome, threads, out_dir)
            self._maybe_cleanup(outcome)
        except EmptyFilesError as e:
            self._log(f"[取消/失败] {e}")
        except Exception as e:  # noqa: BLE001
            self._log(f"[错误] {repr(e)}")
        finally:
            self._busy = False
            self.ui._sync(lambda: self.resolve_btn.configure(state="normal"))
            self._log("== 任务结束 ==")

    def _download(self, outcome, threads, out_dir):
        dl = DownloadManager(ChunkDownloader(), thread_count=threads, out_dir=out_dir)
        link = outcome.link
        on_progress = lambda p: self.prog_q.put(p)
        dl.download(
            link.download_url,
            link.filename,
            headers=outcome.headers,
            known_size=link.size,
            on_progress=on_progress,
        )
        self._log(f"下载完成: {link.filename}")

    def _maybe_cleanup(self, outcome):
        try:
            outcome.handler.cleanup(outcome.resolver, outcome.link)
            self._log("已清理临时转存")
        except Exception as e:  # noqa: BLE001
            self._log(f"清理失败: {e}")


def launch_gui(argv_config=None) -> int:
    root = tk.Tk()
    GuiApp(root, argv_config=argv_config)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(launch_gui())
