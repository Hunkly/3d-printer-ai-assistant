# Phase 3 Increment 2 — Concrete Setup Realization

Status: APPROVED

## Current verified baseline

Increment 1 is published at `186b707` and its approved contract is
`plans/phase-3-preparation-contract-v1.md`. The relevant authoritative values
are `SelectedSetup` and its immutable `AppliedOverride` tuple in
`src/print_engineer/core/preparation.py`. `SelectedSetup` contains:

- `SlicerKind`;
- `ProfileIdentity` references for printer, filament, and process profiles;
- nozzle diameter in millimeters;
- build-plate and material strings;
- the eight-setting typed override allowlist;
- no realization or slicing behavior.

The existing slicing path is an adapter boundary over `Slicer.slice(SliceJob)`
(`src/print_engineer/core/interfaces/slicer.py`). `SliceJob` in
`src/print_engineer/core/types.py` carries a model path, process profile,
filament profile, optional printer profile, slicer kind, output/timeout options,
and export name. It carries neither nozzle, build plate, material, nor
overrides as independent fields.

`BaseSlicerAdapter._prepare_profiles()` in
`src/print_engineer/adapters/slicer/base.py` materializes all three profiles,
requires a printer, and rejects process/filament compatibility mismatches.
`ProfileRepository` and `ProfileMaterializer` in
`src/print_engineer/adapters/slicer/profile.py` resolve local system/user
profiles and inheritance into generated in-memory `ProfileInfo` documents.
They do not mutate installed profiles.

The verified Orca path writes the materialized process, printer, and filament
JSON documents into a per-slice work directory and invokes:

```text
orca-slicer.exe <model> --load-settings process.json;printer.json
  --load-filaments filament.json --slice 1
  --export-3mf <bare-name>.gcode.3mf --outputdir <job-dir>
```

`BambuStudioAdapter.slice()` is currently disabled and raises
`SlicerUnavailable`; Bambu remains available for detection, profile discovery,
and model validation. There is no current adapter-side projection from
`SelectedSetup` to `SliceJob`.

The current recommendation/context path is intentionally more permissive than
authoritative realization: it can resolve a requested nozzle independently
after checking the printer's declared `nozzle_diameter` set, and it treats a
build plate as context/filter input. `recommendation/filament.py` recognizes
only the vocabulary fragments `cool`, `textured`, `hot`, `engineering`, and
`high temp` for plate-temperature filtering. That is not evidence that the
selected plate reaches the slicer invocation.

## Exact Increment 2 problem

Given an already-authoritative `SelectedSetup`, construct one immutable,
deterministic realized-input value that can later be projected into
`SliceJob`/adapter invocation. Realization succeeds only when every selected
component is present in the effective slicer inputs, with canonical semantics
that can be compared later. It does not select a setup, run a slicer, create a
final artifact, verify a slice, orchestrate preparation, or expose an API.

The selected setup remains the intended authority. The realized object is a
separate concrete projection and must not be made to masquerade as
`SelectedSetup`.

## Existing slicer/profile data flow

```text
ProfileRepository.find(kind, name)
  -> ProfileMaterializer.materialize(ProfileInfo)
  -> adapter._prepare_profiles(SliceJob)
  -> per-job process/printer/filament JSON files
  -> Orca --load-settings + --load-filaments
  -> later SliceResult.job and parsed G-code/3MF facts
```

The profile materializer resolves inheritance and produces a generated,
self-contained profile with `source=GENERATED`, but it does not apply an
arbitrary overlay. `ProfileInfo` is frozen at the dataclass level, while its
JSON content is a string and therefore safe to snapshot; recommendation
Pydantic models and profile discovery collections remain mutable outside the
Increment 1 contract.

There is no current fallback in `_prepare_profiles()` for a missing printer,
process, or filament supplied by a `SliceJob`: a missing printer is rejected,
and materialization failures are raised. Earlier recommendation/context code
does have explicit optional default resolution; realization must not call that
path in a way that changes an authoritative name into a default.

## Representability matrix

| Selected field | Current representation/path | Classification | Increment 2 consequence |
|---|---|---:|---|
| `slicer` | `SliceJob.kind`; registry chooses the adapter | A | Preserve exact `SlicerKind`; unsupported adapter capability is a realization failure. |
| printer/profile | `SliceJob.printer`; materialized machine JSON is passed in `--load-settings` | A/B | Resolve exact `ProfileIdentity`; reject missing, ambiguous, wrong-kind, unresolved, or substituted profile. |
| nozzle | Printer JSON key `nozzle_diameter` is parsed by `adapters/slicer/settings.py`; no independent `SliceJob`/CLI field exists | B for a profile-declared nozzle; D independently | Require selected diameter to be supported by the authoritative printer and materialize a deterministic machine overlay that contains the selected `nozzle_diameter` if the adapter capability proof accepts it. Otherwise fail; never retain a conflicting printer default. |
| build plate | Only `PrintContextIntent.build_plate` and recommendation filtering exist; no `SliceJob` field, CLI argument, or verified profile key/invocation mapping exists | D | Block implementation until an adapter-specific, fixture-backed plate mapping is established. Unsupported/unknown plate must fail, never remain narrative context. |
| material | Filament profiles expose `filament_type` and `settings.py` derives `material_type`; no independent `SliceJob` material field | B/derived | Require material to equal the canonical material type derived from the selected filament profile, unless a verified slicer-specific independent material field is added. Mismatch fails. |
| filament profile | `SliceJob.filament`; `--load-filaments` | A/B | Materialize and pass the exact selected profile; no default filament substitution. |
| process profile | `SliceJob.profile`; `--load-settings` | A/B | Materialize and pass the exact selected profile; no printer-default fallback. |
| `layer_height_mm` | process JSON `layer_height`; reader `_FLOAT_FIELDS` confirms the key and millimeter interpretation | B | Overlay the canonical decimal value at the process layer and prove it is present in effective materialized content. |
| `wall_loops` | process JSON `wall_loops`; `_INT_FIELDS` confirms the key | B | Overlay canonical integer representation at the process layer. |
| `sparse_infill_percent` | process JSON `sparse_infill_density`; `_PERCENT_FIELDS` confirms percent parsing | B | Overlay canonical percent representation, including percent units, not a bare unrelated fraction. |
| `sparse_infill_pattern` | process JSON `sparse_infill_pattern`; `_STR_FIELDS` confirms the key | B | Overlay the canonical trimmed string. |
| `support_enablement` | authoritative name maps to process JSON `enable_support`; `_BOOL_FIELDS` confirms the slicer key | B | Overlay the slicer boolean representation and retain the authoritative-to-slicer mapping. |
| `support_type` | process JSON `support_type`; `_STR_FIELDS` confirms the key | B | Overlay the canonical trimmed string. |
| `support_threshold_angle_deg` | process JSON `support_threshold_angle`; `_FLOAT_FIELDS` confirms degrees | B | Overlay canonical finite decimal degrees. |
| `outer_wall_speed_mms` | process JSON `outer_wall_speed`; `_FLOAT_FIELDS` confirms millimeters/second | B | Overlay canonical finite decimal speed. |

