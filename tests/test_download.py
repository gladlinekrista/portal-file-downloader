import http.server
import socketserver
import threading
from pathlib import Path

import requests

from downloader import download_file, safe_filename


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/bad.pdf":
            self.send_response(404); self.end_headers(); return
        self.send_response(200); self.end_headers(); self.wfile.write(b"hello")

    def log_message(self, *a):
        pass


def _server():
    srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_safe_filename():
    assert safe_filename("http://x/a/My File%20(1).pdf") == "My_File_1_.pdf"


def test_idempotent_download(tmp_path: Path):
    srv = _server()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    s = requests.Session()
    assert download_file(s, f"{base}/a.pdf", tmp_path) == "downloaded"
    assert download_file(s, f"{base}/a.pdf", tmp_path) == "skipped"      # re-run is safe
    assert (tmp_path / "a.pdf").read_bytes() == b"hello"
    srv.shutdown()


def test_failure_raises_and_leaves_no_file(tmp_path: Path):
    srv = _server()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        download_file(requests.Session(), f"{base}/bad.pdf", tmp_path)
        assert False, "should raise"
    except requests.HTTPError:
        pass
    assert not list(tmp_path.glob("*.pdf"))
    srv.shutdown()
