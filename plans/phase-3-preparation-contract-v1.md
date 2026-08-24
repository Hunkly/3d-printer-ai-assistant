# Phase 3 Increment 1 — Authoritative Preparation Contract v1

Status: APPROVED

## Current verified baseline

Authoritative product discovery is [plans/phase-3-product-discovery.md](phase-3-product-discovery.md), whose first candidate increment is the internal authoritative preparation/final-plan contract. It requires one bounded, read-only preparation result with one selected concrete setup, deterministic evidence, and—only on success—one final local slice artifact. It explicitly defers realization, slicing, verification logic, orchestration, and MCP exposure to later increments.

The repository already provides:

- `RecommendationGoal` in `src/print_engineer/core/recommendation.py` with the authoritative values `surface_quality`, `strength`, `print_time`, `filament_usage`, and `balanced`;
- deterministic recommendation/setup models, including `RECOMMENDABLE_SETTINGS`, `SetupRecommendation`, and typed process/filament/printer settings;
- frozen shared identities and job/result types in `src/print_engineer/core/types.py`, including `ProfileInfo`, `SliceJob`, `SliceResult`, `ModelAnalysis`, and `SlicerKind`;
- `SliceStatistics` for measured time, filament, and layer facts.

The working tree contains an untracked `src/print_engineer/core/preparation.py` and an untracked `tests/unit/test_preparation_contract.py`, reported as a previous direct implementation. They are audit inputs, not approval evidence. The implementation is not accepted by this plan merely because it exists. The focused test file currently has three tests and does not prove the full contract below.

## Problem

Existing capabilities are separately callable and do not yet own one authoritative preparation result. Increment 1 must define the value contract and invariants that later realization, bounded workflow, slicing, and post-slice finalization will consume. It must not perform any of those operations.

The current unapproved implementation is directionally useful but requires correction after approval: its result always requires a selected setup, its `NOT_READY` branch can contain successful slice/verification values, overrides are arbitrary strings rather than a validated typed representation, slice data repeats the selected setup as a second authority, and several required consistency and deep-immutability rules rely on caller discipline.

## Increment 1 goal

Define immutable internal types for exactly two authoritative outcomes:

1. a ready-for-review successful preparation; or
2. a safe non-ready preparation failure.

The contract is data-only. It may validate values and cross-field invariants at construction time, but it must not select, realize, apply, slice, inspect, persist, expose through MCP, or control a printer.

## Internal result model

Use separate frozen success and failure variants (or an equally strong discriminated constructor-enforced union), rather than a bag of optional fields whose valid combinations depend on callers. The result model must make ambiguous partially-ready success impossible to construct.

The ready variant must contain exactly one authoritative `SelectedSetup`, the preparation identity, deterministic evidence, successful slice facts, a passing post-slice verification representation, and exactly one `FinalArtifactIdentity`. The not-ready variant must contain stable structured failure information and may retain non-authoritative diagnostic evidence or an optional setup selected before the failure, but that setup must not imply successful realization or finalization. It must not claim a final artifact or successful final slice. A failure before setup selection must be representable without fabricating a selected setup.

Do not make a boolean readiness field independently mutable from the outcome tag. If a single result wrapper is used, construction must reject `READY`/failure and `NOT_READY`/successful-final combinations, and must reject any other contradictory state.

## Selected setup contract

The successful result has exactly one authoritative setup preserving these logical identities:

- slicer;
- printer/profile;
- nozzle diameter;
- build plate;
- material;
- filament profile;
- process profile;
- concrete authoritative overrides applied for this preparation run.

Reuse existing repository enums and profile identities where appropriate, but do not embed mutable profile documents or create a second competing setup model without need. Identity references must be stable and sufficient for later comparison with actual slicer inputs. Candidate and rejected setups belong in deterministic evidence/diagnostics only; they must never be alternative authorities in a successful result.

The contract must reject invalid required combinations, including missing/blank identities, non-positive nozzle values, wrong profile kinds where the existing type system can establish that fact, duplicate override keys, and any setup that cannot be compared later with effective slicer inputs.

## Override contract

The authoritative override value is an immutable typed scalar (numeric, string, or boolean only where that exact setting permits it), paired with a setting identity and sufficient canonical representation for later effective-input comparison. Do not use an arbitrary mutable dictionary or an unrestricted `setting_name: str` / `value: str` pair as the preparation authority.

