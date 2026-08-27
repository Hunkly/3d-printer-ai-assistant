# Phase 3 Increment 5 — Focused Public Preparation MCP

Status: **APPROVED**

## PHASE 3 INCREMENT 5 PUBLIC PREPARATION PLAN CORRECTION

PASS

This is a plan only. It does not modify production or tests, and does not stage,
commit, push, contact a printer, or publish MQTT.

## Product Boundary

tool: `print.prepare`

preparation-only: YES. The operation performs model validation, deterministic
recommendation/selection, realization, bounded local slicing, and finalization.

slicer caller-controlled: NO. Internal pipeline is fixed to OrcaSlicer 2.3.2.

printer mutation: NO. No upload, print start/pause/resume/stop, temperature or
speed change, LAN/MQTT operation, or autonomous/background optimization.

Description: “Locally prepare and slice a model. Returns a verified READY result
or structured NOT_READY/failure. Does not upload, start printing, or modify
printer state. Material is optional; explicit material is a hard constraint;
omitted material permits deterministic compatible selection. Uses the supported
local Orca preparation pipeline.”

## Public Request

Exactly these user/product fields are accepted:

| field | type | required |
|---|---|---:|
| `model` | `str` | yes |
| `goal` | `RecommendationGoal` value | yes |
| `material` | `str \\| None` | no |
| `printer` | `str \\| None` | no |
| `build_plate` | `str \\| None` | no |
| `nozzle_diameter_mm` | `float \\| None` | no |

`slicer_kind` public: NO. `use_defaults` public: NO. The schema also excludes
process/filament profile names, `ProfileIdentity`, `PreparationIdentity`,
`PreparationAuthority`, `SelectedSetup`, workspace/config paths, executable,
flags, and timeout.

Internal default behavior: construct `SetupRequest` with
`use_defaults=True`, `slicer_kind="orca_slicer"`, and `use_llm=False`.
An explicit printer wins. An omitted printer uses
`settings.recommend.default_printer`. An explicit build plate wins; an omitted
plate uses `settings.recommend.default_build_plate`; if both are absent, fail
before authoritative setup selection with `setup_selection` /
`default_build_plate_missing`. There is no slicer-plate, first-plate,
filament, or hidden fallback. An explicit nozzle wins; otherwise select in
this exact order: the resolved exact printer profile's sole supported nozzle,
the configured `default_nozzle_diameter` when compatible, `0.4` when
supported, or the sole value in a one-value supported set. A multi-nozzle set
without a compatible configured default or `0.4` fails with
`setup_selection` / `nozzle_not_authoritative`; never choose an arbitrary first
value. This does not change `print.setup` semantics.

## Model Path Policy

settings.root sandbox: NO. No new universal root sandbox is invented.

relative base: the service boundary uses the process working directory.

lexical absolute: construct `Path(model)` and then
`Path(os.path.abspath(os.path.normpath(os.fspath(path))))` without resolving
links. Reject blank input before path construction.

reparse/symlink policy: before `resolve(strict=True)`, inspect the lexical source
path and every existing parent component with `os.lstat`. Reject any component
for which `stat.S_ISLNK(st_mode)` is true or
`getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT` is
true. This is the exact strict policy; there is no “where detectable” fallback.

strict resolve: call `resolve(strict=True)` after the component checks.

regular file: require `is_file()` on the resolved path.

suffix: require lowercase suffix in existing `SUPPORTED_INPUT_SUFFIXES`.

SHA: read/hash the resolved file bytes with genuine lowercase SHA-256. Map
`PermissionError`, `UnicodeError`, and other read/hash `OSError` failures to
`model_unreadable` or `model_hash_failed` without raw exception text/path.

ModelIdentity path: use the one resolved absolute path and digest for hashing,
`ModelIdentity`, `PreparationIdentity`, recommendation boundary validation,
realization, and `SliceExecutor`. The executor's existing source digest check
remains a correlation check on that same path, not a second authority.

