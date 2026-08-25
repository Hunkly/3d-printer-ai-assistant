# Phase 3 Recommendation Material / Build-Plate Compatibility v1

Status: APPROVED

This is a plan-only correction. It authorizes no implementation.

## Product contract

Material is optional. When omitted, recommendation owns material selection. A
caller-supplied build plate is authoritative context. The Phase 3 result must
be a concrete compatible setup; recommendation must not silently change the
plate or substitute a material for an explicit material request.

Downstream authority remains unchanged: `SelectedSetup`, `SetupRealizer`,
filament-type singleton-list validation, source-path resolution, G-code
validation, and `SliceExecutionSuccess` consume one selected setup and do not
reselect material or plate.

## Real Orca incompatibility

Read-only software verification used the installed OrcaSlicer 2.3.2, its real
local profile repository and materializer, and a disposable cube. No printer,
network, MQTT, upload, or physical hardware was used.

Request: goal `balanced`; material omitted; printer `Bambu Lab A1 0.4
nozzle`; process `0.20mm Standard @BBL A1`; plate `cool_plate`.

The current deterministic recommendation selected `Bambu ABS @base`, material
`ABS`, plate `cool_plate`, and materialized `cool_plate_temp = ["0"]`.

The exact real Orca run rejected that setup:

- reproduced: YES;
- selected filament: `Bambu ABS @base`;
- selected plate: `cool_plate` / Orca `Cool Plate`;
- selected field/value: `cool_plate_temp = ["0"]`;
- return code: `4294967235`;
- diagnostic: `Plate 1: Cool Plate does not support filament 1` (present in
  Orca stderr/stdout and surfaced by the adapter);
- `plate_1.gcode` created: NO;
- output 3MF created: NO.

The real positive control using `Bambu PLA Tough+ @base` with the same printer,
process, and plate returned `0` and created `plate_1.gcode`.

## Real materialized-profile survey

The installed repository exposed 1,082 filament profiles. For each of the
three selected plate fields, the observed counts were identical:

| Field | singleton string lists | positive | zero | missing | materialization failures |
|---|---:|---:|---:|---:|---:|
| `cool_plate_temp` | 1,023 | 575 | 448 | 35 | 24 |
| `textured_plate_temp` | 1,023 | 1,023 | 0 | 35 | 24 |
| `hot_plate_temp` | 1,023 | 1,023 | 0 | 35 | 24 |

Across all three fields: scalar strings 0; numeric scalars 0; empty lists 0;
multi-value lists 0; nulls 0; non-string singleton elements 0; non-numeric
strings 0; decimals 0; leading/trailing whitespace 0; negative values 0.
Every observed singleton element was a string containing plain decimal digits,
with examples `"0"`, `"35"`, and `"90"`. The raw profiles commonly inherit
values (1,080 inherited and 2 direct in the survey); compatibility reads the
effective materialized document. Twenty-four profiles failed materialization
or resolution and are unknown, not compatible.

## Exact plate mapping and vocabulary

The compatibility predicate accepts only the already approved canonical keys:

| canonical selected plate | exact materialized field |
|---|---|
| `cool_plate` | `cool_plate_temp` |
| `textured_pei_plate` | `textured_plate_temp` |
| `high_temp_plate` | `hot_plate_temp` |

The predicate must use an exact dictionary lookup. It must not use substring
matching, aliases, display labels, numeric IDs, case variants, or keys such as
`engineering`, `hot`, or `high temp`. Upstream normalization remains
authoritative. A non-`None` selected plate outside the three approved keys is
unapproved at this boundary and fails closed with structured
`incompatible_build_plate` rejection. No vocabulary expansion is in scope.

## Exact value grammar

The accepted representation is exactly a list of length one whose sole element
is a string matching `0|[1-9][0-9]*`. This is the narrow ASCII
decimal-integer lexical grammar established by the real materialized profiles.
No scalar value, numeric element, empty list, multi-value list, null, missing
field, non-string item, sign, decimal point, exponent, or whitespace is
accepted. There is no local documented Orca contract evidence establishing
decimal lexical forms, so decimals remain unsupported and fail closed; in
particular `"0.0"` is malformed/unknown, not a second accepted spelling.

