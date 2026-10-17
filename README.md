# 3D Print Log uploader for OrcaSlicer

An OrcaSlicer 2.5+ plugin that logs your prints to [3D Print Log](https://www.3dprintlog.com), the way the [Slic3r post-processing uploader](https://github.com/HoffmanEngineering/Slic3rPostProcessingUploader) does: the print's settings, note, print time, filament usage and thumbnail reach 3D Print Log, and the "new print" page opens with them filled in.

Work in progress: OrcaSlicer plugins are only in the 2.5.0 alpha so far, and Orca labels the plugin API experimental. See [the design](docs/superpowers/specs/2026-10-17-orcaslicer-plugin-design.md) for what is still to be checked on the alpha.

## Install

1. Download `3d-print-log.whl` from the [latest release](https://github.com/HoffmanEngineering/3d-print-log-orca-plugin/releases/latest). It is one file for Windows, macOS and Linux, with no dependencies.
2. In OrcaSlicer, open **Plugins** from the top menu, install a plugin from a file, and pick the `.whl`.
3. Make sure it is enabled in the Plugins window. A **3D Print Log** tab appears next to Prepare, Preview and Device. That is all: it works for every printer and every process preset, with nothing to add to your presets.

To update, install the new `3d-print-log.whl` the same way; it replaces the old one and keeps your settings. Keep the file name: OrcaSlicer tells plugins apart by it.

## The 3D Print Log tab

Nothing is uploaded until you ask. Slice as usual, then open the **3D Print Log** tab. It lists this session's latest slices (up to 10), newest first, with:

- the model name (or, when OrcaSlicer gives none, the project's name or its objects') and when you sliced it;
- where it went: **Not sent yet**, **Exported to** *file* (with the optional preset step below), **Sent to a print host** (OctoPrint, Moonraker, PrusaLink, Duet, ...), or **Sent to Bambu**;
- whether it is **Not logged** or **Logged**.

Click **Log to 3D Print Log** on a slice. The tab shows "Uploading…", OrcaSlicer may ask for permission (see below), and once the upload is done the 3D Print Log claim page opens in an OrcaSlicer window, where you sign in and save the print. Close the window (or click **Back to OrcaSlicer** on the page) to come back; the tab stays on its list the whole time. A logged slice keeps an **Open claim page** button that opens the window again; clicking Log on it again asks before uploading it a second time.

OrcaSlicer freezes for the few seconds the upload takes (plugins run on its main thread here); that is expected.

After each slice a notification reminds you: "Sliced *model*. Open the 3D Print Log tab to log it." After a send to a Bambu printer: "Sent *model* to the printer. Open the 3D Print Log tab to log it."

You can also log the latest slice from **Plugins**: select **Log last slice to 3D Print Log**, **Run**. That always uploads, even if the slice was logged already.

### Prefer your own browser?

Click **Open in browser** in the tab. Your browser opens a 3D Print Log page that lists the prints this OrcaSlicer uploaded and is waiting for you to claim. It is the same page every time, so OrcaSlicer asks about opening it only once per install or update. Set `open_claim_page` to `"browser"` to open it after every log instead of the claim page window. Use this if signing in inside OrcaSlicer does not work for you (Google sign-in may refuse to run inside an app).

### Optional: log the exact exported file

The tab logs the slice OrcaSlicer keeps for the plate. To log the exact file you export (after any post-processing scripts), add the uploader to a process preset: switch to **Advanced** mode, open the process settings, **Others** tab, **Slicing Pipeline Plugin**, select **3D Print Log uploader**, and save the preset. Exports with that preset then show as **Exported to** *file*, are logged under that file's name, and need no permission for the file. Print-host uploads show the host's name.

## Permission prompts

OrcaSlicer asks before a plugin reads files outside its own folder, uses the network or opens other programs. The prompt is titled **Plugin permission request** and has Yes and No. The uploader only does those things right after you click **Log** or **Open in browser**, so that is when you see the prompts:

| When | OrcaSlicer asks | How often |
|---|---|---|
| Log, first time in a session | "...requesting to read the following file:" the plate's G-code in your temp folder | About once per OrcaSlicer session: the temp folder's name changes every session. Not with the optional preset step. |
| Log, first upload | "...requesting to make an HTTP request to: https://api.3dprintlog.com/api/Slicer/uploads" | Once, then remembered. |
| Log, later sessions | "...open a network connection to: api.3dprintlog.com", or one that says the operation "does not expose a target the audit hook can display" | About once per session. Yes covers every other upload in that session. |
| Open in browser | "...requesting to run the following command: https://www.3dprintlog.com/prints/new/orca?device=..." | Once per install or update. |

Answer **Yes**. If you answer **No**, the tab says "Orca blocked the upload because permission was declined. Click Log again to be asked again." and nothing was sent. Installing or updating the plugin resets your answers.

The upload contains the start and end of the G-code (where the slicer writes its settings, note and thumbnail), the size of each object, your own preset files for the presets used (so the note can show which changes were saved in a preset), and a random id for this install (so the browser page can list your uploads). Host addresses, API keys, passwords and other credentials are removed before anything leaves your computer, and again by the server. Nothing needs an account key: the print belongs to whoever signs in on the claim page.

## Settings

**Plugins**, select **3D Print Log uploader**, **Config**:

| Key | Default | Meaning |
|---|---|---|
| `template` | `"default"` | Note template: `default` (a curated list of settings) or `full` (most settings), as the uploader's `--default` / `--full`. |
| `open_claim_page` | `"window"` | After a log: `"window"` opens the claim page in an OrcaSlicer window; `"browser"` opens your uploads page in your browser. `"tab"` (the 0.1.0 name) means `"window"`. |
| `api_base` | `"https://api.3dprintlog.com"` | Where uploads go. |
| `web_base` | `"https://www.3dprintlog.com"` | Where the claim and uploads pages are. |
| `slice_notification` | `true` | Show the reminders after a slice and a Bambu send. |
| `debug` | `false` | Write what was sent (less the G-code) and the API's answer to `debug/last-upload.json` in the plugin's storage folder. |

## Troubleshooting

The plugin writes to OrcaSlicer's Python log, lines starting with `[3D Print Log]`:

- Windows: `%APPDATA%\OrcaSlicer\log\python_*.log`
- macOS: `~/Library/Application Support/OrcaSlicer/log/python_*.log`
- Linux: `~/.config/OrcaSlicer/log/python_*.log`

| Symptom | Check |
|---|---|
| No 3D Print Log tab | The plugin is enabled in the Plugins window. With many plugin tabs, it may be in the dropdown on the last one (**Preferences > General > Plugins > Visible plugin pages**). |
| The tab is empty | Slice a plate; slices from before the plugin was enabled, or from an earlier session, are not listed. |
| "Orca blocked the upload because permission was declined" | You answered No to a prompt. Click Log again and answer Yes. |
| "This slice is gone" | OrcaSlicer removed its temp file (the plate was re-sliced or the project closed). Slice again and log it. |
| The claim page window is gone and the slice is not claimed yet | Click **Open claim page** on the slice in the tab. |
| Can't sign in inside OrcaSlicer | Use **Open in browser**, or set `open_claim_page` to `"browser"`. |
| `HTTP 429` | Too many uploads from your address in a short time; wait a minute. |
| `HTTP 413` | The G-code's settings or thumbnails are unusually large. |
| The browser does not open | You answered No to the prompt, or no default browser is set. Click Open in browser again. |

Turn on `debug` to see exactly what was sent.

## Development

Python 3.12 (Orca's embedded version), standard library only at runtime.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e . --group dev   # .venv/bin/python on macOS/Linux
.venv/Scripts/python -m pytest
.venv/Scripts/ruff check . && .venv/Scripts/ruff format --check .
.venv/Scripts/python -m build --wheel && .venv/Scripts/python scripts/check_wheel.py dist/*.whl
```

Tests run against a fake `orca` module (`tests/fake_orca/`) that follows the 2.5 bindings and Orca's threading and audit rules: events arrive in the callback context of the Orca thread that raises them; inside a UI-thread callback a file outside the allowed folders, the network or a process launch "prompts" (Yes by default, `PermissionError` when a test answers No); off the UI thread they are denied; and any plugin I/O outside an audit context (a thread the plugin started, import) fails the test. `orca_driver.Page` plays the tab, and `orca.host.ui.create_window` records the claim page windows (`orca_driver.window_message` and `close_window` play them). Only `plugin.py`, `page.py`, `claim_window.py`, `plate.py` and `script.py` import `orca`.

Releases: pushing a `v*` tag builds the wheel and attaches it to a GitHub release.

## License

MIT
