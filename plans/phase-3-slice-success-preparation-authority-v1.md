# Phase 3 Slice Success Preparation Authority Handoff

Status: **APPROVED**

## Verdict

**PASS** — corrected implementation contract for the prerequisite that keeps
I4 blocked until published. This plan changes authority propagation only. It
does not implement finalization, retained-evidence verification, printer
control, or a public orchestration layer.

## Authority Construction Boundary

The repository production orchestrator exists: **NO**. The repository exposes
internal Phase 3 building blocks, but it currently has no production
`recommend → select → realize → execute` orchestrator.

The external authoritative selection handoff: **YES, explicitly defined**.
It is the caller that already owns the authoritative `ModelIdentity`, the
`SetupRecommendation.goal`, and the authoritative `SelectedSetup` selected
from that recommendation. That caller constructs `PreparationAuthority`
exactly once before realization:

```python
PreparationAuthority(
    identity=PreparationIdentity(
        model=authoritative_model_identity,
        goal=setup_recommendation.goal,
    ),
    selected_setup=authoritative_selected_setup,
)
```

The caller must not later pass model, goal, preparation identity, or selected
setup independently into realization or execution. This plan defines the safe
handoff contract; it does not introduce the missing public orchestrator.

## PreparationAuthority

Add this one frozen, slotted aggregate to
`src/print_engineer/core/preparation.py`:

```python
@dataclass(frozen=True, slots=True)
class PreparationAuthority:
    identity: PreparationIdentity
    selected_setup: SelectedSetup
```

The aggregate validates that its fields are exact `PreparationIdentity` and
`SelectedSetup` instances. Existing nested frozen/value-semantic contracts,
tuple-backed collections, and their constructor checks provide deep
immutability; no mutable aliases, copying layer, or reconstruction helper is
introduced.

The same immutable authority object is carried end-to-end:

`external authoritative selection handoff → PreparationAuthority →
SetupRealizer → successful RealizationResult → SliceExecutor →
SliceExecutionSuccess.preparation_authority`.

Downstream layers do not flatten, reconstruct, re-select, or re-resolve it.

PreparationAuthority can validate type and immutability invariants, but it
cannot derive provenance that current types do not encode. `SelectedSetup`
contains no goal or model authority, so the aggregate cannot prove that its
setup belongs to `identity.goal` or `identity.model`. That correlation is
established by the external authoritative selection handoff and frozen so
later layers cannot substitute components.

## PreparationIdentity

Existing `PreparationIdentity` is reused unchanged and is constructed at the
external authoritative selection handoff.

- model source: the authoritative `ModelIdentity` already owned by the
  preparation caller;
- goal source: the exact `SetupRecommendation.goal`;
- goal inferred: **NO**;
- different goals distinguishable: **YES**.

Do not infer the goal from `SelectedSetup`, `ActualInputIdentity`, material,
ranking, profile names, or realization data. Different goals may legitimately
produce the same concrete setup, so they produce different
`PreparationIdentity` values while potentially sharing the same actual-input
and realization identities.

## Realization API

`SetupRealizer.realize` and the underlying `realize_setup` must consume one
authority object:

```python
realize(
    preparation_authority: PreparationAuthority,
    repository: ProfileRepository,
    materializer: ProfileMaterializer | None = None,
    capability: str = ORCA_CAPABILITY,
) -> RealizationResult
```

The exact parameter ordering may follow current conventions, but no
independent `goal`, `PreparationIdentity`, or `SelectedSetup` argument may be
added alongside the aggregate. The realizer obtains
`preparation_authority.selected_setup` and uses that exact value for current
profile resolution, materialization, overlays, and effective-input creation.

`RealizationResult` replaces its independently authoritative
`selected_setup` field with:

```python
preparation_authority: PreparationAuthority
```

Both successful and existing realization-failure results retain the same input
authority, because it is now the single realization input. No authority is
added to unrelated failure types.

