# OrcaSlicer plugin: implementation plan

Spec: [2026-10-17-orcaslicer-plugin-design.md](../specs/2026-10-17-orcaslicer-plugin-design.md). Tracking issue: see
the repo's issues ("OrcaSlicer plugin v0.1").

Three stories in three repos. Each is TDD and lands as one PR. A and B run in parallel; C needs B's package.

## A. Plugin (`3d-print-log-orca-plugin`, branch `feat/plugin`)

1. Scaffold: `pyproject.toml` (hatchling, package `printlog_orca`, `requires-python >=3.12`, no runtime
   dependencies, dev extras pytest + ruff), `tests/fake_orca/` (a stand-in `orca` module with the classes, enums,
   `ExecutionResult`, `register_capability`, `host.plugin.storage()` and a recording `on_lifecycle_event` driver),
   `.github/workflows/ci.yml` (ruff check, ruff format --check, pytest, `python -m build` and upload the wheel as an
   artifact).
2. `scrub.py`: port `SlicerSettingsScrubber` rules (fragments and whole keys) for text lines and preset dicts.
3. `gcode_window.py`: head/tail read matching `GcodeWindow.ReadFromFile` (BOM strip, `\n` join, 2,000,000-byte
   windows, whole file when ≤ 4,000,000 bytes).
4. `object_bounds.py`: port `ObjectBoundsScanner`. Oracle: run the uploader with `--dry-run --full` on its Orca
   fixtures and compare the `{{models}}` lines (bounds + outer-wall line width, rounded) to what the port gives.
5. `presets.py`: resolve user presets by name under `<data_dir>/user/<id>/{process,filament,machine}/`, follow
   `inherits` while the parent is a user preset, scrub.
6. `triggers.py` + `staging.py`: the decision table and the single staged slice, with an injectable clock.
7. `payload.py` + `client.py`: multipart body (gzip `gcode` part, `metadata` JSON), urllib POST, 30 s timeout,
   status mapping, URL builder. Tested against `http.server` on localhost.
8. `worker.py`: background thread running steps 3 to 7, then `webbrowser.open`; cleanup of temp files.
9. `plugin.py` + `script.py`: the capabilities wired to the above; never raise out of `execute` or
   `on_lifecycle_event`.
10. README: install (Plugins > Browse plugins > Install local plugin, the wheel from the GitHub release), adding the
    capability to a process preset, what each prompt means, config keys, troubleshooting (log location).
11. `release.yml`: on `v*` tags, build the wheel and attach it to a GitHub release. (Pushing a tag is Chris's call.)

## B. Library (`Slic3rPostProcessingUploader`, branch `feat/gcode-parser-entry-point`)

1. `ParseOptions` gains in-memory `Models` and `UserPresets` sources; `ModelsPlaceholder` and
   `ModifiedSettingsPlaceholder` use them when present and the disk/toolpath otherwise.
2. Public `SlicerGcodeParser.Parse(gcode, SlicerGcodeOptions)` returning `SlicerGcodeResult` (slicer, version,
   settings JSON identical to the uploader's `CuraSettingDto.settings`). Public types: `SlicerGcodeParser`,
   `SlicerGcodeOptions`, `SlicerGcodeResult`, `ModelBounds`, `UserPresetFile`. Update the pinned public-surface test.
3. Equivalence tests: for each fixture, the in-memory parse equals the uploader's disk parse.
4. README "Releasing the library": note 0.2.0. Chris pushes `lib-v0.2.0` after merge.

## C. API (`3d-print-log-api`, branch `feat/slicer-uploads`)

1. Bump `PrintLog.SlicerSettings` to 0.2.0. Until it is on nuget.org, develop against a local pack (a local
   `nuget.config` source that is not committed); the PR stays draft until the package is published.
2. `SlicerController` `POST api/Slicer/uploads`: multipart parsing, size caps, capped gunzip, scrubbing, parse,
   store `CuraSetting`, return `NewCuraSettingsDto`.
3. Anonymous per-IP rate limit policy (10/min, 200/day) with `Retry-After`.
4. Integration tests per the spec's list.

## After the alpha is installed (Chris)

Run the spec's spike list. Fix what it finds, then tag `v0.1.0` of the plugin.

## A2. Consent rework (plugin, same branch)

The spec's Decision 3 (A2 + B4, B2 fallback). TDD, one commit each:

1. Fake `orca`: modal prompts on the UI thread (answerable, `PermissionError` on No), denial off it, strict
   tracking of plugin I/O outside an audit context, `orca.pages` and a `Page` driver, UI-only notifications.
2. Plugin: drop the background thread; events only record; the "3D Print Log" Pages tab with the two-message Log
   flow; the synchronous uploader; `installId`; Open in browser; the hook keeps a copy; the script uploads in
   place; `PrintJobStarted` replaces the `PrintStateChanged` heuristic.
3. Docs: spec, README, PR body.
