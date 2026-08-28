"""百度网盘 API 封装（对应 YunX BaiduApi.kt / BaiduConstants.kt）。

协议细节转译自 Kotlin 源码与官方抓包，错误码用 errno（0 表示成功）判定。
"""
from __future__ import annotations

import time
from urllib.parse import quote, urlencode

import requests

from .models import DownloadLink, ShareFile


class BaiduApiError(Exception):
    """百度 API 错误，携带服务端 errmsg 与 errno。"""

    def __init__(self, message: str, errno: int = 0):
        super().__init__(message)
        self.message = message
        self.errno = errno


class BaiduConstants:
    UA_WEB = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    UA_NETDISK = "netdisk;12.24.6;piano;android-android;16;JSbridge4.4.0;jointBridge;1.1.0"
    APP_ID = "250528"
    TEMP_DIR_NAME = "YunX临时转存"

    @staticmethod
    def is_valid_cookie(cookie: str | None) -> bool:
        return bool(cookie and "BDUSS=" in cookie)


class _BaiduShareList:
    def __init__(self, title: str, share_id: str, uk: str, files: list[ShareFile]):
        self.title = title
        self.share_id = share_id
        self.uk = uk
        self.files = files


class _BaiduTransferResult:
    def __init__(self, fs_id: str, path: str):
        self.fs_id = fs_id
        self.path = path