The successful realization construction boundary must require
`effective_inputs.actual_inputs.matches(
preparation_authority.selected_setup
)`. A result marked successful with missing effective inputs or mismatched
actual inputs fails closed using the existing structured realization failure
shape. `ActualInputIdentity` remains unchanged and is used only for forward
correlation, never to reconstruct `SelectedSetup`.

## Execution API

Preferred and required design: `SliceExecutor.execute` and `execute_slice`
receive only the `RealizationResult` plus existing execution options. They do
not accept an independent `ModelIdentity` argument:

```python
execute(realization: RealizationResult, *, timeout_seconds: float | None = None)
```

The executor obtains the authoritative model exclusively from
`realization.preparation_authority.identity.model` and uses that exact value
for source-path resolution, source-SHA verification, model correlation, and
successful result construction. No parallel goal/setup/model authority
arguments remain.

## SliceExecutionSuccess

`SliceExecutionSuccess` carries exactly one preparation authority aggregate:

```python
preparation_authority: PreparationAuthority
```

It does not carry flattened `preparation_identity` or `selected_setup` fields.
The existing `model_identity` field is **removed** because it has no distinct
semantics: model authority is available as
`success.preparation_authority.identity.model`. This eliminates contradictory
duplicate model authority and requires no equality fallback or normalization.

The result retains all existing effective and evidence fields:
`actual_input_identity`, `realization_identity`, slicer identity, workspace
authority, config identities, candidate artifact, and observed facts.

Its frozen constructor must require:

```python
actual_input_identity.matches(
    preparation_authority.selected_setup
)
```

If false, construction fails closed, including for manually constructed test
values. No caller-provided model, goal, identity, or setup is accepted to
override the carried authority.

## Correlation Ownership

- `PreparationAuthority` constructor: owns exact member types and deep
  immutability/value-semantic prerequisites. It does **not** claim goal↔setup
  or model↔setup provenance validation.
- successful `RealizationResult` constructor: owns the realizable-success
  invariant that effective actual inputs match the authority's selected setup.
- `SliceExecutionSuccess` constructor: owns the same required forward
  correlation at the manually constructible downstream success boundary.

The latter check is intentional: executor behavior alone cannot protect
manually constructed success values. No reverse reconstruction or
non-derivable provenance check is added.

## Non-Derivable Provenance

- goal ↔ setup derivable from current fields: **NO**;
- model ↔ setup derivable from current fields: **NO**.

Policy: the external authoritative selection handoff establishes the
model/goal/setup combination. `PreparationAuthority` freezes it; later layers
must carry the same value and may enforce only correlations derivable from
their fields, especially actual-input/setup equality.

## Identity Semantics and Duplication Table

| Value | Semantic relationship | Contract |
|---|---|---|
| `PreparationAuthority.identity.model` | sole preparation model authority | authoritative model for execution |
| `PreparationAuthority.identity.goal` | sole preparation goal authority | exact recommendation goal |
| `PreparationAuthority.selected_setup` | sole selected-setup authority | exact external selection |
| `ActualInputIdentity` | correlated but distinct | effective realized inputs; unchanged |
| `realization_identity` | correlated but distinct | effective realization/resource identity; unchanged |
| config identities | correlated but distinct | materialized config semantic identities; unchanged |
| `CandidateSliceArtifact` identity | correlated but distinct | candidate G-code identity; unchanged |
| observed facts | deliberately independent evidence | observed slicer facts; unchanged |

There is no contradictory duplicate preparation authority. Different goals
with identical selected/effective inputs may share `ActualInputIdentity` and
`realization_identity`; changing the goal does **not** change
`realization_identity` when effective realized inputs/resources are identical.

## Failure Contracts

Realization failure change: **YES, minimal**. Existing `RealizationResult`
failure variants retain `preparation_authority` because the aggregate is the
single realization input. They keep their existing failure diagnostics and
remain outside I4 success finalization.

Slice execution failure change: **NO**. `SliceExecutionFailure` remains a
failure-only diagnostic result and gains no optional or partial-success
`PreparationAuthority`. I4 concerns successful handoff only.

## Production Scope

MODIFY exactly:

