#!/bin/bash
# Helpers shared by e2e.sh and calibration sessions (source it: `. /e2e/lib.sh`).
# Coordinates are for a 1600x1000 screen with the main window placed by place_main_window.

OUT=${OUT:-/out}
DATADIR=${DATADIR:-/data/orca}
ORCA=/opt/orca/AppRun
STEP_FILE=$OUT/.step

log()  { echo "[e2e] $*"; }
shot() { # shot <name>: numbered full-screen screenshot in $OUT
    local n; n=$(( $(cat "$STEP_FILE" 2>/dev/null || echo 0) + 1 )); echo "$n" >"$STEP_FILE"
    import -display :99 -window root "$OUT/$(printf '%02d' "$n")-$1.png"
}
fail() { log "FAIL: $*"; shot "fail" || true; exit 1; }
click() { xdotool mousemove "$1" "$2" click "${3:-1}"; sleep "${4:-2}"; }
wclick() { # wclick <window title regex> <x> <y> [sleep]: coordinates relative to that window
    local w; w=$(xdotool search --onlyvisible --name "$1" 2>/dev/null | tail -1 || true)
    [ -n "$w" ] || fail "no window '$1' to click in"
    xdotool mousemove --window "$w" "$2" "$3" click 1; sleep "${4:-2}"
}
main_window() { # the largest visible "... - OrcaSlicer" window
    local best="" best_area=0 w area
    for w in $(xdotool search --onlyvisible --name " - OrcaSlicer" 2>/dev/null || true); do
        eval "$(xdotool getwindowgeometry --shell "$w" 2>/dev/null)"; area=$(( ${WIDTH:-0} * ${HEIGHT:-0} ))
        [ "$area" -gt "$best_area" ] && { best=$w; best_area=$area; }
    done
    echo "$best"
}
place_main_window() { # place_main_window <x> <y> <w> <h>
    local m i   # the title can be briefly empty while Orca loads the model
    for i in $(seq 1 30); do m=$(main_window); [ -n "$m" ] && break; sleep 1; done
    [ -n "$m" ] || fail "no main window"
    xdotool windowmove --sync "$m" "$1" "$2"; xdotool windowsize --sync "$m" "$3" "$4"; sleep 2
}
wait_window() { # wait_window <title regex> [timeout s]
    local deadline=$(( $(date +%s) + ${2:-60} ))
    until xdotool search --onlyvisible --name "$1" >/dev/null 2>&1; do
        [ "$(date +%s)" -lt "$deadline" ] || fail "window '$1' did not appear"
        sleep 1
    done
}
wait_gone() { # wait_gone <title regex> [timeout s]
    local deadline=$(( $(date +%s) + ${2:-60} ))
    while xdotool search --onlyvisible --name "$1" >/dev/null 2>&1; do
        [ "$(date +%s)" -lt "$deadline" ] || fail "window '$1' did not close"
        sleep 1
    done
}
wait_file_grep() { # wait_file_grep <file> <regex> [timeout s]: true once the file has a matching line
    local deadline=$(( $(date +%s) + ${3:-60} ))
    until grep -qE "$2" "$1" 2>/dev/null; do
        [ "$(date +%s)" -lt "$deadline" ] || return 1
        sleep 1
    done
}
windows() { # list visible windows (id, title) for debugging
    local w; for w in $(xdotool search --onlyvisible --name "." 2>/dev/null); do
        echo "$w $(xdotool getwindowname "$w" 2>/dev/null)"
    done
}
start_display() {
    Xvfb :99 -screen 0 1600x1000x24 >"$OUT/xvfb.log" 2>&1 &
    sleep 2
    openbox >"$OUT/openbox.log" 2>&1 &
    sleep 1
}
launch_orca() { # launch_orca <log name> [args...]: start Orca on $DATADIR, wait for the main window
    local name=$1; shift
    ( cd /opt/orca && BROWSER=/e2e/browser.sh ./AppRun --datadir "$DATADIR" "$@" >"$OUT/orca-$name.log" 2>&1 ) &
    ORCA_PID=$!; disown "$ORCA_PID" 2>/dev/null || true
    wait_window " - OrcaSlicer" 180
    sleep 8
}
stop_orca() {
    pkill -f "/opt/orca/bin/orca-slicer" 2>/dev/null || true
    pkill -x orca-slicer 2>/dev/null || true
    pkill -x AppRun 2>/dev/null || true
    local i; for i in $(seq 1 15); do pgrep -f /opt/orca/bin >/dev/null || break; sleep 1; done
    pkill -9 -f /opt/orca/bin 2>/dev/null || true
}
write_cube_stl() { # write_cube_stl <size mm>
    local s=${1:-20}
    echo "solid cube"
    tri() { echo " facet normal 0 0 0"; echo "  outer loop"; for v in "$@"; do echo "   vertex $v"; done; echo "  endloop"; echo " endfacet"; }
    tri "0 0 0" "$s 0 0" "$s $s 0";   tri "0 0 0" "$s $s 0" "0 $s 0"
    tri "0 0 $s" "$s $s $s" "$s 0 $s"; tri "0 0 $s" "0 $s $s" "$s $s $s"
    tri "0 0 0" "0 $s 0" "0 $s $s";   tri "0 0 0" "0 $s $s" "0 0 $s"
    tri "$s 0 0" "$s $s $s" "$s $s 0"; tri "$s 0 0" "$s 0 $s" "$s $s $s"
    tri "0 0 0" "0 0 $s" "$s 0 $s";   tri "0 0 0" "$s 0 $s" "$s 0 0"
    tri "0 $s 0" "$s $s $s" "0 $s $s"; tri "0 $s 0" "$s $s 0" "$s $s $s"
    echo "endsolid cube"
}
# The plugin's toast ("Sliced <model>. Log to 3D Print Log") stacks with Orca's own notifications at
# the bottom right of the 3D view, so its height varies: find the link by template matching.
LINK_TEMPLATE=/e2e/templates/log-link.png
SEARCH_X=840; SEARCH_Y=560; SEARCH_W=480; SEARCH_H=360
find_link() { # prints "x y" (screen centre of the link) when the toast is on screen
    local result score pos
    # A modal permission dialog takes the clicks: wait until it is answered.
    xdotool search --onlyvisible --name "^Plugin permission" >/dev/null 2>&1 && return 1
    import -window root /tmp/screen.png
    result=$(compare -metric RMSE -subimage-search \
        <(convert /tmp/screen.png -crop ${SEARCH_W}x${SEARCH_H}+${SEARCH_X}+${SEARCH_Y} +repage png:-) \
        "$LINK_TEMPLATE" null: 2>&1 | head -1)   # e.g. "123.4 (0.0019) @ 144,119"
    score=$(echo "$result" | sed -n 's/.*(\([0-9.e-]*\)).*/\1/p')
    pos=$(echo "$result" | sed -n 's/.*@ \([0-9]*\),\([0-9]*\).*/\1 \2/p')
    [ -n "$score" ] && [ -n "$pos" ] && awk -v s="$score" 'BEGIN { exit !(s < 0.08) }' || return 1
    set -- $pos
    echo "$(( SEARCH_X + $1 + 70 )) $(( SEARCH_Y + $2 + 11 ))"
}
wait_link() { # wait_link [timeout s]: prints the link position once it appears
    local deadline=$(( $(date +%s) + ${1:-30} )) at
    until at=$(find_link); do
        [ "$(date +%s)" -lt "$deadline" ] || return 1
    done
    echo "$at"
}
click_log_link() { # click_log_link [timeout s]: waits for the toast and clicks "Log to 3D Print Log"
    local at; at=$(wait_link "${1:-30}") || return 1
    set -- $at
    xdotool mousemove "$1" "$2"; sleep 0.3; xdotool click 1
    log "clicked 'Log to 3D Print Log' at $1,$2"
}
