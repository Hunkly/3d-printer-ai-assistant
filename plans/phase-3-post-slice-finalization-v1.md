# Phase 3 Increment 4 — Deterministic Post-Slice Finalization

Status: **APPROVED**

## Verdict

**PASS** — corrected against the independent review findings. Published prerequisite:
`bb92c2d9ca7547589c84fdcab9cf3687de881997`. This is a plan only.

## Contract and trust boundary

I4 accepts exactly one constructor-validated immutable `SliceExecutionSuccess`
and returns `PreparationResult`; it accepts no goal, setup, model, identity,
realization, or other authority argument. Constructor/type invariants are
trusted: `PreparationAuthority`, preparation identity, actual-input
correlation, frozen identities, slicer fields, and observed facts are not
reconstructed. Mutable filesystem evidence is reverified.

There is no `invalid_success_record` runtime result. An inconsistent success
object cannot be constructed through the supported API, and every valid success
supplies the preparation identity required by `NotReadyPreparationResult`.
Existing types are reused unchanged: `FinalArtifactIdentity`,
`SliceRepresentation`, `VerificationRepresentation`,
`PreparationFailure`, `ReadyPreparationResult`, and
`NotReadyPreparationResult`. Existing string failure codes plus
`FailureStage.FINAL_VERIFICATION` are sufficient; no taxonomy enum change is
required.

## Lifecycle and workspace authority

I3 creates and retains the successful workspace and passes its immutable path
authority in `SliceExecutionSuccess`. I4 only performs non-destructive
exact-path/evidence verification. It does not prove historical directory
identity or run ownership, and the path is not deletion authority.

- READY leaves workspace, candidate, three realized configs, and optional archive
  untouched; the final artifact remains in that retained workspace.
- NOT_READY leaves workspace and all candidate/config evidence untouched.
- The caller is responsible for later lifecycle management outside I4.
- I4 must not call `shutil.rmtree`, `Path.unlink`, `os.remove`, directory
  deletion, or any cleanup helper.
- No background janitor, TTL, or cleanup thread exists.

The finalizer is stateless and consumes no evidence. The same immutable success
plus unchanged evidence gives an equivalent READY or equivalent NOT_READY;
external mutation may change the result. Cleanup failure taxonomy, cleanup
evidence, best-effort deletion, and cleanup tests are removed.

## Exact filesystem and Windows reparse policy

The inspected project runtime is CPython 3.12 on Windows. Implement one exact
finalizer-local check using `os.lstat(path)`: reject when
`stat.S_ISLNK(st.st_mode)` or
`st.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT` is nonzero.
This unconditionally rejects symlinks, junctions, and Windows reparse points;
there is no “where detectable” or build-time choice.

For the original `workspace_path`, frozen candidate path, and each expected
config path:

1. Use `os.path.abspath(os.path.normpath(...))` for absolute lexical
   normalization without following links. Reject a supplied lexical `..`
   component that escapes its declared workspace.
2. Require workspace existence, directory type, and no-reparse status.
3. Construct only documented direct-child paths; no wildcard discovery,
   fallback, or alternate filename.
4. Before resolving, apply the same `lstat` check to each existing direct
   evidence path.
5. After rejection, call `Path.resolve(strict=True)` and require each resolved
   evidence path to be a descendant of resolved workspace.
6. Require candidate/config paths to be regular files.

Reparse rejection covers the workspace itself and each direct evidence file.
There are no intermediate child components because I3's evidence paths are
direct children. Unrelated ancestors above workspace are not inspected for
ownership; I4 never authorizes deletion or claims global ownership.

## Candidate verification

Expected candidate path is exactly `workspace / "plate_1.gcode"`. The frozen
`success.candidate_artifact.path` must have the same absolute normalized,
Windows case-normalized lexical path as that expected path. Required order:

1. candidate `slice_run_id == success.slice_run_id);
2. workspace and candidate no-reparse checks;
3. exact path and resolved containment;
4. exists and is a regular file;
5. byte size is greater than zero;
6. fresh byte size equals frozen `byte_size`;
7. fresh SHA-256 equals frozen `sha256`.

`artifact_format` is not reread here. `CandidateSliceArtifact` declares
`artifact_format: Literal["gcode"]` and its `__post_init__` rejects every
other value, so I4 trusts this immutable constructor invariant like the other
constructor-validated `SliceExecutionSuccess` metadata. There is no runtime
artifact-format mismatch path or impossible filesystem matrix case.

Only then may it become final. Mappings are:
`candidate_missing`, `candidate_not_file`, `candidate_empty`,
`candidate_size_mismatch`, `candidate_hash_mismatch`,
`candidate_path_mismatch`, `candidate_run_mismatch`, and
`candidate_reparse_or_unsafe), respectively for the listed failures.

