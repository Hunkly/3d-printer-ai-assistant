# Phase 3A.1 — Filament Ranking Implementation Plan

## Status

APPROVED

## Understanding

The user request concerns **filament ranking** as part of Phase 3A.1 (Print Configuration & Material Recommendation). Filament ranking enumerates locally-installed filament profiles, applies filters (printer compatibility, nozzle diameter, build plate temperature, vendor, material type), and deterministically ranks survivors for a requested goal (print time, filament usage, balanced, surface quality, or strength).

The existing codebase implements filament ranking across several modules, but a **critical bug** exists in the `_rank()` function in `src/print_engineer/recommendation/filament.py` that causes the test `TestVendorVerification.test_rank_penalizes_unverified_and_inconsistent` to fail.

**The bug:** `candidate.score` combines numeric suitability and data quality into a single value via `round(100.0 * base + data_quality, 1)`, capped at 100.0. This creates a "ceiling problem" where min-max normalization can make the best candidate reach 100 before data quality is properly considered, causing the data quality distinction between verified and unverified candidates to be lost at high score values.

The existing test data shows:
- `Bambu PLA Basic @BBL A1`: vendor_verified=True, data_warnings=[], max_volumetric_speed=21, cost=19.99
- `SUNLU PLA Basic @BBL A1`: vendor_verified=False, data_warnings has vendor-name mismatch and a nozzle-temperature range warning, max_volumetric_speed=45, cost=10.79

For BALANCED goal, SUNLU has better numeric metrics (higher speed, lower cost) but the current code's combined score still places SUNLU above Bambu, contradicting the test assertion that Bambu should rank higher due to vendor verification.

**Established constraints (from the repository investigation):**

- data quality **materially affects** filament ranking — it is not a cosmetic hint;
- the existing vendor-verification test is **valid behavioral evidence** — Bambu must outscore SUNLU, and the test must NOT be rewritten to assert the opposite;
- the current defect is the **0..100 score saturation** — the `min(100.0, ...)` cap clips the data-quality bonus at the top of the range;
- do **not** redesign the ranking;
- do **not** turn data quality into a tie-breaker (no secondary sort key).

## Existing Implementation (with bug)

### Filament Candidate Matrix (`filament.py`)

**`_rank()`** (lines 290-318) currently:
1. Computes min-max normalized speed, density, cost scores per goal weights
2. Combines into `metrics` dict per goal (print_time, filament_usage)
3. Computes `base = sum(weights.get(metric, 0.0) * value for metric, value in metrics.items())`
4. **Combines data quality inline**: `data_quality = 15.0 if vendor_verified else 0.0` then `data_quality += max(0.0, 10.0 - 5.0 * len(data_warnings))`
5. **Combines into score**: `candidate.score = max(0.0, min(100.0, round(100.0 * base + data_quality, 1)))`
6. Sorts by `(-candidate.score, candidate.profile_name)`

**The problem:** Step 4 and 5 embed data quality inside candidate.score, and the cap at 100.0 means when `100.0 * base + data_quality > 100`, the data quality bonus is silently lost. The single combined score as the only sort key is the intended design; the defect is the cap. Additionally, the current quality weights (max 25.0 points) are an order of magnitude smaller than the 0..100 numeric scale, so data quality can never materially influence ordering even after the cap is removed (see fixture arithmetic below).

## Required Changes

The fix stays entirely within `_rank()` in `src/print_engineer/recommendation/filament.py`. The ranking design is preserved: data quality remains an **additive component of the single combined `candidate.score`**, and the sort remains `(-candidate.score, candidate.profile_name)`. Data quality is **NOT** converted into a secondary tie-breaker key, and no domain model field is added.

### 1. Fix the saturation: remove the 100.0 cap

The primary defect is `min(100.0, ...)` in the score computation (line 314). Whenever `100.0 * base + data_quality` exceeds 100, the data-quality bonus is silently clipped and the distinction between verified/consistent and unverified/inconsistent candidates disappears at the top of the range.

**Change:** drop the upper cap; keep the lower clamp:

```python
candidate.score = max(0.0, round(100.0 * base + data_quality, 1))
```

`candidate.score` remains the single combined score (numeric suitability 0..100 plus the data-quality bonus) and may legitimately exceed 100.0. No consumer assumes a 0..100 range:

- `src/print_engineer/recommendation/setup.py` line 183/235 formats `top.score` with `:.1f` for narrative text only;
- `FilamentRecommendation.score` (line 397 in `core/recommendation.py`) is a plain `float`;
- no test asserts an absolute score value other than the vendor-verification inequality.

The sort key stays `(-candidate.score, candidate.profile_name)`.

### 2. Recalibrate the data-quality weights so quality materially affects ranking

