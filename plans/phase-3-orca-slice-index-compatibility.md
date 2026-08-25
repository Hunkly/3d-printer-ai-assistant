# Phase 3 Orca Slice Index Compatibility

Status: VERIFIED

Date: 2026-08-24

## Scope

This is the Increment 3 precondition proof only. It does not change the
adapter, tests, profiles, or approved Increment 2 plans.

## Orca environment

- Executable: `C:\\Program Files\\OrcaSlicer\\orca-slicer.exe`
- Detected version: OrcaSlicer `2.3.2`
- Version source used by the repository: installed registry metadata (the
  repository detector also supports the installed `OrcaSlicer.dll` scan).
- Project configuration reference: `src/print_engineer/config.py` exposes
  `SlicerConfig.orca_install_path`; the default adapter install path is the
  executable above in `src/print_engineer/adapters/slicer/orca.py`.
- Direct `--version` and `--help` produced no usable output and no reliable
  textual exit status on this GUI build. No update, installation, or network
  access was used.

## Current adapter

`src/print_engineer/adapters/slicer/orca.py` constructs the argument in
`OrcaSlicerAdapter._build_slice_command()`:

```text
--slice 1
```

The value is hard-coded. `SliceJob` in `src/print_engineer/core/types.py` has
no plate/index field. The adapter assumes the current workflow selects one
ordinary Orca plate and validates the first emitted `plate_*.gcode` artifact.
It passes materialized process and printer JSON through `--load-settings`, the
materialized filament JSON through `--load-filaments`, and writes the export
under the per-job output directory.

The current model path accepts STL and 3MF inputs. A source 3MF can contain
multiple build items according to the model analyzer, but the current
single-model slicing contract and integration fixture use one STL imported by
Orca as one plate with one object.

Existing tests do not assert the command token itself. The unit success test
creates `plate_1.gcode` in its fake subprocess and parses the output; the real
integration test asserts `plate_1.gcode` and a `.gcode.3mf`.

## Proven CLI semantics

The installed Orca 2.3.2 binary contains this local resource/help string:

```text
Slice the plates: 0-all plates, i-plate i, others-invalid
```

Therefore:

- `--slice 0` selects all plates;
- `--slice i`, for a positive plate identifier `i`, selects plate `i`;
- other values are invalid.

The usable Orca output identifies the ordinary first plate as plate `1`:
`Metadata/slice_info.config` reported `index=1`, the output was
`plate_1.gcode`, and the archive contained `Metadata/plate_1.json`.
Consequently the selector is a plate identifier in Orca's one-based project
plate domain, with `0` reserved for the all-plates operation. It is not a
plate count, object index, boolean mode, or build-plate surface/type.

## Hermetic runtime proof

A disposable proof directory was created under the repository and populated
with:

- a generated 20 mm cube STL;
- materialized copies of the existing compatible A1 process, filament, and
  printer profiles selected through `find_compatible_triple()` and
  `ProfileMaterializer`;
- the same command structure used by the adapter:

```text
orca-slicer.exe <model>
  --load-settings process.json;printer.json
  --load-filaments filament.json
  --slice N
  --export-3mf cube_N.gcode.3mf
  --outputdir <run-dir>
```

The input was accepted by Orca as `(20.0, 20.0, 20.0)`, one part, with one
Orca plate generated from the STL.

### `--slice 0`

- Exit code: `0`.
- Runtime: approximately `1.0 s` in the successful controlled run.
- Stdout/stderr: empty.
- Artifacts: `plate_1.gcode` (approximately 443 KB) and a full
  `cube_0b.gcode.3mf` (approximately 74 KB).
- The G-code header identified OrcaSlicer `2.3.2`; the archive contained
  `Metadata/plate_1.gcode`, `Metadata/plate_1.json`, and
  `Metadata/slice_info.config` with plate index `1` and nonzero prediction,
  weight, and filament metadata.
- Meaning: the all-plates selector sliced the only plate, plate `1`.

### `--slice 1`

- Exit code: `0`.
- Runtime: approximately `1.03 s`.
- Stdout/stderr: empty.
- Artifacts: `plate_1.gcode` (approximately 443 KB) and a full
  `cube_1.gcode.3mf` (approximately 74 KB).
