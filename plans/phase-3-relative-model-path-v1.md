# Phase 3 — Relative Model Path Execution Compatibility

Status: APPROVED

## Real defect

The defect is confirmed with a disposable, software-only real-Orca probe using
the installed OrcaSlicer 2.3.2. A generated `cube.stl` was valid from the
caller cwd and was passed as `Path("cube.stl")`. The adapter created a fresh
slice workspace and invoked Orca with that workspace as subprocess cwd. Orca
therefore interpreted `cube.stl` relative to the workspace instead of the
caller cwd:

```text
caller cwd:                 <temporary>/caller
ModelIdentity.path:         cube.stl
resolved caller source:     <temporary>/caller/cube.stl
SliceJob.model_path:        cube.stl
Orca cwd:                    <temporary>/workspace-relative/slicer/orca_slicer/<run>
Orca stderr:                No such file: cube.stl
return code:                4294967293 (signed -3)
plate_1.gcode:              absent
```

The same source bytes supplied as an absolute path produced return code `0`
and the exact `plate_1.gcode`. No network, printer, MQTT, or production/test
changes were involved.

## Existing path contract and root cause

`ModelIdentity` in `src/print_engineer/core/preparation.py` accepts `str` or
`Path`, rejects only blank values, and stores `Path(self.path)` without
requiring an absolute path. The preparation contract test explicitly preserves
the relative spelling `./model.stl`. Relative paths are therefore supported by
the current input contract.

`ModelIdentity.path` is not used in the semantic realization/config identity
calculation. The content authority is `ModelIdentity.sha256`; the path is
source-file evidence needed to locate those bytes. `SliceExecutor` currently
uses the path directly for existence/suffix checks and SHA-256 calculation,
then passes the same still-relative path into `SliceJob`. The realized Orca
branch passes `SliceJob.model_path` unchanged to the command while setting the
workspace as `cwd`. The Python process does not change cwd: the caller-relative
path is verified under the Python process cwd, then the unchanged relative
spelling is handed to `SliceJob`; Orca starts with the workspace cwd, so that
same spelling points somewhere else or is missing, producing the observed
`-3`. `run_cli` correctly honors the cwd supplied by the adapter and does not
own model-path interpretation.

## Chosen ownership boundary

Choose option B: `SliceExecutor` derives a separate absolute operational source
path after entering the Increment 3 execution boundary, while preserving the
original `ModelIdentity` unchanged.

Rejected alternatives:

- A, rewriting `ModelIdentity.path`, would mutate authoritative handoff data
  merely to satisfy a subprocess cwd and would make path spelling observable in
  semantic results.
- C, canonicalizing in `OrcaSlicerAdapter`, is too late for the executor's
  source SHA gate and would make a lower-level adapter reinterpret an
  authoritative source handoff.
- D, changing process cwd behavior, would disturb the existing realized
  workspace/config contract and would not establish a stable source path.

## Semantic versus operational path model

Semantic authority remains:

- the original `ModelIdentity` object and its `sha256` content digest;
- the exact source bytes whose SHA-256 matches that digest;
- all existing realization, config, candidate-artifact, and final-candidate
  semantic identities.

`ModelIdentity.sha256` remains the authoritative model-content digest, and
`ModelIdentity.path` is not included in realization/config/content digests.
However, `ModelIdentity` is a dataclass and `path` participates in its value
equality. Relative and absolute path aliases with the same SHA are therefore
not necessarily equal dataclass values merely because they refer to the same
bytes. The original caller-supplied `ModelIdentity` is the handoff authority;
the operational absolute path is separate filesystem execution data. No
second authoritative `ModelIdentity` is constructed.

The operational execution path is the one concrete absolute filesystem path
stored in `SliceJob.model_path` and handed to Orca. It is not returned as a
replacement `ModelIdentity`.

## Exact canonicalization and resolution timing

At `SliceExecutor` entry, while the Python process cwd still represents the
caller's relative-path base (workspace creation does not change Python cwd):

1. Take `model_identity.path` exactly as supplied.
2. Call `Path(model_identity.path).resolve(strict=True)` exactly once while the
   caller's current process cwd still supplies the base for a relative path.
3. Map `FileNotFoundError`, `OSError`, and `RuntimeError` raised by resolution
   to `invalid_source_model`. `RuntimeError` explicitly covers resolution
   failures such as symlink loops where `resolve(strict=True)` may fail without
   `FileNotFoundError` or `OSError`.
