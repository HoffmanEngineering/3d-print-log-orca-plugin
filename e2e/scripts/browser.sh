#!/bin/sh
# Stands in for a web browser: BROWSER and xdg-open point here. Records each URL it is asked to open.
for url in "$@"; do
    printf '%s\t%s\n' "$(date -u +%FT%TZ)" "$url" >> /out/browser-urls.log
done
exit 0
