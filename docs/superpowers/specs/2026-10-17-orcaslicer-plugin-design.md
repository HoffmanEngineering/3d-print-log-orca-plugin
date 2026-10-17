# 3D Print Log uploader for OrcaSlicer: design

Date: 2026-10-17. Status: approved in chat, building. Revised the same day to a zero-setup design: no preset step.

## Goal

An OrcaSlicer 2.5+ plugin that does what the
[Slic3r post-processing uploader](https://github.com/HoffmanEngineering/Slic3rPostProcessingUploader) does for
OrcaSlicer users today: when a print is sent or exported, its settings, note, print time, filament usage and thumbnail
reach 3D Print Log, and the browser opens the "new print" page with them filled in.

Success means:

- An OrcaSlicer user installs one file and enables it once in the Plugins window. It then works for every printer and
  every process preset, with no per-preset step (the old uploader's installer, and the first design of this plugin,
  needed one per preset). There is no per-platform binary.
- The new-print page gets the same data the uploader sends for the same G-code (same note, time, filament, thumbnail).
- A print-host upload or a Bambu send logs its slice by itself. An export to file shows a notification whose link
  logs it with one click (or by itself, with the optional preset hook). Slicing alone never uploads.
- Each slice is uploaded at most once.
- The G-code parser stays in one place, `PrintLog.SlicerSettings`.

## What OrcaSlicer 2.5 gives a plugin

Checked against OrcaSlicer `main` at fd28f6c (2026-10-17, 2.5.0-dev) and the 2.5.0-alpha release. The plugin API is
labeled experimental and may change.

- Plugins are Python 3.12 (embedded CPython) importing the `orca` module. One entry file per plugin folder: a `.py`
  with a PEP 723 header, or a `.whl`.
- **Lifecycle events reach every enabled capability, with no preset opt-in** (`PluginManager::dispatch_lifecycle_event`
  loops over every enabled capability), synchronously on the raising thread, through `on_lifecycle_event(event, ctx)`.
  The trampoline opens the plugin's audit context for the call. The ones this plugin uses:
  - `GCodeExportFinished` (`src/libslic3r/GCode.cpp` ~2773-2989, on the slicing thread), after every slice, for every
    printer. `ctx.code` is `Ok` with `ctx.msg` = the plate's temp G-code path, or `Error` with
    `ctx.msg = "<path>\n<error>"`. `ctx.name` = `Print::get_model_name()`, which is `model_info->model_name`: the
    model name from a loaded 3MF's metadata, **empty for a project built from STLs** (`model_info` is only created when
    a 3MF carries it). `Print::export_gcode_from_previous_file` (`Print.cpp` ~5528-5575) raises the same event when a
    sliced 3MF is opened and its G-code reused.
  - `UploadStarted` / `UploadFinished` (`src/slic3r/Utils/PrintHost.cpp` ~452/472, on the print-host queue thread), for
    every print-host upload (OctoPrint, Moonraker, PrusaLink, Duet, ...). **`ctx.name` is
    `upload_data.source_path.filename()`, the upload's temp source file** (`.OrcaSlicer.upload.xxxx-xxxx-xxxx-xxxx`,
    or the plate's `.<pid>.<n>.3mf` for a `use_3mf` upload), **not the file name the print host gets**
    (`upload_path`, from the send dialog, is not in the event). `UploadFinished` adds `code` and `msg`.
  - Bambu: `PrintJobStarted` fires only from the multi-machine `TaskManager` (`ctx.name` = project name, `device_id`,
    `job_id`, `source = "task_manager"`); `SendJobStarted` is declared but never fired; `PrintStateChanged` fires
    from `DeviceManager` when a connected printer's status changes (`ctx.name` = device id, `ctx.msg` = status). A
    single-printer send from the Prepare/Preview page fires nothing.
  - No event fires on **Export G-code to file**: `BackgroundSlicingProcess::finalize_gcode` / `export_gcode` copy the
    temp G-code to the chosen path without one.
