"""夸克网盘 API 封装（对应 YunX QuarkApi.kt / QuarkConstants.kt）。

协议细节转译自 Kotlin 源码，字段名以服务端抓包为准。
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable
from urllib.parse import urlencode

import requests

from . import cookie as cookie_util
from .models import DownloadLink, ShareFile, ShareToken


class QuarkApiError(Exception):
    """夸克 API 错误，携带服务端 message 与可选 code。"""

    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.message = message
        self.code = code


class QuarkConstants:
    API_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "quark-cloud-drive/2.5.20 Chrome/100.0.4896.160 Electron/18.3.5.12-a038f7b798 "
        "Safari/537.36 Channel/pckk_other_ch"
    )
    API_BASE = "https://drive-pc.quark.cn"
    SHARE_TOKEN_URL = f"{API_BASE}/1/clouddrive/share/sharepage/token?pr=ucpro&fr=pc"
    SHARE_PASSWORD_URL = f"{API_BASE}/1/clouddrive/share/password?pr=ucpro&fr=pc"
    SHARE_DETAIL_URL = f"{API_BASE}/1/clouddrive/share/sharepage/detail?pr=ucpro&fr=pc"
    DOWNLOAD_URL = f"{API_BASE}/1/clouddrive/file/download?pr=ucpro&fr=pc&sys=win32&ve=3.23.2"
    DEFAULT_PDIR_FID = "0"
    FILE_URL = f"{API_BASE}/1/clouddrive/file?pr=ucpro&fr=pc"
    SAVE_URL = f"{API_BASE}/1/clouddrive/share/sharepage/save?pr=ucpro&fr=pc"
    TASK_URL = f"{API_BASE}/1/clouddrive/task?pr=ucpro&fr=pc"
    DELETE_URL = f"{API_BASE}/1/clouddrive/file/delete?pr=ucpro&fr=pc&uc_param_str="
    CONFIG_URL = f"{API_BASE}/1/clouddrive/config?pr=ucpro&fr=pc"
    TEMP_DIR_NAME = "YunX临时转存"
    DOWNLOAD_REFERER = "https://pan.quark.cn/"


class QuarkApi:
    def __init__(
        self,
        cookie: str,
        session: requests.Session | None = None,
        cookie_sink: Callable[[str], None] | None = None,
    ):
        """cookie_sink：每次响应把合并后的最新 Cookie 回调（保持 __puus/__pus 新鲜）。"""
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

    def _merge_from_response(self, response: requests.Response) -> None:
        set_cookies = response.headers.get_list("Set-Cookie") or []
        if not set_cookies:
            return
        merged = cookie_util.merge_from_set_cookies(self._cookie, set_cookies)
        self._update_cookie(merged)

    def _headers(self) -> dict:
        return {
            "Cookie": self._cookie,
            "User-Agent": QuarkConstants.API_USER_AGENT,
        }

    def _get_json(self, url: str, extra_headers: dict | None = None) -> dict:
        headers = self._headers()
        if extra_headers:
            headers.update(extra_headers)
        resp = self._session.get(url, headers=headers, timeout=60)
        self._merge_from_response(resp)
        return self._parse(resp)

    def _post_json(self, url: str, body: dict, extra_headers: dict | None = None) -> dict:
        headers = self._headers()
        headers["Content-Type"] = "application/json"
        if extra_headers:
            headers.update(extra_headers)
        resp = self._session.post(
            url, headers=headers, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            timeout=60,
        )
        self._merge_from_response(resp)
        return self._parse(resp)

    @staticmethod
    def _parse(resp: requests.Response) -> dict:
        try:
            data = resp.json()
        except ValueError as e:
            raise QuarkApiError(f"响应解析失败: {resp.text[:200]}") from e
        status = data.get("status")
        if status != 200:
            raise QuarkApiError(data.get("message") or "请求失败", data.get("code") or 0)
        return data

    # ---------- 分享解析 ----------

    def get_share_token(self, share_id: str, pwd: str | None) -> ShareToken:
        body = {
            "pwd_id": share_id,
            "passcode": pwd or "",
            "support_visit_limit_private_share": True,
        }
        data = self._post_json(QuarkConstants.SHARE_TOKEN_URL, body)
        d = data.get("data") or {}
        return ShareToken(
            stoken=d.get("stoken") or "",
            title=d.get("title") or "",
            first_fid=d.get("first_fid") or "",
        )

    def verify_share_password(self, share_id: str, passcode: str) -> bool:
        body = {"share_id": share_id, "passcode": passcode}
        resp = self._session.post(
            QuarkConstants.SHARE_PASSWORD_URL,
            headers={**self._headers(), "Content-Type": "application/json"},
            data=json.dumps(body).encode("utf-8"),
            timeout=60,
        )
        return resp.status_code == 200 and (resp.json().get("status") == 200)

    def get_share_files(
        self,
        share_id: str,
        stoken: str,
        pdir_fid: str,
        page: int = 1,
        size: int = 100,
    ) -> list[ShareFile]:
        from urllib.parse import quote

        params = urlencode(
            {
                "pwd_id": share_id,
                "stoken": stoken,
                "pdir_fid": pdir_fid,
                "ver": 2,
                "force": 0,
                "_page": page,
                "_size": size,
                "_fetch_banner": 0,
                "_fetch_share": 0,
                "fetch_relate_conversation": 0,
                "_fetch_total": 1,
                "_sort": "file_type:asc,file_name:asc",
            }
        )
        url = f"{QuarkConstants.SHARE_DETAIL_URL}&{params}"
        # 该接口需携带 Origin/Referer，否则可能返回 400
        data = self._get_json(url, extra_headers={
            "Origin": "https://pan.quark.cn",
            "Referer": "https://pan.quark.cn/",
        })
        return self._parse_share_files(data, use_fid_token=True)

    def list_cloud_files(self, pdir_fid: str, page: int = 1, size: int = 50) -> list[ShareFile]:
        params = {
            "pdir_fid": pdir_fid,
            "_page": page,
            "_size": size,
            "_fetch_total": 1,
            "_fetch_sub_dirs": 0,
            "_sort": "file_type:asc,updated_at:desc",
            "fetch_all_file": 1,
            "fetch_risk_file_name": 1,
        }
        url = f"{QuarkConstants.API_BASE}/1/clouddrive/file/sort?pr=ucpro&fr=pc&{urlencode(params)}"
        data = self._get_json(url, extra_headers={
            "Origin": "https://pan.quark.cn",
            "Referer": "https://pan.quark.cn/",
        })
        return self._parse_share_files(data, use_fid_token=False)

    @staticmethod
    def _parse_share_files(data: dict, use_fid_token: bool) -> list[ShareFile]:
        array = (data.get("data") or {}).get("list") or []
        files = []
        for item in array:
            files.append(
                ShareFile(
                    fid=item.get("fid") or "",
                    fname=item.get("file_name") or item.get("fname") or "",
                    fsize=int(item.get("size") or item.get("fsize") or 0),
                    isdir=bool(item.get("dir")) or item.get("isdir") == 1,
                    pdir_fid=item.get("pdir_fid") or "",
                    fid_token=item.get("share_fid_token") or (
                        item.get("fid_token") if use_fid_token else ""
                    ) or "",
                    modify_time=item.get("updated_at") or item.get("modify_time") or "",
                )
            )
        return files

    # ---------- 转存 ----------

    def create_folder(self, name: str, parent_fid: str) -> str | None:
        body = {
            "pdir_fid": parent_fid,
            "file_name": name,
            "dir_path": "",
            "dir_init_lock": False,
        }
        data = self._post_json(QuarkConstants.FILE_URL, body)
        return (data.get("data") or {}).get("fid")

    def get_file_list(self, pdir_fid: str, page: int = 1, size: int = 100) -> list[ShareFile]:
        url = f"{QuarkConstants.FILE_URL}&pdir_fid={pdir_fid}&page={page}&size={size}"
        data = self._get_json(url)
        return self._parse_share_files({"data": data.get("data")}, use_fid_token=False)

    def save_share_file(
        self,
        share_id: str,
        stoken: str,
        pdir_fid: str,
        fid: str,
        fid_token: str,
        to_pdir_fid: str,
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
        data = self._post_json(QuarkConstants.SAVE_URL, body)
        task_id = (data.get("data") or {}).get("task_id") or ""
        return task_id or None

    def poll_task(self, task_id: str, max_attempts: int = 10, interval: float = 1.0) -> str | None:
        from urllib.parse import quote

        for _ in range(max_attempts):
            url = f"{QuarkConstants.TASK_URL}&task_id={quote(task_id)}&retry_index=0"
            try:
                data = self._get_json(url)
            except QuarkApiError:
                time.sleep(interval)
                continue
            d = data.get("data") or {}
            finished = d.get("finished_at", 0) > 0 or d.get("status") == 2 or d.get("task_status") == 2
            if finished:
                fids = ((d.get("save_as") or {}).get("save_as_top_fids") or [])
                if fids:
                    return fids[0]
            time.sleep(interval)
        return None

    def delete_file(self, fid: str) -> str | None:
        body = {"action_type": 2, "filelist": [fid], "exclude_fids": []}
        data = self._post_json(QuarkConstants.DELETE_URL, body)
        task_id = (data.get("data") or {}).get("task_id") or ""
        return task_id or None

    # ---------- 下载直链 ----------

    def get_download_link(self, fid: str) -> DownloadLink:
        body = {"fids": [fid]}
        data = self._post_json(QuarkConstants.DOWNLOAD_URL, body)
        arr = data.get("data") or []
        if not arr:
            raise QuarkApiError("未返回下载链接")
        item = arr[0]
        return DownloadLink(
            fid=item.get("fid") or "",
            filename=item.get("file_name") or item.get("filename") or "",
            download_url=item.get("download_url") or "",
            size=int(item.get("size") or 0),
        )

    def refresh_session(self) -> str | None:
        """剥离 __puus 后请求 /config，服务端重新下发 __puus/__pus。"""
        headers = self._headers()
        headers["Cookie"] = cookie_util.without_puus(self._cookie)
        headers["Referer"] = QuarkConstants.DOWNLOAD_REFERER
        resp = self._session.get(QuarkConstants.CONFIG_URL, headers=headers, timeout=60)
        merged = cookie_util.merge_from_set_cookies(self._cookie, resp.headers.get_list("Set-Cookie") or [])
        self._update_cookie(merged)
        return merged if merged != self._cookie else None

    request_with_origin = {
        "get_share_files": True,
        "list_cloud_files": True,
    }
