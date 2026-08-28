"""分片下载器（对应 YunX ChunkDownloader.kt）。

- Range 分片 + 断点续传；
- 服务器忽略 Range（返回 200 整文件）时返回 RANGE_IGNORED，由上层回退单流（绝不为单分片下整文件）；
- 写入后严格校验「已写字节 == 预期字节」，杜绝空洞文件损坏。
"""
from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Callable

import requests

logger = logging.getLogger("pywangpan.download")

CHUNK_RETRIES = 3
BUFFER_SIZE = 256 * 1024


class ChunkResult(str, Enum):
    OK = "ok"
    RANGE_IGNORED = "range_ignored"
    FAILED = "failed"


def _content_type_is_html(resp: requests.Response) -> bool:
    ct = resp.headers.get("Content-Type", "").lower()
    return "text/html" in ct


class ChunkDownloader:
    def __init__(self, session: requests.Session | None = None, timeout: int = 60):
        self._session = session or requests.Session()
        self._timeout = timeout

    def get_total_size(self, url: str, headers: dict) -> int | None:
        size = self._probe_size(url, headers, with_range=True)
        if size is not None:
            return size
        return self._probe_size(url, headers, with_range=False)

    def _probe_size(self, url: str, headers: dict, with_range: bool) -> int | None:
        req_headers = dict(headers)
        if with_range:
            req_headers["Range"] = "bytes=0-0"
        try:
            resp = self._session.get(url, headers=req_headers, timeout=self._timeout)
            resp.close()
        except requests.RequestException:
            return None
        if _content_type_is_html(resp):
            return None
        if with_range:
            if resp.status_code != 206:
                return None
            content_range = resp.headers.get("Content-Range", "")
            total = self._parse_content_range_total(content_range)
            if total is None:
                return None
            return total
        else:
            length = resp.headers.get("Content-Length")
            try:
                v = int(length)
                return v if v > 0 else None
            except (TypeError, ValueError):
                return None

    @staticmethod
    def _parse_content_range_total(content_range: str) -> int | None:
        # 形如 "bytes 0-0/12345" 或 "bytes */12345"
        try:
            total = content_range.split("/")[-1].strip()
            if total == "*":
                return None
            return int(total)
        except (IndexError, ValueError):
            return None

    def download_chunk(
        self,
        url: str,
        start: int,
        end: int,
        part_file: str,
        headers: dict,
        on_bytes: Callable[[int], None],
    ) -> ChunkResult:
        existing = os.path.getsize(part_file) if os.path.exists(part_file) else 0
        from_ = start + existing
        expected = end - start + 1
        if existing >= expected:
            return ChunkResult.OK
        for attempt in range(CHUNK_RETRIES):
            res = self._do_chunk_attempt(
                url, from_, end, part_file, headers, existing, expected, on_bytes
            )
            if res != ChunkResult.RANGE_IGNORED:
                return res
            # RANGE_IGNORED 立即返回，由上层决定回退；此处为保持接口语义透传
            return ChunkResult.RANGE_IGNORED
        return ChunkResult.FAILED

    def _do_chunk_attempt(
        self,
        url: str,
        from_: int,
        end: int,
        part_file: str,
        headers: dict,
        existing: int,
        expected: int,
        on_bytes: Callable[[int], None],
    ) -> ChunkResult:
        req_headers = dict(headers)
        req_headers["Range"] = f"bytes={from_}-{end}"
        try:
            resp = self._session.get(url, headers=req_headers, stream=True, timeout=self._timeout)
        except requests.RequestException:
            return ChunkResult.FAILED
        with resp:
            if _content_type_is_html(resp):
                return ChunkResult.FAILED
            if resp.status_code == 206:
                written = self._write_slice(
                    resp, part_file, existing, expected, on_bytes
                )
                if written != expected:
                    return ChunkResult.FAILED
                return ChunkResult.OK
            elif resp.status_code == 200:
                return ChunkResult.RANGE_IGNORED
            else:
                return ChunkResult.FAILED

    @staticmethod
    def _write_slice(resp: requests.Response, part_file: str, existing: int,
                     expected: int, on_bytes: Callable[[int], None]) -> int:
        written = 0
        # 追加写入
        mode = "r+b" if os.path.exists(part_file) else "wb"
        with open(part_file, mode) as f:
            if mode == "r+b":
                f.seek(existing)
            for chunk in resp.iter_content(chunk_size=BUFFER_SIZE):
                if not chunk:
                    break
                allow = min(len(chunk), expected - written)
                if allow <= 0:
                    break
                f.write(chunk[:allow])
                written += allow
                on_bytes(allow)
        return written

    def download_full(
        self,
        url: str,
        part_file: str,
        headers: dict,
        total: int = -1,
        on_bytes: Callable[[int], None] | None = None,
    ) -> bool:
        with open(part_file, "wb") as f:
            on_bytes = on_bytes or (lambda n: None)
            try:
                resp = self._session.get(url, headers=headers, stream=True, timeout=self._timeout)
            except requests.RequestException:
                return False
            with resp:
                if _content_type_is_html(resp):
                    return False
                if not resp.ok:
                    return False
                expected = total if total > 0 else -1
                written = 0
                for chunk in resp.iter_content(chunk_size=BUFFER_SIZE):
                    if not chunk:
                        break
                    allow = len(chunk) if expected < 0 else min(len(chunk), expected - written)
                    if allow <= 0:
                        break
                    f.write(chunk[:allow])
                    written += allow
                    on_bytes(allow)
                if total > 0 and written < total:
                    return False
                return True

    def download_stream(
        self,
        url: str,
        part_file: str,
        headers: dict,
        on_bytes: Callable[[int], None],
    ) -> ChunkResult:
        """开放区间（bytes=from-）流式下载，读到 EOF。"""
        existing = os.path.getsize(part_file) if os.path.exists(part_file) else 0
        req_headers = dict(headers)
        req_headers["Range"] = f"bytes={existing}-"
        try:
            resp = self._session.get(url, headers=req_headers, stream=True, timeout=self._timeout)
        except requests.RequestException:
            return ChunkResult.FAILED
        with resp:
            if _content_type_is_html(resp) or not resp.ok:
                return ChunkResult.FAILED
            if resp.status_code in (206, 200):
                written = 0
                mode = "r+b" if os.path.exists(part_file) else "wb"
                with open(part_file, mode) as f:
                    if mode == "r+b":
                        f.seek(existing)
                    for chunk in resp.iter_content(chunk_size=BUFFER_SIZE):
                        if not chunk:
                            break
                        f.write(chunk)
                        written += len(chunk)
                        on_bytes(len(chunk))
                return ChunkResult.OK
            return ChunkResult.FAILED
