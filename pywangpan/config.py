"""本地登录态/配置存储（简化密钥环：明文 JSON，默认仅本机权限）。

仅用于便捷：首次输入 Cookie/Token 后保存，后续复用，避免反复粘贴。
敏感信息以明文存放，请勿在共享机器上使用，并注意本文件权限。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("PYWANGPAN_CONFIG_DIR", "~/.pywangpan")).expanduser()
CONFIG_PATH = CONFIG_DIR / "config.json"
CHMOD = 0o600


class ConfigStore:
    def __init__(self, path: Path = CONFIG_PATH):
        self.path = Path(path)
        self._data: dict = {}

    def load(self) -> None:
        try:
            if self.path.exists():
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            os.chmod(self.path, CHMOD)
        except OSError:
            pass

    # ---- 通用键值 ----

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        if value is None:
            self._data.pop(key, None)
        else:
            self._data[key] = value
        self.save()

    # ---- 平台凭据 ----

    def get_cookie(self, platform: str) -> str | None:
        return self._data.get("cookies", {}).get(platform)

    def set_cookie(self, platform: str, cookie: str) -> None:
        self._data.setdefault("cookies", {})[platform] = cookie
        self.save()

    def get_token(self, platform: str) -> dict | None:
        return self._data.get("tokens", {}).get(platform)

    def set_token(self, platform: str, data: dict) -> None:
        self._data.setdefault("tokens", {})[platform] = data
        self.save()
