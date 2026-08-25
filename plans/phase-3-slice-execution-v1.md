# Phase 3 Increment 3 — Realized Setup Materialization + Bounded Local Slice

Status: APPROVED

## Contract and boundary

Increment 3 consumes only a successful Increment 2 `RealizationResult` and performs `realized → materialize → slice`. It does not recommend, select, resolve, default, substitute, remap, reinterpret, or realize setup values. It does not produce `READY_FOR_REVIEW`, `ReadyPreparationResult`, `VerificationRepresentation(PASS)`, or `FinalArtifactIdentity`. Increment 4 owns final deterministic verification and conversion of verified candidate evidence into the final authoritative result.

The authoritative inputs are the exact Increment 2 `EffectiveSliceInputs`, including its `ProfileReference`s, canonical printer/process overlays, `ActualInputIdentity`, semantic realization identity, and OrcaSlicer 2.3.2 capability. Increment 3 never calls `ProfileRepository`, `ProfileMaterializer`, recommendation, model-analysis, or setup-selection orchestration to reinterpret those inputs.

The source model is the authoritative `ModelIdentity.path`. Immediately before execution, Increment 3 requires a regular supported file and computes its SHA-256. A present `ModelIdentity.sha256` must match. The verified `ModelIdentity` and `ActualInputIdentity` are retained in the success result.

## Two distinct materialized identities

Increment 2 has two relevant authorities and Increment 3 must not conflate them.

### Base materialized resource identity

For printer, process, and filament, the base identity records the exact Increment 2 materialized resource before Increment 3 applies effective overlays:

- profile kind;
- exact profile identity/name/`setting_id` where available;
- canonical parsed materialized JSON semantic content;
- the Increment 2 `RealizationResource` identity and `content_sha256`.

The base semantic content digest is the canonical parsed-JSON digest of the exact `ProfileReference.content`, and the resource record retains the exact Increment 2 resource identity/content digest. Before writing any effective file, Increment 3 verifies that the base parsed content and profile identity correspond to the matching Increment 2 resource and reference. A mismatch is a `config_materialization_failed` failure. Increment 3 does not require an overlaid file to equal a pre-overlay resource digest.

### Effective materialized config identity

The effective identity records the actual canonical parsed JSON passed to Orca after applying the already-canonical Increment 2 values:

- printer: base printer semantic content plus exact `nozzle_diameter` and `curr_bed_type` effective values;
- process: base process semantic content plus every exact Increment 2 process overlay entry;
- filament: exact selected Increment 2 materialized filament resource, with no Increment 3 semantic overlay.

For each file, the effective identity contains the expected canonical semantic JSON representation and its SHA-256 digest computed *after* overlay application. The effective printer/process digest is therefore expected to differ from the pre-overlay base digest when an overlay changes content. The success result carries both base resource identities and effective config identities; neither is inferred later from mutable repository state.

## Materialization and exact read-back verification

The owner creates one unique run workspace under the configured repository workspace convention, for example `<root>/slicer/orca_slicer/<UTC timestamp>_<collision suffix>/`. Operational workspace names and paths never enter semantic identities.

It parses each Increment 2 resource content as a JSON object, verifies its base resource identity as above, applies only the exact canonical overlays, and writes canonical UTF-8 JSON with sorted keys, compact separators, and `ensure_ascii=False`:

- `printer.realized.json`;
- `process.realized.json`;
- `filament.realized.json`.

After writing each file, before Orca invocation, the execution owner must:

1. read UTF-8 JSON back from disk;
2. parse it and require a JSON object;
3. canonicalize the parsed semantic JSON deterministically;
4. compute its effective semantic SHA-256 digest;
5. compare the parsed semantics with the exact expected effective representation.

Formatting and key order need not match; semantic JSON must match exactly. Any failure returns `config_verification_failed` and Orca is not invoked.

Printer read-back requires all preserved base printer semantic content, `nozzle_diameter` exactly equal to the Increment 2 effective value, `curr_bed_type` exactly equal to the Increment 2 effective value, and no unexpected lost or changed semantic field. Process read-back requires all preserved base process content, every Increment 2 effective process overlay, and no dropped/changed requested overlay or unrelated serialization mutation. Filament read-back requires canonical semantic equality with the exact selected Increment 2 materialized filament resource, with no substitution or mutation.