Deterministic public model categories/stages:

| condition | code | stage |
|---|---|---|
| blank | `model_blank` | `model_input` |
| missing | `model_missing` | `model_input` |
| directory/non-regular | `model_not_file` | `model_input` |
| unsupported suffix | `model_unsupported_suffix` | `model_input` |
| symlink/reparse component | `model_symlink_or_reparse` | `model_input` |
| permission/read failure | `model_unreadable` | `model_input` |
| digest failure | `model_hash_failed` | `model_input` |

No silent search, source mutation, `settings.root` containment check, or unsafe
raw path/OSError message is returned.

## Leading-Hyphen Safety

absolute downstream path guaranteed: YES. `ModelIdentity.path` is the resolved
absolute operational path before `SliceExecutor` and is passed to the realized
`SliceJob`.

option-like argv possible: NO for a model basename such as `-cube.stl`; an
absolute Windows path starts with a drive/UNC prefix and an absolute POSIX path
starts with `/`. The existing Orca argument grammar remains unchanged.

mitigation/test: add a hermetic `-cube.stl` test asserting the service identity
and fake executor receive an absolute path whose full argv element does not
begin with `-`; do not add `--` or alter Orca command construction.

## Selection Authority

display reconstruction used: NO. No recommendation display string, generic
printer model, process display field, or filament name-only lookup may create
authority.

Internal types/APIs: add internal non-public `ResolvedContextAuthority`
(public `ResolvedPrintContext`, exact printer source candidates/
`ProfileInfo`, and exact selected process `ProfileInfo | None`) and
`AuthoritativeSetupSelection` in `src/print_engineer/recommendation/setup.py`
(the existing `SetupRecommendation`, exact printer and process `ProfileInfo`,
exact winning `FilamentCandidate`, and `SelectedSetup`). They are never
serialized through `print.setup`, and no generic model dump is permitted.

printer exact authority source: modify `recommendation/context.py` so the
authoritative resolver retains the exact matched machine `ProfileInfo` selected
after printer/nozzle resolution. A model-name request selects the
nozzle-qualified profile satisfying the resolved nozzle and fails closed on
ambiguity; it never returns only `ResolvedPrinter.name`.

process exact authority source: the same context resolution retains the exact
`ProfileInfo` returned for the printer's `default_print_profile` (or explicit
process internally). No rediscovery from `ProcessRecommendation` is permitted.

filament exact authority source: the existing `FilamentCandidate` already
contains `profile_name` and `setting_id`; use the exact winning candidate from
`SetupRecommendation.matrix.candidates[0]`. Do not modify
`recommendation/filament.py` unless implementation inspection proves that this
existing candidate contract is false; the planned scope below assumes it is
true and performs no display-name rediscovery.

SetupEngine: add an authoritative recommendation method in `setup.py` that
resolves and ranks once and returns `AuthoritativeSetupSelection`; existing
`recommend()` and `SetupRecommendation` dumps remain backward-compatible.

SelectedSetup owner: `PreparationService` maps those source objects directly to
`ProfileIdentity(name, kind, setting_id)` and constructs `SelectedSetup`. It
fails closed if any exact profile, process, filament, nozzle, plate, material,
or compatibility value is absent.

Real A1 authority: retain printer candidates through nozzle resolution and
require exactly one compatible source. The known result is printer `Bambu Lab
A1 0.4 nozzle` / `GM030`; process `0.20mm Standard @BBL A1` / `GP079`; filament
is the exact winning Bambu PLA Tough+ @base candidate with source
`setting_id=None`. Generic `Bambu Lab A1` is never the selected printer
authority.

