# Phase 3 — Real Orca Filament Type Compatibility Fix

Status: APPROVED

## Real failure / reproduction

The integrated Phase 3 chain selected:

- goal: `balanced`
- printer: `Bambu Lab A1 0.4 nozzle`
- process: `0.20mm Standard @BBL A1`
- filament: `Bambu PLA Tough+ @base`
- plate: `cool_plate`
- nozzle: `0.4`

The software-only smoke path reached `SetupEngine.recommend()` and deterministic
selection, then failed in `SetupRealizer.realize()` with
`material_not_provable`. It therefore did not materialize a successful
realization, invoke OrcaSlicer, or produce `SliceExecutionSuccess`.

The failure is reproducible hermetically with the materialized profile shape
`{"filament_type": ["PLA"]}`.

## Root cause

`src/print_engineer/adapters/slicer/realization.py` currently reads
`filament_data.get("filament_type")` into `material_value` and accepts it only
when it is a non-blank `str`. A list, including the actual Orca singleton list,
is classified as unknown and raises `material_not_provable`.

The current comparison is otherwise exact: after the scalar check,
`selected_setup.material != material_value` raises
`material_profile_mismatch`. Expected material authority comes from the
selected setup produced by the deterministic recommendation/selection path;
actual material evidence comes only from the exact resolved and materialized
filament profile. The fix must preserve this exact authority and comparison
model, changing only representation interpretation.

## Orca profile evidence

Inspection used only the local repository-supported `ProfileRepository` and
`ProfileMaterializer` against `%APPDATA%\OrcaSlicer`; no installed files were
modified and no network was accessed.

Selected profile:

- `Bambu PLA Tough+ @base`
- system profile inherits `fdm_filament_pla`
- materialized content contains `filament_type: ["PLA"]`

Other representative materialized profiles:

- `Bambu PLA Basic @base` → `["PLA"]`
- `Bambu ABS @base` → `["ABS"]`
- `Bambu PLA Tough+ @BBL A1` → `["PLA"]`

The local materialized filament inventory observed 1,023 singleton lists and
35 missing values. The observed singleton values included `PLA`, `PETG`, `PC`,
`TPU`, `ABS`, `ASA`, `PVA`, `PA-CF`, `PLA-CF`, and other exact Orca material
classifications. No multi-value list was observed in the installed materialized
filament profiles, and no scalar material value was observed there.

The raw selected profile omits the field because it inherits it; the
materializer resolves that inheritance and emits the authoritative
`["PLA"]` value. This establishes that the relevant shape is a real
materialized Orca representation, not a test-only artifact.

## Orca semantics

The local materializer preserves list values as lists; it does not document or
implement a multi-value material union. The local recommendation/settings
parsers use a generic first-value helper for several profile fields, but that
permissive helper is not sufficient evidence that multiple material classes
are interchangeable for realization.

Therefore:

- a singleton list is one authoritative material classification;
- a multi-value list is ambiguous for this exact compatibility proof and must
  fail closed;
- the implementation must not use an unconditional `list[0]` fallback;
- no multi-value interpretation, aliasing, or material union is introduced by
  this correction.

## Authority model

The correction belongs at the realization/profile semantic interpretation
boundary in `src/print_engineer/adapters/slicer/realization.py`.

The raw authoritative materialized resource remains the source of truth:

- `ProfileReference.identity` remains the exact selected `ProfileIdentity`;
- `ProfileReference.content` remains the exact materialized Orca JSON,
  including `filament_type: ["PLA"]`;
- `ProfileReference.content_sha256` remains the digest of that unchanged
  canonical semantic JSON;
- `RealizationResource.reference`, `content_sha256`, and resource identity
  remain derived from that exact reference/content;
- no repository re-resolution or profile substitution is added.

The interpreted material classification is a local deterministic value used
only for material provability and the existing exact selected-material
comparison. It is not written back into the profile JSON and is not a second
resource identity.

## Accepted and rejected representations

The normalization helper/rule should accept exactly:

- non-blank `str` → the same exact string value;
- a list containing exactly one non-blank `str` → that one exact string value.

The rule should reject exactly:

- missing or `None` → existing `material_not_provable` behavior;
- empty list → `material_not_provable`;
- list with zero or more than one item when not exactly one item →
  `material_not_provable` (multi-value is ambiguous);
- a singleton list whose element is not a `str` → `material_not_provable`;
- blank or whitespace-only string, including a singleton blank string →
  `material_not_provable`;
- non-string, non-list values → `material_not_provable`.

The accepted value is not stripped, lowercased, aliased, fuzzy-matched, or
otherwise coerced. In particular, `"PLA"` remains `"PLA"`, and
`["PLA"]` is interpreted as `"PLA"` only for the compatibility/provability
check. The existing exact `selected_setup.material == interpreted_value`
comparison remains authoritative.

## Materialized config and Increment 3 implications

`SliceExecutor._write_configs()` parses each `ProfileReference.content`, applies
only the existing printer/process overlays, and writes the filament profile
content without a material normalization step. The later `ProfileInfo` passed
to the Orca adapter also uses the same unchanged filament content.

Consequently Increment 3 must receive/write the original Orca-compatible
representation:

```json
"filament_type": ["PLA"]
```

The plan must not change Increment 3, `_write_configs()`, the Orca process
runner, or the G-code parser. Preserving the list is necessary to allow the
real Orca 2.3.2 profile contract to reach slicing after realization succeeds.