The effective digests are calculated from these parsed post-overlay semantics; the base-resource checks and effective-composition checks are separate assertions.

## SliceJob extension and ownership

`src/print_engineer/core/types.py` owns only the minimum bridge needed by the existing `SliceJob`: add an immutable `RealizedConfigPaths` value object with exact fields `printer: Path`, `process: Path`, and `filament: Path`, and add `realized_configs: RealizedConfigPaths | None = None` to `SliceJob`.

The discriminant is explicit: `realized_configs is None` means legacy profile-data mode; non-`None` means realized-config-path mode. The two modes are mutually exclusive authorities. Existing `profile`, `filament`, and optional `printer` fields remain for constructor/API compatibility and provenance in realized mode, but the realized paths are the sole config authority and the adapter must not materialize or reinterpret those profile objects. The execution owner constructs realized mode with all three paths, the exact model path, run output directory, timeout, and `kind=ORCA_SLICER`. No plate index is added. Existing legacy callers/tests retain their behavior.

## Orca realized-path branch

`src/print_engineer/adapters/slicer/orca.py` owns a narrow realized-path branch. When `realized_configs` is present it must:

- skip `_prepare_profiles()` and `_write_job_files()`;
- consume the exact supplied printer/process/filament paths;
- use the exact supplied run-owned `output_dir`, already created by the execution owner;
- pass only the basenames in the existing `--load-settings process.realized.json;printer.realized.json` order and `--load-filaments filament.realized.json`;
- reuse the existing bounded `run_cli()` and timeout/process-tree lifecycle;
- preserve hard-coded `--slice 1`.

The legacy branch remains unchanged except for the smallest dispatch needed to introduce this mode. No recommendation, realization, profile lookup, network, MQTT, printer control, or multi-plate behavior is added.

The realized branch must constrain output discovery to the exact run-owned output directory, which is the retained workspace path supplied as `SliceJob.output_dir`. It passes `--slice 1` and accepts only `<workspace_path>/plate_1.gcode` as the candidate. It never globs `plate_*.gcode`, searches a parent/global directory, or accepts an external path. The execution owner creates a newly isolated workspace/output directory, so `plate_1.gcode` cannot pre-exist; any pre-existing candidate in a supplied directory is a fail-closed workspace-contract violation. The archive is auxiliary only: the realized branch passes the exact bare export name `<source model stem>.gcode.3mf` to `--export-3mf`, so Orca's expected auxiliary path is `<workspace_path>/<source model stem>.gcode.3mf`; it does not discover archives with a wildcard and archive presence or absence does not affect candidate success.

## Exact immutable result types (owned by execution.py)

Increment-3-specific result types live in `src/print_engineer/adapters/slicer/execution.py`, not in core types. They are immutable (`dataclass(frozen=True, slots=True)`) and are not the preparation authority.

`CandidateSliceArtifact` is exactly `@dataclass(frozen=True, slots=True)` with these fields and no others: `slice_run_id: str` (non-empty run association); `path: Path` (operational reference to the exact retained `<workspace_path>/plate_1.gcode`); `artifact_format: Literal["gcode"]` (exactly `"gcode"`); `sha256: str` (64-hex content authority); and `byte_size: int` (content length, strictly greater than zero). Its `slice_run_id` equals the containing success result's `slice_run_id`. It is a candidate type and is never `FinalArtifactIdentity`.

`ObservedSliceFacts` is exactly `@dataclass(frozen=True, slots=True)` with these fields and no others: `plate_number: Literal[1]` (required execution-context fact, not parser-derived); `layer_count: int` (required parser-derived fact, strictly greater than zero); `time_minutes: float | None` (optional parser-derived `parse_gcode` fact); `max_z_height: float | None` (optional parser-derived `parse_gcode` fact); `filament_used_mm: float | None` (optional parser-derived `parse_gcode` fact); `filament_used_cm3: float | None` (optional parser-derived `parse_gcode` fact); and `filament_density: float | None` (optional parser-derived `parse_gcode` fact). The optional fields preserve the parser's `None` representation when their header/footer values are absent. No 3MF metadata, inferred weight, or mutable profile value is added to this type.