The matrix is deliberately conservative. The existing reader proves how
materialized profiles are interpreted; it does not by itself prove that every
write-side value is accepted by every installed slicer version. Increment 2
must add adapter capability fixtures/tests for any write-side mapping before
calling it supported.

## Nozzle realization

The current machine profile can declare one or more values in
`nozzle_diameter`; `PrintContextResolver` parses those values and checks an
explicit requested nozzle against the supported set. The current Orca command
has no separate nozzle argument. Therefore a selected nozzle that differs from
the machine profile's effective/default value cannot currently be represented
by `SliceJob` alone.

The proposed smallest mechanism is a deterministic generated machine-profile
overlay, derived from the fully materialized selected printer, with
`nozzle_diameter` set to the selected canonical millimeter value. The overlay
must retain the printer identity and all inherited settings needed by the
slicer, and must be the exact machine JSON passed to `--load-settings`.
Realization must inspect the effective overlay and record the canonical nozzle
value in its immutable effective-input projection. If the adapter cannot prove
that the selected value is accepted independently of the installed profile,
realization fails with `nozzle_not_representable`.

The plan does not authorize changing the global profile store or adding a
generic CLI option. The machine-overlay approach remains conditional on a
hermetic adapter fixture proving the Orca/Bambu profile semantics.

## Build plate realization

The repository currently proves only recommendation-side plate vocabulary and
filament temperature filtering. It does not prove the actual Orca/Bambu
machine/process setting or CLI mechanism that selects the active build plate.
Consequently build plate is currently **D — NOT REPRESENTABLE**.

Before production implementation, the slicer adapter owner must establish a
small, explicit capability mapping for each supported slicer kind/version
context, backed by local profile fixtures and the existing invocation contract.
The mapping must identify the exact config key/value (or exact CLI/project
mechanism), accepted vocabulary, canonical conversion, and later observation
method. If no such mapping exists for a selected plate, realization fails with
`build_plate_not_representable`. It is forbidden to treat a plate as merely a
filament compatibility filter or to invent a key such as `curr_bed_type`.

### Build-plate mapping research update (2026-08-24)

The repository and the locally available OrcaSlicer/Bambu Studio resources
were inspected read-only. The authoritative Phase 3 vocabulary is not an
enum: `SelectedSetup.build_plate` and `ActualInputIdentity.build_plate` are
non-blank strings in `src/print_engineer/core/preparation.py`, while
`PrintContextIntent.build_plate` and `ResolvedPrintContext.build_plate` are
optional strings. Existing examples include `cool plate`, `textured plate`,
and the contract test value `textured_pei`; these are not a closed supported
domain.

Local slicer evidence identifies the following distinct fields/mechanisms:

| Candidate | Local evidence | Meaning | Current proof status |
|---|---|---|---|
| `default_bed_type` | `%APPDATA%\\OrcaSlicer\\system\\BBL\\machine\\Bambu Lab A1.json` and the corresponding Bambu Studio machine-model file contain `"default_bed_type": "Textured PEI Plate"` | Machine-model default/capability metadata | Not proven to be the current plate selected for a job |
| `not_support_bed_type` | Bambu Studio machine-model profiles for some printers | Unsupported/capability metadata | Not active selection |
| `hot_plate_temp`, `textured_plate_temp`, `cool_plate_temp`, and local `eng_plate_temp` | A1 and other local filament profile JSON files | Plate-specific filament temperature fields | Capability/temperature data only; not active plate selection |
| `curr_bed_type` | `%APPDATA%\\OrcaSlicer\\OrcaSlicer.conf`, `%APPDATA%\\BambuStudio\\BambuStudio.conf`, and local machine-start G-code templates | Application/UI state consumed by template conditions such as `curr_bed_type==\"Textured PEI Plate\"` | A plausible runtime selector, but its ID-to-label vocabulary and deterministic job propagation are unproven |
| `bed_type` | `src/print_engineer/adapters/slicer/gcode.py` and `tests/unit/test_slicer_gcode.py` (`Metadata/plate_1.json`) | Post-slice metadata observation | Output identity only; cannot select the input |

The local application state contains numeric `curr_bed_type` values (`3` in
the current Orca state and `4` in the current Bambu Studio state), and Bambu
Studio also stores a per-machine `user_bed_type_list` with labels such as
`Textured PEI Plate` and `High Temp Plate`. No checked-in or locally configured
profile schema establishes the complete numeric-to-label mapping, and the
repository does not read or write either application-state file. The local
machine profile's `default_bed_type` therefore cannot substitute for a
selected plate.

The current `OrcaSlicerAdapter` writes materialized process, printer, and
filament JSON documents to a per-job directory and invokes only
`--load-settings process.json;printer.json --load-filaments filament.json`.
`SliceJob` has no plate field, and no existing overlay or generated config
path targets a verified active-plate key. Bambu Studio slicing is disabled in
the current adapter. Consequently no exact mapping table can be claimed for
the current authoritative string domain.

Filament interaction is only partially established: local profiles contain
plate-specific temperature fields, and recommendation filtering maps
fragments (`cool`, `textured`, `hot`, `engineering`, `high temp`) to some of
those fields. There is no local proof that a selected active plate causes the
slicer to choose the corresponding field during this repository's CLI path.

