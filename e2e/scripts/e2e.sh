#!/bin/bash
# End-to-end run of the 3D Print Log plugin inside a real OrcaSlicer 2.5.0-alpha, driven headlessly.
#
#   0. Start Xvfb + openbox, the fake OctoPrint server, the localhost forwarder (web app and API)
#      and the permission-prompt watcher.
#   1. First-run wizard (Klipper + Bambu X1 Carbon, stealth mode), unless a datadir snapshot was given.
#   2. Install the plugin wheel the way Orca's "Install local plugin" lays it out, enabled, with no
#      permissions granted; write its config (local API and web addresses, claim page in an Orca window).
#   3. Sessions, each a fresh Orca on the same datadir (see run_session). Every session records the
#      permission prompts it showed (prompts/NN-<session>-<what>.png):
#        session1  Klipper: slice, notification, open the 3D Print Log tab, Log (answer Yes to every
#                  prompt), the claim page opens in its own Orca window: Back to OrcaSlicer, close box, save.
#                  The UI freeze during Log is measured with _NET_WM_PING      -> exactly 1 upload
#        session2  restart, slice, Log                                         -> 1 upload
#        session3  restart, slice, Log, answer No to the first prompt: the tab says it was declined
#                  and nothing was sent; Log again asks again, Yes             -> 1 upload
#        session4  restart in dark mode, slice, Log                            -> 1 upload
#        host      Klipper with an OctoPrint print host: slice, Print -> Upload to the fake OctoPrint:
#                  no upload; the tab marks the slice "Sent to a print host"; Log -> 1 upload
#        slice     Klipper: slice only -> 0 uploads; Open in browser (prompt: run a command)
#        bambu     Bambu Lab X1 Carbon: slice only -> 0 uploads; Open in browser again, no prompt
#        browser-mode  a new install id and open_claim_page "browser": slice, Log -> 1 upload, and
#                  the browser opens the uploads page (the web app opens a single fresh upload)
#
# Inputs (docker cp):  /in/3d-print-log.whl   the plugin wheel
#                      /in/datadir.tgz        optional: a datadir after the wizard (skips step 1)
#                      /e2e/                  these scripts
# Outputs:             /out                   screenshots, logs, results.tsv, checks.tsv, prompts/,
#                                             prompts.log, freeze-*.txt, browser-urls.log, octoprint.log
#
# Coordinates assume a 1600x1000 screen. The scenarios fill it with the main window
# (place_main_window 0 0 1600 1000): below about 1300 px wide, Orca drops the plugin's tab from the
# tab strip. See README.md for recalibrating them.
set -uo pipefail