`SliceExecutionSuccess` is exactly `@dataclass(frozen=True, slots=True)` with these fields and no others:

```python
slice_run_id: str
realization_identity: str
model_identity: ModelIdentity
actual_input_identity: ActualInputIdentity
slicer_name: str
slicer_version: str
workspace_path: Path
printer_config_identity: str
process_config_identity: str
filament_config_identity: str
candidate_artifact: CandidateSliceArtifact
observed_facts: ObservedSliceFacts
```

`slice_run_id` is a non-empty immutable operational run identifier. `realization_identity` is the exact Increment 2 semantic realization identity and is semantic authority. `model_identity` is the exact authoritative `ModelIdentity`, and `actual_input_identity` is the exact Increment 2 `ActualInputIdentity`; both are semantic authority. `slicer_name` and `slicer_version` are exactly `"OrcaSlicer"` and `"2.3.2"` for this execution context and are semantic execution evidence. `workspace_path` is the concrete retained successful run workspace and is operational only, never setup identity. Each `*_config_identity` is a non-optional SHA-256 semantic identity string over the canonical parsed effective config passed to Orca, under the already approved base-versus-effective rules; these three fields are semantic authority. `candidate_artifact` and `observed_facts` have the exact types above. There is no `FinalArtifactIdentity`, `ReadyPreparationResult`, optional required-success evidence, base/effective duplicate field, extra candidate path field, or diagnostics field in this success type.

`SliceExecutionFailure` is immutable and has exactly: `slice_run_id: str | None` (allocated when possible); `stage` from the small stable taxonomy below; concise deterministic `diagnostic`; and bounded diagnostics such as stdout/stderr only under existing conventions. It has no candidate artifact, observed successful facts, successful effective output projection, or partial-success field. A failure cannot contain `CandidateSliceArtifact` or `SliceExecutionSuccess` data.

Stable failure categories are: `invalid_source_model`, `source_model_identity_mismatch`, `workspace_creation_failed`, `config_materialization_failed`, `config_verification_failed`, `slicer_unavailable`, `slicer_timeout`, `slicer_process_failed`, `slice_output_missing`, `slice_output_invalid`, `slice_facts_invalid`, and `candidate_artifact_identity_failed`.

## Required and optional observed facts

The current parser capability fixes the success minimum; no implementation choice remains for this increment.

Required `ObservedSliceFacts` fields are exactly: `plate_number: Literal[1]`, source `EXECUTION_CONTEXT`, set to `1` because the realized command is the single-plate `--slice 1` contract; and `layer_count: int`, source `PARSER_DERIVED`, obtained from `parse_gcode`'s `layer_count` and required to be greater than zero. Optional fields are exactly `time_minutes: float | None`, `max_z_height: float | None`, `filament_used_mm: float | None`, `filament_used_cm3: float | None`, and `filament_density: float | None`, all source `PARSER_DERIVED` and populated directly from the same `parse_gcode` result or left as `None`. No other observed fact is part of the type. The parser does not independently establish plate identity; `plate_number` is therefore execution context only.

## Exact successful execution bar

Success requires all of the following, in order:

1. source model exists;
2. source model SHA-256 equals authoritative `ModelIdentity.sha256`;
3. workspace is newly created successfully;
4. printer, process, and filament configs are written;
5. all three configs are read back and semantically verified;
6. OrcaSlicer 2.3.2 is invoked once with realized config paths and `--slice 1`;
7. `ProcResult.return_code == 0` (the only accepted process return-code rule);
8. the exact run-owned `<workspace_path>/plate_1.gcode` exists;
9. its byte size is greater than zero;
10. the current `parse_gcode` accepts it;
11. `plate_number == 1` and `layer_count > 0` are obtained, with all optional facts represented exactly as above;
12. SHA-256 of the candidate G-Code is computed;
13. `CandidateSliceArtifact` is constructed;
14. `SliceExecutionSuccess` is constructed.

