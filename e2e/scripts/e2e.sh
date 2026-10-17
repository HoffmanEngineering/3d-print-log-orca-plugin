#!/bin/bash
# End-to-end run of the 3D Print Log plugin inside a real OrcaSlicer 2.5.0-alpha, driven headlessly.
#
#   0. Start Xvfb + openbox, the fake OctoPrint server and the permission-prompt watcher.
#   1. First-run wizard (Klipper + Bambu X1 Carbon, stealth mode), unless a datadir snapshot was given.
#   2. Install the plugin wheel the way Orca's "Install local plugin" lays it out, enabled, with no
#      permissions granted; write its config (local API and web addresses, debug on).
#   3. Scenarios, each in a fresh Orca on the same datadir (see run_scenario):
#        a  Klipper printer: slice, click "Log to 3D Print Log" in Orca's notification, then export
#           the G-code to a file                                    -> exactly 1 upload
#        b  Klipper printer with an OctoPrint print host: slice, Print -> Upload to the fake
#           OctoPrint                                               -> exactly 1 upload
#        c  Klipper printer: slice only                             -> 0 uploads
#        d  Bambu Lab X1 Carbon: slice only (no printer to send to) -> 0 uploads
#
# Inputs (docker cp):  /in/3d-print-log.whl   the plugin wheel
#                      /in/datadir.tgz        optional: a datadir after the wizard (skips step 1)
#                      /e2e/                  these scripts
# Outputs:             /out                   screenshots, logs, results.tsv, browser-urls.log,
#                                             octoprint.log, prompts.log, per-scenario debug uploads
#
# Coordinates assume a 1600x1000 screen and the main window at 200,100 sized 1200x800
# (place_main_window). See README.md for recalibrating them.
set -uo pipefail

