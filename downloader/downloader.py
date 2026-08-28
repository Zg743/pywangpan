"""下载任务管理器（对应 YunX DownloadManager.kt）。

- 分片规划：主池 70% 等分 + 弹性区 30% 按字节序领 4MB 块；
- 断点续传：part/seg 文件保留，按磁盘已有长度续传；
- Range 忽略回退单流、失败区间重试、大小校验、进度/速度统计。
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .chunk_downloader import ChunkDownloader, ChunkResult

logger = logging.getLogger("pywangpan.download")

RANGE_WORKERS_CAP = 8
STAGGER_CAP = 8
STAGGER_MS = 25
RANGE_IGNORED_TOLERANCE = 3
DEFAULT_ELASTIC_BLOCK = 4 * 1024 * 1024


@dataclass
class DownloadProgress:
    downloaded: int = 0
    total: int = 0
    speed: float = 0.0  # 字节/秒
    done: bool = False
    error: str | None = None
    filename: str = ""


class _ElasticAllocator:
    """弹性区分配器：按字节顺序领取固定大小块，保证物理相邻。"""

    def __init__(self, total: int, elastic_start: int):
        self._total = total
        self._next_start = elastic_start
        self._lock = threading.Lock()

    def take(self) -> tuple[int, int] | None:
        with self._lock:
            if self._next_start >= self._total:
                return None
            s = self._next_start
            e = min(s + DEFAULT_ELASTIC_BLOCK - 1, self._total - 1)
            self._next_start = e + 1
            return (s, e)


class DownloadManager:
    def __init__(
        self,
        chunk_downloader: ChunkDownloader,
        thread_count: int = 16,
        out_dir: Path | str = ".",
        speed_limit: int = 0,  # 字节/秒，0=不限
    ):
        self._dl = chunk_downloader
        self._thread_count = max(1, thread_count)
        self._out_dir = Path(out_dir)
        self._speed_limit = speed_limit
        self._out_dir.mkdir(parents=True, exist_ok=True)

    def download(
        self,
        url: str,
        filename: str,
        headers: dict | None = None,
        known_size: int = -1,
        on_progress: Callable[[DownloadProgress], None] | None = None,
    ) -> Path:
        headers = headers or {}
        safe_name = filename if filename.strip() else self._derive_name(url)
        work_dir = self._out_dir / (safe_name + ".partdir")
        work_dir.mkdir(parents=True, exist_ok=True)

        total = self._dl.get_total_size(url, headers)
        if total is None:
            total = known_size if known_size > 0 else -1

        if total <= 0:
            return self._stream_download(url, safe_name, headers, work_dir, on_progress)

        progress = DownloadProgress(filename=safe_name, total=total)
        chunk_count = self._chunk_count_for(total, self._thread_count)
        chunk_size = math.ceil(total / chunk_count)
        main_pool_count = max(1, min(int(chunk_count * 0.7), chunk_count))
        elastic_start = main_pool_count * chunk_size

        plan = f"chunks={chunk_count} total={total} main={main_pool_count}"
        plan_file = work_dir / "plan.txt"
        if plan_file.exists() and plan_file.read_text() != plan:
            self._clear_dir(work_dir)
        plan_file.write_text(plan)

        return self._run_plan(
            url, safe_name, headers, work_dir, total, chunk_count, chunk_size,
            main_pool_count, elastic_start, progress, on_progress,
        )

    # ---------- 主计划 ----------

    def _run_plan(self, url, safe_name, headers, work_dir, total, chunk_count, chunk_size,
                  main_pool_count, elastic_start, progress, on_progress) -> Path:
        dl = self._dl

        # 断点续传：已下载量 = 磁盘已有分片之和（钳制到 total）
        init = self._existing_bytes(work_dir, main_pool_count)
        init = min(init, total)
        progress.downloaded = init

        downloaded = [init]  # 线程安全的累计下载量
        lock = threading.Lock()
        next_idx = [0]
        elastic_results: dict[str, ChunkResult] = {}
        range_ignored_count = [0]
        fallback = [False]
        main_failed = [False]

        last_report_ts = [time.time()]
        last_report_bytes = [init]

        def on_bytes(n: int):
            with lock:
                downloaded[0] += n
                cur = min(downloaded[0], total)
            now = time.time()
            if now - last_report_ts[0] >= 1.0:
                dt = now - last_report_ts[0]
                progress.speed = (cur - last_report_bytes[0]) / dt if dt > 0 else 0.0
                last_report_ts[0] = now
                last_report_bytes[0] = cur
                progress.downloaded = cur
                if on_progress:
                    on_progress(progress)

        def worker():
            # 阶段 1：主池等分片
            while not fallback[0]:
                with lock:
                    i = next_idx[0]
                    next_idx[0] += 1
                if i >= main_pool_count:
                    break
                if i > 0:
                    time.sleep(min(i, STAGGER_CAP) * STAGGER_MS / 1000.0)
                start = i * chunk_size
                end = min(start + chunk_size - 1, total - 1)
                res = dl.download_chunk(
                    url, start, end, str(work_dir / f"part_{i}"), headers, on_bytes
                )
                if res == ChunkResult.RANGE_IGNORED:
                    range_ignored_count[0] += 1
                    if range_ignored_count[0] >= RANGE_IGNORED_TOLERANCE:
                        fallback[0] = True
                elif res == ChunkResult.FAILED:
                    main_failed[0] = True
                # OK 不处理

        # 弹性区与主池独立分配器共享实例
        allocator = _ElasticAllocator(total, elastic_start)

        def elastic_worker():
            while not fallback[0] and not main_failed[0]:
                rng = allocator.take()
                if rng is None:
                    break
                s, e = rng
                key = f"{s}_{e}"
                res = dl.download_chunk(
                    url, s, e, str(work_dir / f"seg_{key}.part"), headers, on_bytes
                )
                elastic_results[key] = res
                if res == ChunkResult.RANGE_IGNORED:
                    range_ignored_count[0] += 1
                    if range_ignored_count[0] >= RANGE_IGNORED_TOLERANCE:
                        fallback[0] = True
                elif res == ChunkResult.FAILED:
                    main_failed[0] = True

        # 启动 worker + elastic_worker 并发
        def combined_worker():
            worker()
            if not fallback[0] and not main_failed[0]:
                # 主池完成后该线程转入弹性区
                elastic_worker()

        threads = [
            threading.Thread(target=combined_worker, daemon=True)
            for _ in range(self._thread_count)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 结局判断
        if fallback[0] or main_failed[0]:
            logger.warning("检测到服务器忽略 Range 或分片失败，回退单流")
            return fill(progress, self._single_stream(
                url, safe_name, headers, total, work_dir, on_progress), total, on_progress)

        # 缺失区间重试
        missing = self._collect_missing(work_dir, main_pool_count, chunk_size, total, elastic_results)
        if missing:
            self._retry_missing(url, headers, missing)

        # 完整性校验 + 合并
        merged = work_dir / f"{safe_name}.merged"
        self._merge(work_dir, main_pool_count, merged)
        if total > 0 and merged.stat().st_size != total:
            raise RuntimeError(f"文件大小校验失败：期望 {total}，实际 {merged.stat().st_size}")

        final = self._out_dir / safe_name
        merged.replace(final)
        self._clear_dir(work_dir)
        return finish(progress, final, total, on_progress)

    # ---------- 备用路径 ----------

    def _stream_download(self, url, safe_name, headers, work_dir, on_progress) -> Path:
        progress = DownloadProgress(filename=safe_name, total=0)
        part = work_dir / "part_0"
        res = self._dl.download_stream(url, str(part), headers, progress_cb(progress, on_progress))
        if res != ChunkResult.OK:
            with open(part, "wb"):
                pass
            ok = self._dl.download_full(url, str(part), headers, on_bytes=progress_cb(progress, on_progress))
            if not ok:
                raise RuntimeError("下载失败（Range 与完整下载均失败）")
        final = self._out_dir / safe_name
        part.replace(final)
        self._clear_dir(work_dir)
        return finish(progress, final, progress.downloaded, on_progress)

    def _single_stream(self, url, safe_name, headers, total, work_dir, on_progress) -> Path:
        progress = DownloadProgress(filename=safe_name, total=total)
        full = work_dir / "full_single.bin"
        if full.exists():
            full.unlink()
        ok = self._dl.download_full(url, str(full), headers, total=total, on_bytes=progress_cb(progress, on_progress))
        if not ok:
            raise RuntimeError("单流下载失败")
        final = self._out_dir / safe_name
        full.replace(final)
        self._clear_dir(work_dir)
        return finish(progress, final, total, on_progress)

    # ---------- 辅助 ----------

    @staticmethod
    def _chunk_count_for(total, thread_count):
        # 对齐 Kotlin: chunkCountFor(total, threadCount)
        return max(1, min(thread_count, 512))

    @staticmethod
    def _derive_name(url: str) -> str:
        name = url.rstrip("/").split("/")[-1].split("?")[0]
        return name or f"download_{int(time.time())}"

    def _existing_bytes(self, work_dir, main_pool_count):
        total = 0
        for i in range(main_pool_count):
            p = work_dir / f"part_{i}"
            total += p.stat().st_size if p.exists() else 0
        for f in work_dir.glob("seg_*.part"):
            total += f.stat().st_size
        return total

    def _collect_missing(self, work_dir, main_pool_count, chunk_size, total, elastic_results):
        missing = []
        for i in range(main_pool_count):
            f = work_dir / f"part_{i}"
            s = i * chunk_size
            e = min(s + chunk_size - 1, total - 1)
            if not f.exists() or f.stat().st_size < (e - s + 1):
                missing.append((s, e, f))
        for key, res in elastic_results.items():
            if res != ChunkResult.OK:
                s = int(key.split("_")[0])
                e = int(key.split("_")[1])
                missing.append((s, e, work_dir / f"seg_{key}.part"))
        return missing

    def _retry_missing(self, url, headers, missing):
        if not missing:
            return
        dl = self._dl
        lock = threading.Lock()
        idx = [0]

        def worker():
            while True:
                with lock:
                    pos = idx[0]
                    idx[0] += 1
                if pos >= len(missing):
                    break
                s, e, f = missing[pos]
                dl.download_chunk(url, s, e, str(f), headers, lambda n: None)

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(min(8, len(missing)))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 重试后完整性
        for _s, _e, f in missing:
            if not f.exists() or f.stat().st_size < (_e - _s + 1):
                raise RuntimeError(f"区间 {_s}-{_e} 重试仍失败")

    def _merge(self, work_dir, main_pool_count, target):
        files = [work_dir / f"part_{i}" for i in range(main_pool_count)]
        files += sorted(work_dir.glob("seg_*.part"), key=lambda p: int(p.name[4:].split("_")[0]))
        with open(target, "wb") as out:
            for p in files:
                if not p.exists() or p.stat().st_size <= 0:
                    raise RuntimeError(f"分片缺失/为空 {p}")
                with open(p, "rb") as f:
                    while True:
                        data = f.read(1 << 20)
                        if not data:
                            break
                        out.write(data)

    @staticmethod
    def _clear_dir(dir_path: Path):
        for p in dir_path.iterdir():
            if p.is_file():
                p.unlink(missing_ok=True)


def progress_cb(progress: DownloadProgress, on_progress: Callable[[DownloadProgress], None] | None):
    def cb(n: int):
        progress.downloaded += n
        if on_progress:
            on_progress(progress)
    return cb


def finish(progress: DownloadProgress, final_path: Path, total: int,
           on_progress: Callable[[DownloadProgress], None] | None) -> Path:
    progress.done = True
    progress.downloaded = total
    if on_progress:
        on_progress(progress)
    logger.info(f"下载完成: {final_path}")
    return final_path


def fill(progress: DownloadProgress, path: Path, total: int,
         on_progress: Callable[[DownloadProgress], None] | None) -> Path:
    progress.done = True
    progress.downloaded = total
    if on_progress:
        on_progress(progress)
    return path
