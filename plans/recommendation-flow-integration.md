# Recommendation Flow Integration

## Status

PROPOSED

## Understanding

The user asked for a read-only investigation of whether the two recommendation
orchestration paths in the repository — the setting-level `RecommendationEngine`
(Phase 3A) and the four-layer `SetupEngine` (Phase 3A.1) — need to be integrated
into a single coherent recommendation flow, and, if so, for a minimal
implementation plan.

A prior investigation "suggested" integration "may be needed", but that was
never approved as a requirement. This plan establishes from repository evidence
whether integration is actually required, expected, or supported by any
consumer, spec, test, or documentation.

## Existing implementation

- `src/print_engineer/recommendation/engine.py` — `RecommendationEngine`
  (Phase 3A, implements `Recommender`):
  - `recommend(RecommendationRequest) -> RecommendationSet`.
  - Pipeline: analyze model (`model_path` required, trimesh) → resolve slicer
    profiles (`_resolve_slicer` / `_ProfileTriple`) → optional slice stats
    (`_obtain_stats`, `slice_on_demand`) → `RecommendationInput` → deterministic
    rules (`recommendation/rules.py` `evaluate`) → optional LLM reasoning with
    strict grounding validation (`_validate_llm_set`) → merge.
  - Output: setting-level change directions for an allowlisted settings set
    (`RECOMMENDABLE_SETTINGS`: layer height, walls, infill, supports, speed).
  - `_printer_defaults(printer)` reads `default_print_profile` /
    `default_filament_profile` from printer profile content.
- `src/print_engineer/recommendation/setup.py` — `SetupEngine` (Phase 3A.1,
  concrete class, no interface):
  - `recommend(SetupRequest) -> SetupRecommendation`.
  - Pipeline: `PrintContextResolver.resolve` (printer/nozzle/plate/process
    context) → `FilamentMatrixBuilder` (enumerate, filter, rank local filaments)
    → four layers: material, filament, nozzle, process → optional grounded LLM
    narrative (`_is_grounded`, dropped on failure).
  - Output: concrete selections (`MaterialRecommendation`, `FilamentRecommendation`,
    `NozzleRecommendation`, `ProcessRecommendation`).
  - Never slices (pinned by `TestNoSlice`).
- `src/print_engineer/core/interfaces/recommender.py` — `Recommender` ABC covers
  only the setting-level flow (`RecommendationRequest -> RecommendationSet`).
  `SetupEngine` does not implement it (different request/output types).
- `src/print_engineer/core/recommendation.py` — domain types. Shared between the
  flows: `RecommendationGoal`, `RecommendationMode`, `ProcessSettings`,
  `FilamentSettings`, `PrinterSettings`, `SlicerSettingsDigest`. Flow-specific:
  `RecommendationRequest`/`RecommendationSet`/`LLMRecommendationSet` vs
  `SetupRequest` (extends `PrintContextIntent`)/`SetupRecommendation`/
  `FilamentCandidateMatrix`.
- Consumers:
  - `src/print_engineer/mcp/tools/recommend.py` — three independent tools:
    `print.recommend` (RecommendationEngine), `print.filament_candidates`
    (FilamentMatrixBuilder directly), `print.setup` (SetupEngine). Registered
    independently in `src/print_engineer/mcp/server.py`.
  - `src/print_engineer/cli.py` — independent commands `recommend <model>`,
    `filaments`, `setup`.
- Tests pinning the contracts: `tests/unit/test_recommend_engine.py`,
  `tests/unit/test_setup_recommendation.py`, `tests/unit/test_recommend_mcp.py`,
  `tests/unit/test_setup_mcp.py`, `tests/unit/test_recommendation_schema.py`,
  `tests/unit/test_cli_recommend.py`.

## Repository evidence