. /e2e/lib.sh
export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt   # skips Orca's "use system SSL certificate?" question
mkdir -p "$OUT" "$HOME" /data
rm -f "$STEP_FILE"
PLUGIN_DIR=$DATADIR/orca_plugins/3d-print-log.whl
PROBE_DIR=$DATADIR/orca_plugins/e2e_probe.py
STORAGE=$DATADIR/orca_plugins/plugin_data/3d-print-log
API_BASE=${API_BASE:-http://host.docker.internal:5101}
WEB_BASE=${WEB_BASE:-http://localhost:4320}
PROMPT_ANSWER=${PROMPT_ANSWER:-yes}
FAILURES=0

check() { # check <description> <command...>: record a pass/fail line, keep going
    local what=$1; shift
    if "$@"; then log "PASS $what"; echo "PASS	$what" >>"$OUT/checks.tsv"
    else log "FAIL $what"; echo "FAIL	$what" >>"$OUT/checks.tsv"; FAILURES=$((FAILURES + 1)); fi
}

# ---- 0. Display, fake OctoPrint, forwarder, prompt watcher ------------------------------------------
start_display
python3 /e2e/fake_octoprint.py --port 5555 --dir /tmp/octoprint --log "$OUT/octoprint.log" >"$OUT/octoprint.out" 2>&1 &
curl -sf --retry 5 --retry-connrefused http://127.0.0.1:5555/api/version >/dev/null || fail "fake OctoPrint did not start"
# The claim page opens inside Orca at $WEB_BASE (http://localhost:4320) and calls the API at
# http://localhost:5101: inside the container both are the host's servers.
python3 /e2e/forward.py host.docker.internal ${FORWARD_PORTS:-4320 5101} >"$OUT/forward.log" 2>&1 &
curl -s -o /dev/null --max-time 5 "$API_BASE/" || fail "the API at $API_BASE is not reachable from the container"
curl -s -o /dev/null --retry 5 --retry-connrefused --max-time 10 "$WEB_BASE/" || log "the web app at $WEB_BASE is not reachable (claim page checks will fail)"
: >"$OUT/browser-urls.log"

# Every "Plugin permission request" dialog is screenshotted, read (tesseract) and answered, so a
# prompt can never stall the run, and each one is a recorded result.
# Screenshots go to $OUT/prompts/NN-<session>-<what>.png, <what> from the dialog's text (or in
# MODE=permissions the variant plugin's operation, prompt_subject). The answer is $PROMPT_ANSWER,
# unless /tmp/answer says "no-once": then that prompt gets No and the next ones Yes. After each
# answer the plugins' permissions from .install_state.json are saved next to it.
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
prompt_kind() { # prompt_kind <dialog text>: a short name for what the prompt asks
    case "$1" in
        *"read the following file"*) echo "read-file" ;;
        *"write"*"file"*) echo "write-file" ;;
        *"HTTP request"*) echo "http-request" ;;
        *"run the following command"*) echo "run-command" ;;
        *"network"*|*"socket"*|*"connect"*) echo "network" ;;
        *"thread"*) echo "thread" ;;
        *) echo "other" ;;
    esac
}
watch_prompts() {
    local n=0 w X Y WIDTH HEIGHT what file text answer
    mkdir -p "$OUT/prompts"
    while :; do
        for w in $(xdotool search --onlyvisible --name "^Plugin permission" 2>/dev/null); do
            n=$((n + 1)); sleep 0.7
            eval "$(xdotool getwindowgeometry --shell "$w")"
            import -window root /tmp/prompt.png
            convert /tmp/prompt.png -crop "$((WIDTH + 4))x$((HEIGHT + 40))+$((X - 2))+$((Y - 38))" +repage /tmp/prompt-dialog.png 2>/dev/null
            text=$(convert /tmp/prompt-dialog.png -resize 200% png:- 2>/dev/null | tesseract stdin stdout 2>/dev/null \
                | tr '\n' ' ' | sed 's/  */ /g; s/ *No *Yes *$//; s/^ *//; s/ *$//')
            if [ "${MODE:-scenarios}" = permissions ]; then what=$(prompt_subject); else what=$(prompt_kind "$text"); fi
            what="$(cat /tmp/scenario 2>/dev/null)-$what"
            what=$(echo "$what" | tr 'A-Z' 'a-z' | sed 's/[^a-z0-9]\{1,\}/-/g; s/^-//; s/-$//')
            file=$(printf '%02d-%s' "$n" "$what")
            mv /tmp/prompt.png "$OUT/prompts/$file.png"; mv /tmp/prompt-dialog.png "$OUT/prompts/$file-dialog.png"
            answer=$PROMPT_ANSWER
            if [ "$(cat /tmp/answer 2>/dev/null)" = no-once ]; then answer=no; echo yes >/tmp/answer; fi
            printf '%s\t%s\t%s\t%s\t%s\n' "$(date -u +%FT%T.%3NZ)" "$(cat /tmp/scenario 2>/dev/null)" "$file.png" "$answer" "$text" >>"$OUT/prompts.log"
            # The dialog's two buttons fill its bottom row: No on the left half, Yes on the right.
            if [ "$answer" = yes ]; then xdotool mousemove $((X + WIDTH * 3 / 4)) $((Y + HEIGHT - 38)) click 1
            else xdotool mousemove $((X + WIDTH / 4)) $((Y + HEIGHT - 38)) click 1; fi
            sleep 1.5
            for state in "$DATADIR"/orca_plugins/*/.install_state.json; do
                [ -f "$state" ] && jq -c --arg p "$(basename "$(dirname "$state")")" '{plugin: $p, permissions}' "$state"
            done >"$OUT/prompts/$file-install-state.json" 2>/dev/null
        done
        sleep 0.3
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
VERSION=$(python3 -c 'import sys, zipfile; z = zipfile.ZipFile(sys.argv[1]); m = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA")); print(next(l.split(": ", 1)[1] for l in z.read(m).decode().splitlines() if l.startswith("Version: ")))' /in/3d-print-log.whl)
log "plugin version $VERSION"
rm -rf "$PLUGIN_DIR" "$PROBE_DIR" "$DATADIR/orca_plugins/plugin_data" "$DATADIR/orca_plugins/config.json"
mkdir -p "$PLUGIN_DIR" "$PROBE_DIR"
# The layout PluginLoader::install_plugin creates: orca_plugins/<file name>/<file name> plus the
# .install_state.json sidecar. enabled=true is what activating it in the Plugins window writes.
# No permissions are granted, so every prompt the plugin causes shows up (and is recorded).
cp /in/3d-print-log.whl "$PLUGIN_DIR/3d-print-log.whl"
sidecar() { # sidecar <plugin name> <version>
    jq -n --arg name "$1" --arg version "$2" '{installed_from: "local", installed_version: $version,
        plugin_name: $name, enabled: true, capabilities: [],
        permissions: {fs_read: [], fs_readwrite: [], network_http: [], network_socket: [], process: [], threading: []}}'
}
sidecar "3d-print-log-orca-plugin" "$VERSION" >"$PLUGIN_DIR/.install_state.json"
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
place_main_window 0 0 1600 1000
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
    '{api_base: $api, web_base: $web, open_claim_page: "window", debug: true, slice_notification: true, template: "default"}')
echo "$CAPS" | jq -R --arg v "$VERSION" --argjson cfg "$CONFIG" \
    '{plugin_key: "3d-print-log", capability: ., plugin_version: $v, cap_config: $cfg}' \
    | jq -s '{config: .}' >"$DATADIR/orca_plugins/config.json"
cp "$DATADIR/orca_plugins/config.json" "$OUT/plugin-config.json"

# ---- 3. Sessions --------------------------------------------------------------------------------
write_cube_stl 20 >/tmp/cube.stl
# Where things are with the main window filling the 1600x1000 screen.
TAB_X=782; TAB_Y=54            # the "3D Print Log" tab, right of Project
LOG_X=1490; LOG_Y=185          # "Log to 3D Print Log" on the newest slice (the top row)
BROWSER_X=1511; BROWSER_Y=107  # "Open in browser"
PRINT_X=1524; PRINT_Y=54       # Print (a printer with a print host), top right in Preview

select_presets() { # select_presets <printer preset> <process preset>: what Orca selects on its next start
    local c=$DATADIR/OrcaSlicer.conf
    jq --arg m "$1" --arg p "$2" '.presets.machine = $m
        | (if any(.orca_presets[]; .machine == $m) then . else .orca_presets += [(.orca_presets[0] | .machine = $m)] end)
        | .orca_presets |= map(if .machine == $m then .process = $p else . end)' "$c" >/tmp/conf && cp /tmp/conf "$c"
}
dark_mode() { # dark_mode 0|1: Orca's theme on its next start
    jq --arg d "$1" '.app.dark_color_mode = $d' "$DATADIR/OrcaSlicer.conf" >/tmp/conf && cp /tmp/conf "$DATADIR/OrcaSlicer.conf"
}
uploads() { grep -ah "\[3D Print Log\] INFO Uploaded" "$DATADIR"/log/python_*.log 2>/dev/null | wc -l; }
wait_uploads() { # wait_uploads <count> [timeout s]
    local deadline=$(( $(date +%s) + ${2:-60} ))
    until [ "$(uploads)" -ge "$1" ]; do [ "$(date +%s)" -lt "$deadline" ] || return 1; sleep 1; done
}
prompts_in() { awk -F'\t' -v s="$1" '$2 == s' "$OUT/prompts.log" | wc -l; }
slice() { # slice: focus the 3D view, Slice plate (Ctrl+R), wait for SlicingJobComplete
    local before; before=$(grep -ac "event=SlicingJobComplete" "$DATADIR"/log/python_*.log 2>/dev/null | awk -F: '{s += $NF} END {print s + 0}')
    click 1040 600 1 1; xdotool key ctrl+r
    local deadline=$(( $(date +%s) + 90 )) now
    while :; do
        now=$(grep -ac "event=SlicingJobComplete" "$DATADIR"/log/python_*.log 2>/dev/null | awk -F: '{s += $NF} END {print s + 0}')
        [ "$now" -gt "$before" ] && break
        [ "$(date +%s)" -lt "$deadline" ] || { log "slicing did not finish in 90 s"; return 1; }
        sleep 0.5
    done
    sleep 1   # the notification is up for about 5 s
}
claim_url_on_web_base() { jq -r .url "$STORAGE/debug/last-upload.json" 2>/dev/null | grep -q "^$WEB_BASE/prints/new"; }
open_tab() { click "$TAB_X" "$TAB_Y" 1 4; }
log_slice() { # log_slice <label> [uploads before]: click Log on the newest slice and wait for the upload,
    # measuring how long the UI thread stops answering (freeze-<label>.txt: one ping per line)
    local label=$1 before=${2:-0} w
    w=$(main_window)
    python3 /e2e/ping.py "$w" "$OUT/freeze-$label.txt" 60 &
    local pinger=$!
    xdotool mousemove "$LOG_X" "$LOG_Y" click 1
    wait_uploads $((before + 1)) 60 || log "$label: no upload within 60 s"
    sleep 6   # the claim page window opens and loads
    kill "$pinger" 2>/dev/null; wait "$pinger" 2>/dev/null
    log "$label: longest UI stall $(sort -k2 -n "$OUT/freeze-$label.txt" | tail -1 | awk '{print $2}') ms"
}

run_session() { # run_session <name> <printer> <process> <expected uploads> <action function>
    local name=$1 printer=$2 process=$3 expected=$4 action=$5 got urls_before
    log "---- session $name: $printer / $process"
    echo "$name" >/tmp/scenario
    echo yes >/tmp/answer
    select_presets "$printer" "$process"
    rm -f "$DATADIR"/log/python_*.log "$STORAGE"/debug/last-upload.json
    urls_before=$(wc -l <"$OUT/browser-urls.log")
    launch_orca "$name" /tmp/cube.stl
    place_main_window 0 0 1600 1000
    shot "$name-ready"
    "$action"
    sleep 5   # anything late (a second upload) would land here
    got=$(uploads)
    shot "$name-done"
    stop_orca
    cat "$DATADIR"/log/python_*.log >"$OUT/$name-python.log" 2>/dev/null
    [ -f "$STORAGE/debug/last-upload.json" ] && cp "$STORAGE/debug/last-upload.json" "$OUT/$name-last-upload.json"
    tail -n +"$((urls_before + 1))" "$OUT/browser-urls.log" >"$OUT/$name-browser-urls.log"
    printf '%s\t%s\t%s\t%s\t%s\n' "$name" "$expected" "$got" \
        "$(grep -aoh 'INFO Uploaded .* (setting [0-9a-f-]*)' "$OUT/$name-python.log" | sed 's/.*(setting \(.*\))/\1/' | paste -sd ',')" \
        "$(prompts_in "$name")" >>"$OUT/results.tsv"
    check "session $name: $expected upload(s), got $got" test "$got" -eq "$expected"
}

# The claim page opens in its own Orca window titled "3D Print Log" (1040x820). Its position
# varies (centered over Orca the first time, top left when reopened), so clicks in it are
# relative to its client area: claim_window sets WX, WY, WW, WH.
claim_window() { # claim_window [timeout s]: true once the window is up
    local deadline=$(( $(date +%s) + ${1:-20} )) w X Y WIDTH HEIGHT
    until w=$(xdotool search --onlyvisible --name '^3D Print Log$' 2>/dev/null | tail -1) && [ -n "$w" ]; do
        [ "$(date +%s)" -lt "$deadline" ] || return 1; sleep 0.5
    done
    eval "$(xdotool getwindowgeometry --shell "$w")"; WX=$X; WY=$Y; WW=$WIDTH; WH=$HEIGHT
}
claim_window_gone() { sleep 2; ! xdotool search --onlyvisible --name '^3D Print Log$' >/dev/null 2>&1; }
wscroll() { # wscroll up|down <clicks>: the mouse wheel over the claim window
    local button=5; [ "$1" = up ] && button=4
    xdotool mousemove $((WX + 500)) $((WY + 400)); for _ in $(seq 1 "$2"); do xdotool click "$button"; sleep 0.05; done; sleep 1
}
open_claim_page() { # the logged slice's "Open claim page" (the row moves down when the tab shows a notice)
    local y; for y in 208 178; do click 1496 "$y" 1 1; claim_window 8 && return 0; done; return 1
}
session1() {
    slice
    shot "session1-notification"
    open_tab
    shot "session1-tab-light"
    log_slice session1 0
    check "session1: the claim page opened in an Orca window" claim_window 15
    sleep 3; shot "session1-claim-window"
    wscroll down 10; shot "session1-claim-material"
    wscroll down 10; shot "session1-claim-models"
    wscroll up 30
    click $((WX + 223)) $((WY + 165)) 1 1          # Back to OrcaSlicer
    check "session1: Back to OrcaSlicer closed the window" claim_window_gone
    shot "session1-back-to-list"
    check "session1: Open claim page reopened the window" open_claim_page
    sleep 4; shot "session1-claim-reopened"
    click $((WX + WW - 11)) $((WY - 31)) 1 1       # the window's close box (openbox title bar)
    check "session1: the close box closed the window" claim_window_gone
    open_claim_page; sleep 4
    xdotool mousemove $((WX + 900)) $((WY + 500)) click 3; sleep 1
    shot "session1-claim-right-click"              # Back is greyed out: the claim page is the first entry
    xdotool key Escape; sleep 0.5
    # Save inside the window: pick the seeded printer, Submit, then "Done, back to OrcaSlicer".
    wscroll down 5
    click $((WX + 498)) $((WY + 442)) 1 2          # Printer
    click $((WX + 298)) $((WY + 501)) 1 2          # the first printer
    click $((WX + 467)) $((WY + 741)) 1 6          # Submit
    shot "session1-print-saved"
    click $((WX + 252)) $((WY + 222)) 1 1          # Done, back to OrcaSlicer
    check "session1: Done closed the window" claim_window_gone
    shot "session1-after-save"
    check "session1: the claim page URL is on $WEB_BASE" claim_url_on_web_base
}
session_log() { slice; open_tab; shot "$(cat /tmp/scenario)-tab"; log_slice "$(cat /tmp/scenario)" 0; sleep 4; shot "$(cat /tmp/scenario)-claim-page"; }
session3() {
    slice; open_tab
    echo no-once >/tmp/answer
    click "$LOG_X" "$LOG_Y" 1 8           # the watcher answers No to the first prompt
    shot "session3-declined"
    check "session3: nothing was uploaded after No" test "$(uploads)" -eq 0
    check "session3: the plugin logged the declined permission" \
        grep -aq "declined a permission" "$DATADIR"/log/python_*.log
    local asked; asked=$(prompts_in session3)
    log_slice session3-again 0           # Log again: asked again, Yes
    check "session3: Log again asked again" test "$(prompts_in session3)" -gt "$asked"
    shot "session3-claim-page"
}
session4() { session_log; }
host() {
    slice
    sleep 4
    click "$PRINT_X" "$PRINT_Y" 1 4       # Print (sends to the printer's print host)
    wait_window "Send G-code to printer host" 30
    xdotool key ctrl+a; xdotool type --delay 20 "e2e-host-cube.gcode"; sleep 1
    shot "host-send-dialog"
    # Upload: 195,129 inside the dialog (xdotool's --window offsets miss under openbox).
    local w X Y WIDTH HEIGHT; w=$(xdotool search --onlyvisible --name "Send G-code to printer host" | tail -1)
    eval "$(xdotool getwindowgeometry --shell "$w")"
    click $((X + 195)) $((Y + 129)) 1 2
    wait_file_grep "$OUT/octoprint.log" '"filename": "e2e-host-cube.gcode"' 60 || log "OctoPrint received nothing"
    sleep 8
    check "host: the fake OctoPrint received e2e-host-cube.gcode once" \
        test "$(grep -c '"filename": "e2e-host-cube.gcode"' "$OUT/octoprint.log")" -eq 1
    check "host: the print-host send uploaded nothing to 3D Print Log" test "$(uploads)" -eq 0
    open_tab
    shot "host-tab-sent-to-print-host"
    log_slice host 0
    shot "host-claim-page"
}
open_browser() { # open_browser <label>: click Open in browser, wait for the URL
    local before; before=$(wc -l <"$OUT/browser-urls.log")
    click "$BROWSER_X" "$BROWSER_Y" 1 2
    wait_file_grep "$OUT/browser-urls.log" "prints/new/orca" 30 >/dev/null
    local deadline=$(( $(date +%s) + 30 ))
    until [ "$(wc -l <"$OUT/browser-urls.log")" -gt "$before" ]; do
        [ "$(date +%s)" -lt "$deadline" ] || { log "$1: no browser URL"; break; }; sleep 1
    done
    sleep 2
    shot "$1"
}
slice_only() {
    slice
    sleep 12
    check "slice: slicing alone uploaded nothing" test "$(uploads)" -eq 0
    open_tab
    open_browser "slice-open-in-browser"
    check "slice: Open in browser asked to run a command" test "$(prompts_in slice)" -ge 1
}
bambu() {
    slice
    sleep 12
    check "bambu: slicing alone uploaded nothing" test "$(uploads)" -eq 0
    open_tab
    shot "bambu-tab"
    open_browser "bambu-open-in-browser"
    check "bambu: Open in browser did not ask again" test "$(prompts_in bambu)" -eq 0
}

browser_mode() {
    # open_claim_page "browser" and a new install id (as on another computer): Log uploads, then
    # opens this install's uploads page in the browser instead of the claim page window. With
    # exactly one fresh upload, the web app opens it straight away (checked from the host).
    slice; open_tab
    local before; before=$(wc -l <"$OUT/browser-urls.log")
    log_slice browser-mode 0
    shot "browser-mode-after-log"
    check "browser-mode: Log opened the uploads page in the browser" \
        test "$(wc -l <"$OUT/browser-urls.log")" -gt "$before"
    check "browser-mode: no claim page window" claim_window_gone
}
set_open_mode() { # set_open_mode window|browser
    jq --arg m "$1" '.config |= map(.cap_config.open_claim_page = $m)' "$DATADIR/orca_plugins/config.json" >/tmp/cfg \
        && cp /tmp/cfg "$DATADIR/orca_plugins/config.json"
}

KLIPPER="MyKlipper 0.4 nozzle"; KLIPPER_PROCESS="0.20mm Standard @MyKlipper"
run_session session1 "$KLIPPER" "$KLIPPER_PROCESS" 1 session1
run_session session2 "$KLIPPER" "$KLIPPER_PROCESS" 1 session_log
run_session session3 "$KLIPPER" "$KLIPPER_PROCESS" 1 session3
# Dark mode: Orca's own setting, and on Linux the GTK theme it follows.
dark_mode 1; export GTK_THEME=Adwaita:dark
run_session session4 "$KLIPPER" "$KLIPPER_PROCESS" 1 session4
dark_mode 0; unset GTK_THEME
run_session host "E2E OctoPrint" "$KLIPPER_PROCESS" 1 host
run_session slice "$KLIPPER" "$KLIPPER_PROCESS" 0 slice_only
run_session bambu "Bambu Lab X1 Carbon 0.4 nozzle" "0.20mm Standard @BBL X1C" 0 bambu
cp "$STORAGE/install.json" "$OUT/install.json" 2>/dev/null
rm -f "$STORAGE/install.json"
set_open_mode browser
run_session browser-mode "$KLIPPER" "$KLIPPER_PROCESS" 1 browser_mode
cp "$STORAGE/install.json" "$OUT/install-browser-mode.json" 2>/dev/null

check "every browser URL is this install's uploads page" \
    test "$(grep -c . "$OUT/browser-urls.log")" -eq "$(grep -c '/prints/new/orca?device=' "$OUT/browser-urls.log")"
grep -ah "AUDIT\] block\|AUDIT BLOCKED" "$DATADIR"/log/debug_*.log* >"$OUT/audit-blocks.log" 2>/dev/null
cp "$PLUGIN_DIR/.install_state.json" "$OUT/install-state-final.json"
cp -r "$DATADIR/log" "$OUT/orca-logs"
cp "$DATADIR/OrcaSlicer.conf" "$OUT/OrcaSlicer.conf" 2>/dev/null

for f in "$OUT"/freeze-*.txt; do
    printf '%s\t%s ms\n' "$(basename "$f" .txt | sed 's/^freeze-//')" "$(sort -k2 -n "$f" | tail -1 | awk '{print $2}')"
done >"$OUT/freeze.tsv"
log "results (session, expected, uploads, setting ids, prompts):"
cat "$OUT/results.tsv"
log "prompts:"; cut -f2,3,4,5 "$OUT/prompts.log"
log "longest UI stall per Log click:"; cat "$OUT/freeze.tsv"
[ "$FAILURES" -eq 0 ] && log "PASS" || log "FAILED: $FAILURES check(s)"
exit "$FAILURES"
