# Process Profile Resolution Fix

## Status

APPROVED

## Understanding

The user asked to investigate the pre-existing failure of
`tests/unit/test_setup_recommendation.py::TestSetupEngine::test_full_four_layer_setup`
(`result.process is None`, warning "no process profile available; the printer's
default_print_profile is unset and none was requested") and to produce a plan
that states the root cause, the required changes, and exactly one final verdict.

The failure is unrelated to the approved Phase 3A.1 filament-ranking work
(verified earlier: the failure reproduces identically on pristine HEAD commit
`3294bb1` in a detached worktree). This plan covers only the process-profile
resolution failure.

## Existing implementation

- `src/print_engineer/recommendation/context.py` — `PrintContextResolver`:
  - `_resolve_printer(adapter, slicer_kind, printer_spec, warnings)` performs an
    exact-name lookup via `adapter.find_profile(ProfileKind.PRINTER, printer_spec)`.
    On an exact match it short-circuits to `_printer_from_profiles([exact], printer_spec)`
    and never consults sibling variant profiles. Only when there is no exact match
    does the fallback path collect profiles by `printer_model` / name prefix and
    merge them (this is the path exercised by `test_model_match_union_of_nozzles`).
  - `_printer_from_profiles(profiles, name)` reads each profile's JSON content and
    takes `default_print = data.get("default_print_profile")` (last non-None wins).
    With a single exact-matched profile, the default is whatever that one profile
    declares.
  - `_resolve_process(adapter, intent, printer)` returns `None` when
    `intent.process_profile is None` and `printer.default_print_profile is None`.
- `src/print_engineer/recommendation/setup.py` — `SetupEngine`:
  - `recommend()` emits the warning "no process profile available; the printer's
    default_print_profile is unset and none was requested" when the process layer
    returns `None`.
  - `_process_layer(resolved, request)` has two paths: `resolved.process` (from the
    resolver) or the fallback `elif resolved.printer is not None and resolved.printer.default_print_profile:`.
    Both fail in this scenario because the resolved printer's `default_print_profile`
    is `None`.
- `src/print_engineer/config.py` — `RecommendConfig` (lines 67-76) has
  `default_printer`, `default_nozzle_diameter`, `default_build_plate` but no
  default process profile; there is no config-level fallback for a process.
- `tests/unit/test_setup_recommendation.py` — the failing test and its fixture:
  - `_BASE = {"nozzle_diameter": "0.4;0.2;0.6;0.8"}` — base machine profile
    "Bambu Lab A1", `materialized=False`, **no `default_print_profile`**.
  - `_VARIANT_04` — variant profile "Bambu Lab A1 0.4 nozzle", `materialized=True`,
    includes `"default_print_profile": "0.20mm Standard @BBL A1"`.
  - `_adapter()` puts only `"printer:Bambu Lab A1 0.4 nozzle"` in `materialized`.
  - `test_full_four_layer_setup` requests `SetupRequest(printer="Bambu Lab A1")`
    and asserts `result.process is not None`, `result.process.source == "printer_default"`,
    `result.process.process_profile == "0.20mm Standard @BBL A1"`.
- `tests/unit/test_print_context.py` — passing tests that establish the intended
  semantics: defaults live on the **variant** profile; naming the variant yields
  the default (`test_exact_variant_profile`, `test_printer_default_process_resolved`),
  while naming the base yields the base profile without defaults
  (`test_base_machine_semicolon_nozzles`).

## Root cause

The failing test's expectation is inconsistent with the fixture data and the
established resolution semantics:

1. The test requests the **base** machine name `"Bambu Lab A1"`.
2. `_resolve_printer` exact-match short-circuits to the base profile, whose content
   (`_BASE`) declares no `default_print_profile`.
3. `_printer_from_profiles([base])` therefore yields `default_print_profile=None`,
   `_resolve_process` returns `None`, and `_process_layer`'s fallback
   (`resolved.printer.default_print_profile`) also fails.
4. The value the test expects (`"0.20mm Standard @BBL A1"`) exists only on the
   **variant** profile "Bambu Lab A1 0.4 nozzle", which the exact-match path never
   consults.

The implementation behaves consistently: it reads `default_print_profile` from the
exact-named profile. All passing context tests model "defaults live on the variant;
name the variant to get them". The failing test is the only piece that expects the
base name to yield the variant's default. This is a test/fixture inconsistency, not
an implementation defect.

## Requirements

- The four-layer setup smoke test must be self-consistent with the fixture data and
  the established resolution semantics.
- The test request must keep the base machine name `"Bambu Lab A1"` so the filament
  compatibility filter continues to match `compatible_printers: ["Bambu Lab A1"]`.
- The base machine profile must declare `default_print_profile` so the existing
  exact-match resolution path yields the expected process.
- No production code changes; the resolver's strict exact-match behavior is
  deliberate and consistent with all other tests.
- The variant profile and its defaults remain unchanged; variant semantics stay
  covered by `tests/unit/test_print_context.py` (whose fixture is untouched).

## Required changes

### 1. `tests/unit/test_setup_recommendation.py` — `_BASE` fixture (line 26)

- **Exact change:** add `"default_print_profile"` to the `_BASE` fixture dict so it
  reads:
  ```python
  _BASE = {
      "nozzle_diameter": "0.4;0.2;0.6;0.8",
      "default_print_profile": "0.20mm Standard @BBL A1",
  }
  ```
  The test request stays unchanged:
  `result = engine.recommend(SetupRequest(printer="Bambu Lab A1"))`.
- **Reason:**
  - The base printer name must remain `"Bambu Lab A1"` so the filament
    compatibility filter continues to match `compatible_printers: ["Bambu Lab A1"]`
    declared by the fixture's filament profiles (`filament.py` rejects a candidate
    when `resolved.printer.name` is not in its `compatible_printers`). Requesting
    the variant name would resolve `printer.name` to "Bambu Lab A1 0.4 nozzle"
    (the fixture's `make_profile` call sets no `printer_model` metadata) and reject
    every filament.
  - Adding `default_print_profile` to the base profile lets the existing
    resolution path resolve the expected process: `_printer_from_profiles` reads
    `default_print_profile` from the exact-named profile, `_resolve_process` finds
    `"0.20mm Standard @BBL A1"` in the adapter, and `_process_layer` returns a
    `ProcessRecommendation` with `source == "printer_default"`.
  - This fixes the process-profile test without changing production code.
  - The existing variant semantics (defaults on the variant) remain covered by
    `tests/unit/test_print_context.py`, whose own fixture is not modified.

No other changes are required. The test request, the resolver, `_process_layer`,
`test_print_context.py`, and the config are left untouched.

## Tests

After the change, run the focused tests and the full module:

- `pytest tests/unit/test_setup_recommendation.py -k "TestSetupEngine"` — must pass,
  including `test_full_four_layer_setup`.
- `pytest tests/unit/test_print_context.py` — must remain green; its own fixture is
  unchanged, so the base-name-without-defaults and variant-carrying-defaults
  semantics are preserved (regression guard).
- `pytest tests/unit/test_setup_recommendation.py` — full module (previously
  18 passed, 1 failed; after the fixture change, all should pass). The shared
  `_adapter()` is also used by the matrix/LLM tests; adding `default_print_profile`
  to the base profile does not feed the material/filament/nozzle layers, so no
  other test in the module is expected to change behavior.
- `pytest tests/unit/test_recommendation.py` — regression guard for the Phase 3A.1
  filament-ranking work (unchanged by this plan).

These are proposed verification steps; this plan does not claim they pass.

## Data flow

`SetupRequest(printer="Bambu Lab A1")`
→ `PrintContextResolver.resolve`:
  `_resolve_printer` exact-match finds the base profile, whose content now declares
  `default_print_profile = "0.20mm Standard @BBL A1"`; `printer.name` remains
  `"Bambu Lab A1"`
  → `_resolve_process` resolves `"0.20mm Standard @BBL A1"` from the adapter
  → `resolved.process` is populated
→ `FilamentMatrixBuilder`: the compatibility filter still matches
  `compatible_printers: ["Bambu Lab A1"]` against `printer.name == "Bambu Lab A1"`
→ `SetupEngine._process_layer`: `resolved.process` present, `source = "printer_default"`
→ `ProcessRecommendation(process_profile="0.20mm Standard @BBL A1", source="printer_default", ...)`
→ `result.process` populated; all four layers (material, filament, nozzle, process) resolved.

## Risks

- Low. The change adds one field to a test fixture; no production code is touched.
- The base-name request now resolves a default process. Other tests sharing
  `_adapter()` (matrix, build-plate, vendor/material filters, nozzle, LLM narrative)
  do not consume `default_print_profile`, so no behavior change is expected there;
  this should be confirmed by the full-module run.
- `tests/unit/test_print_context.py` uses its own fixture (`_BASE_MACHINE`, unchanged),
  so its base-name-without-defaults semantics are preserved.
- If a future requirement wants model-name requests to inherit variant defaults,
  that is a separate design decision (see Out of scope) and would require an
  implementation change in `_resolve_printer`.

## Out of scope

- No changes to `src/print_engineer/recommendation/context.py`, `setup.py`,
  `engine.py`, `filament.py`, or any other production module.
- No change to the test request in `test_full_four_layer_setup` (it keeps
  `printer="Bambu Lab A1"`).
- No changes to `tests/unit/test_print_context.py` or its fixture.
- No implementation change to make base-name requests inherit variant defaults
  (rejected: unsupported by any passing test; contradicts the strict resolver design).
- No changes to `plans/phase-3a1-filament-ranking.md` or the filament-ranking
  implementation.
- No Phase 2+/3+ features, printer integration, or slicing behavior.

## Implementation order

1. Edit `tests/unit/test_setup_recommendation.py`:
   add `"default_print_profile": "0.20mm Standard @BBL A1"` to the `_BASE` fixture
   dict; leave the request in `test_full_four_layer_setup` as
   `SetupRequest(printer="Bambu Lab A1")`.
2. Run the focused tests listed under "Tests".
3. Confirm the full `test_setup_recommendation.py` module passes and
   `test_print_context.py` remains green.

## Final verdict

TESTS NEED CORRECTION

PLAN ONLY — no source or test files were modified.