ProfileIdentity is the exact source tuple: profile name, exact ProfileKind, and
the source `setting_id` when present, otherwise `None`. I5 preserves that value
exactly; it does not synthesize an ID, use `base_id`, or inherit an identifier.
Exact authority is valid when that tuple resolves to exactly one profile in the
same authoritative repository. A missing `setting_id` is not itself a failure.
Before or during realization, fail closed when the exact tuple has zero or more
than one matches, using `setup_selection` / `profile_authority_missing` or
`setup_selection` / `profile_authority_ambiguous`, respectively, with only
`details.profile_kind` set to `printer`, `process`, or `filament`.

The exact matching predicate is:
`profile.kind == identity.kind and profile.name == identity.name and
profile.setting_id == identity.setting_id`. No fuzzy names, display strings,
printer model names, prefix matching, source-path guessing, or source mixing is
permitted. The selector retains the exact source `ProfileInfo` used for each
role and maps `identity.setting_id = source.setting_id`, including `None`.
The shared authoritative repository is used for any selection-side uniqueness
check; `SetupRealizer` remains unchanged and its existing exact resolver
semantics enforce unique matching by name plus setting ID.

Published I2 evidence is authoritative: its successful realization tests
construct `ProfileIdentity(..., setting_id=None)` for printer, process, and
filament. I5 must preserve that optional-ID contract rather than introduce a
contradictory nonblank-ID rule.

generic Bambu Lab A1 substitution possible: NO; a test fails if it occurs.

## PreparationAuthority

owner: `src/print_engineer/core/preparation_service.py`, in `PreparationService`.

constructed once immediately after authoritative selection:

```python
PreparationAuthority(
    identity=PreparationIdentity(model=resolved_model,
                                 goal=authoritative.recommendation.goal),
    selected_setup=selected_setup,
)
```

The same object reaches `SetupRealizer`, `SliceExecutor` through realization,
and `SliceFinalizer`; no downstream independent model/goal/setup authority exists.

## Configuration and Dependency Graph

`SlicerConfig` gains exactly `orca_appdata_path: Path | None = None`.
`Settings._rebase` rebases it with the existing `resolve(self.root, value)`
convention only when it is not `None`; `None` remains `None`. Existing
`orca_install_path`, `bambu_install_path`, and `timeout_seconds` semantics are
unchanged, and old config files remain valid.

`PreparationService.from_settings(settings)` computes exactly one
`profile_store_root`: configured `settings.slicer.orca_appdata_path`, or
`Path.home() / "AppData" / "Roaming" /
OrcaSlicerAdapter.appdata_dirname` (currently
`<home>/AppData/Roaming/OrcaSlicer`). One root owns system and user printer,
process, and filament profiles; no separate roots are invented.

The factory creates once, in this graph:

```text
profile_store_root
  -> repository = ProfileRepository(profile_store_root)
  -> materializer = ProfileMaterializer(repository)
  -> internal ProfileReader(repository, materializer)
  -> PrintContextResolver(settings, adapter=ProfileReader)
  -> SetupEngine(settings, resolver=resolver, llm=None)
  -> authoritative selector
  -> SetupRealizer(repository, materializer)
  -> OrcaSlicerAdapter(executable=settings.slicer.orca_install_path when set,
                       appdata=profile_store_root,
                       workdir=settings.storage.workspace_dir,
                       timeout_seconds=settings.recommend.slice_timeout_seconds)
  -> SliceExecutor(settings.storage.workspace_dir, slicer=that adapter)
  -> SliceFinalizer()
```

The internal reader implements `list_profiles(kind)` as
`repository.list_profiles(kind)` and `find_profile(kind, name)` as
`None` for a missing repository profile, otherwise
`materializer.materialize(profile)`. It is the only recommendation profile
authority and is owned by the preparation service/module; there is no
self-constructing Orca adapter or independent `BaseSlicerAdapter._repository`
authority. `SetupRealizer` receives the same repository/materializer objects.
The Orca execution adapter uses the same root only for coherent execution
configuration and never rediscovers profile authority; it consumes frozen
materialized configs. `SliceFinalizer` remains the existing dependency-free
finalizer. `use_llm=False` is internal to I5; `print.setup` LLM behavior is
unchanged. Request execution is stateless, and MCP `prepare.py` obtains one
service through the factory without independently constructing stages.