The implementation must validate override names against the existing deterministic allowlist `RECOMMENDABLE_SETTINGS`. The current repository provides that allowlist and recommendation value vocabulary, but does not provide one complete per-setting runtime validator; therefore the implementation must define only the narrow setting-aware type/domain mapping needed by these allowlisted settings (reusing existing types/constraints where they exist), and must reject values whose type, range, or canonical form cannot be established. Unsupported settings—including temperatures, flow, calibration, and hardware tuning—must be rejected rather than silently accepted. Duplicate keys and non-canonical values must be rejected. Overrides are approved preparation inputs for later realization, not application logic or a generic instruction surface, and no LLM-authored value may become authoritative through this type.

## Model/goal identity

Reuse `RecommendationGoal` exactly; do not add another goal enum or unconstrained goal system. The contract must preserve all five existing values without renaming or remapping them.

Use the smallest stable source-model identity needed by later comparison and evidence: at minimum a normalized/reference path plus an optional content digest when available, with explicit semantics for unknown digest. Do not duplicate the complete `ModelAnalysis` graph. Deterministic model facts may be carried as an immutable projection/reference in evidence or a dedicated value object, while the source identity remains distinct from measured facts.

## Evidence authority

Represent deterministic evidence separately from optional explanatory narrative. The authoritative evidence model must be immutable, machine-readable, and able to carry:

- model facts;
- local profile and compatibility facts;
- deterministic ranking/selection evidence;
- selected setup and accepted overrides;
- assumptions, unknowns, warnings, and meaningful rejected alternatives;
- later slice facts and post-slice verification results.

Evidence should have stable authority/category and code fields, typed or canonical values, and structured details sufficient for comparison. LLM narrative may be stored separately as non-authoritative explanation only. It must never determine setup, compatibility, override values, slice facts, artifact existence, or readiness.

## Artifact contract

Define `FinalArtifactIdentity` only; do not create, write, persist, clean up, or verify files in this increment. A successful result eventually owns exactly one authoritative final local slice artifact, identifiable by a stable path/reference, an explicit immutable identity for the slice result/run it belongs to, and, when available, digest/size metadata. Temporary or intermediate outputs are not final artifacts. A safe failure must not contain an authoritative successful final artifact or fabricate one.

## Slice/verification representation

Define immutable projections that can carry later authoritative slice facts, reusing existing field vocabulary from `SliceResult` and `SliceStatistics` where possible:

- an explicit successful/failed slice outcome;
- actual slicer/output identity;
- an immutable actual-input identity snapshot (not another `SelectedSetup` authority);
- estimated print time;
- filament usage in existing units;
- layer count when available;
- warnings and errors.

Do not embed mutable `SliceResult`/Pydantic settings objects directly as the authoritative result. Existing list-backed fields must be copied into immutable tuples or equivalent immutable value types.

Define an immutable verification representation for later deterministic logic to record expected versus actual setup/input identities, artifact existence, compatibility, mismatch/divergence, and verification warnings/errors. Its outcome must use an explicit status such as `PASS` versus `BLOCKING_MISMATCH` (with any non-blocking warning state clearly distinct), not an unconstrained `passed` boolean whose meaning depends on the contents of other fields. It is a report of checks, not verification logic. Avoid making a second full selected setup authoritative: the selected setup remains the expected authority, while actual inputs are a comparable immutable snapshot.

## Readiness/failure invariants

Construction-time validation must enforce at least:

### Ready

- selected setup is present and unique;
- final artifact identity is present exactly once;
- slice facts represent a successful slice and carry the required available facts without inventing unknown values;
- verification is present with explicit `PASS` status and establishes readiness;
- no failure payload is present;
- expected and actual setup/input identities are consistent, and any blocking divergence makes the result non-ready.

### Not ready

- readiness cannot claim ready;
- no authoritative final artifact is present;
- no fabricated successful slice or successful verification is present;
- stable machine-readable failure code/message/details are present;
- incomplete stages, unsupported inputs, missing profiles, validation errors, slice failures, missing artifacts, and verification failures are representable without fake setup or facts.

Use a small stable failure-stage/category value (for example model/input, setup selection, realization, validation, slicing, artifact, and final verification) plus a machine-readable code. Do not create a large exception taxonomy. Early failures must not require downstream setup, slice, artifact, or verification values; later failures may retain only the non-authoritative upstream values that actually existed.

Warnings may coexist with ready only when verification establishes that they are non-blocking. A not-ready result must not be a “successful result with a false flag.”

## Immutability requirements

All authoritative top-level values must be frozen/immutable. Nested collections must be tuples/frozen mappings or equivalent immutable structures. No mutable list/dict from recommendations, slicer results, model analysis, or profile settings may leak into the result. Repeated property access must return the same deterministic value without exposing shared mutable internal state. Tests must prove top-level and nested mutation attempts fail and that source-container mutation after construction cannot alter the result.

## Exact production scope

