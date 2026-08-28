"""迅雷设备指纹管理器（对应 YunX XunleiDeviceFingerprint.kt）。

每台设备首次启动生成唯一 deviceId/peerId/devicesign，此后永久复用（进程重启不变）。
未初始化时回退到官方抓包指纹，保证不崩。
devicesign 公式：div101.{deviceId}{md5(sha1(deviceId + package + appid + appkey))}
"""
from __future__ import annotations

import hashlib
import os
import secrets
import tempfile

_PACKAGE_NAME = "com.xunlei.downloadprovider"
_APPID = "40"
_APP_KEY = "34a062aaa22f906fca4fefe9fb3a3021"


class _FpFile:
    """把设备指纹持久化到用户数据目录（跨进程重启复用）。"""

    def __init__(self, path: str):
        self.path = path

    def load(self) -> tuple[str, str, str] | None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
            if len(lines) >= 3:
                return lines[0], lines[1], lines[2]
        except OSError:
            pass
        return None

    def save(self, device_id: str, peer_id: str, device_sign: str) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(f"{device_id}\n{peer_id}\n{device_sign}\n")
        except OSError:
            pass


class XunleiDeviceFingerprint:
    """迅雷设备指纹（含 fallback 官方抓包值）。"""

    FALLBACK_DEVICE_ID = "78a70629a2b17d0b4302317ffa94807a"
    FALLBACK_PEER_ID = "92df4c42e0926ff55f1c605ebe4c3754"
    FALLBACK_DEVICE_SIGN = (
        "div101.78a70629a2b17d0b4302317ffa94807a31491e163e795b39e798ed33ae58858b"
    )

    def __init__(self, fp_path: str | None = None):
        if not fp_path:
            fp_path = os.path.join(
                tempfile.gettempdir(), "pywangpan", "xunlei_device_fp.txt"
            )
        self._file = _FpFile(fp_path)
        self._device_id = self.FALLBACK_DEVICE_ID
        self._peer_id = self.FALLBACK_PEER_ID
        self._device_sign = self.FALLBACK_DEVICE_SIGN
        self._initialized = False

    def init(self) -> None:
        if self._initialized:
            return
        saved = self._file.load()
        if saved:
            self._device_id, self._peer_id, self._device_sign = saved
        else:
            new_id = self._random_hex(32)
            new_peer = self._random_hex(32)
            new_sign = self._build_device_sign(new_id)
            self._file.save(new_id, new_peer, new_sign)
            self._device_id, self._peer_id, self._device_sign = new_id, new_peer, new_sign
        self._initialized = True

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def peer_id(self) -> str:
        return self._peer_id

    @property
    def device_sign(self) -> str:
        return self._device_sign

    def new_device_id(self) -> str:
        """生成一个新设备 ID（供需要重置时使用）。"""
        return self._random_hex(32)

    # ---------- 私有 ----------

    @staticmethod
    def _build_device_sign(device_id: str) -> str:
        base = device_id + _PACKAGE_NAME + _APPID + _APP_KEY
        sha1 = hashlib.sha1(base.encode("utf-8")).hexdigest()
        md5 = hashlib.md5(sha1.encode("utf-8")).hexdigest()
        return f"div101.{device_id}{md5}"

    @staticmethod
    def _random_hex(length: int) -> str:
        return secrets.token_hex(length // 2) if length % 2 == 0 else secrets.token_hex(length // 2 + 1)[:length]