## Public Envelope

READY root:

```json
{"ok": true, "preparation": {"status": "READY", "goal": "balanced"}}
```

NOT_READY root:

```json
{"ok": false, "error": {"code": "...", "message": "...",
 "details": {"status": "NOT_READY", "stage": "..."}}}
```

The existing `ok` convention is followed. Expected domain failures are normal
structured results; malformed FastMCP/Pydantic invocation uses transport/schema
failure; unexpected programming bugs use the existing unexpected-error path.

## READY Serialization

Implement an explicit serializer in `mcp/tools/prepare.py`, never `asdict()` or
generic recursion. Exact fields:

```text
preparation.status, preparation.goal
preparation.setup.slicer
preparation.setup.printer.{name,setting_id}
preparation.setup.process.{name,setting_id}
preparation.setup.filament.{name,setting_id}
preparation.setup.material, preparation.setup.nozzle_diameter_mm
preparation.setup.build_plate, preparation.setup.overrides
preparation.artifact.{path,slice_run_id,sha256,size_bytes}
preparation.slice.{layer_count,estimated_time_minutes,filament_used_mm,
filament_used_cm3,filament_weight_g}
preparation.slicer.{kind,name,version}
preparation.verification.status
```

`kind`, enums, and `Path` values use `.value` and `str(...)`; overrides are
explicit `{setting, value}` objects. `filament_weight_g` is nullable and stays
`null` when finalization does not derive it. Slicer is the finalized
`OrcaSlicer`/`2.3.2` mapping. Final artifact path is exposed intentionally;
workspace/config paths, raw evidence (including `workspace_verified`), profile
JSON, authority objects, and dataclass internals are not exposed.

Serialization: `ProfileIdentity` is projected as `{name, setting_id}` (kind is
an internal invariant and is not public); `FinalArtifactIdentity` and
`SliceRunIdentity` are projected field-by-field; `Path` becomes `str`, enums
become `.value`, and nullable fields remain JSON `null`. No `repr()`, raw
dataclass, `MappingProxyType`, or internal object is returned.

Public READY profile identity types are `string | null` for
`printer.setting_id`, `process.setting_id`, and `filament.setting_id`. The
serializer always emits the field, preserving the exact `ProfileIdentity`
value; a genuine missing source ID is JSON `null`, not omission.

## Failure Sanitization

The explicit sanitizer maps every internal failure to stable `code`, concise
deterministic `message`, and details containing only `status` and `stage`, plus
`field`, `supported_values`, or `timeout_seconds` only where proven safe. It
never returns raw details, command/argv, stdout/stderr/diagnostic,
workspace/config/source paths, profile JSON, environment, traceback, or
arbitrary keys. Generic model failures expose no path; a future useful path may
expose basename only.

The only globally allowed detail keys are `status`, `stage`, `field`,
`profile_kind`, `supported_values`, and `timeout_seconds`, and each is emitted
only by an explicit mapping. No denylist or raw-details passthrough exists.
Unsafe keys are forbidden by construction, including `command`, `argv`,
`stdout`, `stderr`, `workspace`, `workspace_path`, `output_dir`, `source`,
`source_path`, `model_path`, `config_path`, `profile_path`, `profile_json`,
`environment`, `traceback`, `diagnostic`, and arbitrary unknown keys.
Stages are `model_input`, `setup_selection`, `realization`, `slicing`, and
`final_verification`. Finalizer NOT_READY is `ok=false`,
`details.status=NOT_READY`, stage `final_verification`; it can never be READY.

### Realization mapping

Every current `realization.py` reachable category maps to public stage
`realization`, with fixed messages and no raw message forwarding:

| internal category | public code | fixed sanitized message | allowed details |
|---|---|---|---|
| `unsupported_slicer_version` | same | `The requested slicer version is unsupported.` | status, stage |
| `printer_profile_missing` | same | `The printer profile is unavailable.` | status, stage, profile_kind=printer |
| `process_profile_missing` | same | `The process profile is unavailable.` | status, stage, profile_kind=process |
| `filament_profile_missing` | same | `The filament profile is unavailable.` | status, stage, profile_kind=filament |
| `ambiguous_profile_resolution` | same | `Profile resolution was ambiguous.` | status, stage |
| `wrong_profile_kind` | same | `A selected profile has the wrong kind.` | status, stage, profile_kind when trusted |
| `profile_materialization_failed` | same | `A selected profile could not be materialized.` | status, stage, profile_kind when trusted |
| `profile_content_invalid` | same | `A selected profile is invalid.` | status, stage, profile_kind when trusted |
| `incompatible_profiles` | same | `The selected profiles are incompatible.` | status, stage |
| `unsupported_nozzle` | same | `The selected nozzle is unsupported by the printer profile.` | status, stage, supported_values only when derived from trusted profile |
| `build_plate_not_representable` | same | `The selected build plate cannot be represented.` | status, stage |
| `material_not_provable` | same | `The filament material cannot be verified.` | status, stage |
| `material_profile_mismatch` | same | `The selected material conflicts with the filament profile.` | status, stage |
| `unsupported_override` | same | `A requested setup override is unsupported.` | status, stage, field only when trusted |
| `invalid_effective_value` | same | `The effective setup contains an invalid value.` | status, stage, field only when trusted |
| `effective_settings_mismatch` | same | `The effective setup does not match the selected setup.` | status, stage, field only when trusted |

This includes the current missing-profile fallback and all
`_RealizationError`/validation paths. Any future category maps to
`realization_failed` / `realization` / `The preparation realization failed.`
with details containing only `status` and `stage`.

### Setup selection mapping

The I5-specific `profile_setting_id_missing` failure is removed. It is not
valid to fail solely because a uniquely-resolvable source has
`setting_id=None`. Selection-side fail-closed mappings are:

| condition | public code | fixed sanitized message | allowed details |
|---|---|---|---|
| exact `(kind, name, setting_id)` matches zero profiles | `profile_authority_missing` | `The selected profile authority is unavailable.` | status, stage, profile_kind |
| exact `(kind, name, setting_id)` matches multiple profiles | `profile_authority_ambiguous` | `The selected profile authority is ambiguous.` | status, stage, profile_kind |

Both use public stage `setup_selection`; no raw paths or diagnostics are
returned. Existing role-specific missing/ambiguity codes may be reused only if
they cover this exact tuple-resolution condition cleanly and consistently.

### Slice mapping

Every current `SliceExecutionFailure.stage` maps to public stage `slicing`,
with fixed messages and no diagnostic/stdout/stderr forwarding:

| internal stage | public code | fixed sanitized message | allowed details |
|---|---|---|---|
| `invalid_source_model` | same | `The source model is invalid.` | status, stage |
| `source_model_identity_mismatch` | same | `The source model identity could not be verified.` | status, stage |
| `workspace_creation_failed` | same | `The slice workspace could not be created.` | status, stage |
| `config_materialization_failed` | same | `The realized slicer configuration could not be prepared.` | status, stage |
| `config_verification_failed` | same | `The realized slicer configuration could not be verified.` | status, stage |
| `slicer_unavailable` | same | `The slicer is unavailable.` | status, stage |
| `slicer_timeout` | same | `The slicer timed out.` | status, stage, trusted configured timeout_seconds only |
| `slicer_process_failed` | same | `The slicer process failed.` | status, stage |
| `slice_output_missing` | same | `The slice output is missing.` | status, stage |
| `slice_output_invalid` | same | `The slice output is invalid.` | status, stage |
| `slice_facts_invalid` | same | `The slice facts are invalid.` | status, stage |
| `candidate_artifact_identity_failed` | same | `The slice artifact identity could not be verified.` | status, stage |

