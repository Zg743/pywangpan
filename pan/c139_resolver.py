"""139（和彩云）解析流程编排（对应 YunX C139ResolveRepository.kt）。

流程：getOutLinkGeneral 取标题/明文提取码 → getOutLinkInfoV6 列目录（匿名、加密）→
dlFromOutLinkV3 取 redrUrl 直链。139 分享无需转存。
"""
from __future__ import annotations

import time

from .c139 import C139Api, C139Constants
from .models import DownloadLink, ShareFile, ShareSession


class C139Resolver:
    def __init__(self, api: C139Api, cookie: str):
        self.api = api
        self.cookie = cookie

    def _account(self) -> str:
        account = C139Constants.extract_account_full(self.cookie)
        if not account:
            raise RuntimeError("登录态缺少账号信息，请重新登录")
        return account

    def create_session(self, share_id: str, pwd: str | None) -> ShareSession:
        # 139 分享无 token：shareId 即 linkID，stoken 暂存提取码
        leaked_pwd = self.api.get_out_link_password(share_id)
        passwd = pwd if pwd else (leaked_pwd or "")
        title = self.api.get_out_link_title(share_id) or share_id
        return ShareSession(share_id=share_id, stoken=passwd, title=title)

    def list_files(self, session: ShareSession, dir_fid: str) -> list[ShareFile]:
        pca_id = "root" if not dir_fid or dir_fid == "0" else dir_fid
        all_files: list[ShareFile] = []
        begin = 1
        while True:
            batch = self.api.get_share_files(
                session.share_id, pca_id, session.stoken, begin, begin + 199
            )
            all_files.extend(batch)
            begin += 200
            if len(batch) < 200 or begin > 20_000:
                break
        return all_files

    def get_share_download_link(self, session: ShareSession, file: ShareFile) -> DownloadLink:
        account = self._account()
        authorization = C139Constants.extract_authorization(self.cookie)
        link = self.api.get_share_download_link(
            file.fid, session.share_id, account, authorization
        )
        if not link:
            raise RuntimeError("获取下载链接失败")
        return DownloadLink(
            fid=link.fid,
            filename=file.fname if file.fname else link.filename,
            download_url=link.download_url,
            size=link.size,
        )