This research leaves build plate at **D — NOT CURRENTLY REPRESENTABLE**. The
Increment 2 implementation must remain blocked until a local, version-scoped
proof establishes the accepted selector/value vocabulary, a deterministic way
to inject it into the existing job invocation, and an observable effective
configuration identity for at least every authoritative plate value that the
increment supports.

## Material/filament realization

Material identity and filament-profile identity are distinct authoritative
fields. The slicer invocation currently receives a filament profile, not a
separate material argument. The existing material reader derives
`material_type` from the materialized filament's `filament_type` field, falling
back only to recognized material tokens in the profile name for recommendation
display. Authoritative realization must not rely on the name heuristic without
recording that it is the canonical supported derivation.

The proposed invariant is: selected `material` must match a non-unknown,
canonical material type from the selected, materialized filament profile. If
material is not independently expressible by the adapter, the filament's
derived type is the effective material and a mismatch or unknown derivation is
`material_profile_mismatch`/`material_not_provable`, not a successful
realization. The exact selected filament profile remains the one passed to
`--load-filaments`; no compatible alternative may be substituted.

## Process-profile realization

The current `SliceJob.profile` is the process profile and Orca passes it in
`--load-settings`. `ProfileMaterializer` resolves inheritance and rejects
missing/cyclic/malformed chains. `PrintContextResolver` and recommendation
code may select a printer default process when no explicit profile was
requested, but that is upstream recommendation behavior and is not permitted
for an authoritative `SelectedSetup`.

Realization must resolve the exact `ProfileIdentity.name` and, where present,
`setting_id`; materialize it; validate its compatibility with the exact
materialized machine identity; and retain its resolved content identity. Any
missing profile, ambiguous resolution, failed inheritance, or fallback to a
different process profile fails realization.

## Override-to-slicer mapping

| Authoritative preparation setting | Slicer config key | Canonical value/units | Target layer | Effective observation |
|---|---|---|---|---|
| `layer_height_mm` | `layer_height` | finite decimal string in mm, using `AppliedOverride.canonical_value` | generated process overlay over selected process | parse effective process JSON with the existing typed settings reader and compare canonical mm |
| `wall_loops` | `wall_loops` | canonical integer string, unitless | generated process overlay | parse effective process JSON and compare integer |
| `sparse_infill_percent` | `sparse_infill_density` | canonical decimal percent string, preserving `%` as required by the slicer config vocabulary | generated process overlay | parse effective process JSON as percent and compare |
| `sparse_infill_pattern` | `sparse_infill_pattern` | trimmed canonical string | generated process overlay | parse effective process JSON and compare exact string |
| `support_enablement` | `enable_support` | canonical `1`/`0` (or the exact adapter-proven boolean token) | generated process overlay | parse effective process JSON as boolean and compare |
| `support_type` | `support_type` | trimmed canonical string | generated process overlay | parse effective process JSON and compare exact string |
| `support_threshold_angle_deg` | `support_threshold_angle` | finite decimal string in degrees | generated process overlay | parse effective process JSON and compare degrees |
| `outer_wall_speed_mms` | `outer_wall_speed` | finite decimal string in mm/s | generated process overlay | parse effective process JSON and compare mm/s |

The key mappings above are the exact read-side mappings in
`adapters/slicer/settings.py`. The plan requires the implementation to prove
the adapter accepts the corresponding write representation in fixture tests;
where a slicer requires a list form or another exact scalar encoding, that
encoding must be captured in a typed adapter mapping rather than inserted by a
generic dictionary helper. All eight settings target the generated process
overlay, override process-profile values, and must be present in the effective
JSON. No LLM key or unchecked setting name may enter the overlay.

## Chosen realization architecture

Use a narrow slicer-domain realization module, likely under
`src/print_engineer/adapters/slicer/` or a new preparation-facing slicer
module, with a dependency direction of:

```text
core/preparation.py (authoritative values)
        ^
realization module -> slicer profile repository/materializer + adapter capability mapping
        -> immutable realized effective inputs + later SliceJob projection
```

The core contract must not import Orca/Bambu implementation details. The
realizer should accept a selected setup, a local profile resolver/materializer,
and an explicit slicer capability/configuration strategy. It should first
resolve and materialize exact profiles, validate compatibility and derived
material consistency, validate adapter capabilities, then construct immutable
overlay specifications and effective profile snapshots. It should expose a
small projection method that later Increment 3 can convert to `SliceJob`; it
must not call `Slicer.slice()`.

Use one deterministic overlay composition path:

1. materialize selected printer, process, and filament profiles;
2. canonicalize selected identity and override order by setting name;
3. create a generated machine overlay only when selected nozzle requires it;
4. create a generated process overlay when overrides or a verified plate key require it;
5. preserve the selected filament as the loaded filament profile;
6. validate every authoritative field against the resulting effective JSON/config;
7. return immutable effective-input values and stable content identities.

Do not modify the existing global slicer behavior or generic `SliceJob` until
the capability proof establishes the minimum required projection change. If a
small `SliceJob` extension is required later, it must be explicitly limited to
the realized immutable inputs/overlay references and covered by regression
tests; it must not become a generic settings dictionary.

## Realized input/result contract

Add a narrow immutable internal result, compositionally related to Increment 1:

```text
RealizationResult
  selected_setup: SelectedSetup
  effective_inputs: EffectiveSliceInputs
  resources: tuple[RealizationResource, ...]
  succeeded: bool
  failure: PreparationFailure | None
```

`EffectiveSliceInputs` should contain the exact slicer kind, resolved profile
identities/content digests or stable canonical references for printer,
filament, and process, effective nozzle, effective build plate, effective
material, and canonical applied overrides. It should also contain enough
immutable data for later `SliceJob` projection without retaining mutable
`ProfileInfo`/JSON containers. Its canonical identity should be a stable hash
of the slicer/version context (when available), selected profile identities and
resolved content, effective machine/process/filament overlays, and sorted
canonical overrides. Same selected setup, local profile dataset, and slicer
version/context must yield the same identity.