1. **The flows are intentionally separate, not accidentally duplicated.**
   - Distinct phase labels in module docstrings: `engine.py` "Phase 3A",
     `setup.py` "Phase 3A.1"; `SetupRequest` docstring: "four-layer setup
     recommendation (Phase 3A.1)".
   - Distinct input domains: `RecommendationRequest` requires `model_path`
     (model-geometry-driven); `SetupRequest` has no model field — it describes
     the printing environment (printer/nozzle/build plate).
   - Distinct output contracts: setting-level change directions vs concrete
     material/filament/nozzle/process selections.
   - Distinct pipelines and slicing behavior: the setting-level engine may slice
     on demand; `SetupEngine` never slices (`TestNoSlice`).
   - AGENTS.md "Current Development Phase" defines Phase 3A.1 as its own
     read-only feature set ("print configuration, material class recommendation,
     local filament candidate discovery, filament compatibility, deterministic
     filament ranking, nozzle recommendation, process recommendation
     integration, optional grounded LLM explanation"). "process recommendation
     integration" refers to the process layer inside the four-layer flow, not to
     merging the two engines.
2. **No consumer needs both flows in one request.** Each CLI command and each
   MCP tool maps to exactly one engine. No code path calls both
   `RecommendationEngine.recommend` and `SetupEngine.recommend` together.
3. **No spec, doc, plan, or test requires or expects a unified flow.** The
   repository has no `docs/` directory; `README.md` is stale (Phase 0/1 era,
   silent on recommendation APIs); `AGENTS.md` scopes Phase 3A.1 explicitly;
   the two existing plans (`plans/phase-3a1-filament-ranking.md`,
   `plans/process-profile-resolution.md`) do not mention engine unification.
   The MCP/CLI/schema tests pin the two flows as independent public contracts.
4. **Unification would break pinned, external-facing contracts.** `print.recommend`
   returns `{"ok": true, "recommendations": RecommendationSet.model_dump(...)}`,
   `print.setup` returns `{"ok": true, "setup": SetupRecommendation.model_dump(...)}`,
   `print.filament_candidates` returns `{"ok": true, "matrix": ...}`; the MCP
   tests (`test_recommend_mcp.py`, `test_setup_mcp.py`) and CLI/schema tests pin
   these shapes and arguments (e.g. `model` required for `print.recommend`,
   `printer` required for `print.setup`).
5. **Incidental, non-flow duplication exists but does not justify integration:**
   - Printer-defaults reading is implemented twice: `engine._printer_defaults`
     (`engine.py:309-327`) and `PrintContextResolver._printer_from_profiles`
     (`context.py:230-235`). This is a shared-helper refactor candidate, not
     evidence that the flows should merge.
   - `mcp/tools/recommend.py` defines `filament_candidates` and `setup` twice
     (identical bodies; the later definition wins). Pre-existing duplication,
     behaviorally neutral, unrelated to the integration question.
6. **All Phase 3A.1 scope items are implemented.** The genuinely incomplete
   repository areas (printer LAN/MQTT control — Phase 2+, print history —
   Phase 3+, Bambu Studio slicing — explicitly disabled until upgrade) are
   explicitly outside Phase 3A.1 scope, and Phase 3A.1 is defined as read-only.

## Requirements

There is no approved requirement to integrate the two recommendation flows.
Per the investigation instructions, integration must not be inferred merely
because it appears architecturally clean. Repository evidence establishes:

- The two flows are intentional, phase-scoped subsystems with different inputs,
  outputs, and read-only guarantees.
- Consumers use them independently, and their public contracts are pinned by
  tests.
- No specification or documentation promises or describes a unified
  recommendation API.

Therefore no requirements for integration changes can be derived from the
repository.

## Root cause / motivation

The only motivation for integration is the unapproved suggestion from a prior
investigation that the flows "may need" to be unified. The repository itself
provides no supporting evidence: no consumer, test, doc, plan, or spec calls for
it, and unifying the flows would require breaking or duplicating pinned public
contracts. There is no defect, gap, or failing requirement that integration
would fix.

## Required changes

**None.**

If the evidence does not establish a requirement, the correct outcome is
NO CHANGES REQUIRED. For completeness, the only evidence-backed candidates
would be small refactors that are explicitly NOT required by this milestone:

- (Not required) `src/print_engineer/recommendation/engine.py` —
  `_printer_defaults` could delegate to `PrintContextResolver._printer_from_profiles`
  to remove duplicated printer-defaults reading. Reason: deduplication only;
  no consumer or requirement demands it, and it touches approved, verified
  behavior.
- (Not required) `src/print_engineer/mcp/tools/recommend.py` — remove the
  duplicated `filament_candidates`/`setup` method definitions. Reason: cosmetic
  cleanup of pre-existing duplication; behaviorally neutral; unrelated to this
  milestone.

Neither candidate is part of this plan; both are noted as observations only.

## API / compatibility impact

- No API changes are proposed. `print.recommend`, `print.setup`,
  `print.filament_candidates`, and the CLI commands (`recommend`, `filaments`,
  `setup`) remain exactly as they are, and their pinned test contracts remain
  unchanged.
- If a future requirement ever demanded a unified flow, it would have to be
  strictly additive (e.g. a new tool/command) to avoid breaking the existing
  serialized contracts; this is recorded here for future reference, not as a
  current requirement.

## Data flow

Unchanged. The two flows continue to operate independently:

- `RecommendationRequest(model_path, ...)` → `RecommendationEngine` → model
  analysis → slicer digest → rules → (optional grounded LLM merge) →
  `RecommendationSet` → `print.recommend` / `cli recommend`.
- `SetupRequest(printer, goal, ...)` → `SetupEngine` → `PrintContextResolver`
  → `FilamentMatrixBuilder` → four layers → (optional grounded LLM narrative) →
  `SetupRecommendation` → `print.setup` / `cli setup`.
- `PrintContextIntent(...)` → `PrintContextResolver` + `FilamentMatrixBuilder`
  → `FilamentCandidateMatrix` → `print.filament_candidates` / `cli filaments`.

## Tests

No changes are proposed, so no new tests are required. If the observation-only
refactors are ever pursued, they must be covered by the existing suites:
`test_recommend_engine.py`, `test_setup_recommendation.py`, `test_recommend_mcp.py`,
`test_setup_mcp.py`, `test_cli_recommend.py`, `test_recommendation_schema.py`.
This plan makes no claim that any test passes.

## Risks

- No implementation risk: no changes are made.
- Risk of acting on the unapproved suggestion: unifying the flows would break
  the pinned external-facing MCP/CLI contracts and contradict the documented
  phase scoping — this plan deliberately avoids that.
- The pre-existing issues recorded in the evidence (duplicated
  `_printer_defaults` logic; duplicated method bodies in `mcp/tools/recommend.py`;
  the separately-tracked pre-existing `test_print_context.py::test_ambiguous_prefix_match_raises`
  failure) are unrelated to this milestone and are intentionally left untouched.

## Out of scope

Explicitly excluded:

- Bambu LAN/MQTT printer control
- automatic printing
- print history / learning
- Bambu Studio slicing
- unrelated Phase 3B functionality
- changes to the already-approved filament ranking implementation
- changes to `plans/phase-3a1-filament-ranking.md` or
  `plans/process-profile-resolution.md`
- the observation-only refactors listed under "Required changes"
- fixing the pre-existing `test_print_context.py` failure (tracked separately)

## Implementation order

Not applicable — no implementation is required by this plan.

## Final verdict

NO CHANGES REQUIRED

PLAN ONLY — no source or test files were modified.