Construct existing `FinalArtifactIdentity` from verified candidate path,
success run id, fresh SHA-256, and fresh `size_bytes`; never copy stale
identity values. After these validations its constructor cannot meaningfully
fail, so no artificial final-identity failure category or test is added.

## Config verification

Use exactly:

- `workspace / "printer.realized.json"`;
- `workspace / "process.realized.json"`;
- `workspace / "filament.realized.json"`.

For each: workspace/no-reparse check; exact expected path; path/no-reparse
check; containment; existence; regular-file check; read bytes; decode/parse
JSON; require the current I3 root shape (object/dict); canonicalize using the
exact I3 algorithm (`ensure_ascii=False`, sorted keys, compact separators);
digest with the exact SHA-256 helper; compare the corresponding frozen identity.
Do not resolve profiles, replay inheritance, or rematerialize.

Direct parametrized cases are required for printer, process, and filament:
missing (`*_config_missing`), non-file (`*_config_not_file`), malformed JSON
or wrong root shape (`*_config_invalid`), semantic identity mismatch
(`*_config_identity_mismatch`), path escape (`*_config_path_mismatch`),
and reparse point (`*_config_reparse_or_unsafe`).

## Facts and model promise

I4 does not parse G-code. I3's `ObservedSliceFacts` come from
`parse_gcode(candidate)` over the candidate bytes; plate 1 is bound by the
exact filename. Fresh size plus exact SHA equality proves the reread bytes are
the I3-parsed bytes, so frozen facts are reused. No sidecar/archive fact source
exists.

I4 does not reread or rehash the source model. READY promises consistency with
source bytes whose `ModelIdentity.sha256` was verified immediately before I3
slicing, not that the source path still has those bytes. Source mutation after
success therefore still yields READY if candidate/config evidence remains valid.

## Slicer identity and READY mapping

Validate `success.slicer_name` and `success.slicer_version` before deriving
the representation. Current I3 publishes the exact supported identity
`slicer_name == "OrcaSlicer"` and `slicer_version == "2.3.2"`. I4 requires
exact equality for both fields: no prefix matching, semantic-version ranges,
aliases, case folding, nonblank-only acceptance, valid-looking-version
acceptance, or future-version inference. A mismatch in either field returns
NOT_READY with the existing `unsupported_slicer_version` compatibility
mapping; this single existing category groups unsupported slicer name and
unsupported slicer version because the taxonomy has no separate I4 category
for them.

Only after both exact comparisons succeed may I4 construct
`SlicerKind.ORCA_SLICER` and the existing `SliceRepresentation`.
`SlicerKind.ORCA_SLICER` is not blindly hardcoded: in I4 v1 it is the
deterministic representation selected only after validating exact supported
`OrcaSlicer 2.3.2`. No alternate slicer/version mapping exists in I4 v1.

READY uses:

- `identity = success.preparation_authority.identity`;
- `selected_setup = success.preparation_authority.selected_setup`;
- `SliceRepresentation` from validated Orca kind, success run id, actual
  inputs, verified artifact reference, and frozen observed facts;
- `FinalArtifactIdentity` from fresh candidate path/hash/size;
- PASS `VerificationRepresentation` with selected setup and actual inputs;
- non-empty immutable evidence for candidate/config checks, correlations,
  slicer identity, and facts.

No authority is reconstructed or duplicated. READY retains the workspace.

## NOT_READY taxonomy

Every failure returns `NotReadyPreparationResult` with
`FailureStage.FINAL_VERIFICATION`, trusted preparation identity, structured
code/message/details, and no PASS verification. Stable codes are:

- `workspace_missing`, `workspace_not_directory`,
  `workspace_reparse_or_unsafe`;
- all candidate codes listed above;
- per-config missing, not-file, invalid, identity-mismatch, path-mismatch, and
  reparse/unsafe codes for printer/process/filament;
- `unsupported_slicer_version` for either exact slicer-name or exact
  slicer-version mismatch;
- `correlation_mismatch` only for a genuinely mutable/runtime correlation not
  protected by the constructor.

There is no `invalid_success_record`, ownership mismatch, cleanup failure, or
final identity construction failure category. All NOT_READY results retain the
workspace and evidence.

## TOCTOU limit

