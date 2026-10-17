"""The HTML of the 3D Print Log tab. Inline (no file read in get_ui), themed with the CSS
variables Orca sets on every hosted page (WebViewHostDialog::theme_user_script: --orca-bg,
--orca-fg, --orca-muted, --orca-border, --orca-accent, --orca-accent-fg, --orca-font)."""

import json

_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>3D Print Log</title>
<style>
  body { margin: 0; padding: 16px 20px 24px; background: var(--orca-bg, #fff);
    color: var(--orca-fg, #262e30); font-family: var(--orca-font, system-ui, sans-serif);
    font-size: 13px; line-height: 1.45; }
  header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px;
    flex-wrap: wrap; margin-bottom: 14px; }
  h1 { font-size: 16px; margin: 0 0 2px; }
  .muted { color: var(--orca-muted, #6b6b6b); }
  .slice { border: 1px solid var(--orca-border, #dbdbdb); border-radius: 6px;
    padding: 10px 12px; margin-bottom: 8px; display: flex; gap: 12px; align-items: center;
    justify-content: space-between; flex-wrap: wrap; }
  .slice.latest { border-color: var(--orca-accent, #009688); }
  .info { min-width: 0; flex: 1 1 260px; }
  .name { font-weight: 600; overflow-wrap: anywhere; }
  .badge { display: inline-block; margin-left: 8px; padding: 0 6px; border-radius: 3px;
    font-size: 11px; font-weight: 600; border: 1px solid var(--orca-border, #dbdbdb);
    color: var(--orca-muted, #6b6b6b); vertical-align: 1px; }
  .badge.logged { color: var(--orca-accent, #009688); border-color: var(--orca-accent, #009688); }
  .badge.uploading { color: var(--orca-fg, #262e30); }
  .message { margin-top: 4px; overflow-wrap: anywhere; }
  .message.error { color: #e05545; }
  .actions { display: flex; gap: 8px; flex-wrap: wrap; }
  button.secondary { background: transparent; color: var(--orca-fg, #262e30);
    border-color: var(--orca-border, #dbdbdb); }
  button:disabled { opacity: 0.55; cursor: default; }
  #notice { margin: 0 0 12px; }
  footer { margin-top: 16px; }
</style>
</head>
<body>
<header>
  <div>
    <h1>3D Print Log</h1>
    <div class="muted">Log a slice, then claim it on 3D Print Log while signed in.</div>
  </div>
  <button id="browser" class="secondary" type="button">Open in browser</button>
</header>
<div id="notice" class="message" hidden></div>
<div id="list"></div>
<p id="empty" class="muted" hidden>No slices yet in this session. Slice a plate, then log it
here.</p>
<footer class="muted">The claim page opens in this tab. To come back to this list, right-click
the page and choose Back (or press Alt+Left). Open in browser shows the prints this OrcaSlicer
uploaded, in your own browser.</footer>
<script>
(function () {
  var TOKEN = __TOKEN__;
  var state = { mode: 'tab', slices: [] };
  var confirming = {};

  function send(type, extra) {
    var message = { type: type, token: TOKEN };
    for (var key in (extra || {})) message[key] = extra[key];
    window.orca.postMessage(message);
  }

  function make(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function button(label, onClick, secondary, disabled) {
    var node = make('button', secondary ? 'secondary' : '', label);
    node.type = 'button';
    node.disabled = !!disabled;
    node.onclick = onClick;
    return node;
  }

  function when(seconds) {
    try {
      return new Date(seconds * 1000).toLocaleString([], {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
      });
    } catch (e) { return ''; }
  }

  function notice(text, isError) {
    var node = document.getElementById('notice');
    node.textContent = text || '';
    node.className = 'message' + (isError ? ' error' : '');
    node.hidden = !text;
  }

  function busy() {
    return state.slices.some(function (s) { return s.status === 'uploading'; });
  }

  function row(s) {
    var item = make('div', 'slice' + (s.latest ? ' latest' : ''));
    var info = make('div', 'info');
    var title = make('div', 'name', s.name);
    var labels = { 'not logged': 'Not logged', 'uploading': 'Uploading…', 'logged': 'Logged' };
    title.appendChild(make('span', 'badge ' + s.status.replace(' ', '-'), labels[s.status]));
    info.appendChild(title);
    info.appendChild(make('div', 'muted', 'Sliced ' + when(s.slicedAt) + ' · ' + s.destination));
    if (s.message) info.appendChild(make('div', 'message' + (s.error ? ' error' : ''), s.message));
    var actions = make('div', 'actions');
    var uploading = s.status === 'uploading';
    if (confirming[s.id]) {
      info.appendChild(make('div', 'message', 'This slice is already logged. Log it again?'));
      actions.appendChild(button('Log again', function () {
        delete confirming[s.id];
        send('log', { id: s.id, relog: true });
      }));
      actions.appendChild(button('Cancel', function () {
        delete confirming[s.id];
        render();
      }, true));
    } else {
      actions.appendChild(button('Log to 3D Print Log', function () {
        notice('');
        send('log', { id: s.id });
      }, false, uploading || busy() || !s.canLog));
      if (s.claimUrl) {
        actions.appendChild(button('Open claim page', function () {
          window.location.href = s.claimUrl;
        }, true, uploading));
      }
    }
    item.appendChild(info);
    item.appendChild(actions);
    return item;
  }

  function render() {
    var list = document.getElementById('list');
    list.textContent = '';
    state.slices.forEach(function (s) { list.appendChild(row(s)); });
    document.getElementById('empty').hidden = state.slices.length > 0;
    document.getElementById('browser').disabled = busy();
  }

  function onEvent(event) {
    if (event.kind === 'uploading') {
      // Let the "Uploading…" state paint before the upload blocks Orca's UI thread.
      window.requestAnimationFrame(function () {
        window.setTimeout(function () { send('upload', { id: event.id }); }, 30);
      });
    } else if (event.kind === 'already_logged') {
      confirming[event.id] = true;
      render();
    } else if (event.kind === 'logged') {
      if (event.mode === 'browser') {
        notice(event.browser && event.browser.ok
          ? 'Logged. Claim it in your browser.' : (event.browser ? event.browser.message : ''),
          !(event.browser && event.browser.ok));
      } else if (event.url) {
        window.location.href = event.url;
      }
    } else if (event.kind === 'failed' && event.id === null) {
      notice(event.message, true);
    } else if (event.kind === 'missing' && event.message) {
      notice(event.message, true);
    } else if (event.kind === 'browser') {
      notice(event.ok ? 'Opened your browser.' : event.message, !event.ok);
    }
  }

  window.orca.onMessage(function (message) {
    if (!message || message.type !== 'state') return;
    state = message;
    render();
    if (message.event) onEvent(message.event);
  });

  document.getElementById('browser').onclick = function () {
    notice('');
    send('open_browser');
  };
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) send('refresh');
  });
  window.setInterval(function () {
    if (!document.hidden && !busy()) send('refresh');
  }, 5000);
  send('ready');
})();
</script>
</body>
</html>
"""


def render(token):
    return _HTML.replace("__TOKEN__", json.dumps(token))