. /e2e/lib.sh
export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt   # skips Orca's "use system SSL certificate?" question
mkdir -p "$OUT" "$HOME" /data
rm -f "$STEP_FILE"
PLUGIN_DIR=$DATADIR/orca_plugins/3d-print-log.whl
PROBE_DIR=$DATADIR/orca_plugins/e2e_probe.py
STORAGE=$DATADIR/orca_plugins/plugin_data/3d-print-log
API_BASE=${API_BASE:-http://host.docker.internal:5101}
WEB_BASE=${WEB_BASE:-http://localhost:4310}
PROMPT_ANSWER=${PROMPT_ANSWER:-no}
FAILURES=0

check() { # check <description> <command...>: record a pass/fail line, keep going
    local what=$1; shift
    if "$@"; then log "PASS $what"; echo "PASS	$what" >>"$OUT/checks.tsv"
    else log "FAIL $what"; echo "FAIL	$what" >>"$OUT/checks.tsv"; FAILURES=$((FAILURES + 1)); fi
}

# ---- 0. Display, fake OctoPrint, prompt watcher ---------------------------------------------------
start_display
python3 /e2e/fake_octoprint.py --port 5555 --dir /tmp/octoprint --log "$OUT/octoprint.log" >"$OUT/octoprint.out" 2>&1 &
curl -sf --retry 5 --retry-connrefused http://127.0.0.1:5555/api/version >/dev/null || fail "fake OctoPrint did not start"
curl -s -o /dev/null --max-time 5 "$API_BASE/" || fail "the API at $API_BASE is not reachable from the container"
: >"$OUT/browser-urls.log"

# Every "Plugin permission request" dialog is screenshotted and answered ($PROMPT_ANSWER), so a
# prompt can never stall the run, and each one is a recorded spike result.
# Screenshots go to $OUT/prompts/NN-<what>.png, <what> being the scenario, or in MODE=permissions
# the variant plugin's operation the prompt is for (prompt_subject). After each answer the
# plugins' permissions from .install_state.json are saved next to it, to show what Orca persisted.
prompt_subject() { # which variant operation a prompt on screen belongs to
    # Synchronous prompt (UI thread): the last "trying" line has no outcome yet, Orca is waiting.
    # Deferred prompt (any other thread): Orca denied the call at once ("audit permission
    # required") and shows the prompt later; it belongs to the oldest such denial not yet named.
    local lines last outcome denied
    lines=$(grep -ah "\[E2E permissions\] " "$DATADIR"/log/python_*.log 2>/dev/null | sed 's/.*\[E2E permissions\] //')
    last=$(echo "$lines" | grep ': trying$' | tail -1 | sed 's/: trying$//')
    outcome=$(echo "$lines" | grep -F "$last: " | grep -v ': trying$' | tail -1)
    if [ -n "$last" ] && [ -z "$outcome" ]; then echo "$last"; return; fi
    while IFS= read -r denied; do
        grep -qxF "$denied" /tmp/named-denials 2>/dev/null && continue
        echo "$denied" >>/tmp/named-denials; echo "$denied (deferred)"; return
    done < <(echo "$lines" | grep 'audit permission required' | sed 's/: [A-Za-z]*Error: .*//')
    # Every denial named already: another deferred prompt from the same call (one per file or
    # audit event it touched).
    denied=$(tail -1 /tmp/named-denials 2>/dev/null)
    if [ -n "$denied" ]; then echo "$denied (deferred, again)"; else echo "$last"; fi
}
watch_prompts() {
    local n=0 w X Y WIDTH HEIGHT what file
    mkdir -p "$OUT/prompts"
    while :; do
        for w in $(xdotool search --onlyvisible --name "^Plugin permission" 2>/dev/null); do
            n=$((n + 1)); sleep 0.7
            what=$(prompt_subject)
            [ -n "$what" ] || what=$(cat /tmp/scenario 2>/dev/null)
            what=$(echo "$what" | tr 'A-Z' 'a-z' | sed 's/[^a-z0-9]\{1,\}/-/g; s/^-//; s/-$//')
            file=$(printf '%02d-%s' "$n" "$what")
            eval "$(xdotool getwindowgeometry --shell "$w")"
            import -window root "$OUT/prompts/$file.png"
            convert "$OUT/prompts/$file.png" -crop "$((WIDTH + 4))x$((HEIGHT + 40))+$((X - 2))+$((Y - 38))" +repage \
                "$OUT/prompts/$file-dialog.png" 2>/dev/null
            echo "$(date -u +%FT%TZ)	$file.png	window=$w	answer=$PROMPT_ANSWER	scenario=$(cat /tmp/scenario 2>/dev/null)" >>"$OUT/prompts.log"
            # The dialog's two buttons fill its bottom row: No on the left half, Yes on the right.
            if [ "$PROMPT_ANSWER" = yes ]; then xdotool mousemove $((X + WIDTH * 3 / 4)) $((Y + HEIGHT - 38)) click 1
            else xdotool mousemove $((X + WIDTH / 4)) $((Y + HEIGHT - 38)) click 1; fi
            sleep 1.5
            for state in "$DATADIR"/orca_plugins/*/.install_state.json; do
                [ -f "$state" ] && jq -c --arg p "$(basename "$(dirname "$state")")" '{plugin: $p, permissions}' "$state"
            done >"$OUT/prompts/$file-install-state.json" 2>/dev/null
        done
        sleep 0.5
    done
}
: >"$OUT/prompts.log"
watch_prompts &

# ---- 1. First-run wizard ------------------------------------------------------------------------
if [ -f /in/datadir.tgz ]; then
    tar -C /data -xzf /in/datadir.tgz
    log "datadir restored from the snapshot"
else
    echo wizard >/tmp/scenario
    ( cd /opt/orca && ./AppRun --datadir "$DATADIR" >"$OUT/orca-wizard.log" 2>&1 ) &
    wait_window "Setup Wizard" 180; sleep 5
    # Where the wizard opens depends on whether Orca's SSL question came first; pin it to where
    # the coordinates below were measured (a web page inside, so give it time to lay out again).
    WZ=$(xdotool search --onlyvisible --name "Setup Wizard" | tail -1)
    xdotool windowsize --sync "$WZ" 818 640; xdotool windowmove --sync "$WZ" 390 172; sleep 4
    shot "wizard-welcome"
    click 807 617 1 4                        # Get Started
    click 804 611 1 1; click 1143 797 1 8    # Region: North America -> Next
    click 701 370 1 2                        # Custom Printer: Klipper
    click 473 220 1 2; xdotool type "X1 Carbon"; sleep 3
    click 505 370 1 2                        # Bambu Lab X1 Carbon
    shot "wizard-printers"
    click 1143 797 1 10                      # Next: filaments
    click 1143 797 1 8                       # Next: stealth mode
    click 520 507 1 1; click 1143 782 1 8    # Enable stealth mode -> Next
    shot "wizard-plugins"
    click 1143 782 1 15                      # Finish
    wait_gone "Setup Wizard" 60
    stop_orca
    [ -f "$DATADIR/OrcaSlicer.conf" ] && grep -q '"Bambu Lab X1 Carbon"' "$DATADIR/OrcaSlicer.conf" \
        || fail "the wizard did not write the Klipper and Bambu printers"
    tar -C /data -czf "$OUT/datadir-after-wizard.tgz" orca
    log "wizard complete"
fi

# ---- MODE=permissions: the prompt spike, with the variant plugin alone -------------------------------
if [ "${MODE:-scenarios}" = permissions ]; then
    trap 'cat "$DATADIR"/log/python_*.log >"$OUT/permissions-python-last.log" 2>/dev/null' EXIT
    VARIANT_DIR=$DATADIR/orca_plugins/permission_probe.py
    rm -rf "$DATADIR"/orca_plugins/*
    mkdir -p "$VARIANT_DIR"
    cp /e2e/variants/permission_probe.py "$VARIANT_DIR/permission_probe.py"
    jq -n '{installed_from: "local", installed_version: "1.0.0", plugin_name: "E2E permission probe",
        enabled: true, capabilities: [],
        permissions: {fs_read: [], fs_readwrite: [], network_http: [], network_socket: [], process: [], threading: []}}' \
        >"$VARIANT_DIR/.install_state.json"
    write_cube_stl 20 >/tmp/cube.stl
    rm -f "$DATADIR"/log/python_*.log
    jq '.presets.machine = "MyKlipper 0.4 nozzle"' "$DATADIR/OrcaSlicer.conf" >/tmp/conf && cp /tmp/conf "$DATADIR/OrcaSlicer.conf"
    echo register >/tmp/scenario
    launch_orca permissions /tmp/cube.stl
    place_main_window 200 100 1200 800
    sleep 5
    shot "permissions-loaded"
    # The prompts appear while this waits for the notification; the watcher answers them. The
    # notification fades after a few seconds, so look for it from the moment slicing starts.
    perm_slice_and_click() { # perm_slice_and_click <label>
        echo "$1" >/tmp/scenario
        click 1040 600 1 1; xdotool key ctrl+r
        if click_log_link 90; then log "$1: clicked the probe's notification"
        else shot "$1-no-notification"; log "$1: the probe's notification did not appear"; fi
        sleep 15
        shot "$1-after-click"
    }
    perm_slice_and_click first-run
    stop_orca
    cp "$VARIANT_DIR/.install_state.json" "$OUT/permissions-install-state-after-first-run.json"
    cat "$DATADIR"/log/python_*.log >"$OUT/permissions-python-first-run.log" 2>/dev/null
    # Restart and do it all again: do the grants persist (no prompts; the calls go through)?
    # Restart and do it all again, twice: do the grants persist, and how many runs until a call
    # from the slicing thread gets through?
    for run in second-run third-run; do
        rm -f "$DATADIR"/log/python_*.log /tmp/named-denials
        echo "$run" >/tmp/scenario
        launch_orca "permissions-$run" /tmp/cube.stl
        place_main_window 200 100 1200 800
        perm_slice_and_click "$run"
        stop_orca
        cp "$VARIANT_DIR/.install_state.json" "$OUT/permissions-install-state-after-$run.json"
        cat "$DATADIR"/log/python_*.log >"$OUT/permissions-python-$run.log" 2>/dev/null
    done
    cat "$OUT"/permissions-python-*-run.log >"$OUT/permissions-python.log"
    grep -ah "AUDIT" "$DATADIR"/log/debug_*.log* | grep -av "AUDIT EVENT\|Denied\|Global allowed\|installed" >"$OUT/permissions-audit.log"
    cp "$VARIANT_DIR/.install_state.json" "$OUT/permissions-install-state-final.json"
    cat "$OUT/browser-urls.log" "$OUT/prompts.log"
    grep -a "E2E permissions" "$OUT/permissions-python.log"
    exit 0
fi

# ---- 2. Install the plugin ----------------------------------------------------------------------
[ -f /in/3d-print-log.whl ] || fail "/in/3d-print-log.whl is missing"
rm -rf "$PLUGIN_DIR" "$PROBE_DIR" "$DATADIR/orca_plugins/plugin_data" "$DATADIR/orca_plugins/config.json"
mkdir -p "$PLUGIN_DIR" "$PROBE_DIR"
# The layout PluginLoader::install_plugin creates: orca_plugins/<file name>/<file name> plus the
# .install_state.json sidecar. enabled=true is what activating it in the Plugins window writes.
# No permissions are granted, so any prompt the plugin causes shows up (and is recorded).
cp /in/3d-print-log.whl "$PLUGIN_DIR/3d-print-log.whl"
sidecar() { # sidecar <plugin name> <version>
    jq -n --arg name "$1" --arg version "$2" '{installed_from: "local", installed_version: $version,
        plugin_name: $name, enabled: true, capabilities: [],
        permissions: {fs_read: [], fs_readwrite: [], network_http: [], network_socket: [], process: [], threading: []}}'
}
sidecar "3d-print-log-orca-plugin" "0.0.0" >"$PLUGIN_DIR/.install_state.json"
# The probe logs every lifecycle event to the Python log (test harness only).
cp /e2e/e2e_probe.py "$PROBE_DIR/e2e_probe.py"
sidecar "E2E event probe" "1.0.0" >"$PROBE_DIR/.install_state.json"

# User presets the scenarios select: a Klipper printer with an OctoPrint print host.
mkdir -p "$DATADIR/user/default/machine"
jq -n '{from: "User", inherits: "MyKlipper 0.4 nozzle", is_custom_defined: "0", name: "E2E OctoPrint",
        printer_settings_id: "E2E OctoPrint", host_type: "octoprint", print_host: "127.0.0.1:5555",
        printhost_apikey: "e2e-not-a-secret", version: "2.5.0.0"}' >"$DATADIR/user/default/machine/E2E OctoPrint.json"

# First start: Orca loads the plugin and records its capabilities in the sidecar.
echo install >/tmp/scenario
launch_orca "install"
place_main_window 200 100 1200 800
shot "plugin-loaded"
stop_orca
CAPS=$(jq -r '.capabilities[] | keys[]' "$PLUGIN_DIR/.install_state.json")
log "capabilities: $(echo "$CAPS" | paste -sd ',')"
check "the plugin loaded and registered its capabilities" test -n "$CAPS"
cp "$PLUGIN_DIR/.install_state.json" "$OUT/install-state-after-load.json"

# Plugin config, as the Plugins window's Config would save it: one entry per capability in
# orca_plugins/config.json. No capability_type: Orca treats such an entry as the config of that
# capability name whatever its type.
CONFIG=$(jq -n --arg api "$API_BASE" --arg web "$WEB_BASE" \
    '{api_base: $api, web_base: $web, open_browser: true, debug: true, slice_notification: true, template: "default"}')
echo "$CAPS" | jq -R --argjson cfg "$CONFIG" \
    '{plugin_key: "3d-print-log", capability: ., plugin_version: "0.0.0", cap_config: $cfg}' \
    | jq -s '{config: .}' >"$DATADIR/orca_plugins/config.json"
cp "$DATADIR/orca_plugins/config.json" "$OUT/plugin-config.json"

# ---- 3. Scenarios -------------------------------------------------------------------------------
write_cube_stl 20 >/tmp/cube.stl

select_presets() { # select_presets <printer preset> <process preset>: what Orca selects on its next start
    local c=$DATADIR/OrcaSlicer.conf
    jq --arg m "$1" --arg p "$2" '.presets.machine = $m
        | (if any(.orca_presets[]; .machine == $m) then . else .orca_presets += [(.orca_presets[0] | .machine = $m)] end)
        | .orca_presets |= map(if .machine == $m then .process = $p else . end)' "$c" >/tmp/conf && cp /tmp/conf "$c"
}
uploads() { grep -ah "\[3D Print Log\] INFO Uploaded" "$DATADIR"/log/python_*.log 2>/dev/null | wc -l; }
wait_uploads() { # wait_uploads <count> [timeout s]
    local deadline=$(( $(date +%s) + ${2:-60} ))
    until [ "$(uploads)" -ge "$1" ]; do [ "$(date +%s)" -lt "$deadline" ] || return 1; sleep 1; done
}
slice() { click 1040 600 1 1; xdotool key ctrl+r; }   # focus the 3D view, then Slice plate (Ctrl+R)

run_scenario() { # run_scenario <name> <printer> <process> <expected uploads> <action function>
    local name=$1 printer=$2 process=$3 expected=$4 action=$5 got urls_before
    log "---- scenario $name: $printer / $process"
    echo "$name" >/tmp/scenario
    select_presets "$printer" "$process"
    rm -f "$DATADIR"/log/python_*.log "$STORAGE"/debug/last-upload.json
    urls_before=$(wc -l <"$OUT/browser-urls.log")
    launch_orca "$name" /tmp/cube.stl
    place_main_window 200 100 1200 800
    shot "$name-ready"
    "$action"
    sleep 10   # anything late (a second upload) would land here
    got=$(uploads)
    shot "$name-done"
    stop_orca
    cat "$DATADIR"/log/python_*.log >"$OUT/$name-python.log" 2>/dev/null
    [ -f "$STORAGE/debug/last-upload.json" ] && cp "$STORAGE/debug/last-upload.json" "$OUT/$name-last-upload.json"
    tail -n +"$((urls_before + 1))" "$OUT/browser-urls.log" >"$OUT/$name-browser-urls.log"
    printf '%s\t%s\t%s\t%s\n' "$name" "$expected" "$got" \
        "$(grep -o 'settingId=[0-9a-f-]*' "$OUT/$name-browser-urls.log" | cut -d= -f2 | paste -sd ',')" >>"$OUT/results.tsv"
    check "scenario $name: $expected upload(s), got $got" test "$got" -eq "$expected"
}

scenario_a() {
    slice
    if click_log_link 45; then shot "a-clicked-log"; else shot "a-no-notification"; fi
    wait_uploads 1 60 || log "no upload within 60 s"
    # Export to a file as well: with zero setup (no hook in the preset) that must not upload again.
    click 1275 155 1 3           # Export G-code file (Ctrl+G is "Export plate sliced file", a .gcode.3mf)
    wait_window "Save G-code file as" 30
    sleep 2; shot "a-export-dialog"
    xdotool key ctrl+a; xdotool type --delay 20 "/tmp/e2e-export-cube.gcode"; xdotool key Return
    wait_gone "G-code file as" 60
    sleep 5
    check "scenario a: the G-code was exported to a file" test -s /tmp/e2e-export-cube.gcode
}
scenario_b() {
    slice
    sleep 12                     # let the slice finish and the notification fade
    click 1323 155 1 4           # Print (sends to the printer's print host)
    wait_window "Send G-code to printer host" 30
    xdotool key ctrl+a; xdotool type --delay 20 "e2e-host-cube.gcode"; sleep 1
    shot "b-send-dialog"
    click 737 570 1 2            # Upload
    wait_file_grep "$OUT/octoprint.log" '"filename": "e2e-host-cube.gcode"' 60 || log "OctoPrint received nothing"
    wait_uploads 1 60 || log "no upload within 60 s"
    check "scenario b: the fake OctoPrint received e2e-host-cube.gcode once" \
        test "$(grep -c '"filename": "e2e-host-cube.gcode"' "$OUT/octoprint.log")" -eq 1
}
scenario_c() {
    slice
    if wait_link 45 >/dev/null; then shot "c-notification-not-clicked"; else log "no notification in c"; fi
    sleep 15
}
scenario_d() {
    slice
    if wait_link 45 >/dev/null; then shot "d-notification-not-clicked"; echo yes >"$OUT/d-notification-shown"
    else log "no notification for the Bambu slice"; echo no >"$OUT/d-notification-shown"; fi
    sleep 15
}

run_scenario a "MyKlipper 0.4 nozzle" "0.20mm Standard @MyKlipper" 1 scenario_a
run_scenario b "E2E OctoPrint" "0.20mm Standard @MyKlipper" 1 scenario_b
run_scenario c "MyKlipper 0.4 nozzle" "0.20mm Standard @MyKlipper" 0 scenario_c
run_scenario d "Bambu Lab X1 Carbon 0.4 nozzle" "0.20mm Standard @BBL X1C" 0 scenario_d

check "every browser URL carries a settingId" \
    test "$(grep -c . "$OUT/browser-urls.log")" -eq "$(grep -c 'settingId=' "$OUT/browser-urls.log")"
check "no permission prompt appeared" test ! -s "$OUT/prompts.log"
grep -ah "AUDIT\] block\|AUDIT BLOCKED" "$DATADIR"/log/debug_*.log* >"$OUT/audit-blocks.log" 2>/dev/null
check "Orca's audit hook blocked nothing" test ! -s "$OUT/audit-blocks.log"
cp "$PLUGIN_DIR/.install_state.json" "$OUT/install-state-final.json"
cp -r "$DATADIR/log" "$OUT/orca-logs"
cp "$DATADIR/OrcaSlicer.conf" "$OUT/OrcaSlicer.conf" 2>/dev/null

log "results (scenario, expected, uploads, setting ids):"
cat "$OUT/results.tsv"
[ "$FAILURES" -eq 0 ] && log "PASS" || log "FAILED: $FAILURES check(s)"
exit "$FAILURES"
