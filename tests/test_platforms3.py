"""UC / 123 / 139 平台模块测试（无网络：验证纯逻辑与加密/签名）。

测试内容：
- UC：列表项解析、视频扩展名判定
- 123：CRC32 签名格式、download-v2 Base64 解码、InfoList 解析
- 139：AES-CBC 加解密往返、mcloud-sign 格式、authorization 账号提取、Cookie 有效性
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pywangpan.pan.uc_resolver import UCResolver  # noqa: E402
from pywangpan.pan.pan123 import Pan123Api, Pan123Constants  # noqa: E402
from pywangpan.pan.models import ShareFile  # noqa: E402
from pywangpan.pan.c139 import C139Api, C139Constants  # noqa: E402


# ---------- UC ----------

def test_uc_video_ext():
    r = UCResolver(api=None)
    assert r._is_video("movie.mp4")
    assert r._is_video("a.MKV")
    assert not r._is_video("doc.pdf")


# ---------- 123 ----------

def test_pan123_sign_format():
    api = Pan123Api()
    ak, av = api.make_sign("/b/api/share/download/info", ts=1700000000)
    # auth-key 为 1-8 位小写 hex（format(x,'x') 不补前导零）
    assert 1 <= len(ak) <= 8 and all(c in "0123456789abcdef" for c in ak)
    # auth-value 形如 ts-rand-crc
    parts = av.split("-")
    assert len(parts) == 3
    assert parts[0] == "1700000000"
    assert parts[2] and all(c in "0123456789abcdef" for c in parts[2])
    # 同一 minute 的 auth-key 确定性（auth-value 含随机数非确定）
    ak2, _ = api.make_sign("/b/api/share/download/info", ts=1700000000)
    assert ak == ak2


def test_pan123_sign_table_mapping():
    """SIGN_TABLE 索引=数字值（忠实移植，表长可能不足 36）。"""
    table = Pan123Constants.SIGN_TABLE
    assert len(table) >= 2
    assert table[0] == "a"
    assert table[1] == "d"


def test_pan123_decode_download_url():
    import base64

    api = Pan123Api()
    # 形态 2：完整 URL 内嵌 params=<base64 URL-safe>（真实形态）
    real = "https://cdn.example.com/file.zip?X-Amz-Signature=abc"
    params = base64.urlsafe_b64encode(real.encode()).decode()
    wrapped = f"https://down.123865.com/download-v2?params={params}&auto_redirect=0"
    assert api._decode_download_url(wrapped) == real
    # 形态 1：整段 base64
    assert api._decode_download_url(base64.b64encode(real.encode()).decode()) == real
    # 非 base64 直链回退 None
    assert api._decode_download_url("https://cdn.example.com/a.zip") is None


def test_pan123_parse_info_list():
    api = Pan123Api()
    data = {
        "InfoList": [
            {"FileId": 1, "FileName": "a.txt", "Size": 5, "Type": 0,
             "S3KeyFlag": "s3-0", "Etag": "etag1", "StorageNode": "node1"},
            {"FileId": 2, "FileName": "dir", "Size": 0, "Type": 1,
             "S3KeyFlag": "s4-0", "Etag": "etag2"},
        ]
    }
    files = api._parse_info_list(data)
    assert files[0].fid == "1" and not files[0].isdir
    assert files[0].fid_token == "s3-0|etag1|node1"
    assert files[1].isdir
    # share_id_of（S3KeyFlag 前缀数字）
    assert api._share_id_of(files[0]) == "s3"


# ---------- 139 ----------

def test_c139_aes_roundtrip():
    api = C139Api()
    plain = '{"getOutLinkInfoReq":{"account":"","linkID":"abc"}}'
    enc = api._aes_encrypt(plain)
    # 加密体以 base64 传输，IV(16B)前置 → 解回原文
    assert api._aes_decrypt(enc) == plain
    # 每次加密 IV 随机 → 密文不同但都可解密
    enc2 = api._aes_encrypt(plain)
    assert enc != enc2
    assert api._aes_decrypt(enc2) == plain


def test_c139_cal_sign_format():
    api = C139Api()
    s = api.cal_sign('{"a":1}', "2024-01-01 00:00:00", "abcdef1234567890")
    # 32 位大写 hex（md5 -> upper）
    assert len(s) == 32 and s == s.upper()
    # 确定性
    s2 = api.cal_sign('{"a":1}', "2024-01-01 00:00:00", "abcdef1234567890")
    assert s == s2


def test_c139_account_from_authorization():
    import base64

    raw = "pc:13800138000:abc123"
    auth = "Basic " + base64.b64encode(raw.encode()).decode()
    assert C139Api.account_from_authorization(auth) == "13800138000"
    # 解码账号全量
    cookie = f"authorization={auth}"
    assert C139Constants.extract_account_full(cookie) == "13800138000"


def test_c139_cookie_validity():
    import base64

    auth = "Basic " + base64.b64encode(b"pc:13800138000:abc").decode()
    assert C139Constants.is_valid_cookie(f"authorization={auth}")
    # 路径 A：Os_SSo_Sid + RMKEY
    assert C139Constants.is_valid_cookie("Os_SSo_Sid=x; RMKEY=y; UserData=z")
    assert not C139Constants.is_valid_cookie("Os_SSo_Sid=x")
    # 无 authorization 时账号提取
    assert C139Constants.extract_account_full("Os_SSo_Sid=x; RMKEY=y") is None


def test_c139_skey():
    assert C139Constants._skey("skey=abc123; other=1") == "abc123"


if __name__ == "__main__":
    test_uc_video_ext()
    test_pan123_sign_format()
    test_pan123_sign_table_mapping()
    test_pan123_decode_download_url()
    test_pan123_parse_info_list()
    test_c139_aes_roundtrip()
    test_c139_cal_sign_format()
    test_c139_account_from_authorization()
    test_c139_cookie_validity()
    test_c139_skey()
    print("All UC/123/139 tests passed")
