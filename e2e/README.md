# End-to-end test: the plugin in a real OrcaSlicer 2.5.0-alpha

Runs the real OrcaSlicer 2.5.0-alpha (Linux AppImage) headlessly in Docker with this plugin
installed, drives it with `xdotool` on an Xvfb display, and checks what reaches a local
3D Print Log API and web app. Nothing touches your own OrcaSlicer install or config: Orca runs on
its own `--datadir` inside the container.

```powershell
.\e2e\run.ps1                      # the sessions below, then the API-side checks (about 12 minutes)
.\e2e\run.ps1 -FreshWizard         # also drive Orca's first-run wizard (otherwise a cached datadir is used)
.\e2e\run.ps1 -PromptAnswer no     # answer No to every permission prompt
.\e2e\run.ps1 -Mode permissions    # the permission-prompt spike (see below)
```

## What it proves

`scripts/e2e.sh`, in order:

1. **Wizard** (with `-FreshWizard`, or when `cache/datadir-after-wizard.tgz` is missing):
   region North America, Generic Klipper Printer and Bambu Lab X1 Carbon, stealth mode. The
   resulting datadir is cached for later runs.
2. **Install**: the wheel goes where Orca's *Install local plugin* puts it,
   `orca_plugins/3d-print-log.whl/3d-print-log.whl`, with an `.install_state.json` sidecar that
   says `enabled: true` and grants no permissions, so every prompt the plugin causes shows up.
   Orca loads it on start; the run checks that it registered its capabilities. The plugin config
   goes to `orca_plugins/config.json`: `api_base` = the local API, `web_base` =
   `http://localhost:4320`, `open_claim_page` = `"window"`, `debug` on. `BROWSER` points at
   `scripts/browser.sh`, which only logs the URL.