I4 verifies current evidence sequentially; it does not create an atomic
filesystem snapshot. Files may mutate after verification. READY proves values
matched frozen I3 authority during the finalization pass, not permanent
immutability.

## API and exact production scope

Add `src/print_engineer/adapters/slicer/finalization.py` with synchronous
`SliceFinalizer` and the unconditional `finalize_slice(...)` convenience
function; `execution.py` establishes this module-level convenience convention
with `execute_slice(...)`. The finalizer has exactly one `SliceExecutionSuccess` input and
`PreparationResult` output. No subprocess, Orca, profile resolution, network,
printer, MQTT, or MCP.

Inspection found module-level importable `_canonical` and `_digest_bytes` in
`adapters.slicer.execution`. Finalization imports and reuses those exact
helpers. No helper promotion is required and no identity algorithm is copied.

Production scope:

ADD:
- `src/print_engineer/adapters/slicer/finalization.py`

MODIFY:
- none

Optional production scope: **NO**.

## Exact test scope

ADD:
- `tests/unit/test_slice_finalization.py`

MODIFY:
- none

REGRESSION-ONLY:
- `tests/unit/test_preparation_contract.py`
- `tests/unit/test_slice_execution.py`
- `tests/unit/test_setup_realization.py`
- `tests/unit/test_setup_recommendation.py`
- `tests/unit/test_recommend_engine.py`
- `tests/unit/test_orca_adapter.py`

Optional test scope: **NO**. Hermetic tests construct valid success records and
call only the finalizer.

## Complete hermetic matrix

Cover directly (parametrize repetitive cases):

1. valid READY; exact preparation identity; exact selected setup; authority
   preservation; fresh artifact hash/size; fact reuse by SHA; no extra authority;
2. candidate missing, non-file, empty, size mismatch, same-size changed hash,
   path mismatch, run mismatch, outside workspace, and reparse;
3. workspace missing, non-directory, reparse, and replacement at the same path
   with incomplete/different evidence;
4. each of printer/process/filament missing, non-file, malformed JSON, wrong
   root shape, identity mismatch, path escape, and reparse;
5. exact `slicer_name == "OrcaSlicer"` and
   `slicer_version == "2.3.2"` produces READY when all other evidence is
   valid; wrong name with `2.3.2`, exact name with `2.3.3`, exact name with a
   blank version, and exact name with an arbitrary nonblank unsupported
   version each produce NOT_READY with `unsupported_slicer_version`. Blank
   version is representable by `SliceExecutionSuccess`, so it is a finalizer
   rejection. Directly prove the constructor contract by asserting
   `CandidateSliceArtifact(..., artifact_format != "gcode", ...)` raises
   `ValueError`; no I4 artifact-format NOT_READY test is required because the
   mismatch cannot be a valid supported input.
6. source mutation after success still READY;
7. READY and NOT_READY leave workspace intact; repeated unchanged READY and
   unchanged NOT_READY are equivalent; external mutation may change result;
8. different goals with same verified artifact retain different preparation
   identities and identical final artifact identities;
9. deep immutability and no subprocess/profile resolution/network/printer/MQTT.

Replacement proves current evidence consistency at the exact expected path; it
does not prove the same directory object or historical run ownership.

## Required real acceptance

Use the existing software-only chain with balanced, material omitted, GM030,
GP079, PLA Tough+, and `cool_plate`:

`SetupEngine → authoritative handoff → PreparationAuthority → SetupRealizer →
SliceExecutor → SliceFinalizer`.

Require READY, genuine model SHA, fresh candidate SHA/size, all three config
identities reverified, exact `slicer_name == "OrcaSlicer"` and
`slicer_version == "2.3.2"` validated before deriving
`SlicerKind.ORCA_SLICER`, exact
`FinalArtifactIdentity`, exact authority, and no extra authority. No printer,
MQTT, network, upload, or print-start action.

From a disposable success, mutate candidate G-code bytes and finalize again.
Require NOT_READY with the exact hash/size failure, retained workspace, no Orca
rerun, no cleanup, and no READY.

## Required return summary

### PHASE 3 INCREMENT 4 FINALIZATION PLAN CORRECTION

PASS

### Lifecycle

workspace deletion performed by I4: **NO**  
READY workspace retained: **YES**  
NOT_READY workspace retained: **YES**  
cleanup taxonomy removed: **YES**

### Workspace Authority

deletion ownership proof required: **NO**  
reason: I4 never deletes; caller owns later lifecycle management.  
verification guarantee: exact-path, no-reparse, containment, regular-file, and
fresh evidence checks against frozen I3 authority.

### Reparse Safety