- The plate's temp G-code (`PartPlate::get_tmp_gcode_path`) is `<temp>/orcaslicer_model/<date>#<pid>#<id>/Metadata/
  .<pid>.<n>.gcode`, outside `data_dir()`. It stays in place after the slice: an export copies it
  (`finalize_gcode` runs post-processing on a `.pp` copy, then copies), a print-host upload copies it to a fresh
  `source_path` before post-processing and queuing (`prepare_upload`, `BackgroundSlicingProcess.cpp` ~977-1030), and
  a Bambu send sends it. A re-slice of the plate first deletes it and then renames the new `.tmp` into place
  (`GCode::do_export`), so the path always holds the plate's latest complete slice, or nothing for a moment. For Bambu
  printers, post-processing scripts and the slicing hook edit it in place right after `GCodeExportFinished`.
- `orca.slicing.SlicingPipelineCapabilityBase.execute(ctx)` at `Step.psGCodePostProcess` receives `gcode_path` (a
  working copy), `host`, `output_name`, `orca_version` and `config_value(key)`. It runs only when the user adds the
  capability to the process preset's `slicing_pipeline_plugin` option (Process > Others, Advanced mode). Where it
  fires (`BackgroundSlicingProcess.cpp`): export to file, every printer (`finalize_gcode`, ~857: `host="File"`,
  `output_name` = the export path); print-host upload, non-Bambu (~1003: `host` = the print host's name,
  `output_name` = the host file name); Bambu, after every slice (~292: `host="File"`, `gcode_path == output_name`).
  Orca grants the G-code's folder as a scoped allowed root for the call.
- **The audit hook** (`src/slic3r/plugin/PluginAuditManager.cpp`) is active only while `current_plugin()` (a
  `thread_local`) is set, which only Orca's trampolines do, for the length of a call into the plugin. Inside one:
  - File events are allowed without a prompt inside `data_dir()` (read/write), `resources_dir()` (read) and the
    call's scoped roots. Anything else, such as the plate's temp G-code read from an event handler, asks.
  - **`_thread.start_new_thread` is audited** ("create a thread", category Threading, persisted per plugin as
    `thread`).
  - Off the UI thread, a prompt is posted for later and the call is **denied** (PermissionError); a "Yes" is
    persisted, so the next attempt passes. On the UI thread the prompt is modal.
  - Paths containing `secret`, `cert` or `conf` are refused outright.
  Outside a callback nothing is audited: module import and `register_capabilities()` (the loader opens no audit
  context), a thread the plugin started itself, and a notification's `on_click` (called on the UI thread without a
  scope).
- `orca.host.ui.push_notification(level, text, hyper_text="", on_click=None)` (`host/PluginHostUi.cpp` ~607) marshals
  to the UI thread with `run_on_ui_blocking` and waits. Safe from the plugin's own thread (it releases the GIL while
  waiting); never from the slicing thread, which the UI thread can be blocked on. It binds `on_click` to
  `PluginAuditManager::current_plugin()`, which is **empty on a plugin-started thread**: the callback still works,
  but `close_windows_for_plugin(<key>)` cannot find it to disable it when the plugin unloads, so the plugin must make
  a late click harmless itself. `on_click()` returning `True` or `None` closes the notification. A
  `CustomNotification` with the same text as one still showing updates it instead of adding another
  (`NotificationManager::activate_existing`). `RegularNotificationLevel` fades after 10 s.
- `orca.host.plugin.storage()` (`<data_dir>/orca_plugins/plugin_data/<plugin_key>`) works only inside a plugin
  callback. There is no `data_dir()` binding; the plugin derives it from the storage path. `orca.host.app_info()`
  (the version) requires the main thread.
- A local plugin's key, install folder, config and storage follow the entry file's name (`PluginLoader.cpp`). A
  wheel's importable package comes from `Import-Name` in its `METADATA`, then `top_level.txt`, then the normalized
  `Name`; every `Requires-Dist` line is a dependency Orca installs.
- No slice-statistics or thumbnail API: everything comes from the G-code comments, as with the uploader. The G-code's
  config block carries `print_settings_id`, `filament_settings_id` and `printer_settings_id` serialized as
  `config_value()` returns them, and the header carries `; generated by OrcaSlicer <version>`.
- Plugin config is plaintext JSON (`get_default_config()` / `get_config()`), edited in the Plugins window.

## Decisions

1. **Thin plugin, parsing on the server.** The plugin sends the G-code's head and tail and the inputs the server
   cannot see; a new anonymous API endpoint parses them with `PrintLog.SlicerSettings` and stores the same
   `CuraSetting` record `/api/Cura/settings` stores. Rejected: wrapping the uploader binary (per-platform install,
   process-spawn prompt) and porting the parser to Python (two parsers to keep in step).
2. **Anonymous upload plus browser claim**, as the uploader and the Cura plugin do: the API returns a setting id, the
   browser opens `/prints/new/cura?...&settingId=`, and the signed-in user who opens it owns it. No API key, so
   nothing secret sits in the plaintext plugin config.
3. **Zero setup: lifecycle events, not the preset hook.** `GCodeExportFinished` records every slice; `UploadStarted`
   and the Bambu events upload it; a notification covers the export to file, which raises no event. The
   psGCodePostProcess hook stays as an optional extra for fully automatic exports to file. Rejected: a script that
   writes the capability into every process preset (touches the user's presets, misses presets created later).
4. **Read the slice at upload time, not at slice time.** The slicing thread only records the path; the background
   thread copies the temp G-code when an upload starts. That is the file Orca exports or sends, and a re-slice in
   between replaces both. Copying at slice time would be I/O on the slicing thread for every background re-slice,
   and from the event handler it would prompt (the temp folder is outside `data_dir()`).
5. **One long-lived background thread, started at import.** Thread creation inside a callback is audited and, off the
   UI thread, denied the first time, so callbacks never start threads: they enqueue, and the thread started at import
   (unaudited) uploads and pushes notifications. It has no audit context, so reading the temp G-code, the upload and
   opening the browser never prompt.
6. **Packaged as a pure-Python wheel** (`py3-none-any`, no dependencies), so the code can be split into small,
   testable modules and still installs as one file. Built with hatchling and `import-names = ["printlog_orca"]`
   (the distribution name does not normalize to the package), dev tools in a dependency group rather than an extra
   (an extra would be a `Requires-Dist` Orca installs), and released under the fixed name `3d-print-log.whl` so a
   new release replaces the old one.

## Components

### Plugin (`3d-print-log-orca-plugin`, Python 3.12, stdlib only)

Package `printlog_orca`:

| Module | Purpose |
|---|---|
| `plugin.py` | `@orca.plugin` class and the `PrintLogUploader` capability (`SlicingPipelineCapabilityBase`): `on_lifecycle_event`, the optional `execute` hook, `on_load` / `on_unload`, default config, the `push_notification` wrapper. With `script.py`, the only modules that import `orca`. |
| `script.py` | `LogLastSlice` script capability: logs the latest slice on demand. |
| `controller.py` | State the capabilities share, without `orca`: config parsing and defaults, the latest slice, the dedupe, building jobs. `plugin.py` and `script.py` pass it plain values. |
| `background.py` | The one long-lived daemon thread: runs uploads in order and pushes the debounced notification. Started at import. |
| `triggers.py` | Pure decisions: what a hook call or Bambu event leads to, and the upload's `outputName`. |
| `staging.py` | Private G-code copies in plugin storage (`uploads/jobs/<id>.gcode`), and clearing an earlier session's leftovers. |
| `gcode_info.py` | Slicer version, preset names and a fallback print name from the G-code text. |
| `gcode_window.py` | Reads the head and tail of a G-code file the same way `GcodeWindow.ReadFromFile` does (2,000,000 bytes each; whole file when it fits). |
| `object_bounds.py` | Port of `ObjectBoundsScanner`: per-object extrusion bounding boxes from the full toolpath, for `{{models}}`. |
| `presets.py` | Finds the user preset JSONs named by `print_settings_id`, `filament_settings_id` and `printer_settings_id` (with their `inherits` chains) under `data_dir()/user/`, and strips credential keys. Numbers are kept as written and the first of duplicate keys wins, as the uploader reads them; an ambiguous (two accounts) or unreadable preset is left out. |
| `scrub.py` | Blanks sensitive `; key = value` lines and preset keys, using the API's `SlicerSettingsScrubber` rules. |
| `payload.py` | Builds the multipart request body. |
| `client.py` | POSTs with `urllib`, maps responses to results, builds the new-print URL. |
| `worker.py` | Copies, reads, scans, builds the payload, uploads and opens the browser, on the background thread. |

#### The latest slice

`GCodeExportFinished` with `code == Ok` records the **latest slice**: the temp G-code path (`ctx.msg`), the model name
(`ctx.name`), the time, the storage folder and the config. A newer slice replaces it. **Slice identity** is that record:
each `Ok` event gets a new sequence number, so a re-slice of the same plate (same path) is a new slice. Every
upload path marks the slice it uploads (`uploaded_by`), and an automatic or one-click upload of a slice already
marked is skipped with a log line. The hook needs no matching: it always concerns the plate just sliced, which is
the latest slice. The handler does no file access, never blocks (a lock held for a few assignments) and never calls
`orca.host.ui`. An `Error` event is ignored.

#### Flow

1. The user enables the plugin once in the Plugins window. No preset step.
2. **Slice** (`GCodeExportFinished`, slicing thread): record the latest slice; if `slice_notification` is on, ask the
   background thread for a notification.
3. **Notification** (background thread, 2 s after the last slice of a burst, so background re-slicing while editing
   gives one): if that slice is still the latest and not uploaded, push "Sliced <model>." with the link
   "Log to 3D Print Log" (`RegularNotificationLevel`; "the plate" when the model name is empty). Orca updates a
   showing notification with the same text instead of stacking another. Clicking the link (UI thread, unaudited)
   queues the upload of the latest slice, once, and closes the notification. A click after the slice was uploaded
   by any path, or after `on_unload`, only logs a line.
4. **Print-host upload** (`UploadStarted`, queue thread): upload the latest slice, `host = "PrintHost"`. The event's
   name is a temp file name, so `outputName` is the model name + `.gcode`.
5. **Bambu send**: `PrintJobStarted`, or `PrintStateChanged` with `msg` in `PREPARE`, `RUNNING` (case-insensitive)
   within `bambu_send_window_minutes` (default 30) of the slice: upload the latest slice, `host = "Bambu"`.
   `outputName` is `PrintJobStarted`'s project name, else the model name, + `.gcode` (not `plate_1.gcode`). A slice
   that is never sent is never uploaded.
6. **Optional hook** (`psGCodePostProcess`, if a preset lists it): an export to file or a print-host upload copies the
   hook's file into storage at once and uploads it with the hook's `host`, `output_name`, `orca_version` and preset
   ids, and marks the latest slice uploaded, so the `UploadStarted` that follows a hooked print-host upload, and the
   notification's link, do nothing. The Bambu after-slice run does nothing (the event already recorded it). It
   never fails the slice: every error is logged and the hook still returns `success` (or `skipped` for steps it does
   not handle).
7. **Script** "Log last slice to 3D Print Log": uploads the latest slice even if it was logged before (a deliberate
   re-log, or a send no event reported).
8. **Worker** (background thread): for a latest-slice upload, copy the temp G-code into `uploads/jobs/` first (a slice
   that is gone, after a re-slice or closing the project, is logged: "slice again"). Read the windowed text, scan the
   copy for object bounds, take the preset names and slicer version from the G-code when no hook supplied them, name
   the print after the first object when there is no model name, collect user presets, scrub, build the payload and
   POST it. On success open the new-print URL with `webbrowser.open` when `open_browser` is true. The copy is deleted
   after the upload. The worker never calls `orca.host`: the storage folder and `data_dir` are read in the callback
   and handed to it.

Uploads run one at a time in the order they were queued. `on_unload` turns clicks and notifications off (the thread
stays, idle); `on_load` turns them back on.

#### Plugin config (`get_default_config()`)

```json
{
  "template": "default",
  "open_browser": true,
  "api_base": "https://api.3dprintlog.com",
  "web_base": "https://www.3dprintlog.com",
  "bambu_send_window_minutes": 30,
  "slice_notification": true,
  "debug": false
}
```

`template` is `default` or `full` (the uploader's `--default` / `--full`). `slice_notification` turns the
after-slice notification off (print-host uploads, Bambu sends, the hook and the script still log). `debug` writes the
payload metadata and the API response to plugin storage, like `--debug`.

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
  "models": [
    { "name": "3DBenchy.stl", "minX": 100.1, "maxX": 159.6, "minY": 95.2, "maxY": 125.7, "maxZ": 48.0 }
  ],
  "userPresets": [
    { "kind": "process", "name": "0.20mm Standard @MyPrinter - 3DPrintLog", "json": "{ ... }" }
  ]
}
```