With the current weights (`15.0` verified, `max(0.0, 10.0 - 5.0 * len(warnings))` consistency, maximum 25.0), data quality can never overcome even a modest numeric deficit — and the vendor-verification test can never pass regardless of the cap. Fixture arithmetic (BALANCED, from the actual test fixtures):

- Bambu: `base = 0.5*0.2727 + 0.25*1.0 + 0.25*0.2459 = 0.4478` → `100 * 0.4478 = 44.8`; `data_quality = 15 + 10 = 25.0` → total 69.8
- SUNLU: `base = 1.0` → `100.0`; `data_quality = 0 + max(0.0, 10 - 5*2) = 0.0` → total 100.0 (cap never binds here; SUNLU wins regardless)
- Numeric deficit Bambu vs SUNLU ≈ **55.2 points** — far above the maximum quality contribution of 25.0

Therefore, to satisfy "data quality materially affects ranking" and keep the vendor-verification test (valid behavioral evidence) passing as-is, the additive quality weights must be recalibrated onto the same scale as the 0..100 numeric component. This is a constant recalibration of the existing formula — not a redesign.

**Derived constraint band** (from the fixtures and the two existing ordering tests):

- `dq(verified, clean) − dq(unverified, 2 warnings) > 55.2` — required for Bambu to outscore SUNLU under BALANCED (vendor test);
- `dq(verified, clean) < 72.7` — required for SUNLU to remain the top candidate under PRINT_TIME (`TestRanking.test_print_time_prefers_max_volumetric_speed` asserts the top candidate has the max volumetric speed).

**Proposed calibration** (within the derived band; the exact values are a calibration decision to be confirmed during implementation, but they must satisfy the band above):

```python
data_quality = 40.0 if candidate.vendor_verified else 0.0
data_quality += max(0.0, 30.0 - 15.0 * len(candidate.data_warnings))
```

Verification of the proposed values against the fixture:

- verified + clean: `40.0 + 30.0 = 70.0` (55.2 < 70.0 < 72.7 ✓)
- SUNLU (unverified, 2 warnings): `0.0 + max(0.0, 30.0 - 30.0) = 0.0`
- **BALANCED**: Bambu `44.8 + 70.0 = 114.8` > SUNLU `100.0 + 0.0 = 100.0` → vendor test passes; PETG `0.0 + 70.0 = 70.0`
- **PRINT_TIME**: SUNLU `100.0 + 0.0 = 100.0` > Bambu `27.3 + 70.0 = 97.3` → speed test keeps SUNLU first; PETG `70.0`
- **FILAMENT_USAGE**: Bambu `62.3 + 70.0 = 132.3` (top; material PLA) → existing assertions (top is PLA, material_type set) still hold
- **STRENGTH / SURFACE_QUALITY**: empty weights → `base = 0.0` for every candidate; `requires_external_evidence` behavior unchanged

### 3. Keep the single sort key — no tie-breaker

The sort remains exactly as today:

```python
candidates.sort(key=lambda candidate: (-candidate.score, candidate.profile_name))
```

Data quality stays inside `candidate.score` (as in the current design). It is **NOT** extracted into a secondary sort key. `profile_name` remains the final deterministic tie-break. This satisfies "do not turn data quality into a tie-breaker".

### 4. Files to Modify

Exactly **one source file**:

- `src/print_engineer/recommendation/filament.py` — `_rank()` (lines 290-318):
  - Lines 311-312: recalibrate the data-quality weights (proposed `40.0` verified bonus; `max(0.0, 30.0 - 15.0 * len(candidate.data_warnings))` consistency bonus)
  - Line 314: remove the upper cap → `candidate.score = max(0.0, round(100.0 * base + data_quality, 1))`
  - Line 318: sort key unchanged — `(-candidate.score, candidate.profile_name)`

**No test files require modification.** `TestVendorVerification.test_rank_penalizes_unverified_and_inconsistent` in `tests/unit/test_setup_recommendation.py` is valid behavioral evidence and must remain unchanged; the fix is calibrated so it passes without editing it. All other ranking assertions (`TestRanking`) continue to hold (see §2). This resolves the contradiction previously recorded in this plan between "Files to Modify" listing only `filament.py` and the Tests section requiring a change to `test_setup_recommendation.py`: **no test change is required**.

**Do NOT modify `_score_list()`** — the min-max normalization in `_score_list()` (lines 272-287) is correct; the defect is the score composition and the cap in `_rank()` only.

**Do NOT touch:**
- printer resolution (`context.py`)
- `SetupEngine` (`setup.py`)
- `RecommendationEngine` (`engine.py`)
- `FilamentCandidate` domain model (`core/recommendation.py`)
- MCP or LLM code
- `tests/unit/test_setup_recommendation.py` or any other test file
- configuration files

## New Files

No new files are required. The fix is contained within `src/print_engineer/recommendation/filament.py` — the `_rank()` function only:

