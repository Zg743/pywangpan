"""GUI 非交互测试：TkUI 主线程桥接、格式化函数。

不弹真实对话框（patch 掉 simpledialog），验证 _sync 能把调用路由回主线程并返回结果。
"""
import os
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_root():
    import tkinter as tk

    try:
        root = tk.Tk()
    except Exception:  # pragma: no cover - 无显示环境
        raise unittest.SkipTest("无法初始化 Tk")
    root.withdraw()
    return root


class TkUITest(unittest.TestCase):
    def test_fmt_size(self):
        from pywangpan.gui import _fmt_size

        self.assertEqual(_fmt_size(0), "未知")
        self.assertEqual(_fmt_size(512), "512.0 B")
        self.assertEqual(_fmt_size(2048), "2.0 KB")
        self.assertEqual(_fmt_size(5 * 1024 * 1024), "5.0 MB")

    def test_sync_bridges_to_main_thread(self):
        from pywangpan.gui import TkUI

        root = _make_root()
        ui = TkUI(root)
        got = {}

        def worker():
            with patch("pywangpan.gui.simpledialog.askstring", return_value="hello"):
                got["v"] = ui.ask("输入", "d")
            with patch("pywangpan.gui.messagebox.askyesno", return_value=True):
                got["c"] = ui.confirm("确认")
            ui._sync(lambda: root.quit())

        t = threading.Thread(target=worker)
        t.start()
        try:
            root.mainloop()
            t.join(timeout=5)
        finally:
            root.destroy()

        self.assertEqual(got.get("v"), "hello")
        self.assertEqual(got.get("c"), True)


if __name__ == "__main__":
    unittest.main()