- `src/print_engineer/core/preparation.py` — add the immutable aggregate.
- `src/print_engineer/adapters/slicer/realization.py` — accept/carry the
  aggregate and enforce successful actual-input correlation.
- `src/print_engineer/adapters/slicer/execution.py` — remove independent model
  authority, consume the carried aggregate, and expose it on success.

No recommendation production file needs modification solely to construct the
aggregate. No finalization module, Orca behavior, G-code behavior, MCP, or
printer code changes. Public/package exports are not required by the current
repository convention; if inspection proves an export is required, the exact
path must be added before implementation rather than discovered during build.

Optional scope remains: **NO**.

## Test Scope

MODIFY exactly:

- `tests/unit/test_preparation_contract.py`
- `tests/unit/test_setup_realization.py`
- `tests/unit/test_slice_execution.py`

Before implementation, verify these are the only test modules that manually
construct affected types. Regression-only coverage is:

- exact relevant recommendation goal/selection tests;
- exact relevant Orca adapter tests;
- exact relevant material and build-plate compatibility tests.

No other test module is optional implementation scope.

## Hermetic Contract Matrix

Required direct tests: **PASS**.

| ID | Required proof |
|---|---|
| A | `PreparationAuthority` is frozen and value-semantic. |
| B | authoritative model + exact recommendation goal + selected setup freeze at the handoff. |
| C | different goals with identical setup yield different preparation authorities. |
| D | same effective inputs under different goals do not change `ActualInputIdentity` semantics. |
| E | realization receives one authority, not parallel authority arguments. |
| F | realization success carries the exact same authority object. |
| G | successful realization enforces actual-input/setup matching. |
| H | inconsistent manually constructed realization success fails where derivable. |
| I | execution gets model authority from the carried aggregate. |
| J | success carries one `preparation_authority` aggregate only. |
| K | mismatched manually constructed success actual inputs fail. |
| L | no duplicate model field remains to disagree. |
| M | no goal inference. |
| N | no `SelectedSetup` reconstruction. |
| O | realization identity remains unchanged for equal realized inputs despite distinct goals. |
| P | config identities remain unchanged. |
| Q | candidate artifact and observed facts remain unchanged. |
| R | aggregate, nested values, and collections remain deeply immutable. |

Tests are hermetic: no physical hardware, network, or MQTT connection.

## Real Acceptance Harness

Type: software-only API-level acceptance harness, not a new production
orchestrator.

Actual production components: `SetupEngine`, the existing authoritative
selection surface, `SetupRealizer`, and `SliceExecutor`, with the real
installed profile repository/materializer and real OrcaSlicer 2.3.2.

Authority construction: at the documented external authoritative selection
handoff, immediately after authoritative selection and before
`SetupRealizer`.

Expected chain:

`SetupEngine → authoritative selection (goal=balanced, material omitted,
A1/cool_plate) → construct PreparationAuthority → SetupRealizer →
SliceExecutor → SliceExecutionSuccess`.

Acceptance must prove `identity.goal == balanced`, the selected setup is the
exact selected value, realization and success carry the same authority,
actual inputs match the selected setup, model authority comes from the
aggregate, Orca returns 0, and a `CandidateSliceArtifact` is produced. It
must not finalize I4 or perform printer/network/MQTT actions.

## I4 Unblock Condition

I4 is unblocked only when one `SliceExecutionSuccess` value contains:

- `preparation_authority.identity.model` and `.goal`;
- `preparation_authority.selected_setup`;
- existing `actual_input_identity`, `realization_identity`, config
  identities, candidate artifact, observed facts, slicer identity, and
  workspace authority;
- constructor/handoff guarantees for every derivable correlation, especially
  `actual_input_identity.matches(preparation_authority.selected_setup)`.

No extra caller-provided goal, `SelectedSetup`, `PreparationIdentity`, or
`ModelIdentity` is required by I4. The blocked I4 plan remains unchanged.

## Required Return Summary

### PHASE 3 PREPARATION AUTHORITY PLAN CORRECTION

PASS

### Authority Construction Boundary