After this plan is approved, production changes are limited to the internal contract owner, preferably `src/print_engineer/core/preparation.py`, plus the smallest directly necessary core value-type/support changes if reuse cannot express the contract. No adapter, recommendation engine, model analyzer, slicer implementation, workflow/orchestration, persistence, MCP, printer, or dependency changes are authorized.

The ownership boundary is appropriate if it remains dependency-light and imports only core/domain types. It must not depend on slicer adapters, filesystem execution, MCP, printer/MQTT, or orchestration modules. If the audit of the approved implementation proves the boundary is wrong, record that as a design correction before implementation; do not move code in this audit.

## Exact test scope

Add or correct focused unit tests for the contract owner. Tests must cover:

- valid ready result with model identity, exact `RecommendationGoal`, every selected-setup identity, immutable overrides, artifact, slice facts, and verification facts;
- stable not-ready result with machine-readable failure and no artifact, fabricated successful slice, or false verification;
- one authoritative setup and preservation of slicer, printer/profile, nozzle, plate, material, filament, process, and overrides;
- override allowlist, typed values, duplicate/unsupported settings, and comparison-ready canonical representation;
- rejection of ready without setup, artifact, successful slice, or passing verification;
- rejection of contradictory readiness/failure and other invalid selected-setup combinations;
- top-level immutability, nested collection immutability, source mutation isolation, and deterministic repeated observation;
- slice/verification representation of expected-versus-actual identity, artifact existence, compatibility, divergence, warnings, and unknown optional statistics;
- regression that existing recommendation, slicer, model, printer, and MCP behavior is unchanged. Existing focused tests should be run as the regression boundary; no hardware is permitted.

Three tests are insufficient for this contract unless they are expanded substantially to prove these categories.

## Preserved behavior

Existing recommendation remains read-only and retains its current `RecommendationGoal` vocabulary and deterministic authority. Existing slicer/model/profile contracts and measured statistics remain unchanged. Existing MCP and printer behavior remains unchanged, including zero printer-state changes and zero new MQTT publish/request surfaces.

## Safety

This increment performs no file generation, persistence, slicer invocation, model mutation, recommendation mutation, MCP exposure, printer operation, MQTT operation, hardware access, network access, staging, commit, or push. Later increments must retain the read-only printer boundary and must not infer readiness from LLM narrative.

## Verification commands

Run after implementation and approval, using the project virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_preparation_contract.py
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_recommend_engine.py tests/unit/test_setup_recommendation.py tests/unit/test_interfaces.py tests/unit/test_orca_adapter.py
.\.venv\Scripts\ruff.exe check src/print_engineer/core/preparation.py tests/unit/test_preparation_contract.py
.\.venv\Scripts\mypy.exe src/print_engineer/core/preparation.py
```

If directly necessary support files change, include those exact files in Ruff/Mypy and run their focused tests. Do not claim hardware verification or run a full suite merely for discovery.

## Explicit out of scope

- orchestration or a preparation workflow;
- recommendation changes or LLM prompt/narrative changes;
- profile/setup realization or materialization;
- applying overrides;
- slicing, artifact creation/persistence, cleanup, or post-slice verification logic;
- automatic retry, optimization, comparison loops, or history/database infrastructure;
- MCP/API exposure;
- printer control, MQTT publish/request surfaces, cloud login, camera, FTPS, discovery, or hardware operations.

## Increment 2 handoff

Increment 2 consumes this immutable contract to deterministically realize exactly one selected setup as actual slicer inputs. It must map existing recommendation/profile facts into printer, nozzle, build plate, material, filament, process, and supported override inputs, prove the slicer receives the effective setup, and return comparable actual-input identity. It must not add a competing selected setup authority or claim final readiness before later slicing and finalization.

## Risks/open questions

- Existing `ProfileInfo`, `SliceResult`, `SliceStatistics`, and recommendation models are useful sources but contain or expose mutable/list-backed data; the implementation must project them into immutable contract values.
- The current repository does not yet establish a universal stable profile identifier beyond names, kinds, paths, and optional setting IDs. The approved implementation must choose the smallest deterministic identity and document unknowns rather than inventing IDs.
- The supported override value schema and effective-input comparison rules may need a narrow core type derived from `RECOMMENDABLE_SETTINGS`; they must not become a generic settings dictionary.
- Artifact digest/size availability and the slicer’s ability to prove nozzle, plate, material, and applied settings are later integration dependencies. The contract must represent unavailable/unknown facts explicitly and make blocking divergence non-ready.
- Whether slice and verification projections should share exact existing names or use dedicated immutable adapters is an implementation decision within this scope; dependency direction and the invariants above are not optional.

## Operations

production modified: NO
tests modified: NO
dependencies modified: NO
hardware/MQTT/network: NO
stage: NO
commit: NO
push: NO
