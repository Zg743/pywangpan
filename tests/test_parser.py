"""分享链接解析器测试。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pywangpan.pan.parser import ShareLinkParser, SharePlatform  # noqa: E402


def test_quark():
    p = ShareLinkParser.parse("夸克分享：https://pan.quark.cn/s/abcdefg12 提取码：1234")
    assert p.share_id == "abcdefg12" and p.pwd == "1234"
    assert p.platform is SharePlatform.QUARK


def test_baidu():
    p = ShareLinkParser.parse("https://pan.baidu.com/s/1AbC123XYz?pwd=abcd")
    assert p.share_id == "AbC123XYz" and p.pwd == "abcd"
    assert p.platform is SharePlatform.BAIDU


def test_pan123():
    p = ShareLinkParser.parse("https://www.123pan.com/s/2785Vv-T4Ded")
    assert p.share_id == "2785Vv-T4Ded"
    assert p.platform is SharePlatform.PAN123


def test_none():
    assert ShareLinkParser.parse("hello world") is None


def test_xunlei():
    p = ShareLinkParser.parse("https://pan.xunlei.com/s/VMi_AbC-123 提取码：abcd")
    assert p.share_id == "VMi_AbC-123"
    assert p.platform is SharePlatform.XUNLEI


if __name__ == "__main__":
    test_quark()
    test_baidu()
    test_pan123()
    test_none()
    test_xunlei()
    print("All parser tests passed")
