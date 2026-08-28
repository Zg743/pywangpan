"""Cookie 工具：合并/剥离 __puus、__pus（对应 YunX QuarkCookieUtil / AList quark_uc）。

__puus 约 3 小时过期，是下载直链签名校验的关键字段。
"""
from __future__ import annotations

# 关键跟踪字段
_TRACKED = {"__puus", "__pus"}


def _set_or_replace(cookie: str, name: str, value: str) -> str:
    parts = [p.strip() for p in cookie.split(";")]
    kv = f"{name}={value}"
    for i, p in enumerate(parts):
        if p.startswith(f"{name}="):
            parts[i] = kv
            return "; ".join(parts)
    parts.append(kv)
    return "; ".join(parts)


def merge_from_set_cookies(original: str, set_cookies: list[str]) -> str:
    """把响应 Set-Cookie 列表里的最新 __puus/__pus 合并回原 Cookie 串。"""
    cookie = original
    for sc in set_cookies:
        kv = sc.split(";")[0].strip()
        if "=" not in kv:
            continue
        name, value = kv.split("=", 1)
        if name in _TRACKED:
            cookie = _set_or_replace(cookie, name, value)
    return cookie


def without_puus(cookie: str) -> str:
    """去掉 __puus，用于触发服务端重新下发。"""
    return "; ".join(
        p.strip() for p in cookie.split(";") if not p.strip().startswith("__puus=")
    )
