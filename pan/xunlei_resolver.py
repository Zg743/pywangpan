"""迅雷解析流程编排（对应 YunX XunleiResolveRepository.kt）。

流程：getShare（带提取码）→ 列表 → 转存临时目录 → 文件详情取直链 → 删除临时转存（直链签名有效）。
"""
from __future__ import annotations

import time

from .models import DownloadLink, ShareFile, ShareSession
from .xunlei import XunleiApi, XunleiConstants, XunleiApiError


class XunleiResolver:
    def __init__(
        self,
        api: XunleiApi,
        access_token: str,
        device_id: str,
        captcha_token: str = "",
        refresh_provider=None,
        temp_dir_name: str = "YunX临时转存",
    ):
        self.api = api
        self._access_token = access_token
        self.device_id = device_id
        self._captcha_token = captcha_token
        self.temp_dir_name = temp_dir_name
        self._pass_codes: dict[str, str] = {}
        # refresh_provider() -> (new_access, new_refresh) | None
        self.refresh_provider = refresh_provider
        if refresh_provider is not None:
            self.api.refresh_token_provider = lambda did: refresh_provider()

    def _ensure_token(self) -> str:
        exp = self.api.jwt_exp(self._access_token)
        if exp > 0 and exp - int(time.time()) > 60:
            return self._access_token
        if self.refresh_provider:
            refreshed = self.refresh_provider()
            if refreshed:
                self._access_token = refreshed[0]
                return self._access_token
        raise XunleiApiError("迅雷登录已过期，请重新登录")

    @property
    def _captcha(self) -> str:
        return self._captcha_token or ""

    def create_session(self, share_id: str, pwd: str | None) -> ShareSession:
        effective_pwd = pwd or ""
        self._pass_codes[share_id] = effective_pwd
        access = self._ensure_token()
        result = self.api.get_share(
            share_id, effective_pwd, access, self.device_id, self._captcha
        )
        if not result:
            raise XunleiApiError("未获取到分享信息")
        return ShareSession(share_id=share_id, stoken=result.pass_code_token, title=result.title)

    def list_files(self, session: ShareSession, dir_fid: str) -> list[ShareFile]:
        access = self._ensure_token()
        files: list[ShareFile] = []
        page_token = ""
        pages = 0
        while True:
            if not dir_fid or dir_fid == "0":
                page = self.api.get_share(
                    session.share_id,
                    self._pass_codes.get(session.share_id, ""),
                    access,
                    self.device_id,
                    self._captcha,
                    page_token,
                )
                if not page:
                    raise XunleiApiError("未获取到文件列表")
                files.extend(page.files)
                page_token = page.next_page_token
            else:
                page = self.api.get_share_detail(
                    session.share_id,
                    dir_fid,
                    session.stoken,
                    access,
                    self.device_id,
                    self._captcha,
                    page_token,
                )
                if not page:
                    raise XunleiApiError("未获取到文件列表")
                files.extend(page.files)
                page_token = page.next_page_token
            pages += 1
            if not page_token or pages >= 100:
                break
        return files

    def ensure_temp_dir(self) -> str:
        access = self._ensure_token()
        root = self.api.get_files("", access, self.device_id, self._captcha) or []
        for f in root:
            if f.isdir and f.fname == self.temp_dir_name:
                return f.fid
        fid = self.api.create_folder(
            self.temp_dir_name, "", access, self.device_id, self._captcha
        )
        if not fid:
            raise XunleiApiError("创建临时目录失败")
        return fid

    def _transfer_file(self, session: ShareSession, file: ShareFile, to_dir_fid: str) -> str:
        access = self._ensure_token()
        new_id = self.api.restore(
            share_id=session.share_id,
            pass_code_token=session.stoken,
            parent_folder_id=to_dir_fid,
            file_ids=[file.fid],
            access_token=access,
            device_id=self.device_id,
            captcha_token=self._captcha,
        )
        if not new_id:
            raise XunleiApiError("转存失败")
        return new_id

    def get_share_download_link(self, session: ShareSession, file: ShareFile) -> DownloadLink:
        dir_fid = self.ensure_temp_dir()
        saved_fid = self._transfer_file(session, file, dir_fid)
        access = self._ensure_token()
        link = self.api.get_file_detail(
            saved_fid, access, self.device_id, self._captcha
        )
        if not link:
            raise XunleiApiError("获取下载链接失败")
        try:
            self.api.batch_delete(
                [saved_fid], access, self.device_id, self._captcha
            )
        except Exception:
            pass
        return link
