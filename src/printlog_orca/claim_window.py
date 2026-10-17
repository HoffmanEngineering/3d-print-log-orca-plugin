"""The claim page in an OrcaSlicer window (open_claim_page "window", the default).

The 3D Print Log tab never leaves its own page: Orca loads it with SetPage(html, base), so once
the tab has navigated to a remote page, Back lands on a blank first history entry and only a
restart brings the list back (seen in the 2.5.0-alpha e2e run). The claim page opens in a window
of its own instead: orca.host.ui.create_window (PluginHostUi.cpp), a modeless WebDialog parented
to Orca's main window.

What the source says about that window:
- create_window(html, title, width, height, on_message, on_close, style) takes HTML only, no URL.
  The WebDialog loads it with SetPage, so the HTML here sends itself on to the claim page
  (location.href). The top-level navigation from Orca's file:// base to http(s) is the one the
  tab made in the e2e run.
- It returns a live handle at once and defers building the window to a CallAfter, so it is safe
  from the tab's on_message (no nested webview creation, no wait, no deadlock).
- Orca injects its window.orca bridge (postMessage, submit, close, onMessage) as a document-start
  user script into every top-level page of that webview, the claim page included, so the web
  app's "Back to OrcaSlicer" can call window.orca.close().
- on_message and on_close run on the UI thread outside any plugin audit context: no I/O there.
  on_close fires once, for a close by the user, by window.orca.close() or by handle.close().
- Its webview comes from the same WebView::CreateWebView as the tab's, with no profile or data
  folder of its own, so it shares the tab's cookie and session store (the user stays signed in).

History Back (or an old web app's history.back()) from the claim page returns to the HTML here,
which would send the window straight back to the claim page. The HTML reports each time it is
shown; the second time, the plugin closes the window, so Back means "back to OrcaSlicer" there too.
"""

import json
import logging
import uuid

import orca

TITLE = "3D Print Log"
WIDTH = 1040
HEIGHT = 820
FAILED = (
    "Logged, but OrcaSlicer could not open the claim page window ({error}). "
    "Click Open claim page to try again, or Open in browser."
)
NOT_LOGGED = "Log this slice first, then open its claim page."

_log = logging.getLogger("printlog_orca")
_current = []  # the one open window: [handle], or empty


def render(url, token):
    """The window's HTML: says where it is going, reports that it was shown, and goes."""
    target = json.dumps(url).replace("<", "\\u003c").replace(">", "\\u003e")
    return _HTML.replace("__TARGET__", target).replace("__TOKEN__", json.dumps(token))


def open_claim_page(url, on_closed):
    """From a UI-thread callback (the tab's on_message, the script). Closes the window already
    open, if any, and opens the claim page in a new one. on_closed() runs when the window closes,
    outside any audit context. Returns {"ok", "message"}. Never raises."""
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        return {"ok": False, "message": NOT_LOGGED}
    close()
    token = uuid.uuid4().hex
    shown = [0]
    handle = []

    def on_message(data):
        # Outside any audit context: no I/O. Only this window's own HTML knows the token.
        if not (isinstance(data, dict) and data.get("token") == token):
            return
        if data.get("type") == "shown":
            shown[0] += 1
            if shown[0] > 1 and handle:
                handle[0].close()

    def on_close():
        # Outside any audit context: no I/O.
        if _current and handle and _current[0] is handle[0]:
            _current.clear()
        try:
            on_closed()
        except Exception as error:
            _log.warning("Could not update the 3D Print Log tab: %s", error)

    try:
        window = orca.host.ui.create_window(
            render(url, token),
            title=TITLE,
            width=WIDTH,
            height=HEIGHT,
            on_message=on_message,
            on_close=on_close,
        )
    except Exception as error:
        _log.warning("Could not open the claim page window: %s", error)
        return {"ok": False, "message": FAILED.format(error=error)}
    handle.append(window)
    _current[:] = [window]
    return {"ok": True, "message": ""}


def close():
    """Closes the open window, if any (its on_close still runs)."""
    if not _current:
        return
    window = _current.pop()
    try:
        if window.is_open():
            window.close()
    except Exception as error:
        _log.warning("Could not close the claim page window: %s", error)


_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>3D Print Log</title>
<style>
  body { margin: 0; padding: 24px; background: var(--orca-bg, #fff);
    color: var(--orca-muted, #6b6b6b); font-family: var(--orca-font, system-ui, sans-serif);
    font-size: 13px; }
</style>
</head>
<body>
<p>Opening the claim page on 3D Print Log…</p>
<script>
(function () {
  var TARGET = __TARGET__;
  var TOKEN = __TOKEN__;
  try { window.orca.postMessage({ type: 'shown', token: TOKEN }); } catch (e) {}
  window.location.href = TARGET;
})();
</script>
</body>
</html>
"""
