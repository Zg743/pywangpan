"""迅雷网盘 API 封装（对应 YunX XunleiApi.kt / XunleiConstants.kt）。

登录：captcha/init → v3/login（可能触发短信）→ sendsms → smslogin → v1/auth/signin/token。
Pan：文件列表 / 分享解析 / 转存 / 直链（Bearer 认证，无需 x-signature）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from urllib.parse import quote

import requests

from .models import DownloadLink, ShareFile
from .xunlei_fingerprint import XunleiDeviceFingerprint


class XunleiApiError(Exception):
    def __init__(self, message: str, code: int = 0):
        super().__init__(message)
        self.message = message
        self.code = code


class XunleiConstants:
    AUTH_BASE = "https://xluser-ssl.xunlei.com"
    PAN_BASE = "https://api-pan.xunlei.com"

    CLIENT_ID = "Xp6pAdwyJv9sQuoN"
    CLIENT_SECRET = "standard_a@api#"
    APP_CLIENT_ID = "Xp6vsxz_7IYVw2BB"
    APP_CLIENT_SECRET = "Xp6vsy4tN9toTVdMSpomVdXpRmES"
    APP_CLIENT_VERSION = "8.31.0.9726"
    APP_PACKAGE_NAME = "com.xunlei.downloadprovider"

    CAPTCHA_SALTS = [
        "9uJNVj/wLmdwKrJaVj/omlQ",
        "Oz64Lp0GigmChHMf/6TNfxx7O9PyopcczMsnf",
        "Eb+L7Ce+Ej48u",
        "jKY0",
        "ASr0zCl6v8W4aidjPK5KHd1Lq3t+vBFf41dqv5+fnOd",
        "wQlozdg6r1qxh0eRmt3QgNXOvSZO6q/GXK",
        "gmirk+ciAvIgA/cxUUCema47jr/YToixTT+Q6O",
        "5IiCoM9B1/788ntB",
        "P07JH0h6qoM6TSUAK2aL9T5s2QBVeY9JWvalf",
        "+oK0AN",
    ]

    APP_UA = (
        "ANDROID-com.xunlei.downloadprovider/8.31.0.9726 netWorkType/5G appid/40 "
        "deviceName/Xiaomi_M2004j7ac deviceModel/M2004J7AC OSVersion/12 protocolVersion/301 "
        "platformVersion/10 sdkVersion/512000 Oauth2Client/0.9 "
        "(Linux 4_14_186-perf-gddfs8vbb238b) (JAVA 0)"
    )
    WEB_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

    CAPTCHA_INIT_URL = f"{AUTH_BASE}/v1/shield/captcha/init"
    LOGIN_URL = f"{AUTH_BASE}/xluser.core.login/v3/login"
    SEND_SMS_URL = f"{AUTH_BASE}/xluser.core.login/v3/sendsms"
    SMS_LOGIN_URL = f"{AUTH_BASE}/xluser.core.login/v3/smslogin"
    TOKEN_URL = f"{AUTH_BASE}/v1/auth/signin/token"
    REFRESH_URL = f"{AUTH_BASE}/v1/auth/token"

    FILES_URL = f"{PAN_BASE}/drive/v1/files"
    SHARE_URL = f"{PAN_BASE}/drive/v1/share"
    SHARE_DETAIL_URL = f"{PAN_BASE}/drive/v1/share/detail"
    RESTORE_URL = f"{PAN_BASE}/drive/v1/share/restore"
    TASKS_URL = f"{PAN_BASE}/drive/v1/tasks"
    TEMP_DIR_NAME = "YunX临时转存"
    MOVE_URL = f"{PAN_BASE}/drive/v1/files:batchMove"
    TRASH_URL = f"{PAN_BASE}/drive/v1/files:batchTrash"
    SHARE_CREATE_URL = f"{PAN_BASE}/drive/v1/share"


@dataclass
class XunleiShareResult:
    title: str
    files: list[ShareFile]
    pass_code_token: str
    share_id: str
    next_page_token: str = ""


@dataclass
class XunleiFilePage:
    files: list[ShareFile]
    next_page_token: str = ""


@dataclass
class XunleiLoginStep:
    need_sms: bool = False
    sms_credit_key: str = ""
    sms_token: str = ""
    session_key: str = ""
    session_id: str = ""
    nickname: str = ""
    user_id: str = ""
    review_url: str = ""
    message: str = ""


class XunleiApi:
    def __init__(self, session: requests.Session | None = None, device_fp=None):
        self._session = session or requests.Session()
        self.device = device_fp
        self._refreshed_captcha: str | None = None
        self._current_access_token: str = ""
        # refresh 回调：refresh_token_provider(device_id) -> (new_access, new_refresh) | None
        self.refresh_token_provider = None
        self._current_user_id: str = ""

    # ---------- 登录 ----------

    def init_captcha(
        self, device_id: str, username: str, action: str = "POST:/auth/signin/token"
    ) -> str | None:
        ts = str(int(time.time() * 1000))
        sign = self._build_captcha_sign(device_id, ts)
        body = {
            "action": action,
            "captcha_token": "",
            "client_id": XunleiConstants.APP_CLIENT_ID,
            "device_id": device_id,
            "meta": {
                "username": username,
                "client_version": XunleiConstants.APP_CLIENT_VERSION,
                "package_name": XunleiConstants.APP_PACKAGE_NAME,
                "timestamp": ts,
                "captcha_sign": sign,
                "user_id": self._current_user_id,
            },
            "redirect_uri": "xlaccsdk01://xunlei.com/callback?state=harbor",
        }
        resp = self._session.post(
            XunleiConstants.CAPTCHA_INIT_URL,
            headers={
                "User-Agent": XunleiConstants.APP_UA,
                "Accept": "application/json;charset=UTF-8",
                "Content-Type": "application/json",
                "X-Client-Id": XunleiConstants.APP_CLIENT_ID,
                "X-Device-Id": device_id,
                "X-Client-Version": "8.31.0.9726",
            },
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            timeout=60,
        )
        try:
            data = self._parse_json(resp)
            token = data.get("captcha_token") or ""
            return token if token else None
        except Exception:
            return None

    def login_with_password(
        self, username: str, password: str, device_id: str, check_code: str = ""
    ) -> XunleiLoginStep:
        body = self._base_login_body(device_id, "25.0.5.25", "513006")
        body.update(
            {
                "userName": username,
                "passWord": password,
                "verifyKey": "",
                "verifyCode": check_code,
                "isMd5Pwd": "0",
            }
        )
        resp = self._session.post(
            XunleiConstants.LOGIN_URL,
            headers={
                "User-Agent": "android-ok-http-client/xl-acc-sdk/version-5.1.3.513006",
                "Content-Type": "application/json",
            },
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            timeout=60,
        )
        return self._parse_login_response(self._parse_json(resp))

    def send_sms(self, mobile: str, device_id: str) -> XunleiLoginStep:
        body = self._base_login_body(device_id, "8.31.0.9726", "231500")
        body.update({"mobile": mobile, "register": "0"})
        resp = self._session.post(
            XunleiConstants.SEND_SMS_URL,
            headers={
                "User-Agent": "android-ok-http-client/xl-acc-sdk/version-5.0.12.512000",
                "Content-Type": "application/json",
            },
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            timeout=60,
        )
        data = self._parse_json(resp)
        return XunleiLoginStep(
            need_sms=True,
            sms_credit_key=data.get("creditkey") or "",
            sms_token=data.get("token") or "",
            message=(data.get("errorDesc") or "短信已发送"),
        )

    def sms_login(
        self,
        mobile: str,
        sms_code: str,
        credit_key: str,
        sms_token: str,
        device_id: str,
    ) -> XunleiLoginStep:
        body = self._base_login_body(device_id, "8.31.0.9726", "231500", credit_key)
        body.update(
            {"mobile": mobile, "smsCode": sms_code, "token": sms_token, "register": "0"}
        )
        resp = self._session.post(
            XunleiConstants.SMS_LOGIN_URL,
            headers={
                "User-Agent": "android-ok-http-client/xl-acc-sdk/version-5.0.12.512000",
                "Content-Type": "application/json",
            },
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            timeout=60,
        )
        return self._parse_login_response(self._parse_json(resp))

    def exchange_token(
        self, session_id: str, device_id: str, captcha_token: str
    ) -> tuple[str, str] | None:
        body = {
            "client_id": XunleiConstants.APP_CLIENT_ID,
            "client_secret": XunleiConstants.APP_CLIENT_SECRET,
            "provider": "access_end_point_token",
            "signin_token": session_id,
        }
        headers = {
            "User-Agent": XunleiConstants.APP_UA,
            "Accept": "application/json;charset=UTF-8",
            "Content-Type": "application/json",
            "X-Client-Id": XunleiConstants.APP_CLIENT_ID,
            "X-Device-Id": device_id,
            "X-Client-Version": "8.31.0.9726",
        }
        if captcha_token:
            headers["X-Captcha-Token"] = captcha_token
        try:
            resp = self._session.post(
                XunleiConstants.TOKEN_URL,
                headers=headers,
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                timeout=60,
            )
            data = self._parse_json(resp)
            at = data.get("access_token") or data.get("accessToken") or ""
            rt = data.get("refresh_token") or data.get("refreshToken") or ""
            if not at:
                return None
            sub = self._jwt_sub(at)
            if sub:
                self._current_user_id = sub
            self._current_access_token = at
            return at, rt
        except Exception:
            return None

    def refresh_token(self, refresh_token: str, device_id: str) -> tuple[str, str] | None:
        body = (
            "grant_type=refresh_token"
            f"&client_id={XunleiConstants.APP_CLIENT_ID}"
            f"&client_secret={XunleiConstants.APP_CLIENT_SECRET}"
            f"&refresh_token={quote(refresh_token, safe='')}"
        )
        try:
            resp = self._session.post(
                XunleiConstants.REFRESH_URL,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Device-Id": device_id,
                },
                data=body,
                timeout=60,
            )
            data = self._parse_json(resp)
            at = data.get("access_token") or data.get("accessToken") or ""
            rt = data.get("refresh_token") or data.get("refreshToken") or ""
            if not at:
                return None
            sub = self._jwt_sub(at)
            if sub:
                self._current_user_id = sub
            self._current_access_token = at
            return at, rt
        except Exception:
            return None

    # ---------- Pan ----------

    def get_files(
        self, parent_id: str, access_token: str, device_id: str, captcha_token: str
    ) -> list[ShareFile] | None:
        filters = quote('{"trashed":{"eq":false}}')
        url = (
            f"{XunleiConstants.FILES_URL}?parent_id={parent_id}"
            f"&page_token=&limit=50&with_audit=true&filters={filters}"
        )
        return self._pan_call(
            captcha_token, device_id, "GET:/drive/v1/files",
            lambda t: self._pan_request(url, access_token, device_id, t),
            lambda data: self._parse_file_array(data.get("files") or []),
        )

    def create_folder(
        self, name: str, parent_id: str, access_token: str, device_id: str, captcha_token: str
    ) -> str | None:
        body = json.dumps(
            {"kind": "drive#folder", "name": name, "parent_id": parent_id, "space": ""},
            ensure_ascii=False,
        ).encode("utf-8")
        return self._pan_call(
            captcha_token, device_id, "POST:/drive/v1/files",
            lambda t: self._pan_request(
                XunleiConstants.FILES_URL, access_token, device_id, t, body
            ),
            lambda data: (data.get("id") or "") or None,
        )

    def get_file_detail(
        self, file_id: str, access_token: str, device_id: str, captcha_token: str
    ) -> DownloadLink | None:
        url = (
            f"{XunleiConstants.FILES_URL}/{file_id}?_magic=2021&usage=PLAY"
            "&thumbnail_size=SIZE_LARGE&with=hdr10&with=subtitle_files&with=task"
            "&with=public_share_tag"
        )
        return self._pan_call(
            captcha_token, device_id, f"GET:/drive/v1/files/{file_id}",
            lambda t: self._pan_request(url, access_token, device_id, t),
            lambda data: self._parse_file_detail(data),
        )

    def get_share(
        self,
        share_id: str,
        pass_code: str,
        access_token: str,
        device_id: str,
        captcha_token: str,
        page_token: str = "",
    ) -> XunleiShareResult | None:
        url = (
            f"{XunleiConstants.SHARE_URL}?share_id={share_id}"
            f"&pass_code={quote(pass_code, safe='')}"
            f"&limit=100&page_token={quote(page_token, safe='')}"
            "&thumbnail_size=SIZE_SMALL"
        )
        return self._pan_call(
            captcha_token, device_id, "GET:/drive/v1/share",
            lambda t: self._pan_request(url, access_token, device_id, t),
            lambda data: self._parse_share(data, share_id),
        )

    def get_share_detail(
        self,
        share_id: str,
        parent_id: str,
        pass_code_token: str,
        access_token: str,
        device_id: str,
        captcha_token: str,
        page_token: str = "",
    ) -> XunleiFilePage | None:
        url = (
            f"{XunleiConstants.SHARE_DETAIL_URL}?share_id={share_id}"
            f"&parent_id={parent_id}&pass_code_token={quote(pass_code_token, safe='')}"
            f"&limit=100&page_token={quote(page_token, safe='')}"
            "&thumbnail_size=SIZE_SMALL"
        )
        return self._pan_call(
            captcha_token, device_id, "GET:/drive/v1/share/detail",
            lambda t: self._pan_request(url, access_token, device_id, t),
            lambda data: XunleiFilePage(
                files=self._parse_file_array(data.get("files") or []),
                next_page_token=data.get("next_page_token") or "",
            ),
        )

    def restore(
        self,
        share_id: str,
        pass_code_token: str,
        parent_folder_id: str,
        file_ids: list[str],
        access_token: str,
        device_id: str,
        captcha_token: str,
    ) -> str | None:
        body = json.dumps(
            {
                "share_id": share_id,
                "pass_code_token": pass_code_token,
                "parent_id": parent_folder_id,
                "ancestor_ids": [],
                "file_ids": file_ids,
                "specify_parent_id": True,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        return self._pan_call(
            captcha_token, device_id, "POST:/drive/v1/share/restore",
            lambda t: self._pan_request(
                XunleiConstants.RESTORE_URL, access_token, device_id, t, body
            ),
            lambda data: self._trace_file_id(data, file_ids),
        )

    def batch_delete(
        self, ids: list[str], access_token: str, device_id: str, captcha_token: str
    ) -> bool:
        body = json.dumps({"ids": ids, "space": ""}, ensure_ascii=False).encode("utf-8")
        return self._pan_call(
            captcha_token, device_id, "POST:/drive/v1/files:batchDelete",
            lambda t: self._pan_request(
                f"{XunleiConstants.FILES_URL}:batchDelete", access_token, device_id, t, body
            ),
            lambda data: True,
        )

    # ---------- 请求构造 ----------

    def _pan_request(self, url: str, access_token: str, device_id: str, captcha_token: str, body: bytes | None = None):
        headers = {
            "User-Agent": XunleiConstants.WEB_UA,
            "Authorization": f"Bearer {self._current_access_token or access_token}",
            "X-Device-Id": device_id,
            "X-Client-Version": "8.31.0.9726",
            "Content-Type": "application/json",
            "Origin": "https://pan.xunlei.com",
            "Referer": "https://pan.xunlei.com/",
        }
        if captcha_token:
            headers["X-Captcha-Token"] = captcha_token
        if body is not None:
            return self._session.post(url, headers=headers, data=body, timeout=60)
        return self._session.get(url, headers=headers, timeout=60)

    def _pan_call(self, captcha_token: str, device_id: str, action: str, build, parse):
        token = self._refreshed_captcha or captcha_token
        for attempt in range(2):
            resp = build(token)
            data = self._parse_json(resp)
            if not resp.ok or "error" in data:
                err = data.get("error") or ""
                if (resp.status_code == 401 or err == "unauthenticated") and attempt == 0:
                    if self.refresh_token_provider:
                        refreshed = self.refresh_token_provider(device_id)
                        if refreshed:
                            self._current_access_token = refreshed[0]
                            new_captcha = self._init_pan_captcha(device_id, action, token)
                            if new_captcha:
                                self._refreshed_captcha = new_captcha
                                token = new_captcha
                            continue
                if err == "captcha_invalid" and attempt == 0:
                    new_token = self._init_pan_captcha(device_id, action, token)
                    if new_token:
                        self._refreshed_captcha = new_token
                        token = new_token
                        continue
                msg = (
                    data.get("error_description") or data.get("message") or err or "请求失败"
                )
                raise XunleiApiError(msg)
            payload = data.get("data") or data
            return parse(payload)
        raise XunleiApiError("验证码刷新后仍失败")

    def _init_pan_captcha(self, device_id: str, action: str, old_token: str) -> str | None:
        ts = str(int(time.time() * 1000))
        sign = self._build_captcha_sign(device_id, ts)
        body = {
            "client_id": XunleiConstants.APP_CLIENT_ID,
            "action": action,
            "device_id": device_id,
            "redirect_uri": "xlaccsdk01://xunlei.com/callback?state=harbor",
            "meta": {
                "client_version": XunleiConstants.APP_CLIENT_VERSION,
                "package_name": XunleiConstants.APP_PACKAGE_NAME,
                "timestamp": ts,
                "captcha_sign": sign,
                "user_id": self._current_user_id,
            },
            "captcha_token": old_token,
        }
        try:
            resp = self._session.post(
                XunleiConstants.CAPTCHA_INIT_URL,
                headers={
                    "User-Agent": XunleiConstants.APP_UA,
                    "Accept": "application/json;charset=UTF-8",
                    "Content-Type": "application/json",
                    "X-Client-Id": XunleiConstants.APP_CLIENT_ID,
                    "X-Device-Id": device_id,
                    "X-Client-Version": "8.31.0.9726",
                },
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                timeout=60,
            )
            data = self._parse_json(resp)
            token = data.get("captcha_token") or ""
            return token if token else None
        except Exception:
            return None

    # ---------- 解析辅助 ----------

    def _parse_file_detail(self, data: dict) -> DownloadLink | None:
        links = data.get("links") or {}
        url = (
            (links.get("application/octet-stream") or {}).get("url")
            or data.get("web_content_link")
            or ""
        )
        return DownloadLink(
            fid=data.get("id") or "",
            filename=data.get("name") or "",
            download_url=url,
            size=int(data.get("size") or 0),
        )

    def _parse_share(self, data: dict, share_id: str) -> XunleiShareResult:
        status = data.get("share_status") or ""
        if status == "PASS_CODE_EMPTY":
            raise XunleiApiError("请输入提取码")
        if status == "PASS_CODE_ERROR":
            raise XunleiApiError("提取码错误")
        if status == "PASS_CODE_NEED":
            raise XunleiApiError("该分享需要提取码")
        return XunleiShareResult(
            title=data.get("title") or "",
            files=self._parse_file_array(data.get("files") or []),
            pass_code_token=data.get("pass_code_token") or "",
            share_id=share_id,
            next_page_token=data.get("next_page_token") or "",
        )

    @staticmethod
    def _trace_file_id(data: dict, file_ids: list[str]) -> str | None:
        trace = ((data.get("params") or {}).get("trace_file_ids")) or ""
        if trace:
            try:
                mapping = json.loads(trace)
                for fid in file_ids:
                    if fid in mapping and mapping.get(fid):
                        return str(mapping[fid])
            except (ValueError, TypeError):
                pass
        return data.get("file_id") or None

    @staticmethod
    def _parse_file_array(array) -> list[ShareFile]:
        files = []
        for item in array:
            files.append(
                ShareFile(
                    fid=item.get("id") or "",
                    fname=item.get("name") or "",
                    fsize=int(item.get("size") or 0),
                    isdir=item.get("kind") == "drive#folder",
                    pdir_fid=item.get("parent_id") or "",
                    fid_token="",
                    modify_time=item.get("modified_time") or "",
                )
            )
        return files

    def _base_login_body(self, device_id: str, client_version: str, sdk_version: str, credit_key: str = "") -> dict:
        device = self.device or XunleiDeviceFingerprint()
        return {
            "protocolVersion": "301",
            "sequenceNo": str(random.randint(10000000, 99999999)),
            "platformVersion": "10",
            "isCompressed": "0",
            "appid": "40",
            "clientVersion": client_version,
            "peerID": device.peer_id,
            "appName": "ANDROID-com.xunlei.downloadprovider",
            "sdkVersion": sdk_version,
            "devicesign": device.device_sign,
            "netWorkType": "WIFI",
            "providerName": "NONE",
            "deviceModel": "M2004J7AC",
            "deviceName": "Xiaomi_M2004j7ac",
            "OSVersion": "12",
            "creditkey": credit_key,
            "hl": "zh-CN",
        }

    def _parse_login_response(self, data: dict) -> XunleiLoginStep:
        error_code = str(data.get("errorCode") or "")
        if error_code == "0" or data.get("error") == "success":
            return XunleiLoginStep(
                need_sms=False,
                session_key=data.get("loginKey") or "",
                session_id=data.get("sessionID") or "",
                nickname=data.get("nickName") or "",
                user_id=str(data.get("userID") or ""),
                message="登录成功",
            )
        error = data.get("error") or ""
        need_sms = (
            error == "review_panel"
            or error_code == "1007"
            or bool(data.get("verifyType"))
        )
        return XunleiLoginStep(
            need_sms=need_sms,
            review_url=data.get("reviewurl") or "",
            message=data.get("errorDesc") or data.get("error_description") or "",
        )

    @staticmethod
    def _build_captcha_sign(device_id: str, ts_ms: str) -> str:
        h = (
            XunleiConstants.APP_CLIENT_ID
            + XunleiConstants.APP_CLIENT_VERSION
            + XunleiConstants.APP_PACKAGE_NAME
            + device_id
            + ts_ms
        )
        for salt in XunleiConstants.CAPTCHA_SALTS:
            h = hashlib.md5((h + salt).encode("utf-8")).hexdigest()
        return f"1.{h}"

    @staticmethod
    def _jwt_sub(token: str) -> str:
        try:
            parts = token.split(".")
            if len(parts) < 2:
                return ""
            payload = parts[1]
            payload += "=" * (-len(payload) % 4)
            raw = base64.urlsafe_b64decode(payload.encode("ascii"))
            return json.loads(raw).get("sub") or ""
        except Exception:
            return ""

    def jwt_exp(self, token: str) -> int:
        try:
            parts = token.split(".")
            if len(parts) < 2:
                return 0
            payload = parts[1]
            payload += "=" * (-len(payload) % 4)
            raw = base64.urlsafe_b64decode(payload.encode("ascii"))
            return int(json.loads(raw).get("exp") or 0)
        except Exception:
            return 0

    @staticmethod
    def _parse_json(resp: requests.Response) -> dict:
        try:
            return resp.json()
        except ValueError:
            return {}