exact API/mechanism: `os.lstat` + `stat.S_ISLNK` +
`FILE_ATTRIBUTE_REPARSE_POINT`.  
workspace check: existing directory and no-reparse.  
candidate check: exact direct child, no-reparse, contained, regular file.  
config check: same exact policy for each direct child.  
ancestors policy: unrelated ancestors above workspace are not inspected.

### Candidate

exact expected path: `workspace / "plate_1.gcode"`.  
checks: run, exact path, safety, containment, existence, regular file, positive
size, fresh size, and fresh SHA. `artifact_format` is trusted as the immutable
constructor invariant described above.  
failure mappings: exact `candidate_*` codes above.

### Configs

exact expected paths: the three documented `*.realized.json` children.  
canonical identity helper: `execution._canonical` and
`execution._digest_bytes`.  
root-shape rule: JSON object/dict.  
failure mappings: exact per-config codes above.

### Facts

reparse: **NO**  
hash correlation: exact fresh candidate SHA/size binds frozen I3 facts.  
sidecar dependency: **NONE**

### Model

rehash: **NO**  
source mutation result: READY when retained evidence remains valid.

### Slicer Identity

success field used: `slicer_name`, `slicer_version`.  
exact supported name: `OrcaSlicer`.  
exact supported version: `2.3.2`.  
name comparison: exact equality.  
version comparison: exact equality.  
aliases/ranges accepted: **NO**.  
unsupported name result: NOT_READY `unsupported_slicer_version`.  
unsupported version result: NOT_READY `unsupported_slicer_version`.  
SliceRepresentation derivation: only after both exact checks, using
`SlicerKind.ORCA_SLICER` plus success run, actual inputs, artifact reference,
and facts. The kind is not blindly hardcoded; no alternate mapping exists in
I4 v1.

### Candidate Artifact Format

constructor inspected: **YES** (`CandidateSliceArtifact` in
`adapters.slicer.execution`).  
non-gcode valid construction possible: **NO**.  
chosen contract: **CONSTRUCTOR_INVARIANT**.  
runtime artifact_format check: **NO**.  
failure mapping: none; an invalid format is rejected at construction and is
not a valid I4 input.  
constructor/regression coverage: the added
`tests/unit/test_slice_finalization.py` directly asserts non-`gcode`
construction raises `ValueError`; `tests/unit/test_slice_execution.py` is
regression-only and requires no modification.

### Invalid Success

runtime category retained: **NO**  
reason: constructor-valid success always provides valid preparation identity.

### Final Artifact

existing type: `FinalArtifactIdentity`.  
fresh fields: verified path, success run id, fresh SHA-256, fresh byte size.  
construction failure possible: **NO** after existing validations.  
failure mapping: none invented.

### READY

type: `ReadyPreparationResult`.  
workspace owner/lifecycle: retained and untouched; caller handles later lifecycle.  
idempotent: **YES** for unchanged success/evidence.

### NOT_READY

type: `NotReadyPreparationResult`.  
workspace lifecycle: retained and untouched.  
idempotent: **YES** for unchanged failed evidence.  
cleanup errors: **NONE**.

### Helper Strategy

exact helper reuse: `execution._canonical`, `execution._digest_bytes`.  
helper promotion required: **NO**

### Production Scope

ADD:
- `src/print_engineer/adapters/slicer/finalization.py`

MODIFY:
- none

optional scope remains: **NO**

### Test Scope

ADD/MODIFY:
- ADD `tests/unit/test_slice_finalization.py`; MODIFY none.

REGRESSION-ONLY:
- exact six files listed above.

optional scope remains: **NO**

### Hermetic Matrix

candidate complete: **YES**  
workspace complete: **YES**  
config complete: **YES**  
slicer complete: **YES** — exact `OrcaSlicer` + `2.3.2` READY; wrong name,
wrong version, blank version, and arbitrary nonblank unsupported version
NOT_READY with `unsupported_slicer_version`.  
artifact-format constructor proof complete: **YES** — non-`gcode` construction
fails; no impossible runtime mismatch case is specified.  
lifecycle complete: **YES**  
authority complete: **YES**  
safety complete: **YES**

### Real Acceptance

READY smoke: required  
mutation NOT_READY smoke: required  
workspace retained in both: **YES**

### Open Questions

**NONE**

### Verdict

plan corrected: **YES**  
implementation-ready: **YES**  
ready for focused independent re-review: **YES**

### Operations

production modified: **NO**  
tests modified: **NO**  
stage: **NO**  
commit: **NO**  
push: **NO**
