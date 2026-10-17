# 3D Print Log uploader for OrcaSlicer

An OrcaSlicer 2.5+ plugin that sends each print you send or export to [3D Print Log](https://www.3dprintlog.com), the way the [Slic3r post-processing uploader](https://github.com/HoffmanEngineering/Slic3rPostProcessingUploader) does: the print's settings, note, print time, filament usage and thumbnail reach 3D Print Log, and your browser opens the "new print" page with them filled in.

Work in progress: OrcaSlicer plugins are only in the 2.5.0 alpha so far, and Orca labels the plugin API experimental. See [the design](docs/superpowers/specs/2026-10-17-orcaslicer-plugin-design.md) for what is still to be checked on the alpha.

## Install

1. Download `3d-print-log.whl` from the [latest release](https://github.com/HoffmanEngineering/3d-print-log-orca-plugin/releases/latest). It is one file for Windows, macOS and Linux, with no dependencies.
2. In OrcaSlicer, open **Plugins** from the top menu, install a plugin from a file, and pick the `.whl`.
3. Make sure it is enabled in the Plugins window. That is all: it works for every printer and every process preset, with nothing to add to your presets.

To update, install the new `3d-print-log.whl` the same way; it replaces the old one and keeps your settings. Keep the file name: OrcaSlicer tells plugins apart by it.

## What uploads, and when

| You do this | What happens |
|---|---|
| Slice a plate | Nothing is uploaded. A notification shows: "Sliced *model*. **Log to 3D Print Log**". |
| Click **Log to 3D Print Log** | The slice is uploaded. Use it after **Export G-code** to a file: Orca raises no event for an export. |
| Upload to a print host (OctoPrint, Moonraker, PrusaLink, Duet, ...) | The slice is uploaded by itself. |
| Send to a Bambu printer | The slice is uploaded by itself when the printer reports that it is preparing or printing, within 30 minutes of the slice (`bambu_send_window_minutes`), or when a multi-machine send starts. |
| Anything not picked up, or to log a slice again | **Plugins**, select **Log last slice to 3D Print Log**, **Run**. |

Each slice is uploaded once, by whichever comes first; after that the notification's link does nothing. Slicing alone never uploads, and a slice you never send or log is never uploaded. The print is named after the model (or the project for a multi-machine Bambu send, or the first object when the project has no model name), since Orca does not tell plugins the file name a print host or a Bambu printer gets.

### Optional: log exports to a file automatically

To have exports to a file uploaded without clicking the notification, add the uploader to a process preset: switch to **Advanced** mode, open the process settings, **Others** tab, **Slicing Pipeline Plugin**, select **3D Print Log uploader**, and save the preset. Exports with that preset then upload by themselves under the exported file's name, and print-host uploads use the host's file name. A slice is still uploaded only once.

The upload contains the start and end of the G-code (where the slicer writes its settings, note and thumbnail), the size of each object, and your own preset files for the presets used, so the note can show which changes were saved in a preset. Host addresses, API keys, passwords and other credentials are removed before anything leaves your computer, and again by the server. Nothing needs an account key: the print belongs to whoever is signed in when the browser page opens.

## Settings

**Plugins**, select **3D Print Log uploader**, **Config**:

| Key | Default | Meaning |
|---|---|---|
| `template` | `"default"` | Note template: `default` (a curated list of settings) or `full` (most settings), as the uploader's `--default` / `--full`. |
| `open_browser` | `true` | Open the new-print page after each upload. |
| `api_base` | `"https://api.3dprintlog.com"` | Where uploads go. |
| `web_base` | `"https://www.3dprintlog.com"` | Where the browser goes. |
| `bambu_send_window_minutes` | `30` | How long after a Bambu slice a printer starting counts as that slice's send. |
| `slice_notification` | `true` | Show the "Log to 3D Print Log" notification after each slice. Print-host uploads and Bambu sends are logged either way. |
| `debug` | `false` | Write what was sent (less the G-code) and the API's answer to `debug/last-upload.json` in the plugin's storage folder. |

## Permission prompts

OrcaSlicer asks before a plugin uses the network, files outside its own folders, threads or other programs, and remembers your answer. The uploader is built so that none of that comes up: it starts its one background thread when OrcaSlicer loads it, and reads the G-code, uploads and opens the browser from that thread, which OrcaSlicer does not check. If Orca asks anyway (about `api.3dprintlog.com`, a thread, a file or opening the browser), allow it; answering no drops that upload (it is logged).

## Troubleshooting

The plugin writes to OrcaSlicer's Python log, lines starting with `[3D Print Log]`:

- Windows: `%APPDATA%\OrcaSlicer\log\python_*.log`
- macOS: `~/Library/Application Support/OrcaSlicer/log/python_*.log`
- Linux: `~/.config/OrcaSlicer/log/python_*.log`

| Symptom | Check |
|---|---|
| Nothing uploads at all | The plugin is enabled in the Plugins window. Look for `[3D Print Log]` lines in the log after a slice. |
| No notification after a slice | `slice_notification` is `true`. A notification fades after about 10 seconds; the script logs the last slice too. |
| A Bambu send did not upload | The printer was connected in Orca when it started, within the window. Otherwise run **Log last slice to 3D Print Log**. |
| "The slice is gone" in the log | Orca removed its temp file (the plate was re-sliced or the project closed). Slice again and log it. |
| `HTTP 429` in the log | Too many uploads from your address in a short time; wait a minute. |
| `HTTP 413` | The G-code's settings or thumbnails are unusually large. |

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

Tests run against a fake `orca` module (`tests/fake_orca/`) that follows the 2.5 bindings and Orca's threading and audit rules: events arrive in the callback context of the Orca thread that raises them, a thread start or a file outside the allowed folders inside a callback raises `PermissionError`, and `orca.host.ui` raises inside an Orca-thread callback. Only `plugin.py` and `script.py` import `orca`.

Releases: pushing a `v*` tag builds the wheel and attaches it to a GitHub release.

## License

MIT
