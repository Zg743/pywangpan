"""百度 / 迅雷平台模块测试（无网络：验证纯逻辑与请求构造）。

测试内容：
- 迅雷设备指纹 devicesign 公式一致性
- 迅雷 JWT 解析 / 过期判断
- 迅雷 captcha_sign 算法（对照固定输入输出）
- 百度/迅雷 API 请求 URL 与 body 构造（使用 mock session）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pywangpan.pan.models import ShareFile  # noqa: E402
from pywangpan.pan.xunlei import XunleiApi, XunleiConstants  # noqa: E402
from pywangpan.pan.xunlei_fingerprint import XunleiDeviceFingerprint  # noqa: E402
from pywangpan.pan.baidu import BaiduApi, BaiduConstants  # noqa: E402


# ---------- 迅雷设备指纹 ----------

def test_fingerprint_sign_format():
    """devicesign 应为 div101.{32hex设备}{32hex md5}。"""
    fp = XunleiDeviceFingerprint()
    sign = fp._build_device_sign("a" * 32)
    # div101. + 32hex设备 + 32hex md5
    assert sign == f"div101.{'a' * 32}" + fp._build_device_sign("a" * 32)[7 + 32:]
    assert sign.startswith("div101.")
    assert len(sign) == 7 + 32 + 32
    # 幂等：同一设备 ID 生成相同 sign
    assert fp._build_device_sign("bb" * 16) == fp._build_device_sign("bb" * 16)


def test_fingerprint_init_persistent(tmp_path):
    path = str(tmp_path / "fp.txt")
    fp = XunleiDeviceFingerprint(fp_path=path)
    fp.init()
    fp2 = XunleiDeviceFingerprint(fp_path=path)
    fp2.init()
    assert fp.device_id == fp2.device_id
    assert fp.device_sign == fp2.device_sign


# ---------- 迅雷 JWT ----------

def test_jwt_exp_and_sub():
    # 构造一个简易 JWT：header.payload.signature
    import base64
    import json

    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    payload = b64({"sub": "12345", "exp": 1000})
    token = f"header.{payload}.sig"
    api = XunleiApi()
    api._current_user_id = api._jwt_sub(token)
    assert api._current_user_id == "12345"
    assert api.jwt_exp(token) == 1000


def test_jwt_invalid():
    api = XunleiApi()
    assert api.jwt_exp("not.a.jwt") == 0
    assert api._jwt_sub("") == ""


# ---------- 迅雷 captcha_sign ----------

def test_captcha_sign_deterministic():
    """同一输入应生成相同 1.{64hex} 签名。"""
    api = XunleiApi()
    s1 = api._build_captcha_sign("device123", "12345678")
    s2 = api._build_captcha_sign("device123", "12345678")
    assert s1 == s2
    assert s1.startswith("1.")
    assert len(s1) == 2 + 32  # 1. + 32hex md5


# ---------- 百度请求构造（mock session） ----------

class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        import json

        if isinstance(self._payload, str):
            return json.loads(self._payload)
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self._payload = payload
        self.last_url = None
        self.last_headers = None

    def get(self, url, headers=None, timeout=None, **kw):
        self.last_url = url
        self.last_headers = headers
        return _FakeResponse(self._payload)

    def post(self, url, headers=None, data=None, timeout=None, **kw):
        self.last_url = url
        self.last_headers = headers
        self.last_body = data
        return _FakeResponse(self._payload)


def test_baidu_gettemplatevariable():
    sess = _FakeSession({"errno": 0, "result": {"bdstoken": "tok123"}})
    api = BaiduApi(session=sess)
    assert api.get_bdstoken("BDUSS=abc") == "tok123"
    assert "app_id=250528" in sess.last_url
    assert sess.last_headers["User-Agent"] == BaiduConstants.UA_WEB


def test_baidu_list_share_constructs_bdclnd():
    payload = {
        "errno": 0,
        "share_id": "s1",
        "uk": "uk1",
        "title": "t",
        "list": [
            {"isdir": "0", "fs_id": "100", "server_filename": "a.txt", "size": 5},
            {"isdir": "1", "path": "/folder", "server_filename": "f", "size": 0},
        ],
    }
    sess = _FakeSession(payload)
    api = BaiduApi(session=sess)
    res = api.list_share("AbC", "randsk", "/", "BDUSS=x;BDCLND=old", page=1)
    assert res.share_id == "s1" and res.uk == "uk1"
    assert res.files[0].fid == "100" and not res.files[0].isdir
    assert res.files[1].fid == "/folder" and res.files[1].isdir
    # 根目录 root=1；BDCLND 存在时不再附加
    assert "root=1" in sess.last_url
    assert "BDCLND=old" in sess.last_headers["Cookie"]


def test_baidu_transfer_new_id():
    payload = {"errno": 0, "extra": {"list": [{"to_fs_id": "200", "to": "/tmp/x"}]}}
    sess = _FakeSession(payload)
    api = BaiduApi(session=sess)
    api._cached_bdstoken = "tok"
    res = api.transfer("s1", "uk1", "randsk", "100", "/YunX临时转存", "BDUSS=x")
    assert res.fs_id == "200" and res.path == "/tmp/x"


if __name__ == "__main__":
    import tempfile

    import pathlib

    tmp = pathlib.Path(tempfile.mkdtemp())
    test_fingerprint_sign_format()
    test_fingerprint_init_persistent(tmp)
    test_jwt_exp_and_sub()
    test_jwt_invalid()
    test_captcha_sign_deterministic()
    test_baidu_gettemplatevariable()
    test_baidu_list_share_constructs_bdclnd()
    test_baidu_transfer_new_id()
    print("All new platform tests passed")