`RealizationResource` is metadata only: kind, deterministic identity, bounded
local path/reference, and content digest. It must not be a final artifact and
must not make the result READY. `ActualInputIdentity` is sufficient for later
comparison of logical selected fields, but it is not sufficient by itself to
describe concrete profile content, overlays, or projection resources; use
composition rather than duplicating the preparation result model. The
semantic realization identity must always include the fixed capability identity
`OrcaSlicer 2.3.2` for the supported Phase 3 path, together with canonical
resolved profile content and canonical overlay bytes; it is not conditional on
whether a version is available. An operational temporary directory is never an
input to that identity.

Successful results must have `effective_inputs` matching every field in
`selected_setup`, with no `None` effective value. Failed results carry a stable
`PreparationFailure(stage=REALIZATION, code, message, details)` and no claim
that slicing inputs were successfully produced.

## Failure contract

Use a small stable code set under `FailureStage.REALIZATION`, including:

- `printer_profile_missing`;
- `filament_profile_missing`;
- `process_profile_missing`;
- `profile_resolution_failed`;
- `ambiguous_profile_resolution`;
- `incompatible_profiles`;
- `unsupported_nozzle` or `nozzle_not_representable`;
- `build_plate_not_representable`;
- `material_profile_mismatch` or `material_not_provable`;
- `override_not_representable`;
- `invalid_effective_value`;
- `overlay_generation_failed`;
- `effective_identity_unprovable`;
- `silent_substitution_detected`.

Details must identify the field/profile, requested canonical value, target
layer/key when applicable, and observed effective value. Do not create a large
exception hierarchy. Existing `InvalidProfile`/structured slicer errors may
be translated at the boundary, but realization must return deterministic
failure information rather than fake success.

## No-substitution invariants

Realization succeeds only if every selected setup component is represented in
the effective slicer inputs exactly according to its canonical semantics.
Specifically, it must reject:

- default printer, nozzle, plate, filament, or process substitution;
- a material derived from a different filament profile;
- a process profile whose resolved name/content differs from the selected one;
- a printer overlay that loses inherited settings or changes identity;
- a dropped, duplicated, or reordered override whose canonical result changes;
- any unsupported build plate or override;
- any effective value that cannot be parsed and compared after construction.

An unavoidable equivalent representation may be accepted only when the
adapter capability contract proves equivalence and records deterministic
evidence. In the absence of such proof, realization fails.

## Temporary resources

Increment 2 creates no filesystem resources and therefore has no temporary-file
cleanup responsibility. It returns canonical in-memory overlay/resource
descriptions whose content digests and semantic identities are path-independent.
Increment 3 owns materializing those descriptions into the existing bounded
per-job workspace (`workdir/slicer/orca_slicer/<timestamp>`) immediately before
the slice invocation, owns cleanup on success, failure, and cancellation, and
must remove a partially materialized workspace on ordinary setup failure where
reasonably testable. The timestamped path is operational isolation only: it is
excluded from canonical bytes, content digests, `ActualInputIdentity`, and the
semantic realization hash. These files are never final artifacts, and no
installed or user profile may be overwritten.

## Exact production scope

After approval, production work is limited to:

- one narrow realization owner and its immutable effective-input/resource
  value types;
- exact profile resolution/materialization and compatibility checks needed for
  the authoritative selected setup;
- typed adapter capability mappings for the verified slicer path;
- deterministic machine/process overlays only where required to express
  selected nozzle, verified build plate, or the eight overrides;
- the smallest projection support needed for a later `SliceJob` conversion;
- structured realization failures and stable effective-input identity.

No recommendation, model-analysis, orchestration, slicer execution, artifact,
verification, MCP, printer, MQTT, database, history, dependency, or global
profile changes are in scope.

## Exact test scope

Hermetic tests must cover:

### Success

- exact printer/profile, nozzle, build plate, material, filament profile, and
  process profile preserved in effective inputs;
- no-overrides realization;
- each of the eight overrides individually and compatible combinations;
- exact key, target layer, units, canonical conversion, and effective parsed
  value for every override;
- `ActualInputIdentity`/effective logical identity matches selected setup;
- selected material agrees with the selected filament's canonical material;
- stable realized identity for identical inputs.

### Failure

- missing printer, filament, or process profile;
- inheritance/materialization failure;
- ambiguous profile resolution;
- unsupported nozzle and selected nozzle differing from an unmodifiable profile;
- unsupported/unknown build plate;
- material/profile mismatch or unknown material derivation;
- incompatible profiles;
- each unrepresentable override and invalid generated value;
- effective identity that cannot be proven;
- every silent fallback/substitution path prevented.

### Determinism and safety

- override input order does not change canonical overlay/result identity;
- generated content identity is stable;
- frozen result and nested tuples cannot be mutated;
- mutation of source profile/config containers after realization cannot change
  the result;
- no slicer subprocess, printer, MQTT, network, background process, global
  profile write, or user-profile mutation occurs.

### Regression boundary

Run the existing focused profile, settings, adapter, recommendation, and
preparation-contract tests. Existing slicing invocation and recommendation
behavior must remain unchanged; no hardware test is permitted.

## Preserved behavior

Recommendation remains read-only and retains its current five goals and
deterministic profile facts. Existing `ProfileRepository`,
`ProfileMaterializer`, `SliceJob`, Orca invocation, Bambu disabled-slice
behavior, MCP tools, printer adapters, and zero-MQTT-publish safety remain
unchanged except for explicitly reviewed realization projection support.

## Safety/side-effect boundary

Increment 2 realization is a local pure transformation plus profile reads and
in-memory canonicalization. It must not invoke a slicer, upload, contact a
printer, use MQTT/network, modify installed/global profiles, persist history or
database state, start processes, or create a final artifact. Any temporary
config required by the later slice invocation belongs to that invocation
boundary and is bounded, local, inspectable, and non-authoritative.

## Verification commands

