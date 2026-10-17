"""Checks the API side of an e2e run: the CuraSettings rows the uploads created.

Reads <out>/results.tsv (session, expected, uploads, setting ids, prompts) written by
scripts/e2e.sh, queries SQL Server in its Docker container for the rows created since the run
started, and checks that each uploaded setting id is stored with a note, print time, filament
usage with a weight, file and print name, the 20 mm cube's size in the note and a snapshot.
Decodes each snapshot to <out>/snapshot-<session>.png and writes <out>/api-rows.json.
Exit code: the number of failed checks.

    python -I e2e/verify.py --out e2e/out --since 2026-10-17T16:00:00Z
"""

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path


def query_rows(container, database, password, since):
    sql = (
        "SET NOCOUNT ON; SELECT CONVERT(varchar(36), Id) + '|' + ISNULL(Slicer, '') + '|' "
        "+ ISNULL(CuraVersion, '') + '|' + ISNULL(PluginVersion, '') + '|' "
        "+ CONVERT(varchar(40), CreatedDate, 127) + '|' + ISNULL(CONVERT(varchar(20), UserId), '') "
        "+ '|' + REPLACE(REPLACE(Settings, CHAR(13), ''), CHAR(10), ' ') FROM CuraSettings "
        f"WHERE CreatedDate >= '{since}' ORDER BY CreatedDate"
    )
    command = [
        "docker",
        "exec",
        container,
        "/opt/mssql-tools18/bin/sqlcmd",
        "-C",
        "-S",
        "localhost",
        "-U",
        "sa",
        "-P",
        password,
        "-d",
        database,
        "-y",
        "0",
        "-Q",
        sql,
    ]
    output = subprocess.run(command, capture_output=True, check=True).stdout.decode(
        "utf-8", "replace"
    )
    rows = []
    for line in output.splitlines():
        parts = line.strip().split("|", 6)
        if len(parts) != 7:
            continue
        setting_id, slicer, version, plugin, created, user_id, settings = parts
        rows.append(
            {
                "id": setting_id.lower(),
                "slicer": slicer,
                "slicerVersion": version,
                "pluginVersion": plugin,
                "createdDate": created,
                "userId": user_id or None,
                "settings": json.loads(settings),
            }
        )
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--since", required=True, help="UTC ISO time the run started")
    parser.add_argument("--sql-container", default="3d-print-log-api-sqlserver-1")
    parser.add_argument("--database", default="PrintLogDb_OrcaE2E")
    parser.add_argument("--password", default="YourStrong@Passw0rd")  # the local dev container's
    args = parser.parse_args()
    out = Path(args.out)
    failures = 0

    def check(ok, what):
        nonlocal failures
        print(("PASS " if ok else "FAIL ") + what)
        failures += 0 if ok else 1

    rows = query_rows(args.sql_container, args.database, args.password, args.since)
    by_id = {row["id"]: row for row in rows}
    results = [
        line.split("\t") for line in (out / "results.tsv").read_text().splitlines() if line.strip()
    ]
    expected_total = sum(int(r[1]) for r in results)
    check(
        len(rows) == expected_total,
        f"{expected_total} CuraSettings row(s) created, found {len(rows)}",
    )

    summary = []
    for scenario, expected, _uploads, *ids in results:
        ids = [i.lower() for i in (ids[0].split(",") if ids and ids[0] else [])]
        check(
            len(ids) == int(expected),
            f"scenario {scenario}: {expected} upload(s) logged with a setting id, got {len(ids)}",
        )
        for setting_id in ids:
            row = by_id.get(setting_id)
            check(row is not None, f"scenario {scenario}: setting {setting_id} is stored")
            if row is None:
                continue
            s = row["settings"]
            usage = s.get("filamentUsage") or []
            check(
                bool(s.get("note")),
                f"scenario {scenario}: note present ({len(s.get('note') or '')} chars)",
            )
            check(
                bool(s.get("estimated_print_time_seconds")),
                f"scenario {scenario}: estimated time {s.get('estimated_print_time_seconds')} s",
            )
            amounts = [(u.get("estimatedLengthInM"), u.get("estimatedAmountMg")) for u in usage]
            check(bool(usage), f"scenario {scenario}: filament usage (m, mg) {amounts}")
            check(
                any((mg or 0) > 0 for _m, mg in amounts),
                f"scenario {scenario}: an estimated weight in grams",
            )
            check(
                "20.0 × 20.0 × 20.0 mm" in (s.get("note") or ""),
                f"scenario {scenario}: the note lists the cube as 20.0 x 20.0 x 20.0 mm",
            )
            check(
                bool(s.get("file_name")), f"scenario {scenario}: file_name {s.get('file_name')!r}"
            )
            check(
                bool(s.get("print_name")),
                f"scenario {scenario}: print_name {s.get('print_name')!r}",
            )
            snapshot = s.get("snapshot")
            check(bool(snapshot), f"scenario {scenario}: snapshot present")
            if snapshot:
                png = base64.b64decode(snapshot)
                check(
                    png[:8] == b"\x89PNG\r\n\x1a\n",
                    f"scenario {scenario}: snapshot is a PNG ({len(png)} bytes)",
                )
                (out / f"snapshot-{scenario}.png").write_bytes(png)
            summary.append(
                {
                    "scenario": scenario,
                    "id": setting_id,
                    "slicer": row["slicer"],
                    "slicerVersion": row["slicerVersion"],
                    "pluginVersion": row["pluginVersion"],
                    "userId": row["userId"],
                    "file_name": s.get("file_name"),
                    "print_name": s.get("print_name"),
                    "estimated_print_time_seconds": s.get("estimated_print_time_seconds"),
                    "material_used_mg": s.get("material_used_mg"),
                    "filamentUsage": usage,
                    "note": s.get("note"),
                }
            )
    (out / "api-rows.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"{failures} failed check(s); rows in {out / 'api-rows.json'}")
    return failures


if __name__ == "__main__":
    sys.exit(main())