## Identity implications

Because raw materialized JSON remains unchanged:

- `RealizationResource.content_sha256` does not change merely because
  `["PLA"]` is interpreted as `"PLA"`;
- the filament resource `identity` remains based on the actual authoritative
  profile content, exact `ProfileReference`, capability, and existing resource
  structure;
- top-level `EffectiveSliceInputs.identity` / realization identity remains
  based on the existing semantic inputs and the exact profile content digest;
- no competing scalar-content resource identity is created;
- a genuine change to raw profile content or profile authority continues to
  change the appropriate identity exactly as before.

## Failure behavior

The helper must fail closed before effective realization is returned whenever
the field cannot yield exactly one non-blank string classification. Missing,
empty, malformed, blank, and ambiguous values retain the existing
`material_not_provable` category. A valid but different exact classification
continues to produce the existing `material_profile_mismatch` category.

No inference from the filament profile name is allowed at this realization
boundary. No comparison is weakened to a name match or a normalized/fuzzy
material vocabulary.

## Production scope

Planned production change:

- `src/print_engineer/adapters/slicer/realization.py`: add a narrow private
  deterministic interpretation helper and replace the current scalar-only
  `filament_type` validation with that helper; retain the interpreted scalar
  in `ActualInputIdentity`, `EffectiveSliceInputs.material`, and the existing
  exact comparison.

No helper/type file is required unless implementation review proves the
private helper cannot remain local to realization. Do not modify recommendation
ranking, preparation contracts, profile repository/materializer behavior,
Increment 3 execution, Orca process execution, G-code parsing, printer code,
or MCP registration.

## Focused test scope

Extend `tests/unit/test_setup_realization.py` with hermetic tests using the
existing local fixture conventions and `ProfileRepository`/`ProfileMaterializer`:

1. Existing scalar `"PLA"` still realizes successfully.
2. A Bambu PLA Tough+-style material profile with `filament_type: ["PLA"]`
   realizes successfully and proves `PLA`.
3. `["PETG"]` when `PLA` is selected produces
   `material_profile_mismatch` (or the exact current mismatch category).
4. `[]` fails with `material_not_provable`.
5. `["PLA", "PETG"]` fails with `material_not_provable`; no first-item
   fallback is permitted.
6. `["PLA", 1]` fails with `material_not_provable`.
7. Missing `filament_type` preserves `material_not_provable`.
8. `"   "` and a singleton blank string fail with
   `material_not_provable`.
9. The singleton-list realization preserves the exact raw materialized
   filament JSON in `effective_inputs.filament.content` and in the filament
   resource reference.
10. The same raw semantic content under different `ProfileReference` authority
    (`setting_id`) remains a distinct resource/top-level realization identity.
11. The singleton-list resource digest and identity are deterministic and are
    calculated from the unchanged actual resource content.
12. The realized filament config input remains `["PLA"]`, proving that the
    compatibility interpretation does not rewrite the Orca-facing config.

The fixture must be hermetic and must not depend on the user's installed Orca
path during ordinary unit tests.

## Regression scope

After implementation, run these focused paths as justified by the changed
contract:

- `tests/unit/test_setup_realization.py`
- `tests/unit/test_slice_execution.py`
- `tests/unit/test_preparation_contract.py`
- `tests/unit/test_orca_adapter.py`
- exact recommendation tests covering setup/filament material selection
- directly relevant profile/materializer tests, if the focused fixture exposes
  a regression there

Do not bundle or reinterpret these known unrelated issues:

- `tests/unit/test_print_context.py::test_ambiguous_prefix_match_raises`;
- the Windows timeout test;
- existing `tests/unit/test_preparation_contract.py` Mypy errors.

Also run focused Ruff/Mypy checks for the changed realization module and test
module, classifying unrelated pre-existing failures separately.

## Required post-fix real smoke retest

After BUILD and independent review, the correction is not fully validated until
the software-only real smoke test passes using the locally installed
OrcaSlicer 2.3.2 and authoritative local profiles:

`SetupEngine.recommend()` → deterministic selection → real
`SetupRealizer.realize()` → real `SliceExecutor` → OrcaSlicer 2.3.2 with
`--slice 1` → exact `plate_1.gcode` → `SliceExecutionSuccess`.

The smoke test must use the actual `Bambu PLA Tough+ @base`-style materialized
profile and verify the generated filament config still contains the exact
Orca representation. It must not use a printer, MQTT, network, upload, print
control, or any other hardware action.

## Safety

This is a plan-only artifact. Orca is not invoked during planning. No printer
or MQTT connection is made. No network is accessed. Installed Orca resources
are inspected read-only and are not modified.

## Acceptance criteria

- Scalar and real singleton-list material representations both pass the same
  exact material authority check.
- Ambiguous, empty, malformed, missing, and blank representations fail closed
  with the specified categories.
- The raw materialized profile remains byte/content-semantic authoritative;
  no scalar rewrite occurs.
- Profile references, content digests, resource identities, and top-level
  realization identity preserve their current authority semantics.
- Increment 3 receives the exact Orca filament representation.
- Focused hermetic tests and relevant static checks pass, with known unrelated
  failures excluded and reported.
- The required real software-only Orca 2.3.2 smoke chain reaches
  `SliceExecutionSuccess` and exact `plate_1.gcode`.

## Open Questions

NONE
