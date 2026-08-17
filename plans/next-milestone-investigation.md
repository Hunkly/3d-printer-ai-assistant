# Next Milestone Investigation — Phase 2+ Printer Integration (Bambu Lab LAN MQTT)

## Status

PROPOSED

## Understanding

The user asked to investigate the repository roadmap and determine the next
officially required development milestone after the completed Phase 3A.1 work,
using repository evidence only, and to record the finding in
`plans/next-milestone-investigation.md` with Status `PROPOSED` and a verdict of
exactly `NEXT MILESTONE IDENTIFIED` or `NEEDS MORE INVESTIGATION`.

This is a READ-ONLY investigation:

- no source, test, or existing plan files may be modified;
- no feature may be invented or inferred from architectural preference;
- the next milestone must be established from repository evidence
  (README, AGENTS.md, interface stubs, config, MCP tool registry).

## Existing implementation

### Phase 3A.1 — complete

Phase 3A.1 (Print Configuration & Material Recommendation) is implemented and
verified:

- `SetupEngine` four-layer flow (printer → process → material → filament) with
  deterministic filament ranking (`filament.py` `_rank`), nozzle
  recommendation, and process recommendation integration;
- the filament-ranking fix and the process-profile fixture fix were approved
  and implemented (fixture `_BASE` in `tests/unit/test_setup_recommendation.py`
  now carries `"default_print_profile": "0.20mm Standard @BBL A1"`);
- the recommendation-flow integration investigation concluded
  NO CHANGES REQUIRED (`plans/recommendation-flow-integration.md`).

### Roadmap sources

- `README.md` phase list:
  - Phase 0 (skeleton, config, logging, core interfaces, policy stub) — done;
  - Phase 1 (Slicer Gateway) — done;
  - **Phase 2+ (Printer integration, LAN MQTT, Bambu Lab A1) — not done**;
  - Phase 3 (Model analysis, trimesh) — done;
  - **Phase 3+ (Print history + recommendations) — not done**.
- `README.md` describes the intended end-to-end workflow: pick a model, analyze
  it, slice it, verify settings against policy, send the print to the printer
  over the local network, and learn from the outcome. Printer integration
  precedes learning from outcomes.
- `AGENTS.md` defines the current phase as Phase 3A.1 (READ-ONLY; must not
  connect to a physical printer, start/stop printing, or modify printer state)
  and explicitly forbids implementing "Phase 3B".

### Phase 2+ groundwork already present (printer integration)

- `src/print_engineer/core/interfaces/printer.py` — `Printer` ABC with
  `connect`, `disconnect`, `get_status`, `start_print`, `stop_print`,
  `pause_print`, `resume_print`, `set_temperature`, `take_snapshot`; docstring:
  "Bambu Lab A1 LAN MQTT in Phase 2+"; implementations must evaluate every
  state-changing action against a `SafetyPolicy`.
- `src/print_engineer/core/types.py` — supporting types: `PrinterState`,
  `PrinterStatus`, `AMSInfo`, `Snapshot`, `TemperatureSetpoint`.
- `src/print_engineer/core/policy.py` — `SafetyPolicy` ABC, `PrinterAction`,
  `PolicyContext`, `PolicyDecision`, and `PermissivePolicy` (Phase-0 inert
  stub: dangerous actions require `confirm=True`, nothing is executed).
- `src/print_engineer/config.py` — `BambuSecrets` (ip, serial, access_code);
  `config/config.example.yaml` has a `printer:` section (host, serial; secrets
  in `.env`).
- `src/print_engineer/adapters/printer/__init__.py` — placeholder:
  "Placeholder until Phase 2+".
- `src/print_engineer/mcp/tools/__init__.py` — plans `printer.*` tools
  ("`system.*` now; `printer.*`, `slicer.*`, `analysis.*`, ..."); current MCP
  server docstrings state existing tools never touch the printer.
- Tests pinning the groundwork: `tests/unit/test_policy.py` (PermissivePolicy
  behavior), `tests/unit/test_interfaces.py` (printer types).

### Phase 3+ groundwork already present (print history)

- `src/print_engineer/core/interfaces/print_history.py` — `PrintHistory` ABC
  (`record`, `recent`, `recommend`).
- `src/print_engineer/core/types.py` — `PrintRecord`, `SettingsFingerprint`,
  `Recommendation`.
- No adapter, storage, MCP tools, or tests exist for print history.

### Phase 3B — referenced but undefined

- `AGENTS.md` ("do not implement Phase 3B") and
  `.opencode/agents/review.md` (out-of-scope list) reference "Phase 3B", but no
  repository document defines what Phase 3B is. It cannot be treated as an
  officially defined milestone.

## Requirements

For this investigation task:

1. Identify the next officially defined milestone after the completed
   Phase 3A.1 work, using repository evidence only.
2. Do not invent features or infer requirements for undefined phases.
3. Produce `plans/next-milestone-investigation.md` with Status `PROPOSED` and
   verdict exactly `NEXT MILESTONE IDENTIFIED` or `NEEDS MORE INVESTIGATION`.

## Required changes

None. This is an investigation only; no source, test, or existing plan files
are to be modified.

## New files

- `plans/next-milestone-investigation.md` (this file).

## Data flow

Not applicable — no runtime data flow. Evidence flow for the conclusion:

README/AGENTS.md phase definitions → interface stubs (`Printer`,
`PrintHistory`) → policy stub → config (`BambuSecrets`) → adapter placeholder →
MCP tool registry → conclusion.

## Tests

No tests are required for this investigation.

Note: a pre-existing, unrelated failure exists —
`tests/unit/test_print_context.py::TestResolvePrinter::test_ambiguous_prefix_match_raises`
— which reproduces identically on pristine HEAD `3294bb1` (verified in a
detached worktree). It is out of scope for this investigation and must not be
fixed here.

## Risks

- Phase 3B is undefined. If the user intends Phase 3B to be the next milestone,
  its requirements must be provided before any planning or implementation.
- Phase 2+ requires a physical Bambu Lab A1 on the LAN and `BambuSecrets`
  (ip, serial, access_code) in `.env`; hermetic tests (mocked MQTT broker) are
  needed so CI does not depend on real hardware.
- `PermissivePolicy` is a Phase-0 inert stub; enabling state-changing printer
  commands will require a stricter policy decision before execution.

## Implementation order

Proposal for the identified milestone — NOT requirements. Per AGENTS.md, the
user's explicit requirements are the source of truth for any Phase 2+ work:

1. Confirm with the user the first increment of Phase 2+ (e.g., a read-only
   subset — `connect`, `get_status`, `take_snapshot` — versus the full command
   set).
2. Implement the Bambu Lab LAN MQTT adapter implementing the `Printer` ABC.
3. Wire `SafetyPolicy` evaluation into every state-changing method.
4. Register `printer.*` MCP tools.
5. Add hermetic tests with a mocked MQTT broker.

## Out of scope

- Phase 3B — undefined; requires user clarification before any work.
- Phase 3+ print history — the milestone after printer integration.
- Any modification to Phase 3A.1 code, tests, or existing plans
  (`plans/phase-3a1-filament-ranking.md`, `plans/process-profile-resolution.md`,
  `plans/recommendation-flow-integration.md`).
- Any physical printer connection during this investigation.

## Final verdict

NEXT MILESTONE IDENTIFIED — Phase 2+ Printer integration (Bambu Lab LAN MQTT).

PLAN ONLY — no source or test files were modified.