This is the exhaustive current 12-stage source taxonomy. Any future stage
maps to `slice_failed` / `slicing` / `The slicing operation failed.` with only
`status` and `stage`.

### Finalization mapping

Every current `SliceFinalizer` category maps to public stage
`final_verification`, a fixed safe message, and only `status`/`stage`:

| internal categories | public code | fixed sanitized message |
|---|---|---|
| `workspace_missing`, `workspace_reparse_or_unsafe`, `workspace_not_directory` | same | `The slice workspace failed final verification.` |
| `candidate_run_mismatch`, `candidate_path_mismatch`, `candidate_missing`, `candidate_not_file`, `candidate_empty`, `candidate_size_mismatch`, `candidate_hash_mismatch` | same | `The slice artifact failed final verification.` |
| `printer_config_path_mismatch`, `printer_config_missing`, `printer_config_not_file`, `printer_config_invalid`, `printer_config_identity_mismatch` | same | `The printer realized configuration failed final verification.` |
| `process_config_path_mismatch`, `process_config_missing`, `process_config_not_file`, `process_config_invalid`, `process_config_identity_mismatch` | same | `The process realized configuration failed final verification.` |
| `filament_config_path_mismatch`, `filament_config_missing`, `filament_config_not_file`, `filament_config_invalid`, `filament_config_identity_mismatch` | same | `The filament realized configuration failed final verification.` |
| `unsupported_slicer_version` | same | `The slicer identity failed final verification.` |

This is the complete current 26-category taxonomy. Any future category maps to
`finalization_failed` / `final_verification` /
`The prepared result failed final verification.` with only `status` and
`stage`. No internal evidence, paths, or raw messages are forwarded.

## Lifecycle

`SliceExecutor` owns unique run workspaces and existing retention/cleanup
semantics. I5 adds no janitor, TTL, copy, move, or background task. The final
artifact is returned in place. Repeated calls are independent non-cached runs
with unique run IDs/workspaces.

## Timeout

public timeout: NO. Executor bound: existing
`settings.recommend.slice_timeout_seconds` (default 600 seconds), passed once.
No repository-level MCP stdio deadline blocks this operation; external client
deadlines are outside I5. No background job system.

## Concurrency

The service is stateless per request apart from immutable shared dependencies.
Each executor call creates a unique run ID/workspace; no current setup/model/
authority is shared. A barrier-controlled parallel test proves isolated
workspaces, artifacts, and authority objects.

## Observability

Use only ordinary existing application/MCP logging if already present. I5 adds
no telemetry, audit database, metrics subsystem, event schema, or content/profile
logging.

## Existing Tool Compatibility

`print.recommend`, `print.filament_candidates`, `print.setup`, `printer.status`,
`printer.issue_info`, and all existing slicer/model tools remain unchanged,
including arguments and response schemas. Only additive registration is made in
`src/print_engineer/mcp/server.py`: import `prepare` and register
`prepare.build_tools(settings)` using the current build_tools loop.

## Failure Short-Circuiting

One attempt only: model failure → no recommendation; no compatible setup → no
realization; realization failure → no slice; slice failure → no finalizer;
finalizer NOT_READY → structured failure. No retry, fallback, reselection,
second material/printer/slice, or silent substitution.

## Production Scope

ADD:

- `src/print_engineer/core/preparation_service.py` — service, model boundary,
  authority mapping, orchestration, and transport-neutral result.
- `src/print_engineer/mcp/tools/prepare.py` — public callable, factory binding,
  explicit serializers and sanitizer.

MODIFY:

- `src/print_engineer/config.py` — additive `orca_appdata_path` field and
  conditional root rebasing.
