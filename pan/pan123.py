"""123 云盘 API 封装（对应 YunX Pan123Api.kt / Pan123Constants.kt）。

鉴权：登录无签名；分享列表匿名；其余 yun.123pan.cn / www.123865.com 请求必须带 auth-key/auth-value
签名头（CRC32 派生）。下载流程：分享文件无需转存，直接拿 ShareKey + FileID + S3KeyFlag + Etag + Size
换 DownloadURL，对 params 做 Base64 解码得真实 CDN 直链，下载带 Referer。
"""
from __future__ import annotations

import base64
import json
import random
import secrets
import time
import zlib
from datetime import datetime, timezone
from urllib.parse import quote

import requests

from .models import DownloadLink, ShareFile


class Pan123ApiError(Exception):
    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.message = message
        self.code = code


class Pan123Constants:
    LOGIN_BASE = "https://user.123pan.cn"
    API_BASE = "https://yun.123pan.cn"
    DOWNLOAD_BASE = "https://www.123865.com"

    LOGIN_URL = f"{LOGIN_BASE}/api/user/sign_in"
    SHARE_GET_URL = f"{API_BASE}/b/api/share/get"
    SHARE_DOWNLOAD_INFO_URL = f"{DOWNLOAD_BASE}/b/api/share/download/info"
    FILE_LIST_URL = f"{API_BASE}/b/api/file/list/new"
    FILE_DOWNLOAD_INFO_URL = f"{API_BASE}/api/file/download_info"
    TRAFFIC_CHECK_URL = f"{API_BASE}/b/api/file/download/traffic/check"
    FILE_TRASH_URL = f"{API_BASE}/b/api/file/trash"
    FILE_RENAME_URL = f"{API_BASE}/b/api/file/rename"
    FILE_MOD_PID_URL = f"{API_BASE}/b/api/file/mod_pid"
    SHARE_CREATE_URL = f"{API_BASE}/b/api/share/create"
    USER_INFO_URL = f"{API_BASE}/b/api/user/info"

    WEB_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    )
    DART_UA = "Dart/3.12 (dart:io)"
    PLATFORM_WEB = "web"
    PLATFORM_ANDROID = "android"
    APP_VERSION_WEB = "3"
    APP_VERSION_ANDROID = "39"
    APP_VERSION_LOGIN = "132"
    DOWNLOAD_REFERER = "https://yun.123pan.cn/"

    SIGN_TABLE = "adefghlmyijnopkqrstubcvwsz"
    SIGN_OS = "web"
    SIGN_VER = "3"
    SIGN_OFFSET_SECONDS = 57600
    EXPIRATION_FOREVER = "2099-12-12T08:00:00+08:00"

    @staticmethod
    def new_login_uuid() -> str:
        return secrets.token_hex(16)


