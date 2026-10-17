"""POSTs an upload to the API and builds the claim page's URL.

Runs on Orca's UI thread inside an audited callback, so Orca's own permission prompts appear while
it connects. A declined prompt is raised as PermissionError (never reported as a network failure),
so the tab can say what happened and that clicking again asks again.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlencode

from printlog_orca import __version__

TIMEOUT_SECONDS = 30
UPLOAD_PATH = "/api/Slicer/uploads"
_HINTS = {
    400: "the API rejected the upload",
    413: "the G-code or its metadata is too large",
    415: "the API rejected the upload's format",
    429: "too many uploads from this address",
}
_MAX_DETAIL = 300


@dataclass(frozen=True)
class UploadResult:
    ok: bool
    status: int | None  # None when no response arrived
    message: str = ""
    setting_id: str | None = None
    retry_after: int | None = None


def upload(api_base, body, content_type, timeout=TIMEOUT_SECONDS):
    request = urllib.request.Request(
        api_base.rstrip("/") + UPLOAD_PATH,
        data=body,
        method="POST",
        headers={
            "Content-Type": content_type,
            "Accept": "application/json",
            "User-Agent": f"3d-print-log-orca-plugin/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except PermissionError:
        raise  # Orca's audit hook: the user declined the prompt
    except urllib.error.HTTPError as error:
        with error:
            return _failure(error.code, error.read(), error.headers.get("Retry-After"))
    except urllib.error.URLError as error:
        if isinstance(error.reason, PermissionError):
            raise error.reason from None  # declined while connecting; urllib wraps OSErrors
        return UploadResult(False, None, f"could not reach {api_base}: {error.reason}")
    except TimeoutError:
        return UploadResult(
            False, None, f"no answer from {api_base} within {timeout} s (timed out)"
        )
    except OSError as error:
        return UploadResult(False, None, f"could not reach {api_base}: {error}")

    setting_id = _setting_id(raw)
    if status != 200 or setting_id is None:
        return UploadResult(False, status, f"unexpected answer (HTTP {status}): {_detail(raw)}")
    return UploadResult(True, status, "uploaded", setting_id)


def new_print_url(web_base, slicer_version, plugin_version, setting_id):
    query = urlencode(
        {"cura_version": slicer_version, "plugin_version": plugin_version, "settingId": setting_id}
    )
    return f"{web_base.rstrip('/')}/prints/new/cura?{query}"


def _failure(status, raw, retry_after_header):
    hint = _HINTS.get(status) or ("server error" if status >= 500 else "")
    retry_after = None
    if status == 429 and retry_after_header and retry_after_header.strip().isdigit():
        retry_after = int(retry_after_header.strip())
        hint += f"; try again in {retry_after} s"
    detail = _detail(raw)
    message = f"HTTP {status}" + (f", {hint}" if hint else "") + (f": {detail}" if detail else "")
    return UploadResult(False, status, message, retry_after=retry_after)


def _setting_id(raw):
    try:
        value = json.loads(raw).get("newSettingId")
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, str) and value else None


def _detail(raw):
    """The API's message: a problem+json detail or title, else the start of the body."""
    text = raw.decode("utf-8", errors="replace").strip()
    try:
        problem = json.loads(text)
    except ValueError:
        problem = None
    if isinstance(problem, dict):
        text = str(problem.get("detail") or problem.get("title") or problem.get("message") or text)
    return text[:_MAX_DETAIL]