Any other outcome is failure. `return_code != 0` maps to `slicer_process_failed` before output can establish success. Exit code 0 alone is not sufficient: no `plate_1.gcode` maps to `slice_output_missing`; an empty or parser-invalid exact file maps to `slice_output_invalid`; missing required facts maps to `slice_facts_invalid`; and archive presence, absence, or unrelated archives do not change this result. No partial success is returned.

## Workspace lifecycle and ownership transfer

The per-run workspace contains at least the three realized configs, Orca output, candidate G-Code/archive, and run-owned temporary execution files. Increment 3 owns creation and the workspace throughout execution.

On every failure before candidate success, it performs bounded best-effort cleanup of all run-owned files and the workspace, including config verification failure, process failure/timeout, and partial output. If cleanup fails, the primary Increment 3 failure remains authoritative; cleanup details are diagnostic only and no success is returned. The source model and any user-owned or external directory are never deleted.

On success, the entire workspace is retained, including configs and candidate artifacts, through Increment 4 handoff. Increment 3 does not clean it before handoff. The success result owns/references the retained workspace. Ownership then transfers explicitly:

This is an ownership transfer, not deferred or unspecified cleanup ownership.

`Increment 3: create → own during execution → delete on failure → transfer on success`.

`Increment 4: receive transferred successful workspace → final verification → later cleanup/release as its contract defines`.

There is no TTL, background janitor, database retention system, or vague “cleanup later” ownership in Increment 3.

The retained workspace is operational, mutable filesystem state and is not an authoritative semantic identity. Increment 3 records immutable base/effective semantic and content digests at success time. Increment 4 may reread the retained configs and artifact and must recompute them against those recorded identities before final readiness. If files mutate, Increment 4 detects the mismatch. This retained-candidate model is why Increment 3 does not produce `READY_FOR_REVIEW`.

## Increment 4 handoff evidence

The successful result is self-contained evidence for: realization identity; `ModelIdentity`; verified `ActualInputIdentity`; Orca identity/version; base/effective config semantic identities and digests; slice run identity; candidate path, SHA-256, and byte size; required observed facts; and retained workspace reference. Increment 4 therefore does not reconstruct execution from mutable `ProfileRepository` state. It may reread retained files as independent final verification and detect any mutation using the recorded identities.

## Candidate/final boundary test

New unit coverage must assert directly that `SliceExecutionSuccess.candidate_artifact` is a `CandidateSliceArtifact`, not a `FinalArtifactIdentity`, and that Increment 3 constructs neither `FinalArtifactIdentity` nor `ReadyPreparationResult`. It must also assert that failed execution returns no candidate artifact and cannot satisfy Increment 1 READY invariants. Only Increment 4 may convert verified candidate evidence to the final artifact/readiness result.

## Exact production scope and responsibilities

- `src/print_engineer/adapters/slicer/execution.py`: realized-input acceptance; source digest verification; workspace creation/ownership; exact base and effective config materialization; read-back semantic verification; `SliceJob` construction; one bounded Orca execution; run-owned output validation; candidate identity; observed facts; failure cleanup; and successful ownership transfer.
- `src/print_engineer/core/types.py`: only `RealizedConfigPaths` and the narrow `SliceJob.realized_configs` extension described above; no execution result types and no plate index.
- `src/print_engineer/adapters/slicer/orca.py`: only the realized-path branch, exact run-owned output directory/discovery, existing bounded process call, config argument order, and `--slice 1`; no profile re-materialization in realized mode.

No other production, test, dependency, Orca, hardware, MQTT, or network scope is authorized.

## Exact regression scope

Preserve the detailed Increment 3 test plan at these existing paths:

- `tests/unit/test_slice_execution.py`: exact successful-realization input
  gate; base-resource identity matching; effective printer nozzle/bed
  composition; all eight process overlay mappings; unchanged filament;
  canonical bytes; semantic read-back equivalence and tamper detection;
  model path/SHA-256 checks; workspace collision/isolation; realized
  `SliceJob` paths; no realization/recommendation/model-analysis repeat;
  immutable success/failure results; candidate identity; no READY result;
  no network/MQTT/hardware; non-zero/timeout/unavailable process outcomes;
  exit-zero missing/empty/malformed output; ambiguous output; and artifact
  identity failure.