After approval and implementation, use the project virtual environment and
run only focused checks first:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_setup_realization.py
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_slicer_profile.py tests/unit/test_slicer_settings.py tests/unit/test_orca_adapter.py tests/unit/test_bambu_adapter.py tests/unit/test_preparation_contract.py
.\.venv\Scripts\ruff.exe check <changed-realization-files> <changed-realization-tests>
.\.venv\Scripts\mypy.exe <changed-realization-files>
```

Use exact changed paths after implementation. Do not invoke an installed
slicer, hardware, MQTT, or network. Do not claim the build-plate mapping is
verified until a local fixture and focused adapter test establish it.

## Explicit out of scope

- analyze a model;
- call `RecommendationEngine` or `SetupEngine` end-to-end;
- choose recommendation candidates or a selected setup;
- invoke Orca/Bambu or any slicer subprocess;
- produce slice statistics or a final artifact;
- post-slice verification or final READY preparation result;
- MCP preparation API;
- printer control, MQTT, network, hardware, cloud, camera, FTPS, discovery;
- automatic slicing/printing, retries, history, persistence, or profile database.

## Increment 3 handoff

Increment 3 may consume a successful `RealizationResult`, project its
`EffectiveSliceInputs` into the existing slicing invocation, then later
orchestrate `analyze -> recommend -> select -> realize -> slice`. It must treat
realization failure as a NOT_READY cause, retain the selected setup as the
authority, and use the effective-input identity as the comparison baseline.
Increment 3 must not re-resolve profiles, reapply defaults, or reconstruct
overrides outside the realization contract.

## Risks / open questions

1. The build-plate key/value mechanism is established only for the local
   OrcaSlicer 2.3.2 capability context. Other slicer versions/kinds remain
   unsupported until separately proven.
2. A machine-profile nozzle overlay is suggested by the verified
   `nozzle_diameter` profile field, but adapter/version fixtures must prove that
   changing it independently is honored by the slicer.
3. The read-side settings parser establishes keys and units, but the precise
   write encoding (scalar versus list/string form) must be tested per adapter.
4. Profile identity currently has name/kind/optional `setting_id`; content
   digest/context inclusion is needed for comparison-ready realized identity
   without claiming unavailable provenance.
5. Bambu slicing is disabled in the current implementation, so a Bambu
   realization may be constructed only as a validated local input projection;
   it cannot be handed to a successful Bambu slice in this increment.

### Increment 2 local Orca capability spike (2026-08-24)

The build-plate blocker is cleared for the locally installed OrcaSlicer
version only. The executable is `C:\\Program Files\\OrcaSlicer\\orca-slicer.exe`,
detected as OrcaSlicer **2.3.2** from the registry. The experiment used the
existing A1 machine/process/filament profile path and the existing
`--load-settings` / `--load-filaments` invocation. This executable produced a
usable local slice with `--slice 0`; `--slice 1` reported a post-slice
validation error without a usable artifact, so the adapter's documented
`--slice 1` behavior needs separate compatibility treatment.

The active selector is `curr_bed_type`. It accepts native **labels**, not the
numeric values found in local application state:

| Native meaning | Key/value | Observed output | Temperature field |
| --- | --- | --- | --- |
| Cool Plate | `curr_bed_type=Cool Plate` (also no-key fallback) | `plate_1.json` reports `bed_type: cool_plate`; G-code reports `curr_bed_type = Cool Plate` | cool-plate path |
| Textured PEI Plate | `curr_bed_type=Textured PEI Plate` | `bed_type: textured_plate`; rendered `G29.1 Z-0.02` branch | temporary `textured_plate_temp_initial_layer=203` |
| High Temp Plate | `curr_bed_type=High Temp Plate` | `bed_type: hot_plate` | temporary `hot_plate_temp_initial_layer=102` |

The temporary filament copy made plate temperatures distinguishable. The
textured and high-temperature runs selected their corresponding fields in
generated G-code, proving plate-specific filament selection for these native
values. `Hot Plate` was not accepted as a native label in this version and
fell back to Cool Plate.

Numeric values `3` and `4`, taken from local application state, silently fell
back to Cool Plate. An arbitrary invalid token also silently fell back to Cool
Plate. Pre-validation is therefore mandatory; successful parsing or slicing
does not prove selection. Later Increment 3 finalization must compare the
requested native label with `Metadata/plate_1.json` (and, where the selected
plate requires it, the rendered G-code branch) and fail on fallback or
mismatch. Increment 2 does not invoke Orca or perform this post-slice check; it
emits the comparison-ready canonical/native/output mapping and expected
effective identity.

The result is **B — SMALL SLICER CONTRACT EXTENSION REQUIRED**. A generated
printer/profile overlay can carry `curr_bed_type`, but the current `SliceJob`
has no authoritative build-plate field and the adapter has no plate-aware
effective-input identity mapping. The minimum Increment 2 implementation is a
narrow Orca 2.3.2 capability mapping, injection into the per-job overlay,
pre-validation, and comparison-ready output identity. Post-slice effective-
plate verification belongs to Increment 3. No numeric ID mapping is supported.

#### `--slice 0` versus `--slice 1` disposition

The current production `OrcaSlicerAdapter._build_slice_command` passes
`--slice 1`; existing adapter tests cover that command construction. The local
spike produced a usable Orca 2.3.2 result only with `--slice 0`, while
`--slice 1` failed post-slice validation. The evidence does not establish
whether that failure is caused by the experimental fixture/configuration or is
a general Orca 2.3.2 compatibility defect. This is therefore **DEFERRED
PRECONDITION FOR INCREMENT 3**, not an Increment 2 blocker: realization must
not invoke a subprocess or change the argument, and can construct the correct
per-job overlay plus immutable effective-input identity independently. Before
Increment 3 invokes slicing, a focused adapter compatibility check must
resolve whether the documented `--slice 1` path is viable and, if necessary,
make the separately approved narrow adapter correction.

This does not change the Increment 1 `build_plate: str` field type. Increment 2
must nevertheless define the following closed Phase 3 canonical identities and
must never make the selected string authoritative by free-form comparison:

| Phase 3 canonical identity | Orca 2.3.2 native `curr_bed_type` label | post-slice `bed_type` |
| --- | --- | --- |
| `cool_plate` | `Cool Plate` | `cool_plate` |
| `textured_pei_plate` | `Textured PEI Plate` | `textured_plate` |
| `high_temp_plate` | `High Temp Plate` | `hot_plate` |

The realization boundary performs exact matching against this allowlist after
receiving the authoritative `SelectedSetup` value. It must not call `strip()`,
`lower()`, `casefold()`, apply aliases, map native labels, map numeric IDs, or
perform any other vocabulary-expanding normalization. Unsupported values fail
before overlay generation or Orca invocation with
`build_plate_not_representable`. The mapping is version-scoped to Orca 2.3.2;
other versions/kinds fail closed until separately proven. Increment 2 records
the native selector and expected output identity; Increment 3 performs the
actual post-slice comparison. A successful realization must preserve both
canonical and native identities so later verification cannot mistake a Cool
Plate fallback for the requested plate.

## Independent review disposition (2026-08-24)

Review verdict: **FAIL — implementation-ready plan: NO**. The increment
boundary is sound and the local plate spike removes the former plate-mapping
blocker, but the plan requires the following exact production/test ownership
before approval:

- `src/print_engineer/adapters/slicer/realization.py`: the narrow realization
  owner, capability mapping, strict profile resolution, immutable effective
  inputs/resources, overlay composition, and structured failures;
- `src/print_engineer/core/types.py`: only the minimal `SliceJob` projection
  fields required to carry realized overlay/resource references, if the
  existing job cannot carry them without reinterpretation;
- `src/print_engineer/adapters/slicer/orca.py`: only the narrow consumption
  change for those realized values; no `--slice` change in Increment 2;
- `tests/unit/test_setup_realization.py`: hermetic realization, plate,
  profile, nozzle, material/filament, all eight override, determinism,
  failure, resource, and side-effect tests;
- `tests/unit/test_orca_adapter.py` and any directly affected settings test:
  only focused regression coverage for the narrow request/config contract.

The implementation must choose these paths (or document a strictly narrower
equivalent before approval); “likely under” and “changed-realization-files”
are not sufficient production scope. The `--slice 0`/`--slice 1` issue is
**DEFERRED PRECONDITION FOR INCREMENT 3**, not a blocker to realization: the
current adapter passes `--slice 1`, the spike proves only `--slice 0`, and no
evidence yet distinguishes fixture failure from a general Orca 2.3.2 defect.
Increment 3 must resolve that compatibility question before invoking slicing.

Status remains **PROPOSED**. No approval is recommended until the exact file
scope, closed plate mapping above, and Increment 3 compatibility handoff are
accepted as part of the plan.

## Canonicalization contract amendment (2026-08-24)

This amendment is a semantic clarification after approval. The plan status is
`PROPOSED` pending a short independent amendment review. It changes no other
Increment 2 requirement.

### Raw input normalization

- Increment 2 does not own raw user-input normalization.
- `SelectedSetup` construction owns the existing surrounding-whitespace
  normalization for its string identities.
- Increment 2 consumes only the resulting authoritative `SelectedSetup`.

The authority boundary is:

```text
raw caller input
  -> SelectedSetup construction
  -> authoritative/canonical SelectedSetup
  -> Increment 2 realization
