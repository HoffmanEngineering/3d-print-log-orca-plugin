# 3D Print Log uploader for OrcaSlicer: design

Date: 2026-10-17. Status: approved in chat, building. Revised the same day twice: first to a zero-setup design with
no preset step, then to the consent design (A2 + B4, with B2 as the browser fallback): every upload starts from a
click and runs where Orca asks the user first.

## Goal

An OrcaSlicer 2.5+ plugin that does what the
[Slic3r post-processing uploader](https://github.com/HoffmanEngineering/Slic3rPostProcessingUploader) does for
OrcaSlicer users today: a print's settings, note, print time, filament usage and thumbnail reach 3D Print Log, and
the "new print" page opens with them filled in.

Success means:

- An OrcaSlicer user installs one file and enables it once in the Plugins window. It then works for every printer and
  every process preset, with no per-preset step. There is no per-platform binary.
- The new-print page gets the same data the uploader sends for the same G-code (same note, time, filament, thumbnail).
- **The user is asked, the way Orca intends.** Nothing is read, uploaded or opened outside Orca's audit hook. Every
  upload starts from the user's click in the plugin's "3D Print Log" tab (or the script), and Orca's own permission
  prompts appear at that moment. A "No" is explained, and the next click asks again.
- Each slice is uploaded once unless the user confirms logging it again.
- The G-code parser stays in one place, `PrintLog.SlicerSettings`.

## What OrcaSlicer 2.5 gives a plugin

Checked against OrcaSlicer `main` at fd28f6c (2026-10-17, 2.5.0-dev) and the 2.5.0-alpha release. The plugin API is
labeled experimental and may change. `PAM` = `src/slic3r/plugin/PluginAuditManager.cpp`.

- Plugins are Python 3.12 (embedded CPython) importing the `orca` module. One entry file per plugin folder: a `.py`
  with a PEP 723 header, or a `.whl`.
- **Lifecycle events reach every enabled capability, with no preset opt-in** (`PluginManager::dispatch_lifecycle_event`
  loops over every enabled capability), synchronously on the raising thread, through `on_lifecycle_event(event, ctx)`.
  The trampoline opens the plugin's audit context for the call. The ones this plugin uses, with their threads:
  - `GCodeExportFinished` (`src/libslic3r/GCode.cpp` ~2773-2989, **slicing thread**), after every slice, for every
    printer. `ctx.code` is `Ok` with `ctx.msg` = the plate's temp G-code path, or `Error` with
    `ctx.msg = "<path>\n<error>"`. `ctx.name` = `Print::get_model_name()`, which is `model_info->model_name`: the
    model name from a loaded 3MF's metadata, **empty for a project built from STLs**.
    `Print::export_gcode_from_previous_file` (`Print.cpp` ~5528-5575) raises the same event when a sliced 3MF is
    opened and its G-code reused.
  - `SlicingJobComplete` (`Plater.cpp:13362`, in `on_process_completed`, **UI thread**), after the slicing job,
    `ctx.name` = the model name, `ctx.code` `Ok` / `Warn` (cancelled) / `Error`.
  - `UploadStarted` / `UploadFinished` (`src/slic3r/Utils/PrintHost.cpp` ~445-472, `PrintHostJobQueue::upload_job`,
    **print-host queue thread**), for every print-host upload (OctoPrint, Moonraker, PrusaLink, Duet, ...).
    **`ctx.name` is `upload_data.source_path.filename()`, the upload's temp source file**
    (`.OrcaSlicer.upload.xxxx-xxxx-xxxx-xxxx`, or the plate's `.<pid>.<n>.3mf` for a `use_3mf` upload), **not the
    file name the print host gets**, and the host's name is not in the event either.
  - Bambu: **`PrintJobStarted` fires from `PrintJob::process`** (`src/slic3r/GUI/Jobs/PrintJob.cpp:181-190`, called
    from every send branch at 584-677: cloud, LAN, FTP, SD card view), which is the normal single-printer send from
    the Prepare/Preview page (`SelectMachine.cpp:3661, 3819`), and from the multi-machine `TaskManager`.
    `ctx.name` = the project name (`set_project_name`, `SelectMachine.cpp:3678-3700`), `ctx.device_id`,
    `ctx.source = "print_job"`. `SendJobStarted` fires from `SendJob::process` (`SendJob.cpp:171`, send to the
    printer's storage without printing). Both run on the **job worker thread** (`replace_job(*m_worker, ...)`).
  - `PrintStateChanged` (`DeviceManager.cpp:2652-2661`, from `parse_json` inside `GUI_App`'s `CallAfter`, **UI
    thread**) fires when a connected Bambu printer's status changes.
  - No event fires on **Export G-code to file**: `BackgroundSlicingProcess::finalize_gcode` / `export_gcode` copy the
    temp G-code to the chosen path without one.
- The plate's temp G-code (`PartPlate::get_tmp_gcode_path`) is `<temp>/orcaslicer_model/<date>/<time>#<pid>#<id>/
  Metadata/.<pid>.<n>.gcode`, outside `data_dir()`. It stays in place after the slice: an export copies it, a
  print-host upload copies it to a fresh `source_path`, and a Bambu send sends it. A re-slice of the plate replaces
  it (`GCode::do_export` deletes it and renames the new `.tmp` into place), so the path always holds the plate's
  latest complete slice. The folder and file names hold a timestamp, the pid and a counter, so they differ every
  session.
- `orca.slicing.SlicingPipelineCapabilityBase.execute(ctx)` at `Step.psGCodePostProcess` receives `gcode_path` (a
  working copy), `host`, `output_name`, `orca_version` and `config_value(key)`. It runs only when the user adds the
  capability to the process preset's `slicing_pipeline_plugin` option (Process > Others, Advanced mode): export to
  file (`host="File"`, `output_name` = the export path), print-host upload, non-Bambu (`host` = the print host's
  name, `output_name` = the host file name), and Bambu after every slice (`host="File"`, `gcode_path ==
  output_name`). It runs on the slicing thread, and Orca grants the G-code's folder as a **scoped allowed root** for
  the call (`SlicingPipelinePluginCapabilityTrampoline.hpp:16-24`).
- **Pages** (`orca.pages.PagesPluginCapabilityBase`, `host/PluginPages.cpp`): a capability that adds a tab to the
  main window beside Prepare/Preview/Device. `get_ui()` returns the HTML, built the first time the tab is shown;
  `on_message(data)` receives each `orca.postMessage(obj)`; `post_message(data)` sends JSON to the page's
  `orca.onMessage` handlers through `CallAfter` (so it arrives after the current callback returns, and is dropped
  before the page is built). **`get_ui` and `on_message` run on the UI thread inside the plugin's audit context**
  (`PagesPluginCapabilityTrampoline.hpp:42-59`, `ORCA_PY_AUDIT_SCOPE`). The page is loaded with
  `SetPage(html, file:///<resources>/web/)` (`WebPanel.cpp:74-80`), themed with CSS variables (`--orca-bg`,
  `--orca-fg`, `--orca-muted`, `--orca-border`, `--orca-accent`, `--orca-accent-fg`, `--orca-font`), and gets a
  `window.orca` bridge injected as a user script into **every top-level document** (`PluginPages.cpp:43-76`), so a
  remote page navigated to inside the tab gets it too. A new-window request loads in the tab itself
  (`PluginPages.cpp:132-138`). There is no CSP or navigation filter. There is no Python API to select the tab
  (only the speed-dial action does, `ActionRegistry.cpp:184`) or to navigate it.
- **The audit hook** (`PAM`) is active only while `current_plugin()` (a `thread_local`) is set, which only Orca's
  trampolines do, for the length of a call into the plugin. Inside one:
  - File events are allowed without a prompt inside `data_dir()` (read/write), `resources_dir()` (read) and the
    call's scoped roots. Anything else, such as reading the plate's temp G-code, asks: "Plugin "X" is requesting to
    read the following file: <path>".
  - Network (`urllib.Request`, `http.client.connect/send`, `socket.getaddrinfo/connect/__new__`), process
    (`os.startfile`, `subprocess.Popen`, ...) and `_thread.start_new_thread` events ask.
  - **On the UI thread the prompt is modal and inline**: a `wxMessageDialog` "Plugin permission request" with Yes /
    No only. Yes persists that **exact target** in `.install_state.json` and records the approved stdlib frames
    (the ancestor cascade), so the rest of that request passes. No raises `PermissionError`.
  - **Off the UI thread** the prompt is posted for later and the call is **denied** (`PermissionError`). Events with
    no target (`socket.__new__`, `http.client.send`) are never persisted, so **urllib can never complete from an
    audited worker thread**.
  - Grants are exact strings, not prefixes (`has_permission`, PAM:795-798). `request_permissions(fs_read=...)` takes
    exact paths only and answering No aborts the plugin load, so it cannot cover the temp G-code.
  - Every install, reinstall or update revokes the plugin's grants.
  Outside a callback nothing is audited: module import and `register_capabilities()`, **a thread the plugin started**,
  a notification's `on_click` and an HTML window's `on_message` (called without a scope, `PluginHostUi.cpp:91-144,
  617-638`). That is a documented gap, not consent.
- `orca.host.ui.push_notification(level, text, hyper_text="", on_click=None)` marshals to the UI thread with
  `run_on_ui_blocking` and waits; never call it from the slicing thread. `RegularNotificationLevel` fades after
  10 s. A `CustomNotification` with the same text as one still showing updates it.
- `orca.host.plugin.storage()` (`<data_dir>/orca_plugins/plugin_data/<plugin_key>`) works only inside a plugin
  callback. There is no `data_dir()` binding; the plugin derives it from the storage path.
- A wheel's importable package comes from `Import-Name` in its `METADATA`; every `Requires-Dist` line is a dependency
  Orca installs.
- No slice-statistics or thumbnail API: everything comes from the G-code comments, as with the uploader. The G-code's
  config block carries `print_settings_id`, `filament_settings_id` and `printer_settings_id`, and the header carries
  `; generated by OrcaSlicer <version>`.
- Plugin config is plaintext JSON (`get_default_config()` / `get_config()`), edited in the Plugins window.

## Decisions

1. **Thin plugin, parsing on the server.** The plugin sends the G-code's head and tail and the inputs the server
   cannot see; a new anonymous API endpoint parses them with `PrintLog.SlicerSettings` and stores the same
   `CuraSetting` record `/api/Cura/settings` stores. Rejected: wrapping the uploader binary (per-platform install,
   process-spawn prompt) and porting the parser to Python (two parsers to keep in step).
2. **Anonymous upload plus a claim page**, as the uploader and the Cura plugin do: the API returns a setting id,
   `/prints/new/cura?...&settingId=` opens, and the signed-in user who opens it owns it. No API key, so nothing
   secret sits in plugin storage, which every plugin can read.
3. **Consent: A2 + B4, with B2 as the browser fallback** (chosen 2026-10-17 from the consent research). The first
   design did all I/O on a thread started at import, which Orca's audit hook does not cover: the user was never
   asked, and it would break outright if Orca closed the gap (urllib can never complete from an audited worker
   thread). Instead:
   - **A2: upload only from audited UI-thread callbacks.** The tab's `on_message` and the script's `execute` read
     the temp G-code and POST. Lifecycle events on worker threads (`GCodeExportFinished`, `UploadStarted`,
     `PrintJobStarted`, `SendJobStarted`) only record. The UI-thread events (`SlicingJobComplete`,
     `PrintStateChanged`) could upload but would pop a modal prompt out of nowhere when a printer starts, so they only
     notify. The plugin starts no thread, and does no I/O in import, `on_click` or window `on_message`.
     Rejected: A1 (the old thread: a bypass), A3 (retry after the async prompt: urllib never passes off the UI
     thread), A4 (ride the session-wide cascade from a thread: relies on behavior the wiki says it does not have),
     ctypes and writing our own `.install_state.json` (bypasses).
   - **B4: a "3D Print Log" Pages tab.** It lists the recent slices with Log buttons, and after a log it navigates
     itself to the claim page inside Orca: no browser launch, so no process prompt and no setting id in a dialog.
     An iframe would not work (3dprintlog.com sends `X-Frame-Options: DENY`).
   - **B2: Open in browser**, for users who prefer their own browser or cannot sign in inside Orca (Google may refuse
     an embedded webview). It opens `{web_base}/prints/new/orca?device=<installId>`, which lists this install's
     uploads waiting to be claimed. The URL is the same every time, so Orca's "run the following command" prompt is
     answered once per install or update instead of once per print. `installId` is a random 128-bit hex value
     (`uuid4().hex`) kept in plugin storage and sent with every upload; it is a bearer value, so the page needs
     sign-in to claim and uploads expire.
   - **What users see** (from PAM, see the consent notes). First Log: "read the following file: <temp G-code>",
     then "make an HTTP request to: <api>/api/Slicer/uploads"; Yes covers the rest of that upload. Later sessions
     ask about once per session: the temp G-code's path differs every session (new pid), and the network asks again
     for the host (`http.client.connect`, then `socket.getaddrinfo`) in sessions 2 and 3, then from session 4 on
     once per session for `socket.__new__` with the cryptic "does not expose a target" text. Later uploads in the
     same session ride the cascade. Open in browser asks once per install (exact URL persisted). An update resets
     all of it. The upstream asks that would remove the per-session prompts (network grants by scheme and host,
     no-target events covered by their parent grant, an audit context for `on_click`) are worth filing.
4. **Events record, the hook keeps a copy.** `GCodeExportFinished` lists the slice (path, model name, time); the
   handler takes a lock for a few assignments and returns. A re-slice of the same plate overwrites its file, so it
   replaces that plate's pending entry. The optional preset hook (A5) copies the exported file into plugin storage
   while Orca grants its folder, so the tab logs that exact file with no file prompt.
5. **The UI thread blocks during an upload.** `on_message` is synchronous, so Orca's UI freezes while the G-code is
   read, scanned for object bounds and POSTed (30 s timeout at most). Log is two messages so the page paints
   "Uploading…" before the freeze.
6. **Packaged as a pure-Python wheel** (`py3-none-any`, no dependencies), built with hatchling and
   `import-names = ["printlog_orca"]`, dev tools in a dependency group, released as `3d-print-log.whl` so a new
   release replaces the old one.

## Components

### Plugin (`3d-print-log-orca-plugin`, Python 3.12, stdlib only)

Package `printlog_orca`:

| Module | Purpose |
|---|---|
| `plugin.py` | `@orca.plugin` class and the `PrintLogUploader` capability (`SlicingPipelineCapabilityBase`): `on_lifecycle_event` (record, notify), the optional `execute` hook, default config, the `push_notification` wrapper. |
| `page.py` | `PrintLogPage` (`PagesPluginCapabilityBase`), the "3D Print Log" tab: `get_ui`, the `on_message` protocol, `refresh()` for UI-thread callers. |
| `page_html.py` | The tab's HTML and script, inline, themed with Orca's CSS variables. |
| `script.py` | `LogLastSlice` script capability: logs the latest slice, again if it was logged. |
| `controller.py` | State the capabilities share, without `orca`: config, the recent slices, the log flow (`request_log`, `finish_log`, `open_browser`, `log_latest`), notification texts. |
| `uploader.py` | One synchronous upload: read the window, scan bounds, collect presets, scrub, POST. Returns an outcome (claim URL, API message, or "declined"). |
| `install.py` | The install id in `<storage>/install.json`. |
| `browser.py` | The device-page URL and `webbrowser.open`, with a declined prompt reported. |
| `triggers.py` | Pure decisions: what a hook call means, and the upload's `outputName`. |
| `staging.py` | The hook's copies in plugin storage (`uploads/exports/<id>.gcode`) and removing unreferenced ones. |
| `gcode_info.py` | Slicer version, preset names and a fallback print name from the G-code text. |
| `gcode_window.py` | Reads the head and tail of a G-code file the same way `GcodeWindow.ReadFromFile` does (2,000,000 bytes each; whole file when it fits). |
| `object_bounds.py` | Port of `ObjectBoundsScanner`: per-object extrusion bounding boxes from the full toolpath, for `{{models}}`. |
| `presets.py` | Finds the user preset JSONs named by the three preset ids (with their `inherits` chains) under `data_dir()/user/`, and strips credential keys. |
| `scrub.py` | Blanks sensitive `; key = value` lines and preset keys, using the API's `SlicerSettingsScrubber` rules. |
| `payload.py` | Builds the multipart request body. |
| `client.py` | POSTs with `urllib`, maps responses to results (re-raising a declined prompt as `PermissionError`), builds the claim URL. |

#### The slices list

The newest 10 slices of the session (`MAX_SLICES`), newest first. Each has an id, the temp G-code path, the model
name, the time, a **destination** and a **status**:

| Destination | Set by |
|---|---|
| Not sent yet | `GCodeExportFinished` |
| Exported to `<file name>` | the hook, `host == "File"` |
| Sent to `<host>` | the hook on a print-host upload |
| Sent to a print host | `UploadStarted` (when no hook named the host) |
| Sent to Bambu | `PrintJobStarted` / `SendJobStarted` (records the project name for `outputName`) |

Status is `not logged`, `uploading` or `logged` (with the claim URL). A re-sliced plate's pending entry is replaced;
a logged one stays (with its claim link) but can no longer be logged, since its file now holds the newer slice. An
`Error` slice is ignored.

#### The tab and its message protocol

Every page message carries a per-session token embedded in the HTML; the plugin ignores messages without it. After
the tab navigates to the claim page, that remote page (and anything it links to) also gets `window.orca` and could
post to `on_message`, but it cannot read the token.

| Page sends | Plugin does (UI thread, audited) | Replies `{type: "state", mode, slices, event}` with `event` |
|---|---|---|
| `ready` (load, reload, Back) | resets a slice left `uploading`, removes unreferenced copies | none |
| `refresh` (visible again, 5 s poll) | nothing | none |
| `log {id, relog}` | no I/O: marks the slice `uploading` | `uploading`, or `already_logged {url}`, `busy`, `missing` |
| `upload {id}` (after the page painted) | reads the G-code, presets, builds the payload with `installId`, POSTs; in browser mode opens the device page | `logged {url, mode, browser?}` or `failed {message, declined}` |
| `open_browser` | `webbrowser.open(device URL)` | `browser {ok, message, url}` |

The page renders each slice (name, time sliced, destination, status badge, message) with **Log to 3D Print Log**,
and **Open claim page** once logged. On `uploading` it waits one animation frame and 30 ms so "Uploading…" paints,
then sends `upload`. On `logged` in tab mode it sets `window.location.href` to the claim URL (top-level navigation).
On `already_logged` it shows "This slice is already logged. Log it again?" with **Log again** (`relog: true`).
A declined prompt shows "Orca blocked the upload because permission was declined. Click Log again to be asked
again."; other failures show the API's message (`HTTP 429, too many uploads ...`) or "slice again" when the temp file
is gone.

**Coming back from the claim page.** Python cannot navigate the tab or call `get_ui` again, and the remote page
cannot reach the plugin's HTML by URL. What works per the source: history Back. The webview's context menu is enabled
(`WebView.cpp:408`), so right-click > Back (or Alt+Left on WebView2) returns. On WebKit (macOS, Linux) that
navigation commits the `file:///<resources>/web/` base URL, which `WebPanel::on_load_event` answers by calling
`get_ui()` again (`WebPanel.cpp:58-61`); on Edge the `SetPage` content is restored by the webview itself
(`WebPanel.cpp:48-52`). Either way the page's script runs again and sends `ready`. The tab's footer says how. A
"Back to OrcaSlicer" button in the web app (shown when `window.orca` exists, calling `history.back()`) would make
this obvious; that is a 3d-print-log-ui follow-up.

#### Flow

1. The user enables the plugin once in the Plugins window. No preset step.
2. **Slice** (`GCodeExportFinished`, slicing thread): list it. No I/O, no UI call.
3. **Notification** (`SlicingJobComplete`, UI thread): if `slice_notification` is on, push "Sliced <model>. Open
   the 3D Print Log tab to log it." once per slice ("the plate" without a model name). Plain text: a link's
   `on_click` runs unaudited, and Python cannot bring the tab forward. The built tab is refreshed too.
4. **Sends** (`UploadStarted`, `PrintJobStarted`, `SendJobStarted`, worker threads): mark the latest slice. A Bambu
   send leaves a notice that the next `PrintStateChanged` (UI thread) shows once: "Sent <model> to the printer. Open
   the 3D Print Log tab to log it." A print-host upload has no UI-thread event after it; the tab shows it.
5. **Optional hook** (`psGCodePostProcess`, if a preset lists it, slicing thread): copy the hook's file into
   `uploads/exports/` (scoped root to plugin storage: no prompt), record `host`, `output_name`, `orca_version` and
   preset ids on the latest slice. The Bambu after-slice run does nothing. It never fails the slice.
6. **Log** (tab, UI thread): the two-message flow above. Orca's prompts appear during step two.
7. **Script** "Log last slice to 3D Print Log" (UI thread): the same upload for the latest slice, again if it was
   logged; the result message says where to claim it. It refreshes the tab.
8. **Upload** (`uploader.run`): read the hook's copy or the temp G-code (windowed text and a full scan for object
   bounds), take the preset names and slicer version from the G-code when no hook supplied them, name the print
   after the first object when there is no model or project name, collect user presets, scrub, build the payload
   and POST it. Orca's file is never modified.

#### Plugin config (`get_default_config()`)

```json
{
  "template": "default",
  "open_claim_page": "tab",
  "api_base": "https://api.3dprintlog.com",
  "web_base": "https://www.3dprintlog.com",
  "slice_notification": true,
  "debug": false
}
```

`template` is `default` or `full` (the uploader's `--default` / `--full`). `open_claim_page` is `tab` (the tab
navigates to the claim page) or `browser` (open the device page in the system browser after each log).
`slice_notification` turns the notifications off. `debug` writes the payload metadata and the API response to
`<storage>/debug/last-upload.json`, like `--debug`. Keys from earlier builds (`open_browser`,
`bambu_send_window_minutes`) are ignored.

### API (`3d-print-log-api`)

`POST /api/Slicer/uploads`, `[AllowAnonymous]`, rate-limited per client IP.

Request: `multipart/form-data` with

- `gcode`: file part, gzip of the UTF-8 windowed G-code text (head, `\n`, tail; or the whole file when it is at most
  4,000,000 bytes).
- `metadata`: JSON string (`outputName` is the file name only, split at either separator: an export path holds
  the user's account name, and the server's `Path.GetFileName` on Linux would not split at a backslash):

```json
{
  "pluginVersion": "0.1.0",
  "slicerVersion": "2.5.0-alpha",
  "outputName": "3DBenchy_PLA_1h2m.gcode",
  "host": "File",
  "template": "default",
  "installId": "0123456789abcdef0123456789abcdef",
  "models": [
    { "name": "3DBenchy.stl", "minX": 100.1, "maxX": 159.6, "minY": 95.2, "maxY": 125.7, "maxZ": 48.0 }
  ],
  "userPresets": [
    { "kind": "process", "name": "0.20mm Standard @MyPrinter - 3DPrintLog", "json": "{ ... }" }
  ]
}
```

`host` is informational: the hook's `host` (`File`, `OctoPrint`, ...), else `PrintHost` (marked by `UploadStarted`),
`Bambu` (`PrintJobStarted` / `SendJobStarted`) or `File`. `installId` is the install's 32-character lowercase hex id
(optional on the API side; the browser fallback lists the uploads that carry it). `kind` is `process`, `filament` or
`machine` (Orca's folder names). `models` and `userPresets` may be empty. Models
carry the raw centerline bounds; the library adds the line width exactly as `ModelsPlaceholder` does.

Server steps: check sizes, gunzip with a cap, scrub the text with `SlicerSettingsScrubber`, scrub each preset, parse
with the new library entry point (below, passing `outputName` as `OutputName`; a bad `template` is a 400), and store a `CuraSetting` with `Slicer` = the detected slicer, `CuraVersion` = the slicer
version, `PluginVersion` = `orca-plugin/<pluginVersion>`.

Responses:

| Status | When |
|---|---|
| 200 `{ "newSettingId": "<guid>" }` | Stored. Same DTO as `/api/Cura/settings`. |
| 400 | Missing, duplicate or malformed part; bad JSON or gzip; empty G-code text; missing `pluginVersion` or `template`; unknown `template`; a model missing a field; a preset `kind` other than `process`/`filament`/`machine`; over 1,000 models, 64 presets or a 256-character preset name. |
| 413 | Request over 8 MiB, decompressed text over 4,000,001 chars, or `metadata` over 1 MiB. |
| 415 | Not multipart. |
| 429 | Rate limit: 10 per minute and 200 per day per client address (IPv6 per /64), with `Retry-After`. Settings `Slicer:UploadRateLimitPerMinute` / `PerDay`. |

A preset whose `json` is not a JSON object is dropped, not rejected. `CuraVersion` falls back to `slicerVersion` when
the header has no version. Preset values are blanked, not removed, so presets and scrubbed text agree. The plugin caps
what it sends at 1,000 models and 64 presets. (Built in 3d-print-log-api#261.)

The claim (new-print) URL stays `{web_base}/prints/new/cura?cura_version=<slicerVersion>&plugin_version=<pluginVersion>&settingId=<id>`,
so the web UI needs no change.

### Library (`PrintLog.SlicerSettings`, in the Slic3rPostProcessingUploader repo)

A new public entry point next to `SlicerConfigParser`, for a server that has a whole (windowed) G-code file:

```csharp
public static class SlicerGcodeParser
{
    public static SlicerGcodeResult Parse(string gcode, SlicerGcodeOptions options);
}

public sealed record SlicerGcodeOptions(
    string Template,                                // "default" or "full", case-insensitive; else ArgumentException
    IReadOnlyList<ModelBounds> Models,              // replaces the toolpath scan for {{models}}
    IReadOnlyList<UserPresetFile> UserPresets)      // replaces reading slicer config roots for {{modified_settings}}
{
    public string? OutputName { get; init; }        // sets print_name and file_name, as the uploader's PrintMetadata does
    public Action<string>? DebugLog { get; init; }
}

public sealed record ModelBounds(string Name, double MinX, double MaxX, double MinY, double MaxY, double MaxZ);
public sealed record UserPresetFile(string Kind, string Name, string Json);

public sealed class SlicerGcodeResult
{
    public string Slicer { get; }
    public string Version { get; }
    public bool IsRecognized { get; }
    public string SettingsJson { get; }             // the uploader's CuraSettings, same serializer
}
```

`SettingsJson` is the uploader's `settings` object, serialized exactly as the uploader serializes it (note, print
name, estimated time, filament weight or per-slot usage, snapshot, file name), so the API stores the same JSON the
uploader would send. The print-title rules moved from the uploader's `TitleService` into the library, so both use one
implementation; they split `OutputName` on `/` and `\`. It never touches the disk or network and never throws for any
text; it does not decode `.bgcode`. Models are capped at 1,000 and names at 256 characters. The uploader keeps its
current path; internally `ParseOptions` gains the two in-memory sources and both callers share one parse.
(Shipped in Slic3rPostProcessingUploader#89.)

The public surface grows on purpose (the pinned list in `SlicerConfigParserTests.PublicSurface_*` is updated), and it
ships as `PrintLog.SlicerSettings` 0.2.0 via a `lib-v0.2.0` tag.

## Errors

- Event handlers and the hook: never raise, never fail the slice, never start a thread, never call `orca.host.ui`
  off the UI thread, never touch files outside plugin storage and the hook's scoped folder. They record.
- Tab and script: every failure becomes a message in the tab (or the script's result). A declined prompt
  (`PermissionError` from the audit hook, also when urllib wraps it in a `URLError`) says so and leaves the slice
  loggable: the next click asks again. Network errors, timeouts (30 s), 4xx and 5xx show the status and the API's
  message. A temp G-code that is gone says to slice again. No retries, no offline queue. A tab reload while a log
  waits for its second message resets the slice.
- Open in browser: a declined prompt (Windows' `webbrowser` returns False on the `OSError`) or no browser is
  explained.
- API: never throws on any text; a parse that fails stores an empty note with slicer `Unknown`, as the connector
  endpoint does.

## Testing

- Plugin: pytest with a fake `orca` module that follows the 2.5 bindings and Orca's threading and audit rules:
  `orca_driver.fire()` raises each lifecycle event in the callback context of the Orca thread that raises it
  (`slicing`, `upload`, `job`, `ui`); the fake audit hook plays the prompts (modal on the UI thread, answered Yes by
  default or No through `_audit.answer`, denied off it, exact-target grants, the per-call cascade); with strict
  tracking on, any plugin I/O outside an audit context is recorded and an autouse guard fails the test;
  `push_notification` is allowed only on the UI thread; `orca.pages` and `orca_driver.Page` play the tab
  (`get_ui`, `on_message` on the UI thread, `post_message` into the page's inbox); `webbrowser.open` is replaced by
  a stand-in that raises the Windows process audit event. Covered: a slice is listed with no I/O and notifies once
  from the UI thread; Log paints "uploading" first, uploads once and navigates to the claim URL; Log twice asks
  before logging again; a declined prompt is explained and the next click asks again; API errors and a gone slice;
  Open in browser opens one constant URL with the install id and asks once; browser mode; the install id persists
  and goes with every upload; tokenless messages (a remote page) are ignored; `UploadStarted` / Bambu events mark
  destinations; the hook's copy is logged without a file prompt; the script; end to end against a local HTTP server
  with every network event on the UI thread; and a test that moving the upload onto a plugin thread trips the guard
  (a manual mutation doing that fails 22 tests). Plus windowing, `object_bounds`, presets, payload and client status
  mapping, as before.
- Library: the in-memory options give the same result as the uploader's disk-based parse for each fixture.
- API: integration tests for 200, 400, 413 (including a gzip bomb), 415, 429, scrubbing of the text and presets, and
  that the stored `Settings` round-trips through `GET /api/Cura/settings`.
- Manual, against 2.5.0-alpha: the spike list below.

## Spike against 2.5.0-alpha (manual, once Chris has the build)

1. The wheel (`3d-print-log.whl`) installs from the Plugins window; the uploader, the script and a **3D Print Log**
   tab appear. Enabling it is the only step.
2. The tab: themed in light and dark; lists a slice right after slicing (and after a print-host upload or a Bambu
   send, within 5 s or on showing the tab).
3. **Prompts at the first Log**: the file read, then the URL; what the second, third and fourth sessions ask
   (expected: the file path each session, the host in sessions 2-3, then `socket.__new__` "does not expose a
   target"). Answer No once: the tab explains it, and Log asks again.
4. **UI freeze**: how long Orca freezes during Log for a small and a large (100 MB+) G-code (the object-bounds scan
   reads the whole file). If it is long, consider scanning in the hook, or a smaller timeout.
5. "Uploading…" paints before the freeze (the page waits a frame and 30 ms).
6. **Claim page in the tab**: the tab navigates to `/prints/new/cura?...`; Auth0 sign-in inside Orca works; does
   Google sign-in (disallowed-useragent)? Does the session persist across Orca restarts?
7. **Back to the list**: right-click > Back and Alt+Left on Windows; right-click > Back on macOS. The list comes back
   (Edge restores the `SetPage` content, WebKit re-runs `get_ui`) and shows the slice as logged.
8. Open in browser: one "run the following command: .../prints/new/orca?device=..." prompt, then none in later
   sessions; the page lists the install's uploads. `open_claim_page: "browser"` opens it after a Log.
9. `GCodeExportFinished` arrives for a Bambu and a non-Bambu printer with `code = Ok` and `msg` = the temp path; what
   `ctx.name` holds for an STL project and for a 3MF with a model name.
10. Notifications: "Sliced ..." once per slice from `SlicingJobComplete`; "Sent ... to the printer" after a Bambu
    send on the next `PrintStateChanged`; whether 10 s is long enough.
11. Bambu single-printer send: `PrintJobStarted` arrives (job worker) and marks "Sent to Bambu"; the upload is named
    after the project.
12. A print-host upload marks "Sent to a print host"; with the hook in the preset, "Sent to <host>" and the host file
    name. Export with the hook: "Exported to <file>", and the logged print matches the exported file.
13. "Slice all": each plate is listed; a re-slice replaces its entry.
14. Thumbnails, time and filament lines in an Orca 2.5 G-code match the parser fixtures; the preset names read from
    the config block find the user presets.
15. The script's messages show in the Plugins window after Run.

## Out of scope for v1

- Publishing to the Orca Cloud plugin hub (the author flow is not documented yet).
- API-key uploads and attaching to an existing print.
- Upstream OrcaSlicer asks, worth filing: network grants by scheme and host in `request_permissions`; no-target
  events covered by their parent grant; an audit context for notification `on_click` and window `on_message`; a
  scoped root for the plate G-code in `GCodeExportFinished` / `UploadStarted`; prefix `fs_read` roots; an event on
  Export G-code to file and the host file name in `UploadStarted`; a way to select or navigate a Pages tab from
  Python.
- Matching an upload to the plate it sends when several plates were sliced.