- `src/print_engineer/mcp/server.py` — additive registration and description.
- `src/print_engineer/recommendation/context.py` — repository-backed reader,
  exact printer/process source retention, and plate/nozzle policy.
- `src/print_engineer/recommendation/setup.py` — internal authority result and
  selection API, preserving public DTO serialization.

`recommendation/filament.py` is deliberately not in scope: the existing
`FilamentCandidate` already exposes the winning `profile_name` and
`setting_id`; no behavior change is required.

No other production scope is optional or implied.

### Focused authority-contract correction to the current uncommitted I5 build

MODIFY only `src/print_engineer/core/preparation_service.py`: remove the
I5-only nonblank `setting_id` rejection and add/retain exact authoritative
repository uniqueness validation for each selected `(kind, name,
setting_id-or-None)` tuple, using `profile_authority_missing` and
`profile_authority_ambiguous` as specified above. Preserve source IDs exactly.

No additional production file is required for this issue. In particular,
`src/print_engineer/mcp/tools/prepare.py` already serializes the exact nullable
`setting_id` values and needs no correction solely for null serialization;
`recommendation/filament.py`, `adapters/slicer/realization.py`,
`adapters/slicer/profile.py`, `core/preparation.py`, and `PreparationAuthority`
remain unchanged.

## Test Scope

ADD:

- `tests/unit/test_preparation_service.py`
- `tests/unit/test_prepare_mcp.py`
- `tests/integration/test_public_prepare_acceptance.py`

MODIFY:

- `tests/unit/test_config.py` — omitted/relative/absolute appdata config and
  old-config compatibility.
- `tests/unit/test_mcp_server.py` — additive registration.
- `tests/unit/test_print_context.py` — exact reader, plate, nozzle, and source
  authority behavior.
- `tests/unit/test_setup_recommendation.py` — exact GM030/GP079/source
  authority and setting-ID failures.

REGRESSION-ONLY: `tests/unit/test_preparation_contract.py`,
`tests/unit/test_setup_realization.py`, `tests/unit/test_slice_execution.py`,
`tests/unit/test_slice_finalization.py`, `tests/unit/test_recommend_engine.py`,
`tests/unit/test_orca_adapter.py`, `tests/unit/test_recommend_mcp.py`, and
existing model/slicer/printer MCP tests. `test_setup_recommendation.py` is
modified above and is not duplicated here. No existing public tool contract
changes.

## Hermetic Matrix

model security: absolute/relative valid paths, missing, non-file, unsupported
suffix, unreadable/read/hash failure, symlink, testable Windows reparse bit,
resolved absolute ModelIdentity, genuine SHA, no root containment assumption,
no shell invocation, and `-cube.stl` absolute non-option-like downstream path.

defaults: direct tests prove explicit plate wins, omitted plate uses configured
default, and omitted plate with no default fails `default_build_plate_missing`;
explicit nozzle wins, then exact printer single nozzle, compatible configured
default, 0.4, sole remaining value, otherwise `nozzle_not_authoritative`;
incompatible configured default is a structured setup/context failure; no
arbitrary first-nozzle selection. Direct tests also cover explicit nozzle,
single-printer-nozzle, configured default, 0.4 fallback, sole remaining
nozzle, and ambiguous multi-nozzle cases.

recommendation/selection: real A1-like GM030/GP079/candidate ID; generic model
substitution and process/filament display reconstruction fail.

dependency graph: configured temporary appdata root contains the only profiles;
sentinel/decoy default-root profiles are not consulted; assert reader.repository
is service.repository, materializer.repository is that repository, and realizer
uses the same repository/materializer. Executor is separate only because it
consumes frozen configs.

PreparationAuthority: constructed once and passed unchanged.

realization/slice/finalization: exact sequencing, timeout, no retry, verified
final artifact only, and finalizer NOT_READY short-circuit.