`host` is informational: the hook's `host` (`File`, `OctoPrint`, ...), or `File` (notification link, script),
`PrintHost` (`UploadStarted`) or `Bambu` for an upload of the latest slice. `kind` is `process`, `filament` or
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

The new-print URL stays `{web_base}/prints/new/cura?cura_version=<slicerVersion>&plugin_version=<pluginVersion>&settingId=<id>`,
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

- Callbacks (lifecycle handlers, the hook, the script): never raise, never fail the slice, never start a thread,
  never call `orca.host.ui`, never touch files outside `data_dir()` and the hook's scoped folder. They enqueue.
- Background thread: a failing task is logged and the thread goes on. Network error, timeout (30 s), 4xx or 5xx are
  logged to Orca's Python log with the status and the API's message. No retries, no offline queue (parity with the
  uploader). A notification that cannot be shown (Orca closing) is logged.
- Permission prompts: none are expected. Callbacks only read config and storage paths and, in the hook, copy the
  hook's file (inside the folder Orca grants for that call) into storage (inside `data_dir()`). Threads are not
  started in callbacks. Reading the temp G-code, the upload and the browser run on the background thread, which Orca
  does not audit. If a later Orca audits plugin threads, the first upload would ask for `api.3dprintlog.com` and a
  "No" is logged and drops the upload.
