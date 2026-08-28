"""123 云盘解析流程编排（对应 YunX Pan123ResolveRepository.kt）。

流程：GET /b/api/share/get（匿名）校验提取码 + 列表 → POST /b/api/share/download/info（需 token+签名）
取直链 → 解码 DownloadURL。123 分享下载无需转存。
"""
from __future__ import annotations

from .models import DownloadLink, ShareFile, ShareSession
from .pan123 import Pan123Api


class Pan123Resolver:
    def __init__(self, api: Pan123Api, token: str):
        self.api = api
        self._token = token

    def _effective_token(self, provided: str) -> str:
        return provided or self._token

    def create_session(self, share_id: str, pwd: str | None) -> ShareSession:
        share_pwd = pwd or ""
        files, _ = self.api.get_share_files(share_id, share_pwd, "0", "0", 1)
        title = files[0].fname if files and files[0].fname else share_id
        return ShareSession(share_id=share_id, stoken=share_pwd, title=title)

    def list_files(self, session: ShareSession, dir_fid: str) -> list[ShareFile]:
        all_files: list[ShareFile] = []
        page = 1
        while True:
            files, _ = self.api.get_share_files(
                session.share_id, session.stoken, dir_fid, "0", page
            )
            all_files.extend(files)
            if not files or page >= 50:
                break
            page += 1
        return all_files

    def get_share_download_link(self, session: ShareSession, file: ShareFile, token: str = "") -> DownloadLink:
        token = self._effective_token(token)
        if not token:
            raise RuntimeError("请先登录123云盘")
        link = self.api.get_share_download_link(session.share_id, file, token)
        if not link:
            raise RuntimeError("获取下载链接失败")
        return DownloadLink(
            fid=link.fid,
            filename=file.fname if file.fname else link.filename,
            download_url=link.download_url,
            size=link.size,
        )