serialization/sanitization: exact JSON-safe allowlist, null weight, enum/path
conversion, no raw evidence/details/paths/argv/output/profile JSON. Direct
serializer/MCP tests inject unmistakable sentinels in command/argv, stdout,
stderr, workspace absolute path, source/model absolute path, config/output
path, profile JSON-like content, arbitrary unknown detail key, traceback-like
text, and internal diagnostic; each sentinel is asserted absent separately.
A READY result with workspace/config/arbitrary evidence is also tested: those
remain private while the final artifact path remains public.

setting IDs: direct printer/process/filament `setting_id=None` tests each
prove valid unique authority and successful downstream resolution. Duplicate
exact printer, process, and filament tuples fail closed with
`profile_authority_ambiguous`; nonexistent exact tuples fail with
`profile_authority_missing`. Tests also prove non-null IDs are preserved
exactly, mixed None/non-null same-name sources do not mix, duplicate non-null
IDs are ambiguous, and public READY serialization emits `setting_id: null`
explicitly. Existing published I2 tests with `ProfileIdentity.setting_id=None`
remain passing evidence.

shadow/user-store authority: one system `Generic PLA` with `setting_id=None`
is valid; system plus user profiles with the same name and `setting_id=None`
are ambiguous; system `None` plus user `ABC123` resolves only the selected
authoritative source and preserves its own ID; two same-name profiles sharing
the same non-null ID are ambiguous. No source-path shadowing or source mixing
is allowed.

concurrency: independent run IDs/workspaces and no authority cross-talk.

existing MCP regression: all existing registrations and response conventions.

### Focused correction tests

MODIFY only the current I5 tests that encode the obsolete failure or real
acceptance expectation: `tests/unit/test_preparation_service.py`,
`tests/unit/test_prepare_mcp.py`, and
`tests/integration/test_public_prepare_acceptance.py`. Update
`tests/unit/test_setup_recommendation.py` only if it asserts a non-null
filament ID; otherwise it remains regression-only. Do not modify unrelated
tests.

## Real Acceptance

public READY: invoke registered `print.prepare` with a disposable local cube,
`goal=balanced`, material omitted, explicit printer `Bambu Lab A1 0.4 nozzle`,
explicit `build_plate=cool_plate`, and explicit `nozzle_diameter_mm=0.4`. Require
`ok=true`, nested `preparation.status=READY`, printer `setting_id="GM030"`,
process `setting_id="GP079"`, filament name `Bambu PLA Tough+ @base` with
`setting_id=null`, `cool_plate`, `0.4`, OrcaSlicer 2.3.2, verified artifact
SHA/size/path, correct slice facts, and nullable weight. The filament's null
ID is valid because exact repository uniqueness remains true.
This acceptance does not depend on machine defaults; defaults are hermetically
tested. The harness supplies no `PreparationAuthority` and performs no
printer/MQTT/network operation.

public fail-closed: invoke registered `print.prepare` with a missing model;
require `ok=false`, `model_missing` (or the plan's exact stable equivalent),
stage `model_input`, no source-path leak, and no executor/slice/printer/MQTT
action.

printer/MQTT/network: NO; both acceptance tests are software-only and local.

## Phase 3 Completion

I5 publication completes Phase 3: YES, only after the reconciled exact
authority and public security contracts are independently reviewed,
re-approved, implemented, and published. Printer execution included: NO.

## Open Questions

NONE.

## Verdict

approved I5 nonblank-ID rule invalid: YES

published authority contract preserved: YES

no synthetic identity required: YES

architectural core changes required: NO

plan correct: YES

implementation-ready: NO; focused correction awaits independent review and
re-approval

ready for focused independent plan review: YES

## Operations

plan modified only: YES

production modified: NO

tests modified: NO

stage: NO

commit: NO

push: NO

printer/MQTT/network: NO

current BUILD preserved: YES

Orca: NO
