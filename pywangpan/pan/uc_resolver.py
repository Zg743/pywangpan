"""UC 解析流程编排（对应 YunX UCResolveRepository.kt）。

流程：token → transfer_share/detail 列表 → 官方 download（无需转存）取直链。
视频优先用 video_preview 原画直链（绕过非会员宣传片替换）。
"""
from __future__ import annotations

import os

from .models import DownloadLink, ShareFile, ShareSession
from .uc import UCApi, UCConstants

_VIDEO_EXTS = {
    "mp4", "mkv", "mov", "avi", "webm", "flv", "ts", "m3u8", "wmv", "rmvb",
}


class UCResolver:
    def __init__(self, api: UCApi, temp_dir_name: str = "YunX临时转存"):
        self.api = api
        self.temp_dir_name = temp_dir_name

    @staticmethod
    def _is_video(name: str) -> bool:
        ext = os.path.splitext(name)[1].lstrip(".").lower()
        return ext in _VIDEO_EXTS

    def create_session(self, share_id: str, pwd: str | None) -> ShareSession:
        token = self.api.get_share_token(share_id, pwd)
        return ShareSession(share_id=share_id, stoken=token.stoken, title=token.title)

    def list_files(self, session: ShareSession, dir_fid: str) -> list[ShareFile]:
        all_files: list[ShareFile] = []
        page = 1
        while True:
            batch = self.api.get_transfer_share_files(
                session.share_id, session.stoken, dir_fid, page=page, size=50
            )
            all_files.extend(batch)
            if len(batch) < 50 or page >= 100:
                break
            page += 1
        return all_files

    def ensure_temp_dir(self) -> str:
        root = self.api.get_file_list(UCConstants.DEFAULT_PDIR_FID)
        for f in root:
            if f.isdir and f.fname == self.temp_dir_name:
                return f.fid
        fid = self.api.create_folder(self.temp_dir_name, UCConstants.DEFAULT_PDIR_FID)
        if not fid:
            raise RuntimeError("创建临时目录失败")
        return fid

    def transfer_file_to(self, session: ShareSession, file: ShareFile, to_dir_fid: str) -> str:
        task_id = self.api.save_share_file(
            share_id=session.share_id,
            stoken=session.stoken,
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

    def get_share_download_link(self, session: ShareSession, file: ShareFile) -> DownloadLink:
        """UC 官方下载无需转存，直接用分享 fid + stoken 取直链。"""
        if self._is_video(file.fname):
            preview = self.api.get_video_preview(
                pwd_id=session.share_id,
                stoken=session.stoken,
                fid=file.fid,
                fid_token=file.fid_token,
            )
            if preview is not None:
                return DownloadLink(
                    fid=file.fid,
                    filename=file.fname,
                    download_url=preview.download_url,
                    size=preview.size,
                )
        return self.api.get_share_download_link(
            fid=file.fid,
            fid_token=file.fid_token,
            stoken=session.stoken,
            pwd_id=session.share_id,
        )