class Pan123Api:
    def __init__(self, session: requests.Session | None = None):
        self._session = session or requests.Session()
        self._loginuuid = Pan123Constants.new_login_uuid()

    # ---------- 签名 ----------

    @staticmethod
    def _crc32_hex(s: str) -> str:
        crc = zlib.crc32(s.encode("utf-8")) & 0xFFFFFFFF
        return format(crc, "x")

    def make_sign(self, path: str, ts: int | None = None) -> tuple[str, str]:
        if ts is None:
            ts = int(time.time())
        minute = datetime.fromtimestamp(ts + Pan123Constants.SIGN_OFFSET_SECONDS, tz=timezone.utc)
        minute_str = minute.strftime("%Y%m%d%H%M")
        substituted = "".join(
            Pan123Constants.SIGN_TABLE[int(c)] for c in minute_str
        )
        auth_key = self._crc32_hex(substituted)

        random_ = random.randint(0, 10_000_000)
        data = f"{ts}|{random_}|{path}|{Pan123Constants.SIGN_OS}|{Pan123Constants.SIGN_VER}|{auth_key}"
        auth_value = f"{ts}-{random_}-{self._crc32_hex(data)}"
        return auth_key, auth_value

    # ---------- 登录 ----------

    def login(self, passport: str, password: str) -> str:
        body = {"passport": passport, "password": password, "remember": False}
        resp = self._session.post(
            Pan123Constants.LOGIN_URL,
            headers={
                "Content-Type": "application/json;charset=UTF-8",
                "platform": Pan123Constants.PLATFORM_WEB,
                "app-version": Pan123Constants.APP_VERSION_LOGIN,
                "loginuuid": self._loginuuid,
                "Origin": Pan123Constants.LOGIN_BASE,
                "Referer": f"{Pan123Constants.LOGIN_BASE}/centerlogin?redirect_url=&source_page=website",
                "User-Agent": Pan123Constants.WEB_UA,
            },
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            timeout=60,
        )
        data = self._parse_json(resp)
        code = data.get("code", -1)
        if code != 200:
            raise Pan123ApiError(data.get("message") or f"登录失败（code={code}）")
        token = (data.get("data") or {}).get("token") or ""
        if not token:
            raise Pan123ApiError("登录失败：未返回 token")
        return token

    def fetch_nickname(self, token: str) -> str | None:
        try:
            data = self._get_auth(Pan123Constants.USER_INFO_URL, "/b/api/user/info", token)
            self._check_ok(data, "获取用户信息失败")
            return (data.get("data") or {}).get("Nickname") or None
        except Exception:
            return None

    # ---------- 分享文件列表（匿名，无签名） ----------

    def get_share_files(
        self, share_key: str, share_pwd: str, parent_file_id: str, next_: str, page: int
    ) -> tuple[list[ShareFile], str | None]:
        url = (
            f"{Pan123Constants.SHARE_GET_URL}?limit=100"
            f"&next={next_}"
            "&orderBy=file_name"
            "&orderDirection=asc"
            f"&shareKey={quote(share_key, safe='')}"
            f"&ParentFileId={parent_file_id}"
            f"&Page={page}"
        )
        if share_pwd:
            url += f"&SharePwd={quote(share_pwd, safe='')}"
        resp = self._session.get(
            url,
            headers={"User-Agent": Pan123Constants.DART_UA},
            timeout=60,
        )
        data = self._parse_json(resp)
        self._check_ok(data, "获取文件列表失败")
        d = data.get("data")
        if not d:
            return [], None
        if d.get("Expired"):
            raise Pan123ApiError("分享已失效")
        files = self._parse_info_list(d)
        next_cursor = d.get("Next")
        return files, next_cursor if next_cursor != "-1" else None

    # ---------- 分享下载信息（需登录+签名） ----------

    def get_share_download_link(self, share_key: str, file: ShareFile, token: str) -> DownloadLink | None:
        s3_key_flag, etag, _ = self._decode_token(file.fid_token)
        body = {
            "ShareKey": share_key,
            "FileID": file.fid,
            "S3KeyFlag": s3_key_flag,
            "Size": file.fsize,
            "Etag": etag,
        }
        data = self._post_auth(
            Pan123Constants.SHARE_DOWNLOAD_INFO_URL,
            "/b/api/share/download/info",
            body,
            token,
            platform=Pan123Constants.PLATFORM_ANDROID,
            app_version=Pan123Constants.APP_VERSION_ANDROID,
        )
        self._check_ok(data, "获取下载链接失败")
        d = data.get("data") or {}
        download_url = d.get("DownloadURL") or ""
        if not download_url:
            return None
        decoded = self._decode_download_url(download_url) or download_url
        real_url = self._follow_redirect_url(decoded)
        return DownloadLink(
            fid=file.fid,
            filename=file.fname,
            download_url=real_url,
            size=file.fsize,
        )

    # ---------- 个人盘 ----------

    def list_cloud_files(self, parent_file_id: str, token: str) -> tuple[list[ShareFile], str | None]:
        url = (
            f"{Pan123Constants.FILE_LIST_URL}?driveId=0&limit=100&next=0"
            "&orderBy=update_time&orderDirection=desc"
            f"&parentFileId={parent_file_id}"
            "&trashed=false&SearchData=&Page=1&OnlyLookAbnormalFile=0"
            "&event=homeListFile&operateType=1&inDirectSpace=false"
        )
        data = self._get_auth(url, "/b/api/file/list/new", token)
        self._check_ok(data, "获取文件列表失败")
        d = data.get("data") or {}
        files = self._parse_info_list(d)
        next_ = d.get("Next")
        return files, next_ if next_ != "-1" else None

    def get_download_link(self, file: ShareFile, token: str) -> DownloadLink | None:
        s3_key_flag, etag, _ = self._decode_token(file.fid_token)
        body = {
            "driveId": 0,
            "etag": etag,
            "fileId": self._to_int(file.fid),
            "s3keyFlag": s3_key_flag,
            "type": 0,
            "fileName": file.fname,
            "size": file.fsize,
        }
        data = self._post_auth(
            Pan123Constants.FILE_DOWNLOAD_INFO_URL,
            "/api/file/download_info",
            body,
            token,
        )
        self._check_ok(data, "获取下载链接失败")
        d = data.get("data") or {}
        raw = d.get("DownloadUrl") or ""
        if not raw:
            return None
        decoded = self._decode_download_url(raw) or raw
        url = self._follow_redirect_url(decoded)
        return DownloadLink(
            fid=file.fid,
            filename=file.fname,
            download_url=url,
            size=file.fsize,
        )

    def copy_save(
        self, share_key: str, share_pwd: str, file: ShareFile, to_dir_fid: str, token: str
    ) -> tuple[int, str] | None:
        share_id = self._share_id_of(file)
        if not share_id:
            raise Pan123ApiError("无法识别分享 ID（缺少 S3KeyFlag）")
        s3_key_flag, etag, storage_node = self._decode_token(file.fid_token)
        file_id = self._to_int(file.fid)
        parent_id = self._to_int(to_dir_fid)
        item = {
            "fileID": file_id,
            "fileId": file_id,
            "size": file.fsize,
            "etag": etag,
            "type": 1 if file.isdir else 0,
            "parentFileID": parent_id,
            "parentFileId": parent_id,
            "fileName": file.fname,
            "driveID": 0,
            "driveId": 0,
            "s3keyFlag": s3_key_flag,
            "S3KeyFlag": s3_key_flag,
            "StorageNode": storage_node,
        }
        body = {
            "fileList": [item],
            "shareKey": share_key,
            "sharePwd": share_pwd or "",
            "currentLevel": 1,
            "superAdmin": None,
        }
        resp = self._session.post(
            f"https://{share_id}.mshare.123pan.cn/b/api/restful/goapi/v1/file/copy/save",
            headers={
                "Authorization": f"Bearer {token}",
                "LoginUuid": self._loginuuid,
                "platform": Pan123Constants.PLATFORM_WEB,
                "Content-Type": "application/json;charset=UTF-8",
                "User-Agent": Pan123Constants.DART_UA,
            },
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            timeout=60,
        )
        data = self._parse_json(resp)
        self._check_ok(data, "转存失败")
        task_id = (data.get("data") or {}).get("taskID")
        if task_id is None:
            return None
        return int(task_id), share_id

    def poll_copy_save(self, task_id: int, share_id: str, token: str) -> str | None:
        for _ in range(15):
            time.sleep(1)
            url = (
                f"https://{share_id}.mshare.123pan.cn/b/api/restful/goapi/v1/file/copy/save/get"
                f"?taskID={task_id}"
            )
            resp = self._session.get(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "LoginUuid": self._loginuuid,
                    "platform": Pan123Constants.PLATFORM_WEB,
                    "User-Agent": Pan123Constants.DART_UA,
                },
                timeout=60,
            )
            data = self._parse_json(resp)
            if data.get("code", -1) != 0:
                msg = data.get("message")
                if msg:
                    raise Pan123ApiError(f"转存失败：{msg}")
                continue
            d = data.get("data") or {}
            status = d.get("status", -1)
            state = (d.get("state") or "").lower()
            done = (
                d.get("finished")
                or status in (2, 3)
                or state in ("success", "done", "2")
                or any(k in d for k in ("fileId", "FileId", "newFileId"))
            )
            if done:
                return (
                    d.get("newFileId")
                    or d.get("FileId")
                    or d.get("fileId")
                    or str(task_id)
                )
        return None

    @staticmethod
    def _share_id_of(file: ShareFile) -> str:
        s3 = file.fid_token.split("|")[0]
        return s3.split("-")[0]

    # ---------- 内部工具 ----------

    @staticmethod
    def _parse_info_list(data: dict) -> list[ShareFile]:
        files = []
        for item in data.get("InfoList") or []:
            type_ = int(item.get("Type") or 0)
            files.append(ShareFile(
                fid=str(item.get("FileId") or ""),
                fname=item.get("FileName") or "",
                fsize=int(item.get("Size") or 0),
                isdir=type_ == 1,
                pdir_fid=str(item.get("ParentFileId") or ""),
                fid_token=(
                    f"{item.get('S3KeyFlag') or ''}|{item.get('Etag') or ''}"
                    f"|{item.get('StorageNode') or ''}"
                ),
                modify_time=item.get("UpdateAt") or "",
            ))
        return files

    @staticmethod
    def _decode_token(fid_token: str) -> tuple[str, str, str]:
        parts = fid_token.split("|")
        return parts[0] if len(parts) > 0 else "", parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else ""

    @staticmethod
    def _decode_download_url(download_url: str) -> str | None:
        trimmed = download_url.strip()
        if "://" not in trimmed:
            try:
                decoded = base64.b64decode(trimmed).decode("utf-8")
                return decoded if decoded.lower().startswith("http") else None
            except Exception:
                return None
        idx = trimmed.find("params=")
        if idx < 0:
            return None
        params = trimmed[idx + len("params="):].split("&")[0]
        try:
            normalized = params.replace("-", "+").replace("_", "/")
            return base64.b64decode(normalized).decode("utf-8")
        except Exception:
            return None

    def _follow_redirect_url(self, initial_url: str) -> str:
        url = initial_url
        for _ in range(5):
            next_url = self._probe_json_redirect(url)
            if next_url is None:
                return url
            url = next_url
        return url

    def _probe_json_redirect(self, url: str) -> str | None:
        try:
            resp = self._session.get(
                url,
                headers={
                    "Referer": Pan123Constants.DOWNLOAD_REFERER,
                    "User-Agent": Pan123Constants.DART_UA,
                },
                timeout=60,
            )
            length = resp.headers.get("Content-Length")
            try:
                length = int(length) if length else -1
            except ValueError:
                length = -1
            if 0 <= length <= 8192:
                body = resp.text
                if body.lstrip().startswith("{"):
                    import json as _json

                    try:
                        redirect_url = _json.loads(body).get("data") or {}
                        redirect_url = redirect_url.get("redirect_url") or ""
                        return redirect_url if redirect_url else None
                    except ValueError:
                        return None
            return None
        except requests.RequestException:
            return None

    def _get_auth(self, url: str, path: str, token: str) -> dict:
        ak, av = self.make_sign(path)
        resp = self._session.get(
            url,
            headers={
                "platform": Pan123Constants.PLATFORM_WEB,
                "app-version": Pan123Constants.APP_VERSION_WEB,
                "authorization": f"Bearer {token}",
                "loginuuid": self._loginuuid,
                "auth-key": ak,
                "auth-value": av,
                "User-Agent": Pan123Constants.WEB_UA,
                "Accept": "application/json, text/plain, */*",
            },
            timeout=60,
        )
        return self._parse_json(resp)

    def _post_auth(
        self, url: str, path: str, body: dict, token: str,
        platform: str = Pan123Constants.PLATFORM_WEB,
        app_version: str = Pan123Constants.APP_VERSION_WEB,
    ) -> dict:
        ak, av = self.make_sign(path)
        resp = self._session.post(
            url,
            headers={
                "platform": platform,
                "app-version": app_version,
                "authorization": f"Bearer {token}",
                "loginuuid": self._loginuuid,
                "auth-key": ak,
                "auth-value": av,
                "Content-Type": "application/json;charset=UTF-8",
                "User-Agent": Pan123Constants.WEB_UA,
            },
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            timeout=60,
        )
        return self._parse_json(resp)

    @staticmethod
    def _check_ok(data: dict, fallback: str) -> None:
        code = data.get("code", -1)
        if code == 0:
            return
        msg = data.get("message") or fallback
        raise Pan123ApiError(f"{msg}（code={code}）")

    @staticmethod
    def _parse_json(resp: requests.Response) -> dict:
        try:
            return resp.json()
        except ValueError as e:
            raise Pan123ApiError(f"响应解析失败: {resp.text[:200]}") from e

    @staticmethod
    def _to_int(value) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