- Recalibrate the `data_quality` weights (lines 311-312)
- Remove the `min(100.0, ...)` upper cap (line 314)
- Sort key unchanged (line 318)

## Data Flow (after fix)

```
_rank(candidates, goal)
  → compute normalized speed/density/cost scores via _score_list()
  → compute metrics (print_time, filament_usage) per goal weights
  → compute base = sum(weights * metrics values)                       # 0..1
  → data_quality = (40.0 if vendor_verified else 0.0)
                   + max(0.0, 30.0 - 15.0 * len(data_warnings))        # 0..70
  → candidate.score = max(0.0, round(100.0 * base + data_quality, 1))  # NO 100.0 cap
  → candidates.sort(key=lambda c: (-c.score, c.profile_name))          # unchanged
  → SetupEngine formats top.score with :.1f and copies it into FilamentRecommendation.score
```

## Tests

The existing test suite is unchanged. The focused tests to run after implementation (results must be verified by running them; none are asserted here):

- `TestVendorVerification.test_rank_penalizes_unverified_and_inconsistent` — expected: Bambu (114.8) > SUNLU (100.0)
- `TestVendorVerification.test_sunlu_inherited_vendor_trap` — metadata flags unchanged
- `TestVendorVerification.test_temperature_outside_declared_range_flagged` — data warnings unchanged
- `TestRanking.test_print_time_prefers_max_volumetric_speed` — expected: SUNLU remains first (97.3 < 100.0)
- `TestRanking.test_filament_usage_prefers_lighter_and_cheaper` — top is PLA; assertions still hold
- `TestRanking.test_strength_goal_marks_external_evidence` — unchanged
- `TestCompatibilityFilters`, `TestNoSlice`, `TestSetupEngine`, `TestLLMNarrative` — unaffected (score is an unconstrained float, formatted `:.1f`)

## Risks

- **Low risk** — the change is localized to `_rank()` in `filament.py`. No SetupEngine, printer resolution, LLM, or domain model code is modified.
- **Score range** — `candidate.score` may now exceed 100.0 (up to 170.0 with the proposed weights). Verified consumers only format or copy the float (`setup.py`, `FilamentRecommendation.score`); no 0..100 range assumption exists in code or tests. Narrative output will print values such as 114.8 — a display-behavior note for reviewers, not a defect.
- **Weight calibration** — the proposed `40.0 / 30.0 / 15.0` values are a proposal satisfying the fixture-derived constraint band `(55.2, 72.7)`. If implementation-time test runs reveal a conflict, the calibration must be adjusted within that band; it must never fall outside it or the vendor test (lower bound) or the PRINT_TIME speed test (upper bound) will fail.
- **LLM grounding** — unchanged; `prompt.py` and `setup_grounding_lines()` operate on separate candidate attributes, not on `score`.

## Implementation Order

Since this read-only planning exercise does not execute implementation, the planned order for a future implementation session would be:

1. In `_rank()` (`filament.py`), recalibrate the data-quality weights (lines 311-312) to the proposed values.
2. Remove the `min(100.0, ...)` upper cap on line 314.
3. Leave the sort key (line 318) unchanged.
4. Run the focused tests listed under "Tests" and verify: the vendor-verification test passes (Bambu > SUNLU) and the PRINT_TIME speed test still passes (SUNLU first).

## Out of Scope

- Connecting to a physical printer
- Starting or stopping a print
- Modifying printer state or slicer profiles
- Modifying the 3D model
- Automatic slicing
- Implementing Phase 3B
- Any write operations on profiles, models, or printer configurations
- Modifying `_score_list()`
- Modifying SetupEngine, the FilamentCandidate model, or any domain model fields
- Modifying `tests/unit/test_setup_recommendation.py` or any other test file
- MCP or LLM code
- Any ranking redesign (goal weights, metric composition, or sort-key structure)

## Final verdict

**IMPLEMENTATION CHANGES REQUIRED**

The plan describes the least-invasive fix for the 0..100 score saturation while preserving the existing ranking design:

1. Remove the `min(100.0, ...)` cap in `_rank()` so the data-quality bonus is never clipped — the combined score `100.0 * base + data_quality` is retained as the single sort key, and data quality is NOT converted into a tie-breaker.
2. Recalibrate the additive data-quality weights onto the 0..100 numeric scale (proposed `40.0` verified / `30.0 - 15.0 * warnings` consistency), within the fixture-derived constraint band `(55.2, 72.7)`, so that data quality materially affects ranking and the existing vendor-verification test (valid behavioral evidence) passes unchanged.

Only `src/print_engineer/recommendation/filament.py` is modified. No test files require modification.

The plan file has been written to:

**plans/phase-3a1-filament-ranking.md**

PLAN ONLY — no source or test files were modified.