class BaiduApi:
    """百度网盘 API（Cookie + BDUSS 认证）。"""

    def __init__(self, session: requests.Session | None = None):
        self._session = session or requests.Session()
        self._cached_bdstoken: str | None = None

    # ---------- 账号 ----------

    def fetch_nickname(self, cookie: str) -> str | None:
        result = self._template_variable(cookie, '["username"]')
        if not result:
            return None
        return result.get("username") or None

    def get_bdstoken(self, cookie: str) -> str | None:
        if self._cached_bdstoken:
            return self._cached_bdstoken
        result = self._template_variable(cookie, '["bdstoken"]')
        if not result:
            return None
        token = result.get("bdstoken") or None
        if token:
            self._cached_bdstoken = token
        return token

    def _template_variable(self, cookie: str, fields: str) -> dict | None:
        url = (
            f"https://pan.baidu.com/api/gettemplatevariable"
            f"?clienttype=0&app_id={BaiduConstants.APP_ID}&web=1&fields={quote(fields)}"
        )
        try:
            resp = self._session.get(
                url,
                headers={"Cookie": cookie, "User-Agent": BaiduConstants.UA_WEB},
                timeout=60,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            if data.get("errno") != 0:
                return None
            return data.get("result")
        except (ValueError, requests.RequestException):
            return None

    # ---------- 分享解析 ----------

    def verify_share(self, surl: str, pwd: str, cookie: str) -> str:
        """验证提取码：返回 randsk（URL 编码形式，直接作为 sekey 使用）。"""
        body = f"pwd={self._urlencode(pwd)}&vcode_str=&vcode="
        resp = self._session.post(
            f"https://pan.baidu.com/share/verify?surl={surl}",
            headers={
                "Cookie": cookie,
                "User-Agent": BaiduConstants.UA_WEB,
                "Referer": f"https://pan.baidu.com/s/{surl}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data=body,
            timeout=60,
        )
        data = self._execute_json(resp)
        self._check_errno(data, "验证提取码失败")
        sekey = data.get("randsk") or ""
        if not sekey:
            raise BaiduApiError("未返回分享密钥")
        return sekey

    def list_share(self, surl: str, sekey: str, dir_path: str, cookie: str, page: int = 1) -> _BaiduShareList:
        """列出分享文件（顶层 root=1，子目录 root=0）。"""
        is_root = not dir_path or dir_path == "/"
        root = "1" if is_root else "0"
        sekey_part = f"&sekey={sekey}" if sekey else ""
        url = (
            f"https://pan.baidu.com/rest/2.0/xpan/share?method=list"
            f"&shorturl={surl}&page={page}&num=100&root={root}&dir="
            f"{self._urlencode(dir_path if dir_path else '/')}"
            f"{sekey_part}"
        )
        auth_cookie = cookie
        if sekey and "BDCLND=" not in cookie:
            auth_cookie = f"{cookie}; BDCLND={sekey}"
        resp = self._session.get(
            url,
            headers={
                "Cookie": auth_cookie,
                "User-Agent": BaiduConstants.UA_WEB,
                "Referer": f"https://pan.baidu.com/s/{surl}",
            },
            timeout=60,
        )
        data = self._execute_json(resp)
        errno = data.get("errno")
        if errno != 0:
            if not sekey:
                raise BaiduApiError("该分享需要提取码")
            self._check_errno(data, "获取分享文件列表失败")
        files = []
        for item in data.get("list") or []:
            isdir = str(item.get("isdir")) == "1"
            path = item.get("path") or ""
            files.append(
                ShareFile(
                    fid=path if isdir else str(item.get("fs_id") or ""),
                    fname=item.get("server_filename") or "",
                    fsize=int(item.get("size") or 0),
                    isdir=isdir,
                    pdir_fid=path,
                    fid_token="",
                    modify_time=str(item.get("server_mtime") or ""),
                )
            )
        return _BaiduShareList(
            title=data.get("title") or "",
            share_id=str(data.get("share_id") or ""),
            uk=str(data.get("uk") or ""),
            files=files,
        )

    # ---------- 个人网盘 / 转存 ----------

    def create_dir(self, path: str, cookie: str) -> bool:
        bdstoken = self.get_bdstoken(cookie)
        if not bdstoken:
            return False
        body = f"path={self._urlencode(path)}&isdir=1&size&block_list=%5B%5D&method=post&dataType=json"
        resp = self._session.post(
            f"https://pan.baidu.com/api/create?a=commit&channel=chunlei&web=1"
            f"&app_id={BaiduConstants.APP_ID}&clienttype=0&bdstoken={bdstoken}",
            headers={
                "Cookie": cookie,
                "User-Agent": BaiduConstants.UA_NETDISK,
                "Referer": "https://yun.baidu.com/disk/main",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
            data=body,
            timeout=60,
        )
        try:
            data = self._execute_json(resp)
            return data.get("errno") == 0
        except Exception:
            return False

    def list_dir(self, dir_path: str, cookie: str) -> list[str]:
        url = (
            f"https://yun.baidu.com/api/list?clienttype=0&app_id={BaiduConstants.APP_ID}"
            f"&web=1&order=time&desc=1&dir={self._urlencode(dir_path)}&num=100&page=1"
        )
        try:
            resp = self._session.get(
                url,
                headers={"Cookie": cookie, "User-Agent": BaiduConstants.UA_NETDISK},
                timeout=60,
            )
            data = self._execute_json(resp)
            if data.get("errno") != 0:
                return []
            return [(item or {}).get("path") or "" for item in data.get("list") or []]
        except Exception:
            return []

    def transfer(
        self, share_id: str, uk: str, sekey: str, fs_id: str, to_dir: str, cookie: str
    ) -> _BaiduTransferResult:
        bdstoken = self.get_bdstoken(cookie)
        if not bdstoken:
            raise BaiduApiError("获取 bdstoken 失败，请重新登录")
        url = (
            f"https://pan.baidu.com/share/transfer?shareid={share_id}&from={uk}"
            f"&channel=chunlei&sekey={sekey}&ondup=newcopy&web=1"
            f"&app_id={BaiduConstants.APP_ID}&bdstoken={bdstoken}&clienttype=0"
        )
        body = f"fsidlist=%5B%22{fs_id}%22%5D&path={self._urlencode(to_dir)}"
        auth_cookie = cookie if "BDCLND=" in cookie else f"{cookie}; BDCLND={sekey}"
        resp = self._session.post(
            url,
            headers={
                "Cookie": auth_cookie,
                "User-Agent": BaiduConstants.UA_WEB,
                "Origin": "https://pan.baidu.com",
                "Referer": "https://pan.baidu.com/s/",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
            data=body,
            timeout=60,
        )
        data = self._execute_json(resp)
        self._check_errno(data, "转存失败")
        extra = data.get("extra") or {}
        first = (extra.get("list") or [None])[0] or {}
        fs_id_new = first.get("to_fs_id") or ""
        if not fs_id_new:
            raise BaiduApiError("转存失败：未返回新文件")
        path_new = first.get("to") or f"{to_dir}/"
        return _BaiduTransferResult(fs_id=str(fs_id_new), path=str(path_new))

    # ---------- 下载直链 ----------

    def locate_download(self, path: str, cookie: str) -> str:
        """获取高速下载直链（locatedownload）。优先选取 appall 明文 https 通道（encrypt=0）。"""
        time_ts = int(time.time())
        url = (
            f"https://d.pcs.baidu.com/rest/2.0/pcs/file"
            f"?method=locatedownload"
            f"&app_id={BaiduConstants.APP_ID}"
            f"&clienttype=17&ver=4.0"
            f"&ant=1&check_blue=1&es=1&esl=1&apn_id=1_-1"
            f"&freeisp=0&queryfree=0&use=1&dtype=1&eck=1&ehps=1"
            f"&err_ver=1.0&network_type=WIFI&channel=0"
            f"&path={self._urlencode(path)}"
            f"&time={time_ts}"
            f"&rand=5ed606e9da222cde0474cdf70eda884b"
            f"&devuid=0F1E9FC2E084472DA5A61C4CF4C759AF"
            f"&cuid=0F1E9FC2E084472DA5A61C4CF4C759AF"
            f"&deviceid=348642637967375013"
            f"&psign=860a071f77c860e8cea06e4e54c518f3"
            f"&version=2.2.111.34&version_app=12.24.6&vip=0"
        )
        resp = self._session.post(
            url,
            headers={
                "Cookie": cookie,
                "User-Agent": BaiduConstants.UA_NETDISK,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data="0",
            timeout=60,
        )
        data = self._execute_json(resp)
        self._check_errno(data, "获取高速下载链接失败")
        candidates = [
            item
            for item in (data.get("urls") or [])
            if (item or {}).get("url")
        ]
        direct = None
        for c in candidates:
            if c.get("encrypt", 1) == 0:
                direct = c["url"]
                break
        for c in candidates:
            if c.get("encrypt", 1) == 0 and c["url"].startswith("https"):
                direct = c["url"]
                break
        if not direct:
            for c in candidates:
                if c["url"].startswith("https"):
                    direct = c["url"]
                    break
        if not direct and candidates:
            direct = candidates[0]["url"]
        if not direct:
            raise BaiduApiError("未返回下载链接")
        return direct

    def file_metas_dlink(self, fs_id: str, cookie: str) -> str:
        bdstoken = self.get_bdstoken(cookie)
        if not bdstoken:
            raise BaiduApiError("获取 bdstoken 失败，请重新登录")
        fsids = quote(f'["{fs_id}"]')
        url = (
            f"https://pan.baidu.com/api/filemetas?dlink=1&fsids={fsids}&bdstoken={bdstoken}"
            f"&clienttype=0&app_id={BaiduConstants.APP_ID}&web=1"
        )
        resp = self._session.get(
            url,
            headers={"Cookie": cookie, "User-Agent": BaiduConstants.UA_WEB},
            timeout=60,
        )
        data = self._execute_json(resp)
        self._check_errno(data, "获取下载链接失败")
        info = data.get("info") or []
        dlink = (info[0] or {}).get("dlink") or "" if info else ""
        if not dlink:
            raise BaiduApiError("未返回下载链接")
        return dlink

    def delete_file(self, path: str, cookie: str) -> bool:
        bdstoken = self.get_bdstoken(cookie)
        if not bdstoken:
            return False
        body = f"filelist={quote(json_of([path]))}"
        resp = self._session.post(
            f"https://pan.baidu.com/api/filemanager?async=2&onnest=fail&opera=delete"
            f"&bdstoken={bdstoken}&newVerify=1&clienttype=0&app_id={BaiduConstants.APP_ID}&web=1",
            headers={
                "Cookie": cookie,
                "User-Agent": BaiduConstants.UA_NETDISK,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
            data=body,
            timeout=60,
        )
        try:
            data = self._execute_json(resp)
            return data.get("errno") == 0
        except Exception:
            return False

    def list_cloud_files(self, dir_path: str, cookie: str) -> list[ShareFile]:
        url = (
            f"https://yun.baidu.com/api/list?clienttype=0&app_id={BaiduConstants.APP_ID}"
            f"&web=1&order=time&desc=1&dir={self._urlencode(dir_path)}&num=100&page=1"
        )
        try:
            resp = self._session.get(
                url,
                headers={
                    "Cookie": cookie,
                    "User-Agent": BaiduConstants.UA_NETDISK,
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://yun.baidu.com/disk/main",
                },
                timeout=60,
            )
            data = self._execute_json(resp)
            if data.get("errno") != 0:
                return []
            files = []
            for item in data.get("list") or []:
                files.append(
                    ShareFile(
                        fid=str(item.get("fs_id") or ""),
                        fname=item.get("server_filename") or "",
                        fsize=int(item.get("size") or 0),
                        isdir=int(item.get("isdir") or 0) == 1,
                        pdir_fid=dir_path,
                        fid_token=item.get("path") or "",
                        modify_time=str(item.get("server_mtime") or ""),
                    )
                )
            return files
        except Exception:
            return []

    # ---------- 公共 ----------

    @staticmethod
    def _execute_json(resp: requests.Response) -> dict:
        try:
            return resp.json()
        except ValueError as e:
            raise BaiduApiError(f"响应解析失败: {resp.text[:200]}") from e

    @classmethod
    def _check_errno(cls, data: dict, fallback: str) -> None:
        errno = data.get("errno")
        if errno != 0:
            msg = (
                data.get("err_msg") or data.get("show_msg") or fallback
            )
            raise BaiduApiError(f"{msg}（errno={errno}）", errno=errno)

    @staticmethod
    def _urlencode(value: str) -> str:
        return quote(value, safe="")


def json_of(values: list) -> str:
    """构造 JSON 数组字符串（保留双引号，等价于 Kotlin 字符串序列化）。"""
    import json as _json

    return _json.dumps(values, ensure_ascii=False)
