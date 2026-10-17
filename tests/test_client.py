"""Status mapping against a real HTTP server on localhost."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from printlog_orca import client


class Api:
    """Answers POST /api/Slicer/uploads with whatever the test set; records requests."""

    def __init__(self):
        self.status = 200
        self.body = b'{"newSettingId":"6f9619ff-8b86-d011-b42d-00c04fc964ff"}'
        self.headers = {"Content-Type": "application/json"}
        self.delay = None
        self.requests = []


@pytest.fixture
def api():
    state = Api()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server's name
            length = int(self.headers.get("Content-Length", 0))
            state.requests.append((self.path, dict(self.headers), self.rfile.read(length)))
            if state.delay:
                state.delay.wait(5)
            self.send_response(state.status)
            for key, value in state.headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(state.body)))
            self.end_headers()
            self.wfile.write(state.body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    state.base = f"http://127.0.0.1:{server.server_address[1]}"
    yield state
    if state.delay:
        state.delay.set()
    server.shutdown()
    server.server_close()


def upload(api, timeout=5):
    return client.upload(api.base, b"--b--\r\n", "multipart/form-data; boundary=b", timeout=timeout)


def test_success_returns_the_setting_id_and_posts_the_body(api):
    result = upload(api)

    assert result.ok
    assert result.status == 200
    assert result.setting_id == "6f9619ff-8b86-d011-b42d-00c04fc964ff"
    path, headers, body = api.requests[0]
    assert path == "/api/Slicer/uploads"
    assert headers["Content-Type"] == "multipart/form-data; boundary=b"
    assert headers["User-Agent"].startswith("3d-print-log-orca-plugin/")
    assert body == b"--b--\r\n"


def test_a_trailing_slash_on_the_base_is_fine(api):
    assert client.upload(api.base + "/", b"", "multipart/form-data; boundary=b").ok
    assert api.requests[0][0] == "/api/Slicer/uploads"


@pytest.mark.parametrize("body", [b"{}", b"not json", b'{"newSettingId": 5}', b"[]"])
def test_a_200_without_a_setting_id_is_a_failure(api, body):
    api.body = body

    result = upload(api)

    assert not result.ok
    assert result.status == 200
    assert "unexpected" in result.message


@pytest.mark.parametrize(
    ("status", "hint"),
    [
        (400, "rejected"),
        (413, "too large"),
        (415, "rejected"),
        (429, "too many"),
        (500, "server error"),
        (503, "server error"),
        (404, "HTTP 404"),
    ],
)
def test_error_statuses_map_to_a_message(api, status, hint):
    api.status = status
    api.body = b'{"title":"Bad Request","detail":"metadata is not JSON"}'
    api.headers = {"Content-Type": "application/problem+json"}

    result = upload(api)

    assert not result.ok
    assert result.status == status
    assert hint in result.message
    assert "metadata is not JSON" in result.message


def test_a_429_reports_retry_after(api):
    api.status = 429
    api.body = b""
    api.headers = {"Retry-After": "42"}

    result = upload(api)

    assert result.retry_after == 42
    assert "42 s" in result.message


def test_a_plain_text_error_body_is_quoted_and_cut_short(api):
    api.status = 400
    api.body = b"x" * 5000
    api.headers = {"Content-Type": "text/plain"}

    result = upload(api)

    assert len(result.message) < 600


def test_connection_refused_is_a_failure_without_a_status():
    result = client.upload("http://127.0.0.1:1", b"", "multipart/form-data; boundary=b", timeout=5)

    assert not result.ok
    assert result.status is None
    assert result.message


def test_a_timeout_is_a_failure_without_a_status(api):
    api.delay = threading.Event()

    result = upload(api, timeout=0.5)

    assert not result.ok
    assert result.status is None
    assert "timed out" in result.message


def test_the_default_timeout_is_30_seconds():
    assert client.TIMEOUT_SECONDS == 30


def test_new_print_url():
    url = client.new_print_url(
        "https://www.3dprintlog.com/", "2.5.0-alpha", "0.1.0", "6f9619ff-8b86-d011"
    )

    parsed = urlsplit(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://www.3dprintlog.com/prints/new/cura"
    )
    assert parse_qs(parsed.query) == {
        "cura_version": ["2.5.0-alpha"],
        "plugin_version": ["0.1.0"],
        "settingId": ["6f9619ff-8b86-d011"],
    }
    assert list(parse_qs(parsed.query)) == ["cura_version", "plugin_version", "settingId"]


def test_response_body_is_valid_json_in_the_fixture():
    assert json.loads(Api().body)["newSettingId"]