After the exact lexical check, parse as a finite number. A value greater than
zero proves compatibility. The accepted string `"0"` parses to zero but is
unsupported. Any malformed, non-finite, missing, null, empty, multi-value,
non-string, negative, or unmaterializable value is unknown and does not prove
compatibility. The implementation must never strip whitespace or silently take
`list[0]` from an ambiguous list.

## Zero sentinel semantics

`cool_plate_temp = ["0"]` is the schema observation. Its compatibility meaning
is established by combined evidence, not by a generic physical claim:

- real Orca rejects the materialized ABS profile with the selected cool plate;
- real Orca accepts the materialized positive PLA value with that plate;
- the installed profile survey shows zero is the explicit non-positive value
  used for the unsupported cool-plate cases;
- no separate conditional support expression was found in the materialized
  profile documents.

Therefore the Orca profile-contract predicate is: exact materialized field
parses under the grammar and has numeric value `> 0` → supported; value `== 0`
→ incompatible; unknown or malformed evidence → compatibility unproven →
reject.

## Compatibility relationship

The plate-support decision is exactly:

`exact materialized filament document + canonical selected plate`
→ selected plate-specific temperature field
→ supported / unsupported / unknown.

Materialization and inheritance depend on the existing profile repository and
materializer authority. Existing printer/process compatibility dimensions remain
governed by the current recommendation logic and are not redesigned here. The
plate predicate does not semantically read printer/process fields as proof of
plate support, and plate support must not be described as encoded by the full
printer/process/filament/plate tuple.

## Recommendation authority and lookup invariant

Current architecture is:

`FilamentMatrixBuilder` → `find_profile(ProfileKind.FILAMENT, raw.name)` →
materialized JSON → `FilamentCandidate`.

The correction preserves this existing recommendation profile-resolution
authority. It does not add `ProfileReference` to recommendation types, does not
require `setting_id` preservation, and does not redesign recommendation
identity. Real materialized candidates can expose `setting_id = None`.

The compatibility predicate must use the same materialized document already
resolved by `_build_one` and used to construct/evaluate that candidate. It must
not perform a second lookup by profile name and must not permit same-name
substitution. This correction introduces no additional profile lookup. Any
same-name resolution limitation is a separate latent architecture concern and
is out of scope.

## Current bug and correction boundary

Current `filament.py` maps plate text through `_PLATE_FIELDS` and then checks
`getattr(candidate, plate_field) is None`. `_parse_float()` first accepts the
singleton list, strips strings, and converts `"0"` to `0.0`; because `0.0` is
not `None`, the candidate survives. The current branch is therefore
effectively “parseable/present,” not “proven supported.”

The correction changes only eligibility: exact canonical plate mapping, exact
materialized value grammar, finite parse, and strictly positive value. Ranking
formulas and tie-breaking are unchanged and run only after compatibility
filtering. No slicer-time workaround or downstream reselection is added.

## Empty compatible-set contract

Chosen option: **A — existing empty recommendation is the failure contract**.

Read-only inspection shows `SetupEngine.recommend()` legitimately returns a
`SetupRecommendation` when the matrix is empty. It preserves the resolved
context, nozzle/process layers as currently available, records rejected
filament candidates in `matrix.rejected`, and returns `material=None` and
`filament=None`; it does not create a `SelectedSetup`, invoke realization, or
invoke slicing. There is no existing automatic `empty matrix →
PreparationFailure` path, so this plan does not invent one or call the result a
`PreparationFailure`.

The supported selection path must be tested to prove that an empty filament
matrix has no selectable filament and cannot produce a `SelectedSetup` or begin
realization. If a future caller bypasses that selection boundary, that is a
separate contract change, not part of this narrow correction.

For `material="ABS"` plus `cool_plate`, all ABS candidates are rejected by the
existing material filter and then the plate predicate; the public result is an
empty compatible filament matrix with structured rejection evidence and no
material/filament recommendation. PLA is not selected, the plate is not
changed, and no Orca invocation is permitted.

## Explicit material and no-material contracts

`material="ABS"` is a hard candidate filter. If all ABS candidates are
incompatible, the engine must not relax material, select PLA, or change the
plate. Explicit compatible `material="PLA"` succeeds when its selected field
is positive.

