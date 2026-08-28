"""百度解析流程编排（对应 YunX BaiduResolveRepository.kt）。

流程：verify（可选，加密分享）→ xpan/share 列文件 → 转存临时目录 → locatedownload 取 appall 直链
→ 立即删除临时转存（直链自带签名，删除不影响下载）。
"""
from __future__ import annotations

from .baidu import BaiduApi, BaiduConstants
from .models import DownloadLink, ShareFile, ShareSession


class BaiduResolver:
    def __init__(self, api: BaiduApi, cookie: str, temp_dir_name: str = "YunX临时转存"):
        self.api = api
        self.cookie = cookie
        self.temp_dir_name = temp_dir_name
        self._sekeys: dict[str, str] = {}
        self._share_infos: dict[str, tuple[str, str]] = {}

    def create_session(self, share_id: str, pwd: str | None) -> ShareSession:
        """share_id 即 surl（parser 已去除 '1' 前缀）。"""
        effective_pwd = pwd if pwd else ""
        sekey = ""
        if effective_pwd:
            sekey = self.api.verify_share(share_id, effective_pwd, self.cookie)
        self._sekeys[share_id] = sekey
        return ShareSession(share_id=share_id, stoken=sekey, title="")

    def list_files(self, session: ShareSession, dir_fid: str) -> list[ShareFile]:
        sekey = session.stoken or self._sekeys.get(session.share_id, "")
        all_files: list[ShareFile] = []
        page = 1
        result = None
        while True:
            result = self.api.list_share(
                session.share_id, sekey, dir_fid, self.cookie, page=page
            )
            all_files.extend(result.files)
            if len(result.files) < 100 or page >= 100:
                break
            page += 1
        if result:
            self._share_infos[session.share_id] = (result.share_id, result.uk)
        return all_files

    def _require_share_info(self, session: ShareSession) -> tuple[str, str]:
        cached = self._share_infos.get(session.share_id)
        if cached:
            return cached
        sekey = session.stoken or self._sekeys.get(session.share_id, "")
        result = self.api.list_share(session.share_id, sekey, "/", self.cookie)
        info = (result.share_id, result.uk)
        self._share_infos[session.share_id] = info
        return info

    def ensure_temp_dir(self) -> str:
        dir_path = f"/{self.temp_dir_name}"
        exists = dir_path in self.api.list_dir("/", self.cookie)
        ok = exists or self.api.create_dir(dir_path, self.cookie)
        return dir_path if ok else "/"

    def get_share_download_link(self, session: ShareSession, file: ShareFile) -> DownloadLink:
        share_id, uk = self._require_share_info(session)
        dir_path = self.ensure_temp_dir()
        transferred = self.api.transfer(
            share_id, uk, session.stoken, file.fid, dir_path, self.cookie
        )
        dlink = self.api.locate_download(transferred.path, self.cookie)
        self._delete_transferred(transferred.path)
        return DownloadLink(
            fid=transferred.fs_id,
            filename=file.fname,
            download_url=dlink,
            size=file.fsize,
        )

    def _delete_transferred(self, path: str) -> None:
        try:
            self.api.delete_file(path, self.cookie)
        except Exception:
            pass
        temp_dir = f"/{self.temp_dir_name}"
        if path.startswith(f"{temp_dir}/"):
            try:
                self.api.delete_file(temp_dir, self.cookie)
            except Exception:
                pass
