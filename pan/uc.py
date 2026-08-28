"""UC 网盘 API 封装（对应 YunX UCApi.kt / UCConstants.kt）。

与夸克共用 API 结构，仅域名 / pr / UA 不同；Cookie __puus/__pus 保鲜逻辑复用 cookie.py。
官方下载流程无需转存：直接用 分享fid + fid_token + stoken + pwd_id 调 download 接口取直链。
"""
from __future__ import annotations

import json
import time
from urllib.parse import quote, urlencode

import requests

from . import cookie as cookie_util
from .models import DownloadLink, ShareFile, ShareToken


class UCApiError(Exception):
    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.message = message
        self.code = code


class UCConstants:
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    CLOUD_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) uc-cloud-drive/1.6.1 Chrome/100.0.4896.160 "
        "Electron/18.3.5.16-b62cf9c50d Safari/537.36 Channel/ucpan_other_ch"
    )
    WEB_ORIGIN = "https://drive.uc.cn"
    DOWNLOAD_REFERER = "https://drive.uc.cn/"

    API_BASE = "https://pc-api.uc.cn"
    ACCOUNT_INFO_URL = "https://drive.uc.cn/account/info"
    SHARE_TOKEN_URL = f"{API_BASE}/1/clouddrive/share/sharepage/token?pr=UCBrowser&fr=pc"
    SHARE_DETAIL_URL = f"{API_BASE}/1/clouddrive/share/sharepage/v2/detail?pr=UCBrowser&fr=pc"
    TRANSFER_SHARE_DETAIL_URL = (
        f"{API_BASE}/1/clouddrive/transfer_share/detail?entry=ft&fr=pc&pr=UCBrowser"
    )
    DOWNLOAD_URL = f"{API_BASE}/1/clouddrive/file/download?entry=ft&fr=pc&pr=UCBrowser"
    CLOUD_DOWNLOAD_URL = f"{API_BASE}/1/clouddrive/file/download"
    VIDEO_PREVIEW_URL = f"{API_BASE}/1/clouddrive/share/sharepage/video_preview"
    DEFAULT_PDIR_FID = "0"
    FILE_URL = f"{API_BASE}/1/clouddrive/file?pr=UCBrowser&fr=pc"
    SAVE_URL = f"{API_BASE}/1/clouddrive/share/sharepage/save?pr=UCBrowser&fr=pc"
    TASK_URL = f"{API_BASE}/1/clouddrive/task?pr=UCBrowser&fr=pc"
    CONFIG_URL = f"{API_BASE}/1/clouddrive/config?pr=UCBrowser&fr=pc"
    CLOUD_FILE_SORT_URL = f"{API_BASE}/1/clouddrive/file/sort?pr=UCBrowser&fr=pc"
    RENAME_URL = f"{API_BASE}/1/clouddrive/file/rename?pr=UCBrowser&fr=pc"
    MOVE_URL = f"{API_BASE}/1/clouddrive/file/move?pr=UCBrowser&fr=pc"
    DELETE_URL = f"{API_BASE}/1/clouddrive/file/delete?pr=UCBrowser&fr=pc"
    TEMP_DIR_NAME = "YunX临时转存"