```

Increment 2 must not retain, reconstruct, or validate the original raw caller
string after `SelectedSetup` construction. The value on `SelectedSetup` is the
authoritative Phase 3 semantic identity consumed by realization.

### Realization validation

Realization performs exact matching against the closed Phase 3 canonical plate
vocabulary it receives. Realization itself must not call `strip()`, `lower()`,
`casefold()`, perform alias mapping, native-label mapping, numeric-ID mapping,
or any other vocabulary-expanding normalization. Unsupported values present in
`SelectedSetup` fail deterministically with the existing failure category.

The closed allowlist remains exactly:

- `cool_plate` -> `curr_bed_type = "Cool Plate"`
- `textured_pei_plate` -> `curr_bed_type = "Textured PEI Plate"`
- `high_temp_plate` -> `curr_bed_type = "High Temp Plate"`

### Build-plate semantic examples

| Raw input | `SelectedSetup.build_plate` | Realization |
| --- | --- | --- |
| `"cool_plate"` | `"cool_plate"` | SUCCESS |
| `" cool_plate "` | `"cool_plate"` | SUCCESS |
| `"Cool Plate"` | `"Cool Plate"` | FAIL |
| `" Hot Plate "` | `"Hot Plate"` | FAIL |
| `"Engineering Plate"` | `"Engineering Plate"` | FAIL |
| `"COOL_PLATE"` | `"COOL_PLATE"` | FAIL |
| `"3"` | `"3"` | FAIL |

Both supported raw forms produce the same authoritative semantic build-plate
identity and therefore the same realization plate mapping and realization
identity, assuming all other semantic inputs are identical. Surrounding
whitespace around unsupported values is removed upstream, but the resulting
unsupported authoritative value still fails; for example, `" Hot Plate "`
becomes `"Hot Plate"` and is rejected.

### Corrected test contract

Tests must not require realization to reject `" cool_plate "` or
`"cool_plate "` as raw inputs. That expectation belongs before or inside
`SelectedSetup` construction. The hermetic realization tests must instead
prove that:

- `SelectedSetup(build_plate=" cool_plate ")` stores canonical
  `"cool_plate"`;
- realization of that setup produces the same semantic plate mapping and
  realization identity as `SelectedSetup(build_plate="cool_plate")`, with all
  other semantic inputs identical;
- no realization test passes a pre-construction raw string directly to the
  realization boundary;
- authoritative unsupported values representing `Cool Plate`, `Hot Plate`,
  `Engineering Plate`, `COOL_PLATE`, numeric-ID-like values, and arbitrary
  aliases/tokens fail deterministically;
- no manual construction backdoor is introduced to bypass `SelectedSetup`
  validation merely to test raw-input behavior.

The strict canonical allowlist and all other approved/corrected Increment 2
requirements remain unchanged.

## Operations

## Increment 2 write-path proof and implementation disposition (2026-08-24)

This section is the authoritative update after the independent write-path
capability proof. It supersedes the earlier open questions and review-failure
wording where they conflict. The plan remains `PROPOSED`; no implementation is
authorized by this proof.

### Exact current contract

The authoritative selected setup is `SelectedSetup` in
`src/print_engineer/core/preparation.py`. Its nozzle representation is the
finite positive `SelectedSetup.nozzle_diameter_mm` float in millimeters. Its
profile identities are `ProfileIdentity(name, kind, setting_id)`:

- selected printer: `SelectedSetup.printer`, `ProfileKind.PRINTER`;
- selected process profile: `SelectedSetup.process_profile`,
  `ProfileKind.PROCESS`;
- selected filament profile: `SelectedSetup.filament_profile`,
  `ProfileKind.FILAMENT`.

The exact approved override names and canonical contract domains are:

| Phase 3 setting | Canonical type and range |
| --- | --- |
| `layer_height_mm` | finite `float`, `0.01..100.0` mm |
| `wall_loops` | `int`, `1..100`, excluding `bool` |
| `sparse_infill_percent` | finite `float`, `0.0..100.0` percent |
| `sparse_infill_pattern` | non-blank trimmed `str` |
| `support_enablement` | `bool` |
| `support_type` | non-blank trimmed `str` |
| `support_threshold_angle_deg` | finite `float`, `0.0..90.0` degrees |
| `outer_wall_speed_mms` | finite `float`, `0.01..1000.0` mm/s |

`AppliedOverride.canonical_value` is the immutable comparison representation;
it is not an arbitrary free-form setting/value dictionary. The authoritative
plate domain for this Orca capability is the closed set `cool_plate`,
`textured_pei_plate`, and `high_temp_plate`. The material remains a selected
string that must equal the canonical material type derived from the selected
filament profile; material identity and filament-profile identity remain
separate fields.

### Orca context and safe proof method

The local executable is `C:\Program Files\OrcaSlicer\orca-slicer.exe`,
version **2.3.2**. The proof used disposable, materialized copies of the
installed A1 machine/process/filament profiles in
`.orca-write-proof-temp`, passed through the existing `--load-settings
process.json;printer.json --load-filaments filament.json` path. No installed
or user profile was modified. The local software-only parse/export run
generated `plate_1.gcode` and a 3MF; it was used only to observe Orca's
effective-settings header, not to investigate or alter `--slice` semantics.

Baseline evidence contained:

```text
; curr_bed_type = Cool Plate
; layer_height = 0.2
; nozzle_diameter = 0.4
```

A distinct supported nozzle overlay set `nozzle_diameter` to the serialized
string `"0.6"`, and the effective header contained `nozzle_diameter = 0.6`.
An all-override overlay produced effective header entries for all eight
settings, including `curr_bed_type = Textured PEI Plate`, `layer_height =
0.16`, `wall_loops = 3`, `sparse_infill_density = 35%`,
`sparse_infill_pattern = gyroid`, `enable_support = 1`, `support_type =
tree(auto)`, `support_threshold_angle = 45`, and `outer_wall_speed = 80`.
This proves write acceptance and effective-value observation, not merely
read-side profile presence.

### Proven write mapping

| Phase 3 setting | Canonical type | Exact Orca key | Units | Layer | Serialized example | Write accepted | Effective verified | Invalid behavior |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `layer_height_mm` | finite float | `layer_height` | mm | process overlay | `"0.16"` | YES | YES, G-code effective header | core range/type validation; reject non-finite, non-numeric, or out-of-range |
| `wall_loops` | int | `wall_loops` | unitless | process overlay | `"3"` | YES | YES | core integer/range validation; reject bool, fractional, or out-of-range |
| `sparse_infill_percent` | finite float | `sparse_infill_density` | percent | process overlay | `"35%"` | YES | YES | core range/type validation; retain `%`, reject malformed/out-of-range |
| `sparse_infill_pattern` | trimmed string | `sparse_infill_pattern` | none | process overlay | `"gyroid"` | YES | YES | core rejects blank/non-string; realization rejects unrepresentable token if capability cannot consume it |
| `support_enablement` | bool | `enable_support` | boolean token | process overlay | `"1"` for `true` | YES | YES | core rejects non-boolean; realization must reject any non-canonical conversion/drop |
| `support_type` | trimmed string | `support_type` | none | process overlay | `"tree(auto)"` | YES | YES | core rejects blank/non-string; realization rejects unrepresentable token |
| `support_threshold_angle_deg` | finite float | `support_threshold_angle` | degrees | process overlay | `"45"` | YES | YES | core range/type validation; reject non-finite, non-numeric, or out-of-range |
| `outer_wall_speed_mms` | finite float | `outer_wall_speed` | mm/s | process overlay | `"80"` | YES | YES | core range/type validation; reject non-finite, non-numeric, or out-of-range |

The Orca write encoding is a JSON scalar string for every row. `true` is
converted to Orca's accepted `"1"` token and `false` to `"0"`; it is not
serialized as JSON `true`/`false` by the overlay writer. The effective
observation is the generated G-code effective-settings header, parsed into a
typed effective projection and compared with the canonical value. A process
exit code alone is never sufficient.

### Nozzle proof and printer consistency

The exact machine key is `nozzle_diameter`, owned by the generated printer
overlay. The selected value is serialized as a canonical decimal string in
millimeters. The A1 profile declares the supported set in its
`nozzle_diameter` value (the local profile vocabulary includes `0.2`, `0.4`,
`0.6`, and `0.8`); the proof used baseline `0.4` and distinct valid `0.6`.
Orca accepted and effectively preserved both. Orca rejected `0`, `-0.4`, and
`malformed` with a configuration error, but accepted `0.7` and preserved it,
even though it is outside the printer's declared set. Therefore the exact
deterministic rule is: selected nozzle must be finite and positive and must
be an exact member of the selected printer's parsed supported-nozzle set;
otherwise realization fails before overlay generation. There is no silent
default nozzle and no reliance on Orca to enforce capability membership.
Increment 3/4 observes the effective nozzle from the parsed G-code/3MF
metadata and the realization identity; a mismatch is blocking.

### Strict profile resolution ownership

`ProfileRepository` remains the sole profile-store reader and
`ProfileMaterializer` remains the sole inheritance/materialization owner.
Increment 2's strict resolution owner is the new
`src/print_engineer/adapters/slicer/realization.py` realization boundary,
which must resolve each `ProfileIdentity` by exact `(kind, name, setting_id)`
against repository candidates before calling `ProfileMaterializer`.
`ProfileRepository.find()` alone is insufficient because it applies user
shadowing and does not report ambiguity.

For printer, process, and filament the rules are identical: exact match
succeeds; missing, wrong kind, failed inheritance, or malformed content is a
deterministic realization failure; more than one exact candidate is an
`ambiguous_profile_resolution` failure. No name alias, configured default,
printer default process, or fallback/substitution is accepted at this
authoritative boundary. Existing recommendation-time `use_defaults` behavior
remains upstream and is not called by realization.

The realization owner must preserve the selected profile identity, materialize
the resolved content, check process/filament compatibility against the exact
materialized printer identity, and include resolved content digests in the
immutable effective identity. The adapter's existing `_prepare_profiles()`
continues to own the later generic SliceJob materialization/compatibility
path; it is not a second Phase 3 resolver.

### Exact realization architecture and contract extension

`SelectedSetup` is received unchanged by
`src/print_engineer/adapters/slicer/realization.py`. That module canonicalizes
the closed plate identity and native Orca label, validates nozzle membership,
resolves/materializes the exact three profiles, derives/checks material,
sorts overrides by setting name, writes typed overlay specifications, and
returns the planned `RealizationResult`/`EffectiveSliceInputs` projection.
The minimum later slicer projection carries:

- canonical build plate plus native `curr_bed_type` and expected output
  `bed_type`;
- independently selected nozzle and printer-overlay reference;
- the eight canonical overrides in sorted order;
- strict resolved printer/process/filament references and content digests;
- derived effective material;
- `ActualInputIdentity` projected from those effective values.

Increment 2 does not extend `SliceJob` and does not change
`src/print_engineer/adapters/slicer/orca.py`: it performs no projection and no
slicing. Increment 3 may consume the immutable realization result through a
separately approved projection change, but it must not interpret arbitrary
Phase 3 strings or expose a generic settings dictionary. Existing callers and
the current optional `SliceJob` fields remain unchanged in Increment 2.

### Temporary overlay design

An overlay is required at the later Orca SliceJob projection boundary because
the current CLI consumes files, but realization itself remains non-persistent
and does not invoke a subprocess. One generated printer JSON overlay carries
`nozzle_diameter` and `curr_bed_type`; one generated process JSON overlay
carries the eight process keys. The selected materialized filament JSON is
passed unchanged. JSON objects use deterministic lexicographic key ordering,
UTF-8 encoding, no insignificant whitespace, and canonical scalar strings;
the resource identity is a SHA-256 of the canonical bytes plus the fixed
capability identity `OrcaSlicer 2.3.2`. Increment 2 does not create these
files. Increment 3 materializes the descriptions into the existing bounded
per-job workspace `workdir/slicer/orca_slicer/<timestamp>/` immediately before
slicing, using names `printer.realized.json`, `process.realized.json`, and
`filament.realized.json`; Increment 3 owns cleanup on success, failure, and
cancellation, including ordinary partial-setup failure. The timestamped path
is excluded from canonical bytes, content digests, `ActualInputIdentity`, and
semantic realization identity. The files are not final artifacts. No global or
installed profile is modified.

### Exact production and test scope

Production paths for Increment 2 are exactly:

- `src/print_engineer/adapters/slicer/realization.py` — strict exact profile
  resolution, Orca 2.3.2 capability mappings, overlay composition,
  immutable effective inputs/resources, deterministic identity, and
  structured realization failures;
- `src/print_engineer/core/types.py` — only the minimal immutable SliceJob
  no Increment 2 change; `SliceJob` remains unchanged because realization
  does not project or invoke slicing;
- `src/print_engineer/adapters/slicer/orca.py` — only consumption of those
  no Increment 2 change; Increment 3 owns consumption after resolving the
  `--slice 0`/`--slice 1` compatibility precondition.

The `core/types.py` and `orca.py` entries above are explicit no-change
entries for Increment 2; they are listed only to close the previously
ambiguous scope. No SliceJob field, adapter pass-through, subprocess command,
or `--slice` argument changes in this increment.

Test paths are exactly:

- `tests/unit/test_setup_realization.py` — hermetic nozzle, plate, all eight
  override write mappings/effective observations, strict profile resolution,
  material consistency, deterministic identity/order, path-independent
  in-memory resource descriptions, no Increment 2 filesystem materialization,
  and no-substitution/side-effect behavior;
- `tests/unit/test_orca_adapter.py` — focused regression for the narrow
  no Increment 2 change; the adapter has no changed pass-through or command
  construction in this increment;
- `tests/unit/test_slicer_settings.py` — only if the effective-value parser
  modify only if implementation adds a new effective-value parser assertion
  needed by the eight overlay mappings; otherwise this file is not in scope.

The `test_orca_adapter.py` entry is an explicit no-change regression boundary.
The `test_slicer_settings.py` entry is conditional only on adding a new
effective-value parser assertion for the eight mappings; otherwise it is not
modified.

The required realization tests include all canonical plates and rejection of
numeric IDs, `Hot Plate`, `Engineering Plate`, aliases, and arbitrary tokens;
exact/missing/ambiguous printer, process, and filament cases; all eight
mapping rows; invalid/unrepresentable override rejection; equivalent input
ordering producing identical identity; deterministic overlay contents and
zero filesystem writes by realization; and zero subprocess/network/printer/
MQTT activity. Increment 3, not this test scope, must add bounded workspace
materialization and success/failure/cancellation cleanup tests before slicing.

### Disposition

All authoritative selected fields now have deterministic write mappings for
the scoped Orca 2.3.2 capability, with effective-value proof for the nozzle,
plate selector, and all eight overrides. Determinism/no-substitution is
**PASS** subject to the specified prevalidation rule for Orca's permissive
unsupported-nozzle behavior. The plan is implementation-ready after this
proof. The `--slice 0`/`--slice 1` compatibility question remains exactly a
**DEFERRED PRECONDITION FOR INCREMENT 3** and is not reopened here.

## Operations

production modified: NO
tests modified: NO
dependencies modified: NO
hardware/MQTT/network: NO
stage: NO
commit: NO
push: NO
