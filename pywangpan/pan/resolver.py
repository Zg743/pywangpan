"""夸克解析流程编排（对应 YunX QuarkResolveRepository.kt）。

流程：token → 列表 → 转存到唯一临时子目录 → 取下载直链 →（下载完成后清理临时转存）。
"""
from __future__ import annotations

import random
import time

from .models import DownloadLink, ShareSession, ShareFile
from .quark import QuarkApi


class QuarkResolver:
    def __init__(self, api: QuarkApi, temp_dir_name: str = "YunX临时转存"):
        self.api = api
        self.temp_dir_name = temp_dir_name

    def create_session(self, share_id: str, pwd: str | None) -> ShareSession:
        token = self.api.get_share_token(share_id, pwd)
        return ShareSession(share_id=share_id, stoken=token.stoken, title=token.title)

    def list_files(self, share_id: str, stoken: str, dir_fid: str) -> list[ShareFile]:
        all_files: list[ShareFile] = []
        page = 1
        while True:
            batch = self.api.get_share_files(share_id, stoken, dir_fid, page=page, size=100)
            all_files.extend(batch)
            if len(batch) < 100 or page >= 100:
                break
            page += 1
        return all_files

    def ensure_temp_dir(self) -> str:
        root = self.api.get_file_list("0")
        for f in root:
            if f.isdir and f.fname == self.temp_dir_name:
                return f.fid
        fid = self.api.create_folder(self.temp_dir_name, "0")
        if not fid:
            raise RuntimeError("创建临时目录失败")
        return fid

    def transfer_file_to(
        self,
        share_id: str,
        stoken: str,
        file: ShareFile,
        to_dir_fid: str,
    ) -> str:
        task_id = self.api.save_share_file(
            share_id=share_id,
            stoken=stoken,
            pdir_fid=file.pdir_fid,
            fid=file.fid,
            fid_token=file.fid_token,
            to_pdir_fid=to_dir_fid,
        )
        if not task_id:
            raise RuntimeError("转存失败")
        new_fid = self.api.poll_task(task_id)
        if not new_fid:
            raise RuntimeError("转存超时，请稍后重试")
        return new_fid

    def get_share_download_link(
        self,
        share_id: str,
        stoken: str,
        file: ShareFile,
    ) -> DownloadLink:
        base_dir = self.ensure_temp_dir()
        sub_dir_name = f"tr_{int(time.time() * 1_000_000)}_{random.randint(0, 1_000_000)}"
        sub_dir_fid = self.api.create_folder(sub_dir_name, base_dir)
        if not sub_dir_fid:
            raise RuntimeError("创建临时转存目录失败")
        saved_fid = self.transfer_file_to(share_id, stoken, file, sub_dir_fid)
        link = self.api.get_download_link(saved_fid)
        return DownloadLink(
            fid=link.fid,
            filename=link.filename,
            download_url=link.download_url,
            size=link.size,
            cleanup_dir_fid=sub_dir_fid,
        )

    def cleanup_temp_dir(self, dir_fid: str) -> None:
        """下载完成后清理临时转存子目录；失败不阻断。"""
        try:
            self.api.delete_file(dir_fid)
        except Exception:
            pass