- API: never throws on any text; a parse that fails stores an empty note with slicer `Unknown`, as the connector
  endpoint does.

## Testing

- Plugin: pytest with a fake `orca` module that follows the 2.5 bindings and Orca's threading and audit rules:
  `orca_driver.fire()` raises each lifecycle event in the callback context of the Orca thread that raises it
  (`slicing`, `upload`, `device`); inside any callback the fake audit hook refuses a thread start and file access
  outside `data_dir()` and the scoped roots with `PermissionError`; `orca.host.ui` raises inside an Orca-thread
  callback; `push_notification` records the notification, its `on_click` and the calling thread, and
  `orca_driver.click()` clicks it as the UI thread would. Covered: slice, notification from the plugin thread, click
  uploads once; slice then `UploadStarted` uploads under the model name; hook plus `UploadStarted` give one upload;
  Bambu events with model and project naming; a failed `GCodeExportFinished` is ignored; a re-slice replaces the
  slice; a click after an automatic upload or after unload does nothing; `slice_notification: false`; the debounce;
  windowing against the C# boundaries; `object_bounds` against expected values from the C# scanner on the uploader
  repo's fixture G-code; preset lookup with an `inherits` chain and scrubbing; payload shape; client status mapping
  against a local HTTP server; end to end through the real background thread and worker against a local HTTP
  server.
