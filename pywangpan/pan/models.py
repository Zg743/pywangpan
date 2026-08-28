"""共享数据模型（对应 YunX ShareModels.kt）。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ShareSession:
    share_id: str
    stoken: str
    title: str


@dataclass
class ShareToken:
    stoken: str
    title: str
    first_fid: str


@dataclass
class ShareFile:
    fid: str
    fname: str
    fsize: int
    isdir: bool
    pdir_fid: str
    fid_token: str
    modify_time: str = ""


@dataclass
class DownloadLink:
    fid: str
    filename: str
    download_url: str
    size: int
    cleanup_dir_fid: str | None = None
    is_hls: bool = False
