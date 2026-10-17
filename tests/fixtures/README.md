# Test fixtures

Real OrcaSlicer G-code, gzipped (`gzip -9 -n`), copied from the Slic3r post-processing uploader's
parser tests: `PrintLog.SlicerSettings.Tests/TestData/OrcaSlicer/` in
[Slic3rPostProcessingUploader](https://github.com/HoffmanEngineering/Slic3rPostProcessingUploader)
at `810dc93`.

| File | What it is |
|---|---|
| `orcaslicer-2.4.0-benchy-x16.gcode.gz` | 16 Benchies on a Prusa MK4S; four layers of the real export are kept |
| `orcaslicer-2.2.0-rc-calibration-cube.gcode.gz` | One calibration cube |
| `orcaslicer-2.2.0-rc-calibration-cube-two-filament.gcode.gz` | A cube and a two-color calibration cube |

## Expected object sizes

`test_object_bounds.py` checks the port of `ObjectBoundsScanner` against the uploader itself. These
are the `{{models}}` lines `Slic3rPostProcessingUploader --dry-run --full --opt-out-telemetry
<fixture>` printed (uploader at `db1e636`): extrusion box plus one `outer_wall_line_width`, rounded
to 0.1 mm.

| Fixture | `{{models}}` |
|---|---|
| benchy-x16 | `3DBenchy.drc ×16 55.6 × 30.9 × 48.0 mm` (`outer_wall_line_width` 0.45) |
| calibration-cube | `Calibration Cube.stl ×1 20.0 × 20.0 × 20.0 mm` (0.4) |
| calibration-cube-two-filament | `Calibration Cube.stl ×1 51.6 × 23.4 × 20.0 mm`, `Cube ×1 23.0 × 23.0 × 23.0 mm` (0.4) |