3. **Sessions**, each a fresh Orca on the same datadir with a 20 mm cube from the command line and
   the main window filling the 1600x1000 screen. Presets are picked by editing `OrcaSlicer.conf`
   before the start.

   | Session | Printer | Does | Expects |
   |---|---|---|---|
   | session1 | Generic Klipper | slice, screenshot the notification, open the **3D Print Log** tab, **Log to 3D Print Log**, Yes to every prompt; the claim page opens in its own Orca window ("3D Print Log"); scroll it; **Back to OrcaSlicer**; **Open claim page**; the close box; reopen, right-click; pick the printer, **Submit**, **Done, back to OrcaSlicer** | 1 upload; the window opens, closes from Back, the close box and Done; the tab keeps its list |
   | session2 | Generic Klipper | restart, slice, Log | 1 upload |
   | session3 | Generic Klipper | restart, slice, Log, **No** to the first prompt (screenshot of the declined message), Log again (asked again), Yes | 1 upload |
   | session4 | Generic Klipper | restart in dark mode (Orca's setting and `GTK_THEME=Adwaita:dark`), slice, Log | 1 upload |
   | host | Generic Klipper with an OctoPrint print host (`E2E OctoPrint` user preset) | slice, **Print**, **Upload** to the fake OctoPrint (`scripts/fake_octoprint.py`): nothing is uploaded to 3D Print Log; the tab says "Sent to a print host"; Log | OctoPrint gets the file once; 1 upload |
   | slice | Generic Klipper | slice only, then **Open in browser** | 0 uploads, 1 browser URL |
   | bambu | Bambu Lab X1 Carbon | slice only, then Open in browser again | 0 uploads, no prompt |
   | browser-mode | Generic Klipper | a new install id and `open_claim_page: "browser"`: slice, Log | 1 upload, the browser opens this install's uploads page |

   Uploads are counted from the plugin's `Uploaded ... (setting <id>)` log lines. Every
   **Plugin permission request** dialog is screenshotted to `out/prompts/NN-<session>-<what>.png`
   (and `-dialog.png`), read with tesseract into `out/prompts.log`, and answered; the plugins'
   permissions after each answer go to `NN-...-install-state.json`.

   The UI freeze is measured with the window manager's ping (`scripts/ping.py`): while Log runs
   on Orca's UI thread, Orca cannot answer `_NET_WM_PING`. One ping at a time, 20 ms apart, from
   the click until the claim page has loaded; `out/freeze-<session>.txt` has one line per ping and
   `out/freeze.tsv` the longest stall per Log click. A permission prompt runs a nested event loop,
   so the time a prompt waits for its answer is not counted.
4. **API side** (`verify.py`, on the host): the `CuraSettings` rows created during the run, read
   from SQL Server in its Docker container. One row per upload, each with a note that lists the
   cube as 20.0 x 20.0 x 20.0 mm, the estimated print time, filament usage with a weight,
   `file_name`, `print_name` and a snapshot (decoded to `out/snapshot-<session>.png`). All of it
   goes to `out/api-rows.json`.

A second plugin, `scripts/e2e_probe.py`, is installed next to the real one: it logs every
lifecycle event Orca raises (name, thread, `ctx.name`, `ctx.msg`) to the Python log, which ends
up in `out/<session>-python.log`. The harness also waits for its `SlicingJobComplete` line to know
a slice is done.

## Running it

Needs Docker Desktop (Linux engine), `gh`, Python 3.12, the API from the `feat/slicer-uploads`
branch on port 5101 in the `E2ETesting` environment against the SQL Server container on port 1434
(`docker compose up -d` in the API repo), and the web app on port 4320 against that API:

```bash
cd ../3d-print-log-api-worktrees/slicer-uploads
# appsettings.E2ETesting.json (gitignored): copy the .example and point it at your own database,
#   Server=localhost,1434;Database=PrintLogDb_OrcaE2E;...  E2ETesting drops and re-creates it on start.
dotnet restore -s https://api.nuget.org/v3/index.json -s D:/Development/local-nuget
ASPNETCORE_ENVIRONMENT=E2ETesting ASPNETCORE_URLS=http://0.0.0.0:5101 dotnet run --project PrintLogApi --no-launch-profile

cd ../../3d-print-log-ui-worktrees/orca-pending
# src/environments/environment.e2e.ts: printLogApiUrl 'http://localhost:5101' (do not commit)
npx ng serve --configuration e2e --host 0.0.0.0 --port 4320
```

Both must listen on all interfaces: inside the container, `scripts/forward.py` forwards
`localhost:4320` and `localhost:5101` to the host, so the claim page opens inside Orca at the same
`http://localhost:4320/...` address it has on the host, and calls the API at `localhost:5101`.
The e2e build's dev-auth bypass signs the page in as user 1.

The first run downloads the 210 MB AppImage into `e2e/cache/` (gitignored) and builds the
image (about 1 GB of apt packages, extracted AppImage included). Files go in and out with
`docker cp`, never bind mounts.

To look at the uploads in your own browser, open a URL from `out/browser-urls.log`
(`/prints/new/orca?device=<install id>`) with `&devUserId=1` added. The browser-mode install has
one fresh upload, so within ten minutes of the run the page opens it straight away; the first
install's URL lists all of its uploads.

## Artifacts (`out/`, gitignored)

- `NN-<step>.png`: a screenshot of each step; `NN-fail.png` where a step failed.
- `prompts/`, `prompts.log` (time, session, screenshot, answer, the dialog's text).
- `checks.tsv`, `results.tsv` (session, expected, uploads, setting ids, prompts), `api-rows.json`.
- `freeze-<session>.txt`, `freeze.tsv`.
- `browser-urls.log`, `octoprint.log`, `audit-blocks.log` (Orca's `[AUDIT] block` lines).
- `<session>-python.log` (the plugin's and the probe's lines), `<session>-last-upload.json`
  (the plugin's debug record of what it sent), `install.json` (the install id), `orca-logs/`.

## Recalibrating clicks

Coordinates assume a 1600x1000 screen with the main window filling it (`place_main_window 0 0
1600 1000`) and the wizard pinned at 390,172; the named ones are at the top of section 3 of
`e2e.sh`. Below about 1300 px wide, Orca drops the plugin's tab from the tab strip without an
overflow button, so do not shrink the window. When Orca's layout changes, a step fails with
`NN-fail.png`; compare it with the numbered screenshots of a good run, then:

1. Start a container to explore in:
   `docker run -d --name orca-explore --entrypoint bash printlog-orca-e2e -c "sleep infinity"`,
   `docker cp e2e/scripts/. orca-explore:/e2e/`.
2. `docker exec orca-explore bash -c '. /e2e/lib.sh; start_display; ...; import -window root /out/x.png'`
   and `docker cp orca-explore:/out/x.png .` to see the screen. `windows` lists window titles.
3. Read the new coordinates off the screenshot and update `e2e.sh`.

Slicing is Ctrl+R. The send dialog's Upload button is clicked relative to the dialog's
position (`xdotool --window` offsets miss under openbox).

## The permission-prompt spike (`-Mode permissions`)

`variants/permission_probe.py` is a throwaway plugin (never shipped) that does the plugin's kinds
of I/O where Orca does audit them: `request_permissions(fs_read=[...])` at registration, a urllib
POST from a lifecycle handler on the slicing thread, a thread started inside a callback, and a
notification whose click does a POST and two `webbrowser.open` calls with different URLs. It runs
alone (the real plugin is not installed), every prompt is answered **Yes**, and Orca is restarted
to see whether the grants persist. It was written for the earlier background-thread design and
is kept for reference; the sessions above cover the tab design.


## What the 2.5.0-alpha showed (2026-10-17, plugin 0.1.0 at 127c894, claim page window)

The last run passed every check (27 in the container, all of `verify.py`). Earlier runs on the
0.1.0 tab design (claim page inside the tab) are summarized at the end.

- **The tab**: "3D Print Log" sits right of Project. Below about 1300 px of window width Orca
  drops it from the tab strip with nothing to show it is there (1250 px: gone; 1300 px: shown).
- **Notification**: "Sliced cube. Open the 3D Print Log tab to log it."; the list names the slice
  "cube".
- **Log**: the row shows "Uploading..." behind the prompts, then a modeless "3D Print Log" window
  (1038x795 client area) opens over Orca, centered the first time, with the claim page signed in
  (the dev-auth bypass; the tab and the window share the web view's storage). The tab keeps its
  list and shows "Logged. The claim page opened in an OrcaSlicer window." Opening the window
  asks for no permission.
- **Claim form**: snapshot, title Cube, 9m 29s, filament usage **4 g, Measure: Weight**, the note
  lists `cube.stl x1 20.0 x 20.0 x 20.0 mm` (checked in the stored row).
- **Closing**: "Back to OrcaSlicer" closes the window and the tab shows Logged; **Open claim page**
  reopens it (at the top left of the screen, not centered); the window's close box closes it.
  Right-click **Back** is greyed out on the claim page (it is the window's first history entry),
  so it closes nothing there; after navigating inside the window it goes back as in a browser.
- **Save inside the window**: picking the printer and Submit shows the "Print saved" card ("Your
  print is saved to 3D Print Log. You can close this window."); **Done, back to OrcaSlicer**
  closes the window. The tab never went blank.
- **Declined**: answering No shows "Orca blocked the upload because permission was declined.
  Click Log again to be asked again." in red, nothing is sent, and Log asks again.
- **Print host**: the send to OctoPrint uploads nothing; the tab marks the slice "Sent to a print
  host"; Log uploads it once (as `cube.gcode`: without the hook in the preset the plugin logs
  Orca's plate G-code, not the host file name).
- **Slice only** (Klipper and Bambu): no upload, no prompt.
- **Open in browser** opens `{web_base}/prints/new/orca?device=<install id>`: the web app lists
  the install's uploads, and with exactly one fresh upload opens it straight away. With
  `open_claim_page: "browser"`, Log shows "Logged. Claim it in your browser." and the footer
  "Log opens your browser at the prints this OrcaSlicer uploaded, where you claim it." The print
  saved in session1 still appears in its install's list (marked "Already opened").
- **Dark mode**: with Orca dark (`dark_color_mode` plus `GTK_THEME=Adwaita:dark` on Linux), the
  tab and the claim page window follow it.
- **Prompts per session** (Yes unless noted; the same in all three runs except where shown):

  | Session | Prompts |
  |---|---|
  | 1 | read the temp G-code; HTTP request `http://host.docker.internal:5101/api/Slicer/uploads` |
  | 2 | read the temp G-code; HTTP request to host `host.docker.internal` (asked twice in one run) |
  | 3 | read the temp G-code (**No**); read it again; read `encodings/__pycache__/idna.cpython-312.pyc` |
  | 4 | read the temp G-code; read `__pycache__/stringprep.cpython-312.pyc` |
  | host | read the temp G-code; read `encodings/__pycache__/utf_8_sig.cpython-312.pyc`; open a network connection to `host.docker.internal` |
  | slice (Open in browser) | read/write `/dev/null` |
  | bambu (Open in browser) | none |
  | browser-mode (new install id) | read the temp G-code; audit event `socket.__new__` or a network connection to the host; audit event `open` (no target) |

  The temp G-code is asked for on every Log (its path changes per slice); the others depend on
  which grants earlier sessions saved and which modules Python loads lazily. No prompt ever said
  "run the following command": opening the browser asked only for `/dev/null` (with `BROWSER` set
  to a script; a desktop's `xdg-open` may differ).
- **Freeze** (longest stall of Orca's UI thread per Log click, prompts excluded): 962, 1206, 71,
  65, 71, 77 ms in the last run; 550 to 3422 ms on a session's first upload across runs, under
  0.15 s after that.
- **Tab design (claim page inside the tab, before 127c894)**: "Back to OrcaSlicer" and right-click
  Back both left the tab blank for the rest of the session. The window design fixes that.