- `tests/unit/test_orca_adapter.py`: realized-path command construction;
  exact `process.realized.json;printer.realized.json` and filament basename;
  explicit output-directory use; exact run-owned discovery; legacy profile
  path regression; preserved `--slice 1`; timeout and non-zero handling.
- `tests/unit/test_slicer_gcode.py`: focused malformed/empty/layer-count
  parser cases and optional time/filament facts only.
- `tests/unit/test_slicer_process.py`: reuse of timeout and process-tree
  cleanup semantics without a subprocess-runner redesign.
- optional `tests/integration/test_orca_slice.py`: version-scoped local Orca
  test using a successful Increment 2 realization, exact three realized
  configs, direct model path, Orca 2.3.2, `--slice 1`, and valid run-owned
  `plate_1.gcode`/`.gcode.3mf`; no hardware or network.

Add explicit coverage for:

- `tests/unit/test_preparation_contract.py`: READY/NOT_READY contracts remain unchanged and candidate types cannot weaken final readiness semantics;
- `tests/unit/test_setup_realization.py`: exact Increment 2 realization authority remains unchanged and Increment 3 consumes rather than repeats realization;
- `tests/unit/test_setup_recommendation.py`: recommendation/setup behavior remains unchanged and Increment 3 adds no selection or recommendation. This is the exact existing deterministic recommendation path selected from repository inspection;
- new Increment 3 tests: candidate type differs from `FinalArtifactIdentity`, no `ReadyPreparationResult` can be returned, failed execution has no candidate artifact, and the required layer-count parser fact is asserted;
- cleanup tests: pre-slice failure removes workspace; config verification failure removes workspace; process failure/timeout removes run-owned outputs/temp files; success retains the full workspace; success transfers a workspace reference without Increment 3 cleanup; user-owned model is never deleted; cleanup failure cannot become success;
- parser tests: malformed/empty G-Code fails, valid supported G-Code with a deterministic layer count succeeds, and optional time/filament facts remain optional when absent.

The existing relevant Increment 3 unit and Orca adapter tests remain in scope; the optional local Orca integration remains version-scoped and is not invoked by this plan correction. Increment 2 remains filesystem-free and no hardware test is implied.

The focused contract additions must explicitly test:

- success shape: every `SliceExecutionSuccess` field above is present with its exact type/value; required values are immutable; `workspace_path` is operational; config/realization/model/input identities are semantic evidence; and `candidate_artifact` is `CandidateSliceArtifact`, never `FinalArtifactIdentity`;
- candidate shape: exact `plate_1.gcode` path, `Literal["gcode"]`, matching non-empty `slice_run_id`, content SHA-256, and `byte_size > 0`;
- observed facts: `plate_number == 1` comes from execution context; `layer_count > 0` comes from `parse_gcode`; the five exact optional parser fields retain `None` when absent;
- return codes: `0` plus valid exact output may succeed; non-zero plus otherwise valid output is `slicer_process_failed`; `0` with no exact file is `slice_output_missing`; `0` with an empty or parser-invalid exact file is `slice_output_invalid`; exit zero alone never succeeds;
- output discovery: exact `plate_1.gcode` is accepted; `plate_2.gcode` and arbitrary `plate_*.gcode` are not; external and stale files are not accepted; a newly isolated workspace prevents stale pre-existence; and no wildcard can affect realized-mode authority;
- archive policy: missing `<workspace_path>/<source model stem>.gcode.3mf` does not fail; an unrelated `*.gcode.3mf` is not candidate authority; the exact bare export name is deterministic and no archive wildcard is used.

## Risks (not unresolved decisions)

- Existing legacy profile-mode discovery remains outside Increment 3 realized-mode authority; realized mode has the exact rules above.
- Parser diagnostics may vary by fixture, but only the explicitly listed `layer_count` fact is required; optional fields have the exact `None` behavior above.

There are no implementation-blocking open questions in this plan.

## Plan status and operations

Status: APPROVED

production modified: NO  \
tests modified: NO  \
dependencies modified: NO  \
hardware/MQTT/network: NO  \
Orca invoked: NO  \
stage: NO  \
commit: NO  \
push: NO