- The G-code, plate metadata, slice metadata, and parsed identity matched the
  `--slice 0` result apart from normal timestamps/metadata details.
- Meaning: the explicit positive plate selector sliced plate `1`.

### Invalid control `--slice 2`

- The input had only plate `1`, so `2` was out of range.
- Exit code: `0` on this CLI build, with empty stdout/stderr.
- Artifact: only a small `cube_2.gcode.3mf` project archive; no
  `plate_1.gcode`, no plate G-code entry, and no `slice_info.config` plate
  metadata.
- Meaning: Orca's CLI is permissive at process exit for this invalid target;
  post-slice artifact validation must reject the result. It does not fall back
  to plate `1`.

A synthetic two-plate archive was attempted but Orca rejected that hand-built
archive as malformed; it was not counted as evidence. The installed binary's
embedded selector resource and the valid one-plate runtime control are
sufficient to distinguish `0` (all) from `1` (plate 1).

## Prior spike explanation

The prior result cannot be reproduced from a retained fixture or log in the
repository: the Increment 2 plan records the outcome but not the disposable
input archive. The current controlled experiment proves that `--slice 1` is a
valid Orca 2.3.2 operation for the repository's current one-plate STL
workflow. Therefore the prior `--slice 1` post-slice failure was not expected
Orca indexing behavior and was fixture/configuration or post-slice artifact
validation specific. It cannot be attributed to a general Orca 2.3.2 defect
without the prior disposable fixture. `--slice 0` worked there because it
selects all plates; on a one-plate input that includes plate `1`.

## Production assessment

**CURRENT --slice 1 CORRECT**

No production change from `1` to `0` is required for the existing contract.
Using `0` would broaden the operation to all plates and would be incorrect as
the canonical selector if a future input contains more than one plate.

The current hard-coding is correct only because the repository contract is
single-plate. If that contract is later expanded to arbitrary multi-plate
projects, the value must be derived from an explicit selected Orca plate
identity rather than silently changing the constant to `0`.

## Increment 3 dependency

Compatibility precondition: cleared.

Increment 3 must retain:

```text
--slice 1
```

for the current single-plate workflow. The compatibility result does not
require a `SliceJob` plate-index field, a new CLI contract, or an adapter
change. Increment 3 may still modify the already-authorized slicing and
post-slice finalization paths to consume realized overlays and verify the
selected plate/build-plate evidence, but that is separate from this selector
proof. A future multi-plate increment would need an explicit plate identity
and a derived positive Orca plate selector.

## Build plate versus plate index

These are separate concepts:

- `curr_bed_type` selects the Orca build-plate surface/type (`Cool Plate`,
  `Textured PEI Plate`, or `High Temp Plate`) and is observed after slicing as
  a bed-type value such as `cool_plate`.
- `--slice N` selects the Orca project plate target: `0` means all plates and
  positive `N` means plate `N`.

The build-plate realization must not infer one from the other.

## Existing test impact

Because production remains `--slice 1`, no existing test requires adjustment
for this precondition. The relevant existing assertions are:

- `tests/unit/test_orca_adapter.py::test_slice_success_parses_all_stats` —
  fake output is `plate_1.gcode`; it does not inspect the command token.
- `tests/integration/test_orca_slice.py::test_orca_slices_generated_cube` —
  real output must be `plate_1.gcode` and a `.gcode.3mf`.
- `tests/unit/test_slicer_gcode.py` — parser fixtures use
  `plate_1.gcode`/`Metadata/plate_1.json`; these test output parsing, not CLI
  selector semantics.

The timeout test `tests/unit/test_orca_adapter.py::test_slice_timeout_raises`
does not depend on the selector: it replaces the subprocess result with a
timeout and only checks the timeout error. It is not relevant to this proof.

If a future change derives a plate index, add a focused command-construction
test and a multi-plate integration fixture; do not alter the parser fixtures
to imply zero-based plate numbering.

## Operations and cleanup

No production files, tests, installed profiles, user profiles, dependencies,
printer state, MQTT, network, staged files, commits, or pushes were changed.
The disposable proof directory and all generated models, profile copies,
3MFs, G-code, and logs were removed successfully; no residual proof files
remain.
