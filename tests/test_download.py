"""下载引擎单元测试：用本地 HTTP 服务器模拟支持 Range 的下载，校验分片正确性。"""
import hashlib
import http.server
import os
import socketserver
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pywangpan.downloader.chunk_downloader import ChunkDownloader
from pywangpan.downloader.downloader import DownloadManager  # noqa: E402

DATA = bytes(range(256)) * 4096  # 1 MB
SHA = hashlib.md5(DATA).hexdigest()


class RangeHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        rng = self.headers.get("Range")
        if rng:
            parts = rng.replace("bytes=", "").split("-")
            s = int(parts[0])
            e = int(parts[1]) if parts[1] else len(DATA) - 1
            body = DATA[s : e + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {s}-{e}/{len(DATA)}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(len(DATA)))
            self.end_headers()
            self.wfile.write(DATA)

    def log_message(self, *args):
        pass


def test_range_download():
    with socketserver.TCPServer(("127.0.0.1", 0), RangeHandler) as srv:
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{port}/file.bin"
        tmp = tempfile.mkdtemp()
        dl = DownloadManager(ChunkDownloader(), thread_count=8, out_dir=tmp)
        dl.download(url, "test.bin", headers={})
        out = os.path.join(tmp, "test.bin")
        got = open(out, "rb").read()
        assert len(got) == len(DATA), f"length {len(got)} != {len(DATA)}"
        assert hashlib.md5(got).hexdigest() == SHA, "content mismatch"
        print(f"PASS: range download {len(got)} bytes, md5 match, threads=8")
        srv.shutdown()


if __name__ == "__main__":
    test_range_download()
    print("All tests passed")
