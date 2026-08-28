"""分享链接解析器：从链接/文案中提取 share_id、提取码与平台。

移植自 YunX 项目的 ShareLinkParser.kt。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class SharePlatform(str, Enum):
    QUARK = "quark"
    UC = "uc"
    XUNLEI = "xunlei"
    BAIDU = "baidu"
    C139 = "c139"
    PAN123 = "pan123"


@dataclass
class ParsedShare:
    share_id: str
    pwd: str | None
    platform: SharePlatform


class ShareLinkParser:
    _url_regex = re.compile(r"https?://[^\s]+")
    _quark_id = re.compile(r"pan\.quark\.cn/s/([A-Za-z0-9]+)", re.IGNORECASE)
    _uc_id = re.compile(r"drive\.uc\.cn/s/([A-Za-z0-9]+)", re.IGNORECASE)
    _xunlei_id = re.compile(r"pan\.xunlei\.com/s/([A-Za-z0-9_-]+)", re.IGNORECASE)
    _baidu_id = re.compile(r"pan\.baidu\.com/s/(1[A-Za-z0-9_-]+)", re.IGNORECASE)
    _c139_id = re.compile(r"yun\.139\.com/shareweb/.*?/w/i/([A-Za-z0-9_-]+)", re.IGNORECASE)
    _pan123_id = re.compile(r"123(?:865|pan)\.(?:com|cn)/s/([A-Za-z0-9]+-[A-Za-z0-9]+)", re.IGNORECASE)
    _pan123_sub = re.compile(r"share\.123pan\.cn/123pan/([A-Za-z0-9-]+)", re.IGNORECASE)
    _pan123_srr = re.compile(r"api/srr\?sk=([A-Za-z0-9-]+)", re.IGNORECASE)
    _pwd_url = re.compile(r"[?&]pwd=([A-Za-z0-9]+)")
    _pwd_text = re.compile(r"(?:提取码|访问码|密码)[：:]\s*([A-Za-z0-9]{4,8})")

    @classmethod
    def _pwd(cls, url: str, text: str) -> str | None:
        m = cls._pwd_url.search(url)
        if m:
            return m.group(1)
        m = cls._pwd_text.search(text)
        return m.group(1) if m else None

    @classmethod
    def parse(cls, text: str) -> ParsedShare | None:
        """从文本中解析分享链接，返回 ParsedShare 或 None。"""
        m = cls._url_regex.search(text.strip())
        if not m:
            return None
        url = m.group(0).rstrip("。，,；;)]}\"'")
        handlers = [
            (cls._pan123_id, SharePlatform.PAN123),
            (cls._pan123_sub, SharePlatform.PAN123),
            (cls._pan123_srr, SharePlatform.PAN123),
            (cls._c139_id, SharePlatform.C139),
            (cls._baidu_id, SharePlatform.BAIDU),
            (cls._xunlei_id, SharePlatform.XUNLEI),
            (cls._uc_id, SharePlatform.UC),
            (cls._quark_id, SharePlatform.QUARK),
        ]
        for regex, platform in handlers:
            m = regex.search(url)
            if m:
                sid = m.group(1)
                if platform is SharePlatform.BAIDU:
                    # 百度 surl 不包含开头 "1"，verify/list 用其后的部分
                    sid = sid.removeprefix("1")
                return ParsedShare(share_id=sid, pwd=cls._pwd(url, text), platform=platform)
        return None