class UCApi:
    def __init__(
        self,
        cookie: str,
        session: requests.Session | None = None,
        cookie_sink=None,
    ):
        self._cookie = cookie
        self._session = session or requests.Session()
        self.cookie_sink = cookie_sink

    @property
    def cookie(self) -> str:
        return self._cookie

    def _update_cookie(self, merged: str) -> None:
        if merged != self._cookie:
            self._cookie = merged
            if self.cookie_sink:
                self.cookie_sink(merged)

    @staticmethod
    def _set_cookies_from_response(response: requests.Response) -> list:
        try:
            return response.raw.getheaders("Set-Cookie") or []
        except Exception:
            value = response.headers.get("Set-Cookie")
            return [value] if value else []

    def _merge_from_response(self, response: requests.Response) -> None:
        set_cookies = self._set_cookies_from_response(response)
        if not set_cookies:
            return
        merged = cookie_util.merge_from_set_cookies(self._cookie, set_cookies)
        self._update_cookie(merged)

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {
            "Cookie": self._cookie,
            "User-Agent": UCConstants.USER_AGENT,
        }
        if extra:
            headers.update(extra)
        return headers

    def _request(self, method, url, body=None, headers_extra=None):
        headers = self._headers(headers_extra)
        if body is not None:
            headers["Content-Type"] = "application/json;charset=UTF-8"
        resp = self._session.request(
            method,
            url,
            headers=headers,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
            timeout=60,
        )
        self._merge_from_response(resp)
        return resp

    @staticmethod
    def _parse(resp: requests.Response) -> tuple[dict, dict]:
        try:
            data = resp.json()
        except ValueError as e:
            raise UCApiError(f"响应解析失败: {resp.text[:200]}") from e
        if data.get("status") != 200:
            raise UCApiError(data.get("message") or "请求失败")
        return data, data.get("data") or {}

    # ---------- 账号 ----------

    def fetch_nickname(self) -> str | None:
        try:
            resp = self._session.get(
                UCConstants.ACCOUNT_INFO_URL,
                headers={"Cookie": self._cookie, "User-Agent": UCConstants.USER_AGENT},
                timeout=60,
            )
            data = resp.json()
            if data.get("success"):
                nick = (data.get("data") or {}).get("nickname") or ""
                return nick if nick else None
        except (ValueError, requests.RequestException):
            pass
        return None

    # ---------- 分享解析 ----------

    def get_share_token(self, share_id: str, pwd: str | None) -> ShareToken:
        body = {
            "pwd_id": share_id,
            "passcode": pwd or "",
            "share_for_transfer": True,
        }
        resp = self._request("POST", UCConstants.SHARE_TOKEN_URL, body)
        _, d = self._parse(resp)
        return ShareToken(
            stoken=d.get("stoken") or "",
            title=d.get("title") or "",
            first_fid=d.get("first_fid") or "",
        )

    def get_transfer_share_files(
        self, share_id: str, stoken: str, pdir_fid: str, page: int = 1, size: int = 50
    ) -> list[ShareFile]:
        params = {
            "pwd_id": share_id,
            "pdir_fid": pdir_fid,
            "fetch_file_list": 1,
            "passcode": "",
            "_page": page,
            "_size": size,
            "_fetch_total": 1,
            "_fetch_task": 1,
            "_fetch_share": 1,
            "_sort": "",
            "stoken": stoken,
        }
        url = f"{UCConstants.TRANSFER_SHARE_DETAIL_URL}&{urlencode(params)}"
        resp = self._session.get(
            url,
            headers=self._headers({
                "Origin": "https://fast.uc.cn",
                "Referer": "https://fast.uc.cn/",
            }),
            timeout=60,
        )
        self._merge_from_response(resp)
        _, d = self._parse(resp)
        array = d.get("list") or (d.get("detail_info") or {}).get("list") or []
        return self._build_files(array)

    # ---------- 个人网盘 / 转存 ----------

    def get_file_list(self, pdir_fid: str, page: int = 1, size: int = 100) -> list[ShareFile]:
        url = f"{UCConstants.FILE_URL}&pdir_fid={pdir_fid}&page={page}&size={size}"
        resp = self._request("GET", url)
        _, d = self._parse(resp)
        array = d.get("list") or []
        files = []
        for item in array:
            fname = item.get("file_name") or item.get("fname") or ""
            isdir = bool(item.get("dir")) or int(item.get("isdir") or 0) == 1
            size_ = int(item.get("size") or item.get("fsize") or 0)
            files.append(ShareFile(
                fid=item.get("fid") or "",
                fname=fname,
                fsize=size_,
                isdir=isdir,
                pdir_fid=item.get("pdir_fid") or "",
                fid_token=item.get("fid_token") or "",
                modify_time=item.get("modify_time") or "",
            ))
        return files

    def create_folder(self, name: str, parent_fid: str) -> str | None:
        body = {
            "pdir_fid": parent_fid,
            "file_name": name,
            "dir_path": "",
            "dir_init_lock": False,
        }
        resp = self._request("POST", UCConstants.FILE_URL, body)
        _, d = self._parse(resp)
        return d.get("fid") or None

    def save_share_file(
        self, share_id: str, stoken: str, pdir_fid: str, fid: str,
        fid_token: str, to_pdir_fid: str,
    ) -> str | None:
        body = {
            "pwd_id": share_id,
            "stoken": stoken,
            "pdir_fid": pdir_fid,
            "to_pdir_fid": to_pdir_fid,
            "fid_list": [fid],
            "fid_token_list": [fid_token],
            "scene": "link",
        }
        resp = self._request("POST", UCConstants.SAVE_URL, body)
        _, d = self._parse(resp)
        task_id = d.get("task_id") or ""
        return task_id or None

    def poll_task(self, task_id: str, max_attempts: int = 10, interval: float = 1.0) -> str | None:
        for _ in range(max_attempts):
            url = f"{UCConstants.TASK_URL}&task_id={quote(task_id)}&retry_index=0"
            try:
                resp = self._request("GET", url)
                _, d = self._parse(resp)
            except UCApiError:
                time.sleep(interval)
                continue
            finished = d.get("finished_at", 0) > 0 or d.get("status") == 2 or d.get("task_status") == 2
            if finished:
                fids = (d.get("save_as") or {}).get("save_as_top_fids") or []
                if fids:
                    return fids[0]
            time.sleep(interval)
        return None

    def delete_file(self, fid: str) -> str | None:
        body = {"action_type": 2, "filelist": [fid], "exclude_fids": []}
        resp = self._request("POST", UCConstants.DELETE_URL, body)
        _, d = self._parse(resp)
        return d.get("task_id") or None

    # ---------- 下载直链 ----------

    def get_share_download_link(
        self, fid: str, fid_token: str, stoken: str, pwd_id: str
    ) -> DownloadLink:
        body = {
            "fids": [fid],
            "pwd_id": pwd_id,
            "stoken": stoken,
            "fids_token": [fid_token],
        }
        resp = self._request("POST", UCConstants.DOWNLOAD_URL, body)
        data = self._parse_download(resp)
        item = (data.get("data") or [None])[0] or {}
        if not item:
            raise UCApiError("未返回下载链接")
        return DownloadLink(
            fid=item.get("fid") or "",
            filename=item.get("file_name") or item.get("filename") or "",
            download_url=item.get("download_url") or "",
            size=int(item.get("size") or 0),
        )

    def _parse_download(self, resp: requests.Response) -> dict:
        try:
            data = resp.json()
        except ValueError as e:
            raise UCApiError(f"响应解析失败: {resp.text[:200]}") from e
        status = data.get("status")
        code = data.get("code")
        if status != 200 and code not in (0, None):
            raise UCApiError(data.get("message") or "获取下载链接失败", code)
        return data

    def get_download_link(self, fid: str) -> DownloadLink:
        body = {"fids": [fid]}
        resp = self._request("POST", UCConstants.DOWNLOAD_URL, body)
        data = self._parse_download(resp)
        array = data.get("data") or []
        if not array:
            raise UCApiError("未返回下载链接")
        item = array[0]
        return DownloadLink(
            fid=item.get("fid") or "",
            filename=item.get("file_name") or item.get("filename") or "",
            download_url=item.get("download_url") or "",
            size=int(item.get("size") or 0),
        )

    def get_video_preview(self, pwd_id: str, stoken: str, fid: str, fid_token: str) -> DownloadLink | None:
        url = (
            f"{UCConstants.VIDEO_PREVIEW_URL}?pr=UCBrowser&fr=h5"
            f"&pwd_id={quote(pwd_id, safe='')}"
            f"&stoken={quote(stoken, safe='')}"
            f"&fid={quote(fid, safe='')}"
            f"&fid_token={quote(fid_token, safe='')}"
        )
        resp = self._session.get(
            url,
            headers=self._headers({
                "Origin": UCConstants.WEB_ORIGIN,
                "Referer": UCConstants.DOWNLOAD_REFERER,
                "Content-Type": "application/json",
            }),
            timeout=60,
        )
        try:
            data = resp.json()
        except ValueError:
            return None
        if data.get("status") != 200 and data.get("code") not in (0, None):
            return None
        play_info = (data.get("data") or {}).get("play_info") or {}
        direct_url = play_info.get("url") or ""
        if not direct_url:
            return None
        return DownloadLink(
            fid=fid,
            filename="",
            download_url=direct_url,
            size=int(play_info.get("size") or 0),
        )

    def refresh_session(self) -> str | None:
        headers = self._headers({"Referer": UCConstants.DOWNLOAD_REFERER})
        headers["Cookie"] = cookie_util.without_puus(self._cookie)
        resp = self._session.get(UCConstants.CONFIG_URL, headers=headers, timeout=60)
        merged = cookie_util.merge_from_set_cookies(self._cookie, self._set_cookies_from_response(resp))
        self._update_cookie(merged)
        return merged if merged != self._cookie else None

    # ---------- 工具 ----------

    @staticmethod
    def _build_files(array) -> list[ShareFile]:
        files = []
        for item in array:
            fname = item.get("file_name") or ""
            if not fname:
                fname = item.get("fname") or ""
            files.append(ShareFile(
                fid=item.get("fid") or "",
                fname=fname,
                fsize=int(item.get("size") or 0),
                isdir=bool(item.get("dir")),
                pdir_fid=item.get("pdir_fid") or "",
                fid_token=item.get("share_fid_token") or item.get("fid_token") or "",
                modify_time=item.get("updated_at") or "",
            ))
        return files