production owner of type semantics: `src/print_engineer/core/preparation.py`;
authority construction owner: external authoritative selection caller;
realization owner: `SetupRealizer`; execution owner: `SliceExecutor`;
repository production orchestrator exists: **NO**; external handoff
explicitly defined: **YES**.

### PreparationAuthority

fields: `identity: PreparationIdentity`, `selected_setup: SelectedSetup`;
flattened downstream: **NO**; same aggregate carried end-to-end: **YES**;
constructor validates: exact member types and deep immutability/value
semantics; constructor cannot derive: goal↔setup or model↔setup provenance.

### PreparationIdentity

constructed: external handoff; model source: authoritative caller-owned
`ModelIdentity`; goal source: exact `SetupRecommendation.goal`; goal inferred:
**NO**; different goals distinguishable: **YES**.

### Realization API

exact input shape: one `PreparationAuthority` plus existing repository,
materializer, and capability dependencies; parallel authority args: **NO**;
success carries: the same `preparation_authority`; correlation validation:
successful actual inputs must match its selected setup.

### Execution API

exact input shape: successful/attempted `RealizationResult` plus existing
execution options; independent `ModelIdentity` argument remains: **NO**;
model authority source: `realization.preparation_authority.identity.model`;
parallel goal/setup args: **NO**.

### SliceExecutionSuccess

preparation_authority field: **YES**; flattened identity/setup fields: **NO**;
model_identity retained: **NO**; ActualInputIdentity/setup validation:
constructor requires `actual_input_identity.matches(
preparation_authority.selected_setup)`.

### Correlation Ownership

PreparationAuthority constructor: member type/deep immutability invariants;
Realization success constructor: actual-input/setup correlation;
SliceExecutionSuccess constructor: actual-input/setup correlation for all
manual constructions.

### Non-Derivable Provenance

goal↔setup derivable: **NO**; model↔setup derivable: **NO**; policy: external
authoritative handoff establishes the combination, aggregate freezes it.

### Identity Semantics

PreparationIdentity: preparation model + exact goal; ActualInputIdentity:
effective realized inputs; realization_identity: effective
realization/resources; config identities: materialized config semantics;
candidate artifact identity: candidate G-code identity; different goal + same
concrete setup changes realization_identity: **NO**.

### Failure Contracts

realization failure change: retain the single input authority in existing
`RealizationResult` failure variants; slice execution failure change: **NO**.

### Production Scope

MODIFY:

- `src/print_engineer/core/preparation.py`
- `src/print_engineer/adapters/slicer/realization.py`
- `src/print_engineer/adapters/slicer/execution.py`

optional scope remains: **NO**.

### Test Scope

MODIFY:

- `tests/unit/test_preparation_contract.py`
- `tests/unit/test_setup_realization.py`
- `tests/unit/test_slice_execution.py`

REGRESSION-ONLY: exact relevant recommendation, Orca, material, and
build-plate compatibility suites. Optional scope remains: **NO**.

### Hermetic Contract Matrix

PASS — A–R above are required, including manual constructor rejection and
deep immutability.

### Real Acceptance Harness

type: software-only API-level harness; actual production components used:
`SetupEngine`, existing selection surface, `SetupRealizer`, `SliceExecutor`,
real profile repository/materializer, OrcaSlicer 2.3.2; authority constructed
where: external authoritative selection handoff; expected chain: selection →
`PreparationAuthority` → realization → execution success with the same
aggregate.

### I4 Unblock Condition

One constructor-valid `SliceExecutionSuccess` contains the complete authority
aggregate plus existing actual-input, realization, config, artifact, facts,
slicer, and workspace values, with all derivable correlations guaranteed.

### Safety

printer/MQTT/network: **NO**; public orchestrator/MCP introduced: **NO**.

### Open Questions

**NONE**.

### Verdict

plan corrected: **YES**; implementation-ready: **YES**; ready for focused
independent re-review: **YES**; I4 remains blocked until prerequisite
published: **YES**.

### Operations

production modified: **NO**; tests modified: **NO**; blocked I4 plan modified:
**NO**; stage: **NO**; commit: **NO**; push: **NO**.