- Library: the in-memory options give the same result as the uploader's disk-based parse for each fixture.
- API: integration tests for 200, 400, 413 (including a gzip bomb), 415, 429, scrubbing of the text and presets, and
  that the stored `Settings` round-trips through `GET /api/Cura/settings`.
- Manual, against 2.5.0-alpha (see the spike list): install, prompts, each trigger, browser open, the stored print.

## Spike against 2.5.0-alpha (manual, once Chris has the build)

1. The wheel (`3d-print-log.whl`, the name releases use) installs from the Plugins window and both capabilities
   appear. Enabling it is the only step: no preset change. Installing it again replaces it in place and keeps the
   config.
2. No permission prompt at all through a slice, a click, a print-host upload and a Bambu send. In particular: the
   background thread started at import (the import is outside an audit context), reading the temp G-code from it,
   the upload and `webbrowser.open`. If one appears, note which (`orca_plugins/.../install_state` lists grants).
3. `GCodeExportFinished` arrives after every slice for a Bambu and a non-Bambu printer, with `code = Ok` and
   `msg` = the temp path; what `ctx.name` holds for an STL project (expected empty, so the print is named after the
   first object) and for a 3MF with a model name.
4. The notification: shows from the background thread without a deadlock or a stall of the UI; the link works and
   closes it; one per slice while background slicing re-slices; whether 10 s (`RegularNotificationLevel`) is long
   enough or `ImportantNotificationLevel` (20 s) reads better; that a click after disabling the plugin does nothing.
5. Export G-code to file, then the link: the print matches the exported file. With a post-processing script in the
   preset, note that the upload is the temp G-code, before the script ran (non-Bambu exports post-process a copy).
6. A print-host upload (OctoPrint or Moonraker) uploads once, named after the model; with the hook in the preset as
   well, still once, under the host file name. A `use_3mf` host upload also logs once.
7. Bambu single-printer send: `PrintStateChanged` arrives with `PREPARE`/`RUNNING` and the slice uploads once, named
   after the model, not `plate_1.gcode`. A multi-machine send uses the project name.
8. A Bambu printer that starts a print from its own screen within the window after a non-Bambu slice: does it log
   that slice? (Expected yes: the rule cannot tell. If it bites, require the slice to come from a Bambu printer.)
9. "Slice all" then an upload: the latest slice is the last plate sliced, which may not be the plate uploaded. Check
   how often that matters.
10. Opening a sliced 3MF raises `GCodeExportFinished` from `export_gcode_from_previous_file`: one notification, and its
    link logs the stored G-code.
11. Thumbnails, the time and filament lines in an Orca 2.5 G-code match the parser fixtures; the preset names read from
    the config block find the user presets.
12. The script's `skipped` / `success` message shows in the Plugins window after Run.
13. `on_unload` / `on_load` on disable / enable, and whether a plugin update re-imports the module (each import starts
    a new idle thread; the old one stays).

## Out of scope for v1

- Publishing to the Orca Cloud plugin hub (the author flow is not documented yet).
- API-key uploads and attaching to an existing print.
- An upstream OrcaSlicer change to fire `SendJobStarted` / `PrintJobStarted` from the single-printer send and an event
  on Export G-code to file, and to put the host file name in `UploadStarted`; worth proposing. The
  `PrintStateChanged` rule and the export notification can go once they land.
- Matching an upload to the plate it sends when several plates were sliced.