4. Require the resulting path to be a regular file and have a supported input
   suffix; otherwise return `invalid_source_model`.
5. Read and hash that resolved path.
6. Compare the digest with `model_identity.sha256`.
7. Create/continue the slice workspace.
8. Construct `SliceJob` using that same resolved absolute `Path` as
   `model_path`.
9. Retain the original `ModelIdentity` in `SliceExecutionSuccess`.

`resolve(strict=True)` is the exact API: it makes the operational path absolute,
resolves symlinks, requires the target to exist, and uses normal Windows drive
and path semantics. There is no repository-root, workspace-root, executable-
directory, filename-search, or fallback resolution. Absolute inputs are also
resolved once by the same rule, preserving current behavior while making the
handoff invariant explicit.

The resolved path is one concrete absolute operational source path used for
source validation, SHA reading, identity comparison, and `SliceJob.model_path`.
Do not resolve the original relative path again later. There is no
repository-root fallback, filename search, or workspace-relative fallback.
No filesystem locking is required; this prevents path reinterpretation but does
not claim complete protection against source mutation between verification and
Orca execution (ordinary filesystem TOCTOU remains outside this correction).

## Failure mapping

Preserve the existing Increment 3 taxonomy:

- missing, non-regular, unsupported, or resolution-exception source path →
  `invalid_source_model`;
- specifically, `FileNotFoundError`, `OSError`, and `RuntimeError` from
  `resolve(strict=True)` all map to `invalid_source_model`;
- missing digest → `invalid_source_model`;
- resolved source bytes whose SHA-256 differs from `ModelIdentity.sha256` →
  `source_model_identity_mismatch`;
- no new path-specific category.

The resolved path must be used for both the existence/type gate and digest
calculation, so the file passed to Orca is the exact file verified by SHA-256.

## SliceJob handoff

`SliceJob` remains a single `model_path: Path` field. No duplicate source-path
field and no `core/types.py` change are required. In the Increment 3 realized
handoff, `SliceJob.model_path` receives the resolved absolute operational path.
The existing profile/config, output, slicer kind, timeout, export-name, and
plate-index contracts remain unchanged.

## Orca adapter and process runner

`src/print_engineer/adapters/slicer/orca.py` requires no modification. It may
continue to pass `job.model_path` unchanged and continue to run with the
workspace cwd; the executor now supplies a path whose meaning is independent
of that cwd. `src/print_engineer/adapters/slicer/process.py` also remains
unchanged.

## Success-result authority

`SliceExecutionSuccess.model_identity` must contain the exact original
`ModelIdentity` object supplied to `SliceExecutor.execute`, including its
original relative path spelling when applicable. The executor must not build a
new absolute-path `ModelIdentity`. This preserves the authoritative handoff,
content digest authority, and identity-object behavior already asserted by the
execution tests.

## Minimum production scope

The exact modified production file is:

- `src/print_engineer/adapters/slicer/execution.py`: resolve the source once at
  the execution boundary, map resolution failures, verify the resolved file,
  and pass that operational path into `SliceJob`.

No other production file may be modified during BUILD.

No changes to `core/types.py`, `orca.py`, `process.py`, realization,
preparation, gcode parsing, recommendation, profile repository/materializer,
printer, MQTT, or MCP code are authorized. If inspection during implementation
proves the existing `SliceJob` type cannot carry an absolute `Path`, stop and
report that contract conflict before expanding scope.

## Direct hermetic test scope

Primary modified test path:

- `tests/unit/test_slice_execution.py`

Add focused coverage for:

- valid relative source: caller cwd contains source, resolution is absolute,
  SHA verifies, the absolute path reaches `SliceJob`, and execution succeeds;
- success preserves the exact original object (`result.model_identity is
  original_model`), while `SliceJob.model_path` is separately absolute;
- absolute input remains successful;
- missing relative path maps to `invalid_source_model`;
- mocked `FileNotFoundError`, `OSError`, and `RuntimeError`/symlink-loop-style
  resolution failures each map to `invalid_source_model`;
- SHA mismatch maps to `source_model_identity_mismatch`;
- caller source A and workspace source B with the same basename never
  substitute B; the exact resolved A is passed to the slicer;
- workspace/subprocess cwd cannot reinterpret the absolute job path;
- no search or fallback occurs.

Only `tests/unit/test_slice_execution.py` may be modified. The following are
regression-only/run-only and must not be modified: `tests/unit/test_orca_adapter.py`,
`tests/unit/test_slicer_gcode.py`, `tests/unit/test_setup_realization.py`,
`tests/unit/test_preparation_contract.py`, `tests/unit/test_setup_recommendation.py`,
and `tests/unit/test_recommend_engine.py`.

