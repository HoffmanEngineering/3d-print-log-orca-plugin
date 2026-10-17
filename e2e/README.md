# End-to-end test: the plugin in a real OrcaSlicer 2.5.0-alpha

Runs the real OrcaSlicer 2.5.0-alpha (Linux AppImage) headlessly in Docker with this plugin
installed, drives it with `xdotool` on an Xvfb display, and checks what reaches a local
3D Print Log API. Nothing touches your own OrcaSlicer install or config: Orca runs on its own
`--datadir` inside the container.

```powershell
.\e2e\run.ps1                      # the four scenarios, then the API-side checks
.\e2e\run.ps1 -FreshWizard         # also drive Orca's first-run wizard (otherwise a cached datadir is used)
.\e2e\run.ps1 -Mode permissions    # the permission-prompt spike (see below)
```

## What it proves

`scripts/e2e.sh`, in order:

1. **Wizard** (with `-FreshWizard`, or when `cache/datadir-after-wizard.tgz` is missing):
   region North America, Generic Klipper Printer and Bambu Lab X1 Carbon, stealth mode. The
   resulting datadir is cached for later runs.
2. **Install**: the wheel goes where Orca's *Install local plugin* puts it,
   `orca_plugins/3d-print-log.whl/3d-print-log.whl`, with an `.install_state.json` sidecar that
   says `enabled: true` and grants no permissions. Orca loads it on start; the run checks that it
   registered its capabilities. The plugin config is written to `orca_plugins/config.json`
   (`api_base` = the local API, `web_base` = `http://localhost:4310`, `debug` and
   `open_browser` on). `BROWSER` points at `scripts/browser.sh`, which only logs the URL.
3. **Scenarios**, each in a fresh Orca on the same datadir, with a 20 mm cube from the command
   line. Presets are picked by editing `OrcaSlicer.conf` before the start, not with the combo boxes.

   | | Printer | Does | Expects |
   |---|---|---|---|
   | a | Generic Klipper | slice, click **Log to 3D Print Log** in Orca's notification, then export the G-code to a file | 1 upload, 1 browser URL with `settingId` |
   | b | Generic Klipper with an OctoPrint print host (`E2E OctoPrint` user preset) | slice, **Print**, **Upload** to the fake OctoPrint in the container (`scripts/fake_octoprint.py`) | OctoPrint receives the file once; 1 upload |
   | c | Generic Klipper | slice only | 0 uploads |
   | d | Bambu Lab X1 Carbon | slice only (no printer to send to) | 0 uploads |

   Uploads are counted from the plugin's `Uploaded ... (setting <id>)` log lines, per scenario.
   Any **Plugin permission request** dialog is screenshotted to `out/prompts/` and answered
   (No by default, `-PromptAnswer yes` for Yes); the run fails if one appears, or if Orca's audit
   hook blocked anything.
4. **API side** (`verify.py`, on the host): the `CuraSettings` rows created during the run, read
   from SQL Server in its Docker container. One row per upload, each with a note, the estimated
   print time, filament usage, `file_name`, `print_name` and a snapshot (decoded to
   `out/snapshot-<scenario>.png`). All of it goes to `out/api-rows.json`.

A second plugin, `scripts/e2e_probe.py`, is installed next to the real one: it logs every
lifecycle event Orca raises (name, thread, `ctx.name`, `ctx.msg`) to the Python log, which ends
up in `out/<scenario>-python.log`.

## Running it

Needs Docker Desktop (Linux engine), `gh`, Python 3.12, and the API from the
`feat/slicer-uploads` branch on port 5101 in the `E2ETesting` environment against the SQL Server
container on port 1434 (`docker compose up -d` in the API repo):

```bash
cd ../3d-print-log-api-worktrees/slicer-uploads
# appsettings.E2ETesting.json (gitignored): copy the .example and point it at your own database,
#   Server=localhost,1434;Database=PrintLogDb_OrcaE2E;...  E2ETesting drops and re-creates it on start.
dotnet restore PrintLogApi/PrintLogApi.csproj -s https://api.nuget.org/v3/index.json -s D:/Development/local-nuget
ASPNETCORE_ENVIRONMENT=E2ETesting ASPNETCORE_URLS=http://localhost:5101 dotnet run --project PrintLogApi --no-launch-profile
```

The first run downloads the 210 MB AppImage into `e2e/cache/` (gitignored) and builds the
image (about 1 GB of apt packages, extracted AppImage included). A run takes about 6 minutes.
Files go in and out with `docker cp`, never bind mounts.

