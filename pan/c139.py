"""139 网盘（和彩云）API 封装（对应 YunX C139Api.kt / C139Constants.kt）。

分享接口（share-kd-njs.yun.139.com）请求/响应均经 AES-CBC 加密：base64(IV(16B) ‖ AES_CBC(KEY, IV, 明文))；
mcloud-sign 按「明文 body」计算，mcloud-skey 可省略。
个人网盘接口（personal-kd-njs）为明文 JSON + Authorization + mcloud-sign + 全套渠道头。
加密用 `cryptography` 库（pycryptodome 不可用时）。
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import random
import string
from datetime import datetime
from urllib.parse import quote

import requests

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad, unpad

    _HAS_PYCRYPTO = True
except ImportError:  # pragma: no cover
    _HAS_PYCRYPTO = False
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .models import DownloadLink, ShareFile


class C139ApiError(Exception):
    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.message = message
        self.code = code


class C139Constants:
    SHARE_BASE = "https://share-kd-njs.yun.139.com"
    SHARE_LIST_URL = (
        f"{SHARE_BASE}/yun-share/richlifeApp/devapp/IOutLink/getOutLinkInfoV6"
    )
    SHARE_LINK_URL = (
        f"{SHARE_BASE}/yun-share/richlifeApp/devapp/IOutLink/dlFromOutLinkV3"
    )
    SHARE_GENERAL_URL = (
        f"{SHARE_BASE}/yun-share/richlifeApp/devapp/IOutLink/getOutLinkGeneral"
    )
    SHARE_AES_KEY = "PVGDwmcvfs1uV3d1"
    TRANSFER_CREATE_URL = (
        f"{SHARE_BASE}/yun-share/richlifeApp/devapp/IBatchOprTask/createOuterLinkBatchOprTask"
    )
    TRANSFER_QUERY_URL = (
        f"{SHARE_BASE}/yun-share/richlifeApp/devapp/IBatchOprTask/queryBatchOprTaskDetail"
    )

    CLOUD_BASE = "https://personal-kd-njs.yun.139.com"
    FILE_LIST_URL = f"{CLOUD_BASE}/hcy/file/list"
    FILE_UPDATE_URL = f"{CLOUD_BASE}/hcy/file/update"
    BATCH_MOVE_URL = f"{CLOUD_BASE}/hcy/file/batchMove"
    BATCH_TRASH_URL = f"{CLOUD_BASE}/hcy/recyclebin/batchTrash"
    DOWNLOAD_URL = f"{CLOUD_BASE}/hcy/file/getDownloadUrl"
    TASK_GET_URL = f"{CLOUD_BASE}/hcy/task/get"

    YUN_CHANNEL_SOURCE = "10000034"
    MCLOUD_VERSION = "7.17.9"
    MCLOUD_CLIENT = "10701"
    MCLOUD_CHANNEL = "1000101"
    YUN_MODULE_TYPE = "100"
    M4C_SRC = "10002"
    M4C_CALLER = "PC"
    X_DEVICEINFO = (
        "||9|7.17.9|chrome|116.0.0.0|2cdaf7ada9e353c70eba99092e177991||windows 10||zh-CN|||"
    )
    X_CLIENT_INFO = (
        "||9|7.17.9|chrome|116.0.0.0|2cdaf7ada9e353c70eba99092e177991||windows 10||zh-CN|||dW5kZWZpbmVk||"
    )

    SHARE_X_DEVICEINFO = "||3|12.27.0|||||chrome 150.0.0.0|360X444|zh-cn|||"
    SHARE_X_HUAWEI_CHANNELSRC = "10245500"
    SHARE_X_MM_SOURCE = "0002"
    SHARE_MOBILE_UA = (
        "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36"
    )
    PC_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    _KEEP_KEYS = {
        "Os_SSo_Sid", "RMKEY", "UserData", "Login_UserNumber",
        "_139_index_isLoginType", "UUIDToken", "JSESSIONID",
        "areaCode8011", "provCode8011",
        "authorization", "auth_token", "token", "ud_id",
        "ORCHES-I-ACCOUNT-SIMPLIFY", "ORCHES-I-ACCOUNT-ENCRYPT", "nation_code",
        "platform", "cutover_status", "isUserDomainError", "a_k", "skey", "WT_FPC",
        "hecaiyun_stay_url", "hecaiyun_stay_time",
        "hecaiyundata2021jssdkcross", "sajssdk_2015_cross_new_user",
    }

    @staticmethod
    def is_valid_cookie(cookie: str | None) -> bool:
        if not cookie:
            return False
        parts = [kv.strip() for kv in cookie.split(";")]
        if any(kv.startswith("authorization=") and len(kv) > len("authorization=") for kv in parts):
            return True
        required = {"Os_SSo_Sid", "RMKEY"}
        return all(
            any(kv.startswith(f"{key}=") and len(kv) > len(key) + 1 for kv in parts)
            for key in required
        )

    @staticmethod
    def extract_authorization(cookie: str | None) -> str | None:
        if not cookie:
            return None
        for kv in cookie.split(";"):
            kv = kv.strip()
            if kv.startswith("authorization="):
                v = kv.split("=", 1)[1]
                return v if v else None
        return None

    @staticmethod
    def extract_account_full(cookie: str | None) -> str | None:
        if not cookie:
            return None
        for kv in cookie.split(";"):
            kv = kv.strip()
            if kv.startswith("ORCHES-I-ACCOUNT-ENCRYPT="):
                v = kv.split("=", 1)[1]
                if v:
                    try:
                        decoded = base64.b64decode(v).decode("utf-8")
                        if decoded:
                            return decoded
                    except Exception:
                        pass
        auth = C139Constants.extract_authorization(cookie)
        if auth:
            account = C139Api.account_from_authorization(auth)
            if account:
                return account
        for kv in cookie.split(";"):
            kv = kv.strip()
            if kv.startswith("Login_UserNumber="):
                v = kv.split("=", 1)[1]
                if v:
                    return v
        return None

    @staticmethod
    def extract_account(cookie: str | None) -> str | None:
        if not cookie:
            return None
        for kv in cookie.split(";"):
            kv = kv.strip()
            if kv.startswith("ORCHES-I-ACCOUNT-SIMPLIFY="):
                v = kv.split("=", 1)[1]
                if v:
                    return v
        for kv in cookie.split(";"):
            kv = kv.strip()
            if kv.startswith("ORCHES-I-ACCOUNT-ENCRYPT="):
                v = kv.split("=", 1)[1]
                if v:
                    try:
                        decoded = base64.b64decode(v).decode("utf-8")
                        return decoded if decoded else v
                    except Exception:
                        return v
        for kv in cookie.split(";"):
            kv = kv.strip()
            if kv.startswith("Login_UserNumber="):
                v = kv.split("=", 1)[1]
                if v:
                    return v
        return None

    @staticmethod
    def _skey(cookie: str | None) -> str | None:
        if not cookie:
            return None
        for kv in cookie.split(";"):
            kv = kv.strip()
            if kv.startswith("skey="):
                return kv.split("=", 1)[1]
        return None


class C139Api:
    def __init__(self, session: requests.Session | None = None):
        self._session = session or requests.Session()
        self._aes_key = C139Constants.SHARE_AES_KEY.encode("utf-8")

    # ---------- 签名 / 加密 ----------

    @staticmethod
    def account_from_authorization(authorization: str | None) -> str | None:
        if not authorization:
            return None
        try:
            b64 = authorization.removeprefix("Basic").strip()
            decoded = base64.b64decode(b64).decode("utf-8")
            return decoded.split(":")[1] if len(decoded.split(":")) > 1 and decoded.split(":")[1] else None
        except Exception:
            return None

    @staticmethod
    def _md5_hex(s: str) -> str:
        return hashlib.md5(s.encode("utf-8")).hexdigest()

    @staticmethod
    def _encode_uri_component(s: str) -> str:
        return (
            quote(s, safe="!()*'")
            .replace("+", "%20")
        )

    def cal_sign(self, body_json: str, ts: str, rand: str) -> str:
        encoded = self._encode_uri_component(body_json)
        sorted_chars = "".join(sorted(encoded))
        b64 = base64.b64encode(sorted_chars.encode("utf-8")).decode("ascii")
        res = self._md5_hex(b64) + self._md5_hex(f"{ts}:{rand}")
        return self._md5_hex(res).upper()

    def _sign_header(self, body_json: str) -> str:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pool = string.ascii_letters + string.digits
        rand = "".join(random.choice(pool) for _ in range(16))
        return f"{ts},{rand},{self.cal_sign(body_json, ts, rand)}"

    def _aes_encrypt(self, plaintext: str) -> str:
        iv = os.urandom(16)
        data = plaintext.encode("utf-8")
        if _HAS_PYCRYPTO:
            cipher = AES.new(self._aes_key, AES.MODE_CBC, iv=iv)
            ct = cipher.encrypt(pad(data, AES.block_size))
        else:  # pragma: no cover
            pad_len = 16 - (len(data) % 16)
            padder = data + bytes([pad_len]) * pad_len
            cipher = Cipher(algorithms.AES(self._aes_key), modes.CBC(iv))
            enc = cipher.encryptor()
            ct = enc.update(padder) + enc.finalize()
        return base64.b64encode(iv + ct).decode("ascii")

    def _aes_decrypt(self, b64: str) -> str:
        raw = base64.b64decode(b64)
        iv = raw[:16]
        ct = raw[16:]
        if _HAS_PYCRYPTO:
            cipher = AES.new(self._aes_key, AES.MODE_CBC, iv=iv)
            d = cipher.decrypt(ct)
            try:
                d = unpad(d, AES.block_size)
            except Exception:
                pass
        else:  # pragma: no cover
            cipher = Cipher(algorithms.AES(self._aes_key), modes.CBC(iv))
            dec = cipher.decryptor()
            d = dec.update(ct) + dec.finalize()
            if d:
                pad_len = d[-1]
                if 1 <= pad_len <= 16:
                    d = d[:-pad_len]
        if len(d) > 2 and d[0] == 0x1F and d[1] == 0x8B:
            d = gzip.decompress(d)
        return d.decode("utf-8")

    # ---------- 分享解析 ----------

    def _share_post(self, url: str, plain_body: str, authorization: str | None, sign: bool) -> dict:
        encrypted = self._aes_encrypt(plain_body)
        headers = {
            "hcy-cool-flag": "1",
            "x-deviceinfo": C139Constants.SHARE_X_DEVICEINFO,
            "x-huawei-channelsrc": C139Constants.SHARE_X_HUAWEI_CHANNELSRC,
            "x-mm-source": C139Constants.SHARE_X_MM_SOURCE,
            "Content-Type": "application/json;charset=UTF-8",
            "User-Agent": C139Constants.SHARE_MOBILE_UA,
            "Origin": "https://yun.139.com",
            "Referer": "https://yun.139.com/",
            "Accept": "application/json, text/plain, */*",
        }
        if sign:
            headers["mcloud-sign"] = self._sign_header(plain_body)
        if authorization:
            headers["Authorization"] = authorization
        resp = self._session.post(url, headers=headers, data=encrypted, timeout=60)
        body = resp.text
        try:
            return json.loads(self._aes_decrypt(body))
        except Exception:
            try:
                return json.loads(body)
            except ValueError:
                return {}

    def get_out_link_title(self, link_id: str) -> str | None:
        req = {"linkID": link_id, "isPasswd": 1, "account": ""}
        resp = self._share_post(C139Constants.SHARE_GENERAL_URL, json.dumps({"getOutLinkGeneralReq": req}), None, sign=False)
        if resp.get("resultCode") not in (None, "", "0"):
            return None
        if resp.get("success") is False:
            return None
        data = (resp.get("data") or {}).get("getOutLinkGeneralResp") or {}
        array = data.get("outLinkGeneral") or []
        if not array:
            return None
        return (array[0] or {}).get("lkName") or None

    def get_out_link_password(self, link_id: str) -> str | None:
        req = {"linkID": link_id, "isPasswd": 1, "account": ""}
        resp = self._share_post(C139Constants.SHARE_GENERAL_URL, json.dumps({"getOutLinkGeneralReq": req}), None, sign=False)
        if resp.get("resultCode") not in (None, "", "0"):
            return None
        if resp.get("success") is False:
            return None
        data = (resp.get("data") or {}).get("getOutLinkGeneralResp") or {}
        array = data.get("outLinkGeneral") or []
        if not array:
            return None
        return (array[0] or {}).get("passwd") or None

    def get_share_files(
        self, link_id: str, pca_id: str, passwd: str, begin: int = 1, end: int = 200
    ) -> list[ShareFile]:
        req = {
            "account": "",
            "linkID": link_id,
            "passwd": passwd,
            "caSrt": 1,
            "coSrt": 1,
            "srtDr": 0,
            "bNum": begin,
            "pCaID": pca_id,
            "eNum": end,
        }
        resp = self._share_post(
            C139Constants.SHARE_LIST_URL,
            json.dumps({"getOutLinkInfoReq": req}),
            None,
            sign=False,
        )
        result_code = resp.get("resultCode") or ""
        if result_code and result_code != "0":
            raise C139ApiError(resp.get("desc") or f"获取文件列表失败（{result_code}）")
        if resp.get("success") is False:
            raise C139ApiError(resp.get("desc") or "获取文件列表失败")
        data = resp.get("data") or {}
        result: list[ShareFile] = []
        for item in data.get("caLst") or []:
            result.append(ShareFile(
                fid=item.get("caID") or "",
                fname=item.get("caName") or "",
                fsize=0,
                isdir=True,
                pdir_fid=pca_id,
                fid_token="",
                modify_time=item.get("udTime") or item.get("ctTime") or "",
            ))
        for item in data.get("coLst") or []:
            isdir = bool(item.get("isdir")) or int(item.get("coType") or 1) == 2
            result.append(ShareFile(
                fid=item.get("coID") or "",
                fname=item.get("coName") or "",
                fsize=int(item.get("coSize") or 0),
                isdir=isdir,
                pdir_fid=pca_id,
                fid_token="",
                modify_time=item.get("udTime") or item.get("ctTime") or "",
            ))
        return result

    def get_share_download_link(
        self, co_id: str, link_id: str, account: str, authorization: str | None
    ) -> DownloadLink | None:
        req_v3 = {
            "account": account,
            "linkID": link_id,
            "coIDLst": {"item": [co_id]},
            "commonAccountInfo": {"account": account, "accountType": 1},
        }
        resp = self._share_post(
            C139Constants.SHARE_LINK_URL,
            json.dumps({"dlFromOutLinkReqV3": req_v3}),
            authorization,
            sign=True,
        )
        result_code = resp.get("resultCode") or ""
        if result_code and result_code != "0":
            raise C139ApiError(resp.get("desc") or f"获取下载链接失败（{result_code}）")
        if resp.get("success") is False:
            raise C139ApiError(resp.get("desc") or "获取下载链接失败")
        data = resp.get("data") or {}
        url = data.get("redrUrl") or ""
        if not url:
            return None
        fname = data.get("fileName") or data.get("coName") or co_id
        size = data.get("coSize") or data.get("size") or 0
        return DownloadLink(
            fid=co_id,
            filename=fname,
            download_url=url,
            size=int(size),
        )

    # ---------- 转存 ----------

    def create_transfer_task(
        self, co_id_lst: list, catalog_id_lst: list, to_folder_id: str,
        link_id: str, account: str, authorization: str | None,
    ) -> str | None:
        task_info = {
            "contentInfoList": [f"/{cid}" for cid in co_id_lst],
            "catalogInfoList": catalog_id_lst,
            "newCatalogID": to_folder_id,
            "linkID": link_id,
            "newCatalogName": "手机图片",
            "needPassword": True,
        }
        req = {
            "createOuterLinkBatchOprTaskReq": {
                "msisdn": account,
                "ownerAccount": "",
                "taskType": 1,
                "taskInfo": task_info,
                "linkID": link_id,
                "needPassword": True,
            },
            "commonAccountInfo": {"account": account, "accountType": 1},
        }
        resp = self._share_post(
            C139Constants.TRANSFER_CREATE_URL, json.dumps(req), authorization, sign=True
        )
        code = resp.get("resultCode") or resp.get("code") or ""
        if code and code != "0":
            raise C139ApiError(resp.get("desc") or f"创建转存任务失败（{code}）")
        return (resp.get("data") or {}).get("taskID") or None

    def query_transfer_task(self, task_id: str, account: str, authorization: str | None) -> tuple[bool, dict]:
        req = {
            "queryBatchOprTaskDetailReq": {
                "taskID": task_id,
                "msisdn": account,
                "commonAccountInfo": {"account": account, "accountType": 1},
            }
        }
        resp = self._share_post(
            C139Constants.TRANSFER_QUERY_URL, json.dumps(req), authorization, sign=True
        )
        code = resp.get("resultCode") or resp.get("code") or ""
        if code and code != "0":
            raise C139ApiError(resp.get("desc") or f"查询转存结果失败（{code}）")
        data = resp.get("data") or {}
        task = data.get("batchOprTask") or {}
        done = int(task.get("progress") or 0) >= 100 and int(task.get("taskStatus") or 0) == 2
        mapping = {}
        for item in (data.get("contentList") or {}).get("idRspInfo") or []:
            if item.get("reason") == "0000":
                mapping[item.get("srcId")] = item.get("rstId")
        return done, mapping
