"""The system-browser fallback: this install's page of uploads waiting to be claimed.

webbrowser.open is audited by Orca as a process launch whose target is the URL (os.startfile on
Windows, xdg-open on Linux; osascript, with the URL on stdin, on macOS). The device URL is the
same for every upload from this install, so the "run the following command" prompt is answered
once per install or update instead of once per print. Called only from audited UI-thread
callbacks, so that prompt is modal and comes right after the user's click.
"""

import logging
import webbrowser
from urllib.parse import urlencode

DECLINED = (
    "Could not open the browser: Orca's permission was declined, or there is no default browser. "
    "Click Open in browser again to be asked again."
)

_log = logging.getLogger("printlog_orca")


def device_url(web_base, install_id):
    return f"{web_base.rstrip('/')}/prints/new/orca?{urlencode({'device': install_id})}"


def open_page(url):
    """(ok, message). Never raises."""
    try:
        opened = webbrowser.open(url)
    except PermissionError as error:
        _log.warning("Orca declined opening the browser: %s", error)
        return False, DECLINED
    except Exception as error:
        _log.warning("Could not open the browser at %s: %s", url, error)
        return False, f"Could not open the browser: {error}"
    if not opened:
        # Windows' webbrowser swallows the OSError a declined prompt raises and returns False.
        return False, DECLINED
    return True, "Opened your browser."