To look at the result in the web app, serve the UI against the same API (`environment.e2e.ts`
with `printLogApiUrl: 'http://localhost:5101'`, `ng serve --configuration e2e --port 4310`) and
open a URL from `out/browser-urls.log` with `&devUserId=1` added.

## Artifacts (`out/`, gitignored)

- `NN-<step>.png`: a screenshot of each step; `NN-fail.png` where a step failed.
- `checks.tsv`, `results.tsv` (scenario, expected, uploads, setting ids), `api-rows.json`.
- `browser-urls.log`, `octoprint.log`, `prompts.log`, `audit-blocks.log`.
- `<scenario>-python.log` (the plugin's and the probe's lines), `<scenario>-last-upload.json`
  (the plugin's debug record of what it sent), `orca-logs/` (Orca's own logs).

## Recalibrating clicks

Coordinates assume a 1600x1000 screen with the main window at 200,100, 1200x800
(`place_main_window`), and the wizard pinned at 390,172. When Orca's layout changes, a step
fails with `NN-fail.png`; compare it with the numbered screenshots of a good run, then:

1. Start a container to explore in:
   `docker run -d --name orca-explore --entrypoint bash printlog-orca-e2e -c "sleep infinity"`,
   `docker cp e2e/scripts/. orca-explore:/e2e/`.
2. `docker exec orca-explore bash -c '. /e2e/lib.sh; start_display; ...; import -window root /out/x.png'`
   and `docker cp orca-explore:/out/x.png .` to see the screen. `windows` lists window titles.
3. Read the new coordinates off the screenshot and update `e2e.sh`.

What does not need coordinates: slicing is Ctrl+R; the notification link is found by matching
`scripts/templates/log-link.png` ("Log to 3D Print Log", 140x22) against the bottom-right of the
3D view, because Orca stacks its own notifications under it. If the link's look changes, crop a
new template from a screenshot (`convert shot.png -crop 140x22+X+Y +repage log-link.png`).
Ctrl+G is *Export plate sliced file* (a `.gcode.3mf`), so the export clicks the button instead.

## The permission-prompt spike (`-Mode permissions`)

`variants/permission_probe.py` is a throwaway plugin (never shipped) that does the plugin's kinds
of I/O where Orca does audit them: `request_permissions(fs_read=[...])` at registration, a urllib
POST from a lifecycle handler on the slicing thread, a thread started inside a callback, and a
notification whose click does a POST and two `webbrowser.open` calls with different URLs. It runs
alone (the real plugin is not installed), every prompt is answered **Yes**, and Orca is restarted
once to see whether the grants persist. Each dialog lands in `out/prompts/NN-<what>.png` (full
screen) and `NN-<what>-dialog.png` (the dialog), with the plugins' `permissions` from
`.install_state.json` after the answer in `NN-<what>-install-state.json`. The rest goes to
`out/permissions/`.

## What the 2.5.0-alpha showed (2026-10-17)

- Loading: the sidecar alone (`enabled: true`, no permissions) is enough; Orca extracts the wheel
  to `__whl_extracted__/` and fills `capabilities` in the sidecar on first load. A thread started
  at import is not audited; nothing prompts during load, the slice, the notification click, the
  upload or `webbrowser.open` from the plugin's own thread.
- Events: `GCodeExportFinished` fires once per slice (slicing thread, `ctx.msg` = the plate's temp
  G-code) with an **empty `ctx.name`**, so the notification reads "Sliced the plate." Exporting
  the G-code to a file afterwards raises no further `GCodeExportFinished`. `UploadStarted` and
  `UploadFinished` come on the print-host thread with `ctx.name` = Orca's temp upload name
  (`.OrcaSlicer.upload.xxxx-xxxx-xxxx-xxxx`), not the host file name. A Bambu slice shows the
  notification as well.
- The notification is a regular one: it fades about 5 s after it appears, and Orca's "Slice
  complete" notification stacks under it.
- Upload content: note, estimated time (569 s for the cube), `file_name`, `print_name` and
  snapshot arrive; filament usage has the length (1.341 m) but no weight
  (`material_used_mg` and `estimatedAmountMg` are null although Orca shows 4.00 g).
- Prompts (`-Mode permissions`): an audited call from any thread but the UI thread is refused at
  once and its prompt shown later; a Yes is saved to `.install_state.json` per exact target, and
  each layer of one urllib request asks separately (`urllib.Request` URL, then
  `http.client.connect` host, then files Python opens lazily such as `encodings/idna.py` and its
  `.pyc`), one restart at a time. `on_click` of a notification runs unaudited.
