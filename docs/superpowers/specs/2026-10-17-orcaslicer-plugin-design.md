# 3D Print Log uploader for OrcaSlicer: design

Date: 2026-10-17. Status: approved in chat, building.

## Goal

An OrcaSlicer 2.5+ plugin that does what the
[Slic3r post-processing uploader](https://github.com/HoffmanEngineering/Slic3rPostProcessingUploader) does for
OrcaSlicer users today: when a print is exported or sent, its settings, note, print time, filament usage and thumbnail
reach 3D Print Log, and the browser opens the "new print" page with them filled in.

Success means:

- An OrcaSlicer user installs one file and adds the plugin to a process preset; there is no per-platform binary.
- The new-print page gets the same data the uploader sends for the same G-code (same note, time, filament, thumbnail).
- One export or one send gives exactly one upload. Slicing alone never uploads.
- The G-code parser stays in one place, `PrintLog.SlicerSettings`.

## What OrcaSlicer 2.5 gives a plugin

Checked against OrcaSlicer `main` at fd28f6c (2026-10-17, 2.5.0-dev) and the 2.5.0-alpha release. The plugin API is
labeled experimental and may change.

- Plugins are Python 3.12 (embedded CPython) importing the `orca` module. One entry file per plugin folder: a `.py`
  with a PEP 723 header, or a `.whl`.
- `orca.slicing.SlicingPipelineCapabilityBase.execute(ctx)` at `Step.psGCodePostProcess` receives `gcode_path` (a
  working copy), `host`, `output_name`, `orca_version` and `config_value(key)`. It runs only when the user adds the
  capability to the process preset's `slicing_pipeline_plugin` option (Process > Others, Advanced mode).
- Where it fires (`src/slic3r/GUI/BackgroundSlicingProcess.cpp`):
  - Export to file, every printer (`finalize_gcode`, line 857): `host="File"`, `output_name` = the export path.
  - Upload to a print host, non-Bambu printers (line 1003): `host` = the print host's name (`OctoPrint`, ...).
  - Bambu (BBL) printers, right after every slice (line 292): `host="File"`, and `gcode_path == output_name` = the
    plate's temp G-code. A Bambu send (cloud or LAN) does not fire the hook again; it sends this file.
- Lifecycle events reach every enabled capability through `on_lifecycle_event(event, ctx)`, synchronously on the
  raising thread. For Bambu sends: `PrintJobStarted` fires only from the multi-machine `TaskManager`;
  `SendJobStarted` is declared but never fired; `PrintStateChanged` fires when a connected printer's status changes
  (`ctx.name` = device id, `ctx.msg` = status). A single-printer send from the Prepare/Preview page fires nothing.
- No slice-statistics or thumbnail API: everything comes from the G-code comments, as with the uploader.
- Network, file and process calls go through an audit hook that asks the user once per target and remembers the
  answer (reset on plugin update). No prompt for `data_dir()` and the plugin's own storage. Paths containing
  `secret`, `cert` or `conf` are refused.
- Plugin config is plaintext JSON (`get_default_config()` / `get_config()`), edited in the Plugins window.
- `orca.host.ui.*` must not be called from the slicing thread.

## Decisions

1. **Thin plugin, parsing on the server.** The plugin sends the G-code's head and tail and the inputs the server
   cannot see; a new anonymous API endpoint parses them with `PrintLog.SlicerSettings` and stores the same
   `CuraSetting` record `/api/Cura/settings` stores. Rejected: wrapping the uploader binary (per-platform install,
   process-spawn prompt) and porting the parser to Python (two parsers to keep in step).
2. **Anonymous upload plus browser claim**, as the uploader and the Cura plugin do: the API returns a setting id, the
   browser opens `/prints/new/cura?...&settingId=`, and the signed-in user who opens it owns it. No API key, so
   nothing secret sits in the plaintext plugin config.
3. **Upload on export or send, never on slice alone.** Bambu's after-slice run stages the file; the send uploads it.
4. **Packaged as a pure-Python wheel** (`py3-none-any`, no dependencies), so the code can be split into small,
   testable modules and still installs as one file.

## Components

### Plugin (`3d-print-log-orca-plugin`, Python 3.12, stdlib only)

Package `printlog_orca`:

| Module | Purpose |
|---|---|
| `plugin.py` | `@orca.plugin` class and the `PrintLogUploader` capability (`SlicingPipelineCapabilityBase`): `execute`, `on_lifecycle_event`, default config. With `script.py`, the only modules that import `orca`. |
| `script.py` | `LogLastSlice` script capability: uploads the staged slice on demand (the Bambu fallback). |
| `triggers.py` | Pure decision logic: given a hook call or lifecycle event and the staged state, say upload now / stage / upload staged / ignore. |
| `staging.py` | Copies the working G-code into plugin storage and remembers the staged slice (path, output name, config values, time). One staged slice; a new slice replaces it. |
| `gcode_window.py` | Reads the head and tail of a G-code file the same way `GcodeWindow.ReadFromFile` does (2,000,000 bytes each; whole file when it fits). |
| `object_bounds.py` | Port of `ObjectBoundsScanner`: per-object extrusion bounding boxes from the full toolpath, for `{{models}}`. |
| `presets.py` | Finds the user preset JSONs named by `print_settings_id`, `filament_settings_id` and `printer_settings_id` (with their `inherits` chains) under `data_dir()/user/`, and strips credential keys. |
| `scrub.py` | Blanks sensitive `; key = value` lines and preset keys, using the API's `SlicerSettingsScrubber` rules. |
| `payload.py` | Builds the multipart request body. |
| `client.py` | POSTs with `urllib`, maps responses to results, builds the new-print URL. |
| `worker.py` | Runs read, scan, payload, upload and open-browser on a background thread so the export never waits. |

#### Flow

1. `execute(ctx)` at `psGCodePostProcess` (on the export thread): copy `ctx.gcode_path` into plugin storage, record
   `host`, `output_name`, `orca_version` and the config keys below, return `success` at once. It never fails the
   slice: every error is logged and the hook still returns `success` (or `skipped` for steps it does not handle).
2. `triggers.py` decides:
   - `host != "File"`: an upload to a print host. Upload now.
   - `host == "File"` and `gcode_path != output_name`: an export to file. Upload now.
   - `host == "File"` and `gcode_path == output_name`: Bambu after-slice. Stage only.
3. A staged slice is uploaded, once, by the first of:
   - `PrintJobStarted` (multi-machine send).
   - `PrintStateChanged` with `msg` in `PREPARE`, `RUNNING` (case-insensitive) within `bambu_send_window_minutes`
     (default 30) of staging.
   - The "Log last slice to 3D Print Log" script.
   An export to file of the same slice uploads that export and clears the staged slice. A slice that is never sent
   expires; nothing uploads.
4. The worker reads the windowed text, scans the full file for object bounds, collects user presets, scrubs, builds
   the payload and POSTs it. On success it opens the new-print URL with `webbrowser.open` when `open_browser` is true.
   The working copy and staged files are deleted after the upload.

Config keys read through `ctx.config_value`: `print_settings_id`, `filament_settings_id`, `printer_settings_id`.
Everything else comes from the G-code itself.

#### Plugin config (`get_default_config()`)

```json
{
  "template": "default",
  "open_browser": true,
  "api_base": "https://api.3dprintlog.com",
  "web_base": "https://www.3dprintlog.com",
  "bambu_send_window_minutes": 30,
  "debug": false
}
```

`template` is `default` or `full` (the uploader's `--default` / `--full`). `debug` writes the payload metadata and the
API response to plugin storage, like `--debug`.

### API (`3d-print-log-api`)

`POST /api/Slicer/uploads`, `[AllowAnonymous]`, rate-limited per client IP.

Request: `multipart/form-data` with

- `gcode`: file part, gzip of the UTF-8 windowed G-code text (head, `\n`, tail; or the whole file when it is at most
  4,000,000 bytes).
- `metadata`: JSON string:

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

`kind` is `process`, `filament` or `machine` (Orca's folder names). `models` and `userPresets` may be empty. Models
carry the raw centerline bounds; the library adds the line width exactly as `ModelsPlaceholder` does.

Server steps: check sizes, gunzip with a cap, scrub the text with `SlicerSettingsScrubber`, scrub each preset, parse
with the new library entry point (below), set `print_name` / `file_name` from `outputName` the way the uploader's
`PrintMetadata.Apply` does, and store a `CuraSetting` with `Slicer` = the detected slicer, `CuraVersion` = the slicer
version, `PluginVersion` = `orca-plugin/<pluginVersion>`.

Responses:

| Status | When |
|---|---|
| 200 `{ "newSettingId": "<guid>" }` | Stored. Same DTO as `/api/Cura/settings`. |
| 400 | Missing part, bad JSON, bad gzip, unknown `template`. |
| 413 | Request over 8 MB, decompressed text over 4,000,001 chars, or `metadata` over 1 MB. |
| 415 | Not multipart. |
| 429 | Rate limit: 10 per minute and 200 per day per IP, with `Retry-After`. |

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
    string Template,                                // "default" or "full"
    IReadOnlyList<ModelBounds> Models,              // replaces the toolpath scan for {{models}}
    IReadOnlyList<UserPresetFile> UserPresets,      // replaces reading slicer config roots for {{modified_settings}}
    Action<string>? DebugLog = null);
```

`SlicerGcodeResult` carries the slicer, version and the uploader's `settings` object, serialized exactly as the
uploader serializes it (note, estimated time, filament weight or per-slot usage, snapshot), so the API stores the same
JSON the uploader would send. It never touches the disk or network. The uploader keeps its current path; internally
`ParseOptions` gains the two in-memory sources and both callers share one parse.

The public surface grows on purpose (the pinned list in `SlicerConfigParserTests.PublicSurface_*` is updated), and it
ships as `PrintLog.SlicerSettings` 0.2.0 via a `lib-v0.2.0` tag.

## Errors

- Plugin hook: never raises, never fails the slice, never calls `orca.host.ui` from the slicing thread.
- Worker: network error, timeout (30 s), 4xx or 5xx are logged to Orca's Python log with the status and the API's
  message; a `push_notification` is attempted only if the spike shows it is safe off the main thread. No retries, no
  offline queue (parity with the uploader).
- Permission prompts: the first upload asks for network access to `api.3dprintlog.com`; opening the browser may ask
  once. A "No" is logged and the upload is dropped.
- API: never throws on any text; a parse that fails stores an empty note with slicer `Unknown`, as the connector
  endpoint does.

## Testing

- Plugin: pytest with a fake `orca` module. Trigger table (export, print host, Bambu after-slice then each commit
  event, re-slice, expiry, export after staging). Windowing against the C# boundaries. `object_bounds` against
  expected values produced by the C# scanner on the uploader repo's fixture G-code. Preset lookup with an `inherits`
  chain and scrubbing. Payload shape. Client status mapping against a local HTTP server.
- Library: the in-memory options give the same result as the uploader's disk-based parse for each fixture.
- API: integration tests for 200, 400, 413 (including a gzip bomb), 415, 429, scrubbing of the text and presets, and
  that the stored `Settings` round-trips through `GET /api/Cura/settings`.
- Manual, against 2.5.0-alpha (see the spike list): install, prompts, each trigger, browser open, the stored print.

## Spike against 2.5.0-alpha (manual, once Chris has the build)

1. Wheel installs through "Install local plugin"; the capability appears in Process > Others.
2. Export to file and a print-host upload each upload once; Bambu slice alone uploads nothing.
3. Bambu single-printer send: `PrintStateChanged` arrives with `PREPARE`/`RUNNING` and the staged slice uploads.
4. `webbrowser.open` from the worker thread works (and what it prompts).
5. `push_notification` from the worker thread: safe or not.
6. The network prompt appears once for `api.3dprintlog.com` and is remembered.
7. Thumbnails, the time and filament lines in an Orca 2.5 G-code match the parser fixtures.

## Out of scope for v1

- A setup script that adds the capability to the user's process presets (the wizard's job for `post_process`).
- Publishing to the Orca Cloud plugin hub (the author flow is not documented yet).
- API-key uploads and attaching to an existing print.
- An upstream OrcaSlicer change to fire `SendJobStarted` / `PrintJobStarted` from the single-printer send; worth
  proposing, and the `PrintStateChanged` rule can go once it lands.