With material omitted, compatibility filtering occurs before ranking. In the
real A1 / `0.20mm Standard @BBL A1` / `cool_plate` / balanced context, the
current matrix has 94 candidates; 53 remain after positive cool-plate support
filtering. Re-ranking only those 53 candidates produces the deterministic
winner `Bambu PLA Tough+ @base`, material `PLA`, score `132.6`, and
`cool_plate_temp = ["35"]`. This profile is an acceptance expectation, not a
production hard-code.

## Structured rejection

Use the existing structured reason code `incompatible_build_plate` for zero,
missing, malformed, unmaterializable, and unapproved-plate compatibility
evidence, provided the reason text identifies the exact field/value condition.
The matrix retains `RejectedFilamentCandidate` records. No message-based
decision logic and no new rejection code are needed.

## Exact production scope

MODIFIED:

- `src/print_engineer/recommendation/filament.py` — exact canonical plate map,
  exact materialized value validation, positive-support filtering, and existing
  structured rejection evidence.

Additional production: NONE.

Do not modify setup engine/result types, preparation, realization, execution,
Orca adapter, process/G-code code, MCP, printer, or MQTT code.

## Exact test scope

MODIFIED:

- `tests/unit/test_setup_recommendation.py` — hermetic matrix and setup-engine
  tests, including the empty compatible-set and explicit-material contracts.

REGRESSION-ONLY:

- `tests/unit/test_recommend_engine.py`;
- `tests/unit/test_setup_realization.py`;
- `tests/unit/test_slice_execution.py`;
- `tests/unit/test_orca_adapter.py`;
- `tests/unit/test_preparation_contract.py`;
- the existing real Orca/recommendation integration path used for software-only
  acceptance when Orca 2.3.2 and the local repository are available.

Optional modified scope: NO.

## Hermetic test matrix

The modified test file must prove all of the following:

- `cool_plate` with `["0"]` is rejected as `incompatible_build_plate`;
- positive `cool_plate_temp` is accepted;
- `textured_pei_plate` reads only `textured_plate_temp`;
- `high_temp_plate` reads only `hot_plate_temp`;
- aliases, display labels, numeric IDs, and substring matches do not infer
  compatibility;
- missing, null, empty-list, multi-value, non-string, malformed, whitespace,
  decimal, zero/`0.0`, negative, and non-finite cases follow the exact grammar;
- materialization failure is rejected as unknown;
- material omitted removes incompatible candidates before deterministic ranking;
- ranking is deterministic over compatible candidates only;
- explicit incompatible ABS returns the empty recommendation contract;
- explicit compatible PLA succeeds;
- explicit ABS never substitutes PLA;
- the selected plate is unchanged;
- the same already-materialized candidate document supplies compatibility and
  candidate facts, with no additional profile lookup;
- no `SelectedSetup` is produced through the supported selection path when no
  filament candidate exists.

Tests must not add `ProfileReference` behavior that recommendation does not
currently provide.

## Required real acceptance after implementation

Using the installed OrcaSlicer 2.3.2, real profile repository, and real
materializer, run the full software-only no-material path with the same A1,
process, balanced goal, and `cool_plate` context:

`recommend → ABS rejected → Bambu PLA Tough+ @base selected → realize →
SliceExecutor → Orca 2.3.2 → --slice 1 → return code 0 → exact
plate_1.gcode → SliceExecutionSuccess`.

Do not pass `material="PLA"`. No printer/network/MQTT operation is permitted.

## Required real explicit ABS check

With the same real context and `material="ABS"`, require all ABS candidates to
be rejected, no compatible filament candidate to remain, no PLA substitution,
no `SelectedSetup`, and no Orca invocation. Record:

- PLA substitution: NO;
- `SelectedSetup` produced: NO;
- Orca invoked: NO.

## Required real PLA control

With `material="PLA"` and `cool_plate`, recommendation must succeed using a
positive selected plate field. If the no-material full-chain acceptance already
uses the same PLA profile, a second full slice is not required.

## Other Phase 3 authority

Unchanged: YES. No downstream compatibility workaround, reselection, profile
identity redesign, preparation finalization change, source-path change,
filament-type change, G-code change, MCP change, or printer-control change is
authorized.

## Open Questions

NONE.

## Verdict

plan corrected: YES

implementation-ready: YES

ready for focused re-review: YES

## Operations

production modified: NO

tests modified: NO

stage: NO

commit: NO

push: NO

printer/MQTT/network: NO