## Regression scope

Run the focused execution and these regression-only/run-only paths:

- `tests/unit/test_slice_execution.py`;
- `tests/unit/test_orca_adapter.py`;
- `tests/unit/test_slicer_gcode.py`;
- `tests/unit/test_setup_realization.py`;
- `tests/unit/test_preparation_contract.py`;
- `tests/unit/test_setup_recommendation.py`;
- `tests/unit/test_recommend_engine.py`.

Run Ruff on changed production/test files and Mypy on the changed module and
relevant test surface. Do not bundle or alter the known
`test_print_context.py::test_ambiguous_prefix_match_raises`, Windows timeout,
or pre-existing preparation-test Mypy failures.

## Required real relative-path smoke retest

After implementation, run a fresh disposable, software-only acceptance test
using the actual installed OrcaSlicer 2.3.2 and a relative `ModelIdentity.path`:

```text
real recommendation
→ deterministic selection
→ realization
→ SliceExecutor with relative source path
→ resolve and SHA-256 verify source
→ SliceJob with absolute operational source path
→ OrcaSlicer 2.3.2 with --slice 1
→ return code 0
→ exact plate_1.gcode
→ structural validation PASS
→ positive parser-derived layer_count
→ CandidateSliceArtifact
→ SliceExecutionSuccess
```

Capture selected setup, realization identity, executable/version, exact model
argument, caller cwd, Orca cwd, resolved source path, source SHA-256, return
code in signed/unsigned form when relevant, candidate size/digest, structural
validation result, layer count, optional facts, and concise stdout/stderr. Do
not dump full G-Code. Evidence must explicitly prove that the original
`ModelIdentity.path` was relative, the operational path handed to Orca was
absolute, that path pointed to the SHA-verified source, and the return code was
`0`. The previously observed `4294967293` / signed `-3` must not recur for this
same relative input. No mocks are permitted in this acceptance smoke.

## Absolute-path regression

Required: rerun the existing equivalent absolute-path real-Orca smoke, or the
same full chain with an absolute `ModelIdentity.path`, and require return code
`0`, exact `plate_1.gcode`, structural validation, positive layer count, and
`SliceExecutionSuccess`.

## Identity regression

UNCHANGED: the original `ModelIdentity` handoff object in the result;
`ModelIdentity.sha256` content authority; `ActualInputIdentity`;
`realization_identity`; `RealizationResource` identities; realized config
identities; `CandidateSliceArtifact` digest semantics; and observed facts.
Relative-path and absolute-path `ModelIdentity` values are not claimed equal:
they may differ under dataclass equality because path participates in value
equality and the path spelling differs. Operational canonicalization is
separate from authoritative handoff preservation.

## Safety

This is plan-only. No printer, MQTT, network, upload, print start, physical
hardware action, staging, commit, or push is authorized. The reproduction and
later smoke are local software-only operations in disposable workspaces.

## Acceptance criteria

1. Relative `ModelIdentity.path` remains accepted as an input contract.
2. Resolution occurs at executor entry against the caller/current cwd, before
   the relative path is handed to an Orca subprocess whose cwd is the run
   workspace; workspace creation itself does not change Python cwd. It uses
   `resolve(strict=True)` exactly once.
3. The SHA-256-verified file and the file handed to Orca are the same resolved
   path.
4. The original `ModelIdentity` remains the success-result authority.
5. Missing/unresolvable paths and digest mismatches retain the existing failure
   stages.
6. Orca/process, recommendation/realization, and semantic identity contracts
   remain unchanged.
7. Focused hermetic tests and relevant Ruff/Mypy checks pass, with known
   unrelated failures classified separately.
8. The real relative-path full chain succeeds with OrcaSlicer 2.3.2, `--slice 1`,
   return code `0`, exact `plate_1.gcode`, structural validation, positive
   layer count, and `SliceExecutionSuccess`.
9. The absolute-path full-chain regression still succeeds.

## Open Questions

NONE. Repository evidence resolves relative-path support, semantic versus
operational path roles, the `resolve(strict=True)` API, caller cwd timing,
success-result authority, SliceJob handoff, Orca ownership, and minimum scope.

## Verdict

Plan implementation-ready: YES

Ready for independent plan review: YES

## Operations

Production modified: NO

Tests modified: NO

Stage: NO

Commit: NO

Push: NO
