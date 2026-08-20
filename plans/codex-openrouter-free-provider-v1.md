# Codex OpenRouter Free Provider v1

Status: APPROVED

## Objective

Keep `tools/codex-controller/` as the single repository-agent entry point and Codex as the only agent, while adding invocation-level automatic choice between subscription-backed Codex and the project-selected, currently verified-free OpenRouter path. `auto` is the default; `primary` and `openrouter-free` remain exact manual/debug overrides. Outside the existing ChatGPT subscription, inference spend must remain exactly `$0.00`.

This is an architecture delta, not an implementation or cleanup plan. It changes no printer, MQTT, product, or hardware behavior.

## Clarified User Requirement

There is one agent and three provider modes:

```text
tools/codex-controller
        |
 CODEX_PROVIDER_MODE
        |
   +----+---------+-------------------+
   |              |                   |
primary          auto          openrouter-free
   |              |                   |
   |       machine-readable           |
   |     account/quota/model          |
   |          decision                |
   |         /      \                 |
   +--------+        +----------------+
            |        |
 subscription Codex  custom OpenRouter provider
            |        |
            +-- Codex +
```

`auto` decides once before each controller invocation and never during a running Codex execution. `openrouter-free` changes only the provider/model beneath Codex. No mode invokes OpenCode, another coding agent, a paid API fallback, or another repository-agent framework. Automatic selection uses only the pinned machine-readable Codex protocol; it never consumes inference to probe quota and never parses human-readable output.

## Relationship to Existing Architecture

Precedence for overlapping architecture is:

1. this plan, `Codex OpenRouter Free Provider v1`, for provider-mode parsing, machine-readable primary availability, invocation-level auto selection, Codex custom-provider execution, Codex-compatible-model qualification, model syntax, and Codex thread identity;
2. `OpenRouter Free Model Selector v1` for catalog ordering, strict zero-cost qualification, preflight, producer provenance, worktree-state hashing, locks, and select-only behavior;
3. `Multi-Model Execution Fallback v1` for unrelated worktree, task/plan validation, prompt, secret, Git/publication, and safety contracts.

This plan supersedes OpenCode as the active fallback executor, the OpenCode-only `openrouter/<vendor/model:free>` mapping, OpenCode version/spawn requirements on the active path, and executor-specific “successful OpenCode execution” wording. It does not supersede selector ordering, zero-cost rules, provenance, plan validation, worktree safety, Git ownership, or printer/MQTT/hardware restrictions.

This plan owns Codex provider selection and the auto primary/fallback decision. `OpenRouter Free Model Selector v1` continues to own OpenRouter free-model selection and producer provenance. `Multi-Model Execution Fallback v1` remains historical/base for the unaffected orchestration contracts.

## Current Repository Baseline

Targeted inspection on 2026-08-19 established:

- `tools/codex-controller/src/index.ts` uses `@openai/codex-sdk`, canonical phases `general|plan|build|review`, `CODEX_THREAD_MODE=resume|fresh`, fresh review by default, isolated linked worktrees, and controller-owned Git publication in GitHub issue mode.
- The controller currently persists only `threadId`, branch, worktree, issue, and PR metadata; it has no provider/model identity.
- Its lockfile pins `@openai/codex-sdk` and `@openai/codex` 0.147.0 despite `package.json` using `latest`.
- The installed 0.147.0 TypeScript declarations expose supported `CodexOptions.config`, `CodexOptions.env`, and per-thread `model`, `workingDirectory`, sandbox, approval, and network options.
- `tools/model-runner/` is the implemented OpenCode runner from the approved historical plan. The selector plan is still PROPOSED; its prospective `openrouter.ts`, `model-selector.ts`, and `provenance.ts` do not yet exist.
- `.opencode/agents/*` remains local fallback infrastructure and need not be deleted to activate Codex-backed fallback.
- The working tree was already dirty before this plan; those changes are user work and remain untouched.

## Selector Baseline Preserved

All 40 selector decisions restated by the user are mandatory. In particular, one complete role-specific server-ordered catalog response is used; `limit` and `offset` are omitted; `data.length === total_count`; no local scoring or randomization exists; the first locally qualified candidate wins; exact `:free` identity and strict lexical zero pricing are both required; `openrouter/free`, `openrouter/auto`, paid/unknown/ambiguous pricing, and candidate-two fallback are forbidden; there is no catalog cache; and the selected record is revalidated immediately before Codex starts. For BUILD/PLAN coding selection, server ordering remains authoritative through `sort=coding-high-to-low`, `min_coding_index=0`, and `min_agentic_index=0`.

The selector's exact role queries, pricing schema, context minimum, expiration handling, override rules, PLAN/BUILD producer semantics, worktree-state hash, Git-common-dir provenance paths/schema, atomic writes, and mutation lock remain unchanged except for the catalog-filter and pricing-schema corrections below and the fact that executor success now means successful Codex execution using the custom OpenRouter provider.

`MODEL_PLAN_PATH` remains required for fallback PLAN, approval, BUILD, and REVIEW. PLAN provenance is written only after successful Codex exit plus an actual valid proposed-plan change. BUILD provenance is written only after successful Codex exit plus an actual worktree-state change. Failed or unchanged runs never replace provenance.

## OpenRouter Catalog Filter Compatibility Correction

Live zero-inference validation on 2026-08-20 found that the current OpenRouter catalog API rejects a request containing both `category=programming` and `supported_parameters=tools` with HTTP 400 (`Cannot provide both category and supported_parameters`). The same diagnostics established that the category-only request succeeds with `data_length=1` and `total_count=1`, while the complete corrected TOOLS_ONLY request succeeds with `data_length=9` and `total_count=9`. The conflict is therefore specifically the combination of those two filters; `total_count` remains a valid completeness field.

The current official [OpenRouter Models API reference](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties) documents every retained query parameter: `supported_parameters` is a string filter for supported parameters; `input_modalities` and `output_modalities` are string modality filters; `context` is an integer minimum context-length filter; `max_price` and `max_output_price` are nullable numeric maximum prompt and completion/output price filters; `sort` is server-side ordering and explicitly includes `coding-high-to-low`; and `min_coding_index` and `min_agentic_index` are nullable numeric minimum Artificial Analysis coding and agentic index filters, each with minimum allowed value `0`. The same response schema requires `total_count` as an integer containing the total number of models matching the query. Thus the complete retained query shape is both documented by the current API and empirically accepted together by the live API; this is not merely evidence from separate individual-parameter requests.

For the coding/agentic BUILD/PLAN role-specific catalog request, send exactly these filters and ordering controls, with no `limit` or `offset`:

```text
supported_parameters=tools
input_modalities=text
output_modalities=text
context=32768
max_price=0
max_output_price=0
sort=coding-high-to-low
min_coding_index=0
min_agentic_index=0
```

Do not send `category=programming`, and never generate a request containing both `category` and `supported_parameters`. Removing the category filter does not move tool validation local-only: `supported_parameters=tools` remains the server-side hard capability filter, and returned model metadata must still locally prove native tools capability under the existing qualification contract.

Use one successful server-ordered response, preserve `data.length === total_count` and all existing no-limit/no-offset completeness validation, and select the first locally qualified candidate. Do not add local scoring, randomness, a category-only retry, an alternate catalog query, or candidate/model fallback after a catalog or exact probe-model qualification failure.

The compatibility probe still accepts exactly one user-specified `vendor/model:free` candidate, but that exact ID must occur in the current corrected qualifying catalog response before any Codex execution. A previously used candidate that is absent from that response fails closed; it is neither grandfathered nor replaced by a hard-coded model ID.

## OpenRouter Pricing Schema Compatibility Correction

Live zero-inference validation on 2026-08-20 found a current model metadata shape that the prior pricing schema rejected incorrectly. Both the catalog record and exact-model response for `z-ai/glm-5.2:free` returned:

```json
{
  "prompt": "0",
  "completion": "0"
}
```

The record also independently passed the existing exact `:free` identity, text input/output, native `tools`, and 256000-token model/top-provider context checks. The absence of `request` and `image` does not mean those dimensions are zero; those dimensions are non-applicable or unreported for this text inference record. The previous requirement that `request` always exist therefore rejected a live current zero-priced model before final preflight.

The current official [OpenRouter Models API documentation](https://openrouter.ai/docs/guides/overview/models) defines pricing values as strings in USD per token/request/unit and states that string `"0"` means the feature is free. Its current Pricing Object documents exactly these base keys for this v1 schema:

```text
prompt
completion
request
image
web_search
internal_reasoning
input_cache_read
input_cache_write
overrides
```

`prompt` and `completion` are mandatory for this project's text inference contract. Both must be present strings satisfying the existing strict lexical-zero grammar. The only approved optional scalar keys are `request`, `image`, `web_search`, `internal_reasoning`, `input_cache_read`, and `input_cache_write`: if absent they do not invalidate an otherwise valid text-model pricing record; if present they must be strings satisfying the same strict lexical-zero grammar. No present billable dimension may be ignored, and `max_price=0` or `max_output_price=0` remains defense-in-depth rather than proof of local zero cost. Pricing dimensions not currently pinned by this authoritative schema, including `audio`, `audio_output`, `image_output`, `image_token`, `input_audio_cache`, and `input_cache_write_1h`, are unknown for v1 and fail closed if encountered; this does not claim they are unsupported by every OpenRouter API.

`overrides` is an optional conditional pricing array documented by OpenRouter for prompt-token thresholds and UTC time windows. It is allowed only when absent or exactly an empty array. A present non-empty array, malformed/non-array value, `discount`, any non-scalar pricing structure, or any other conditional/override-based value fails closed; v1 does not interpret override conditions or prove individual branches zero. The pricing object must contain no unknown keys. A model with any pricing structure that is not explicitly represented by the recognized schema is forbidden unless a later approved correction establishes safe semantics. Do not use an unbounded `Object.values(pricing).every(...)` check without first validating the exact recognized key set and field semantics.

The same `isStrictZeroPricing` qualification function and rules apply to both catalog candidates and exact-model final preflight. The live `z-ai/glm-5.2:free` prompt/completion-only record must therefore qualify as strictly zero-priced and advance to independent exact-model preflight, while Codex compatibility remains unproven until the explicit compatibility probe succeeds. Pricing/catalog/preflight validation remains metadata-only and stops before Codex on any failure. Exact `vendor/model:free` identity remains mandatory; `openrouter/free` and `openrouter/auto` remain forbidden.

## Codex Custom Provider Evidence

## Proposed OpenRouter CODEX_HOME Isolation Correction

Status: PROPOSED — implementation is not authorized by this correction.

Live compatibility validation found an execution-environment isolation gap in
the approved OpenRouter fallback. The pinned controller runtime is
`@openai/codex` `0.147.0`. The current fallback environment has
`fallback_has_CODEX_HOME=false` and `fallback_has_USERPROFILE=true`; under that
environment `codex login status` reports `Logged in using ChatGPT`, proving that
the normal user Codex home remains discoverable through `USERPROFILE`. A
diagnostic environment with an explicit fresh `CODEX_HOME` had
`auth_json_exists=false`, exited with code `1`, and reported `Not logged in`.
That diagnostic home was under `%TEMP%` and Codex refused to create helper
binaries there, so production must use a persistent non-temporary location.

The supporting upstream risk is recorded separately from the local failure
diagnosis: [OpenAI Codex issue #37245](https://github.com/openai/codex/issues/37245)
reports that a ChatGPT-authenticated `CODEX_HOME` can silently ignore a custom
`model_provider` and route sampling through the ChatGPT/OpenAI transport. This
is supporting risk evidence only; it does not prove that the failed GLM probe
took that route. Provider/auth isolation is independently required by the local
evidence.

Primary mode remains on the user's normal Codex home and ChatGPT subscription
authentication. `openrouter-free` mode must use a dedicated persistent home at
the exact Windows-derived path:

```text
resolve(LOCALAPPDATA, "print-engineer-codex", "openrouter-home-v1")
```

`LOCALAPPDATA` must be present, non-empty, absolute, and valid under the
approved Windows contract. The fallback must never use `%TEMP%`, `%TMP%`, the
repository/worktree, `~/.codex`, or a model-specific location. Construct
`%LOCALAPPDATA%\\print-engineer-codex\\openrouter-home-v1` only from that
validated base. Safely create missing controller-owned directory components
recursively, then use the pinned public Node filesystem APIs' `lstat`-style
metadata for each existing controller-owned component (`print-engineer-codex`
and `openrouter-home-v1`). Each component must exist after creation, be a
directory, and not be reported as a symbolic link, including Node-recognized
directory links/junctions. If path creation fails, `lstat` fails, a component
does not exist after creation, is not a directory, is reported as a symbolic
link/junction, or cannot be safely classified, stop before Codex with the
fixed category `OPENROUTER_CODEX_HOME_INVALID`. This v1 contract does not
claim that Node detects every possible Windows reparse tag; it forbids every
redirect the pinned public API is required to classify and fails closed on
classification failure. The directory must not be deleted or recreated per
invocation.

Immediately before every OpenRouter Codex execution — compatibility probe,
PLAN, BUILD, and REVIEW — validate that `auth.json` is absent from the isolated
home. If it exists, stop with the fixed category
`OPENROUTER_CODEX_HOME_AUTH_PRESENT`; do not parse, print, delete, or log it.
The isolated home prevents the normal user `config.toml` and ChatGPT
`auth.json` from affecting fallback. The existing explicit OpenRouter SDK
configuration remains authoritative: provider `openrouter`, the existing
OpenRouter base URL and `OPENROUTER_API_KEY` environment key,
`requires_openai_auth=false`, `wire_api=responses`, retry counts zero, and
websockets disabled.

Fallback environment construction must first build the existing sanitized
environment from the approved safe OS allowlist plus `OPENROUTER_API_KEY`.
`CODEX_HOME` is not an inherited environment variable and must not be added to
that generic allowlist. After the controller-owned fallback home has been
resolved and validated, inject exactly `CODEX_HOME=<validated fallback home>`
into the final environment object. Caller-supplied or inherited `CODEX_HOME`
is ignored and cannot override the controller-computed value. The final
fallback environment must contain the existing approved safe Windows fields,
`OPENROUTER_API_KEY`, and the controller-injected `CODEX_HOME`; it must exclude
`OPENAI_API_KEY`, `CODEX_API_KEY`, and other external inference credentials.
`USERPROFILE` may remain for ordinary Windows process behavior but must not be
relied upon for isolation. Primary environment construction remains unchanged:
no fallback `CODEX_HOME` and no `OPENROUTER_API_KEY`.

There must be one shared production OpenRouter fallback home/environment
contract, with one resolver, one persistent path, one directory
create/validation gate, one mandatory `lstat` link/directory validation gate,
one `auth.json` absence gate, and one isolated environment builder. The shared
contract performs, in order: validated `LOCALAPPDATA`; deterministic path
resolution; recursive directory creation; mandatory `lstat` classification of
the controller-owned path components; `auth.json` absence validation; sanitized
environment construction; and controller-owned `CODEX_HOME` injection after
inherited-environment filtering. Normal `openrouter-free` PLAN, BUILD, and
REVIEW execution and `--compatibility-probe` must call those same production
operations. Neither path may duplicate fallback-home logic, use an
unisolated `fallbackEnvironment()` path, or create a probe-only home. Both
Codex environments must contain `CODEX_HOME` set to the exact shared path
under `LOCALAPPDATA`.

The compatibility probe must use that same isolated-home resolver, directory
creation/validation, mandatory `lstat` link/directory validation, auth gate,
sanitized environment construction, and controller-owned environment builder
as normal OpenRouter execution. Its order becomes:

```text
catalog
→ strict free qualification
→ exact-model preflight
→ shared fallback-home resolver
→ create/validate shared CODEX_HOME
→ mandatory lstat validation of controller-owned path components
→ verify auth.json absent
→ construct sanitized environment
→ inject controller-owned CODEX_HOME
→ Codex execution
```

The executor must not be reached when the shared fallback-home resolver,
directory creation/validation, mandatory `lstat` validation, or `auth.json`
gate fails. Normal fallback uses the same shared resolver and gates immediately
before constructing the fallback Codex instance, including the same
post-sanitization `CODEX_HOME` injection, so both paths have identical
provider/auth isolation behavior.

The probe remains model-specific and does not establish compatibility for
`z-ai/glm-5.2:free`; its partial execution and terminal rate-limit outcome
remain inconclusive `UNPROVEN` evidence until a new explicitly authorized probe
is performed after this correction is built and reviewed. No special probe-only
home is allowed.

Preserve the existing thread identity rule of provider, exact model, role, and
worktree. The stable isolated home exists so fallback thread/session state can
persist across invocations. Never resume a primary thread in the fallback home
or a fallback thread in the primary home. The zero-cost contract, exact
`:free` identity, strict pricing, compatibility registry, exact preflight,
OpenRouter key requirement, and no-paid-fallback rules are unchanged.

Add hermetic controller tests proving: normal fallback uses the shared
resolver; compatibility probe uses the shared resolver; both use the same
deterministic `CODEX_HOME` under `LOCALAPPDATA` with the exact
`print-engineer-codex/openrouter-home-v1` suffix; externally supplied
`process.env.CODEX_HOME` is not inherited; final fallback environments contain
the controller-computed `CODEX_HOME` injected after sanitization; neither uses
`TEMP`/`TMP` or falls back to `~/.codex`; missing parent and final-home
directories are safely created and validated; normal existing directories
pass; parent or final-home symbolic link/junction reports from `lstat` fail
before the executor; parent or final-home regular files fail before the
executor; `lstat` and mkdir failures fail before the executor; invalid
`LOCALAPPDATA` blocks both paths before the executor; absent `auth.json`
allows both paths to reach their executor boundary; present `auth.json` blocks
both paths without reading, parsing, renaming, deleting, or logging out; the
primary path neither invokes the fallback resolver nor receives fallback
`CODEX_HOME`; primary excludes `OPENROUTER_API_KEY`; fallback excludes
`OPENAI_API_KEY` and `CODEX_API_KEY`; and no executor invocation occurs on any
home/link/auth validation failure. Tests must use injected filesystem
dependencies or temporary hermetic fixtures and must not depend on the
developer's actual Codex home or `~/.codex`.

After Build and independent Review, a separate zero-inference diagnostic must
prove the explicit persistent `CODEX_HOME`, absent `auth.json`, and pinned
`codex login status` output `Not logged in`. Only after that gate may one new
explicit compatibility probe be retried for the same exact model. No real
inference, provider execution, printer, MQTT, or hardware operation is part of
this plan correction.

This correction changes no package, catalog query, provider-routing decision,
registry, provenance, worktree hashing, or architecture package. It remains
within the existing 31-path implementation scope and authorizes no new file.

Current official evidence:

- The [Codex configuration reference](https://developers.openai.com/codex/config-reference) defines `model_provider`, `model_providers.<id>`, `base_url`, `env_key`, `requires_openai_auth`, and `wire_api`; `responses` is the only supported custom-provider wire value.
- The [Codex SDK documentation](https://developers.openai.com/codex/sdk) documents programmatic thread start/resume and per-thread model/workspace behavior.
- The [Codex CLI reference](https://developers.openai.com/codex/cli/reference) documents invocation-scoped `-c key=value` configuration overrides, but CLI use is unnecessary because the installed SDK exposes the same supported override channel.
- The installed public 0.147.0 SDK declarations document `CodexOptions.config` as additional CLI `--config` overrides, flattened to dotted paths and serialized as TOML; this is a public type, not an internal implementation dependency.
- OpenRouter's [Responses API documentation](https://openrouter.ai/docs/api/reference/responses/overview) documents `POST https://openrouter.ai/api/v1/responses`, bearer authentication, streaming, reasoning, and tool calling, but labels the API beta and stateless.

The direct technical risk is model-specific behavior, not provider configuration: OpenRouter's catalog-level `tools` metadata does not prove that every free model completes Codex's exact streamed Responses tool loop. The compatibility registry below fails closed on that gap.

## OpenRouter Multi-Agent Compatibility Correction

Status: PROPOSED — implementation is not authorized by this correction.

Post-isolation diagnostics established that the production OpenRouter metadata
flow succeeds through catalog GET, local strict qualification, exact-model
preflight, and the executor boundary. The pinned Codex 0.147.0 runtime accepts
the exact production custom-provider configuration, and a zero-inference
localhost Responses mock reached `POST /api/v1/responses` with
`model=z-ai/glm-5.2:free`, `stream=true`, and the native Codex
`multi_agent_v1` namespace tool present. With only `agents.enabled=false`, the
same SDK path still reached the endpoint and changed the outbound inventory
from eight tools to seven, with zero namespace tools and no `multi_agent_v1`.

Therefore the fallback contract must add this provider-specific configuration
field to the single shared production `openRouterConfig()` source:

```ts
agents: {
  enabled: false,
},
```

The complete fallback configuration remains:

```ts
{
  model_provider: "openrouter",
  model_providers: {
    openrouter: {
      name: "OpenRouter",
      base_url: "https://openrouter.ai/api/v1",
      env_key: "OPENROUTER_API_KEY",
      requires_openai_auth: false,
      wire_api: "responses",
      request_max_retries: 0,
      stream_max_retries: 0,
      supports_websockets: false,
    },
  },
  agents: {
    enabled: false,
  },
}
```

This setting applies only to OpenRouter-backed Codex execution: normal
`openrouter-free` PLAN, BUILD, and REVIEW, and `--compatibility-probe`. All
four paths must consume the same shared `openRouterConfig()` contract; there
must be no probe-only override or separate provider configuration object.
Primary subscription-backed Codex keeps its existing configuration and must
not receive `agents.enabled=false`; Codex multi-agent behavior is not disabled
globally.

On the OpenRouter fallback, the native Codex tool
`type=namespace`, `name=multi_agent_v1` is forbidden in the outbound
Responses request. Ordinary repository tools remain enabled, including
`shell_command`, `update_plan`, `request_user_input`, `view_image`,
`get_goal`, `create_goal`, and `update_goal`. The compatibility probe still
requires an actual successful command execution reading `AGENTS.md`.

The localhost evidence proves that `agents.enabled=false` removes
`multi_agent_v1` while preserving SDK request construction and transport. It
does not prove that `multi_agent_v1` was the sole cause of the failed real
OpenRouter probe. `z-ai/glm-5.2:free` remains compatibility `UNPROVEN` and is
not added to the compatibility registry. The failed real probe is not
converted into compatibility evidence.

All existing zero-cost gates, exact vendor/model identity, current catalog,
strict zero pricing, exact-model immediate preflight, no generic router, no
paid fallback, no candidate-two retry, and no mid-run switching remain
unchanged. The isolated `%LOCALAPPDATA%\\print-engineer-codex\\openrouter-home-v1`
CODEX_HOME, post-sanitization controller injection, directory/link validation,
and `auth.json` absence gate remain unchanged. Selector and selector-pricing
behavior remain unchanged, as do provider modes, primary eligibility,
preferred primary model, provenance, thread identity, SDK 0.147.0, package
versions, `$0.00` spend, and no OpenCode runtime fallback.

Build must add hermetic coverage proving that `openRouterConfig()` contains
`agents.enabled === false` and all existing provider fields exactly; normal
OpenRouter PLAN/BUILD/REVIEW and the compatibility probe consume that shared
config; primary does not receive the override; and existing
thread/provenance/isolation contracts remain unchanged. A pinned SDK
0.147.0 localhost HTTP-server regression (or equivalent behavioral integration
test using the real SDK) must exercise production configuration serialization
and prove that `POST /api/v1/responses` is reached with namespace tool count
zero and `multi_agent_v1` absent. Tests use a synthetic key and localhost only;
no real OpenRouter request or inference is allowed.

After Build and independent Review PASS, require one zero-inference
production-config loopback gate proving `production_config_used=true`,
`agents_enabled=false`, local `POST /api/v1/responses` reachability,
`namespace_tool_count=0`, `multi_agent_v1_present=false`,
`real_openrouter_key_passed=false`, and `model_inference=NO`. Only then may
exactly one new real compatibility probe run for the same
`z-ai/glm-5.2:free`. If it fails, stop and diagnose that single failure: do
not retry, select candidate two, or switch models.

This correction requires no new files and does not expand the approved
31-path implementation scope.

## SDK vs CLI Decision

`SDK_SUPPORTED`

Use `@openai/codex-sdk` for both modes. Construct the fallback `Codex` instance with its documented `config` and `env` options, then start a thread with the selected exact model and existing working directory/sandbox policy. Do not spawn `codex exec`, fork/patch the SDK, or depend on private APIs.

This decision is pinned to the controller's exact installed SDK/runtime version. Targeted local inspection on 2026-08-19 found `@openai/codex-sdk` 0.147.0, TypeScript 7.0.2, and `@types/node` 26.2.0 in both `node_modules` and the existing untracked controller lockfile. Build must replace every relevant `latest` specification with exact versions: `"@openai/codex-sdk": "0.147.0"`, `"typescript": "7.0.2"`, and `"@types/node": "26.2.0"`. No caret or tilde ranges are permitted. Upgrading any of them is outside this increment.

## Final Architecture

`tools/codex-controller` remains the orchestration owner. It parses provider mode, resolves the controller phase/selector role, uses the shared selector package only for `openrouter-free`, constructs the supported Codex SDK configuration, enforces thread identity, runs Codex, and delegates fallback provenance transactions to the shared selector package.

A new narrow package, `tools/openrouter-free-selector/`, owns the selector/provenance contract. This is option B from the requested alternatives. It is chosen because the selector code described by the PROPOSED selector plan does not yet exist, both executors need it, and making the active controller import a legacy executable named `model-runner` would create the wrong ownership direction. The package is not a generic provider framework: it supports only OpenRouter verified-free selection, compatibility registry validation, and selector-v1 provenance.

`tools/model-runner` keeps its current OpenCode execution code but becomes inactive for the controller path. No selector logic is duplicated in it. If legacy manual OpenCode invocation is retained, it may import the same shared selector package in a later migration; it is not an active automatic fallback.

## Provider Mode Contract

Add `CODEX_PROVIDER_MODE` with exactly three canonical values:

```text
auto
primary
openrouter-free
```

Parse by trimming and case-insensitive comparison and store the canonical lowercase value. Missing or empty defaults to `auto`. Reject every other value before worktree creation, selector access, Codex construction, or inference.

- `auto`: run the pinned machine-readable account/quota/model decision before every invocation, then execute exactly one chosen branch.
- `primary`: force ChatGPT-authenticated, subscription-backed Codex; still run the same safety status check and fail unless primary is proven available. Never fall back.
- `openrouter-free`: skip primary status checks and run the existing verified-free selector/compatibility/preflight path. Never fall back to primary or paid usage.

The decision is invocation-level, not turn migration. After the decision metadata is printed and Codex starts, provider and exact model are immutable for that invocation.

## Machine-Readable Primary Status Contract

Use the stable Codex app-server JSON-RPC surface documented by OpenAI and shipped by the controller's pinned `@openai/codex` 0.147.0 runtime. Build must spawn exactly:

```text
process.execPath
tools/codex-controller/node_modules/@openai/codex/bin/codex.js
app-server
--listen
stdio://
--strict-config
```

Resolve the script as the exact controller-relative absolute path; require its package version to equal the pinned SDK/runtime version `0.147.0`; do not use a PATH/global `codex`, shell, CLI inference command, private library import, third-party JSON-RPC package, or OpenAI API endpoint. Spawn with Node `child_process.spawn`, pipes, no shell, and the same sanitized primary environment later used for primary execution. Node built-ins are sufficient.

The transport is one JSON object per UTF-8 line on stdin/stdout. The pinned rust-v0.147.0 app-server uses JSON-RPC 2.0 semantics but omits the `"jsonrpc":"2.0"` header on the wire. Requests and notifications sent by the controller must therefore use the headerless generated app-server shapes below. Valid responses must not be rejected merely because they lack `jsonrpc`; response validation uses the actual pinned envelope (`id` plus exactly one of `result` or `error`), exact request correlation, and the method-specific result schema. The generated pinned Rust envelope structs do not use `deny_unknown_fields`, so an unexpected top-level `jsonrpc` property is ignored by the pinned envelope decoder; it is neither required nor a reason alone to fail closed. Unknown fields inside method-specific payloads remain subject to the applicable pinned schema validation. Ignore well-formed server notifications that have no matching request `id`; reject non-JSON stdout, duplicate terminal responses for an id, a JSON-RPC error response, a mismatched id, premature EOF/exit, or a response whose required fields fail the pinned schema. Do not parse stderr for status. Buffer stderr only up to 8 KiB for secret-redacted fixed-category diagnostics; never use its text in the decision.

For each status check, start one fresh app-server process and perform exactly:

1. Send this complete request and no additional fields: `{"id":1,"method":"initialize","params":{"clientInfo":{"name":"print_engineer_codex_controller","title":"Print Engineer Codex Controller","version":"0.1.0"},"capabilities":null}}`. There is no `jsonrpc` member on the wire. The pinned 0.147.0 generated `InitializeParams` contract requires `clientInfo` and permits `capabilities`; v1 deliberately sends literal `null` and Build has no discretion to omit or replace it.
2. Receive a structurally valid successful initialize response shaped as `{"id":1,"result":<valid InitializeResponse>}` with no `error` and no required `jsonrpc`; validate that its request id is numeric `1`. Only then send this complete notification and no additional fields: `{"method":"initialized"}`. The pinned 0.147.0 generated `ClientNotification` member is exactly `{ method: "initialized" }`; it has no `jsonrpc`, `id`, or `params`, and an initialized notification containing any of them is invalid and forbidden.
3. Only after step 2, send id `2`: `{"id":2,"method":"account/read","params":{"refreshToken":false}}`.
4. Only when id `2` proves `account.type === "chatgpt"`, send id `3`: `{"id":3,"method":"account/rateLimits/read"}`. The pinned generated request shape has no params member for this method.
5. Only when the quota response is structurally valid, enumerate `model/list` with the exact pinned `ModelListParams` fields only: `includeHidden:true`, `limit:100`, and `cursor` omitted for the first page; subsequent requests include the returned non-null `cursor` and retain only those same pagination fields. The first request is id `4`, followed by monotonically increasing ids, stopping only at `nextCursor:null`. Do not invent fields. Reject repeated cursors, more than 100 pages, or malformed pages/records. Validate every model record before qualification, including string `id`, string `model`, and an array `inputModalities` containing only valid string members. For a repeated exact model ID on the same or a later page, structurally identical validated records are collapsed to one logical record; records with that ID that differ in any field are conflicting duplicates and make the entire model listing `PRIMARY_STATUS_UNKNOWN`. This rule applies equally within one page and across pagination pages; Build has no alternate duplicate policy.
6. After the needed responses, close stdin, wait up to 1 second for clean exit, then call `child.kill()` if still alive. A forced shutdown after valid responses does not alter the decision; any process failure before all needed responses is `PRIMARY_STATUS_UNKNOWN`.

Use one 5,000 ms wall-clock deadline from successful spawn through receipt of all required responses. On expiry, terminate the child and classify `PRIMARY_STATUS_UNKNOWN`. Do not retry, refresh tokens, log in, consume reset credits, poll, send inference, or persist an app-server session. This check runs once at the start of every PLAN, plan-approval, BUILD, REVIEW, and any other controller invocation that can execute Codex. V1 has no daemon, timer, watcher, or background polling.

Official evidence: the [Codex app-server documentation](https://developers.openai.com/codex/app-server) specifies the stdio JSON-RPC lifecycle and these account/rate-limit/model methods. The pinned `rust-v0.147.0` generated public bindings for [ClientNotification](https://github.com/openai/codex/blob/rust-v0.147.0/codex-rs/app-server-protocol/schema/typescript/ClientNotification.ts) and `InitializeParams` are the exact handshake baseline, together with the locally installed controller runtime package version `0.147.0`. If those bindings or runtime behavior materially contradict this contract, Build stops as a plan conflict.

## Primary Availability Rule

Define the one exact v1 allowlist as:

```text
PRIMARY_SUBSCRIPTION_PLAN_TYPES = { "go", "plus", "pro" }
```

These are the only current PlanType values positively documented as ordinary personal ChatGPT web subscriptions and admitted by this project's `$0 beyond the user's existing subscription` routing policy. OpenAI's [ChatGPT billing guidance](https://help.openai.com/en/articles/9039756-chatgpt-search) classifies Go, Plus, and Pro as personal web subscriptions. The pinned 0.147.0 PlanType values deliberately rejected by v1 are exactly `free`, `prolite`, `team`, `self_serve_business_prolite`, `self_serve_business_usage_based`, `business`, `ent26`, `enterprise_cbp_automation`, `enterprise_cbp_usage_based`, `enterprise`, `edu`, and `unknown`. The business, enterprise, and education families are excluded because OpenAI's [flexible-pricing guidance](https://help.openai.com/en/articles/11487671-flexible-pricing-for-chatgpt-enterprise-plans) permits credit-pool or overage semantics; `prolite`, `team`, `ent26`, and other internally named variants lack sufficient official evidence for this project's stronger no-additional-charge guarantee. Names alone are never evidence.

Authentication is proven safe only when all of the following exact conditions hold: `account/read.result.account` exists; `account/read.result.account.type === "chatgpt"`; `account/read.result.account.planType` is literally one member of `PRIMARY_SUBSCRIPTION_PLAN_TYPES`; and `account/read.result.requiresOpenaiAuth === true`. There are no substring, truthiness, non-empty-string, recognized-enum, or “anything except free” checks. `account:null`, `apiKey`, `amazonBedrock`, unknown types, missing fields, any rejected/current PlanType, or any future/unrecognized PlanType makes primary forbidden. Presence of `OPENAI_API_KEY`, `CODEX_API_KEY`, or any other key is never positive evidence.

The exact safe-primary predicate is the preceding authentication classification AND the existing authoritative `codex` quota snapshot satisfying `PRIMARY_AVAILABLE` conditions below AND complete model pagination proving the preferred-model rule. `planType === "free"` and `planType === "unknown"` never authorize primary. A usage-based plan must never automatically authorize primary unless that exact literal is deliberately reviewed and added to this allowlist by a future approved change.

Select the authoritative quota snapshot as follows: if `rateLimitsByLimitId` is non-null, require its own-property `codex` with `limitId === "codex"`; otherwise require top-level `rateLimits.limitId === "codex"`. Do not combine buckets or use credits to extend allowance.

Classify the selected snapshot exactly:

- `PRIMARY_UNAVAILABLE` when `rateLimitReachedType` is any documented non-null reached value, `spendControlReached === true`, or any present `primary` or `secondary` window has finite `usedPercent >= 100`.
- `PRIMARY_AVAILABLE` only when `rateLimitReachedType === null`, `spendControlReached` is `false` or `null`, `primary` is present with finite `0 <= usedPercent < 100`, and a present `secondary` also has finite `0 <= usedPercent < 100`. `secondary:null` is allowed. `credits`, `individualLimit`, reset credits, `resetsAt`, and window duration are informational only and never authorize paid/credit usage.
- `PRIMARY_STATUS_UNKNOWN` for missing/malformed responses, no authoritative `codex` snapshot, absent primary window, non-finite/out-of-range percentages, unknown reached enum, method/process/timeout failure, or any condition not matching the two preceding definitions.

There is no invented reserve threshold: the controller uses the backend reached classification and the mathematical exhaustion boundary of the returned percentage. `PRIMARY_STATUS_UNKNOWN` never selects primary.

## Preferred Primary Model Rule

The sole primary model for v1 is exact `gpt-5.6-sol`. Model availability is proven only by complete successful pagination of `model/list(includeHidden:true, limit:100)` under the same ChatGPT-authenticated app-server process and an entry whose `id === "gpt-5.6-sol"`, `model === "gpt-5.6-sol"`, and `inputModalities` includes `"text"`. Hidden status and `isDefault` do not disqualify it. API model documentation alone is not account availability evidence.

If subscription auth/quota is usable but this exact model is absent, malformed, or model listing fails, primary is not chosen: auto proceeds to verified-free OpenRouter; manual `primary` stops with `PRIMARY_MODEL_UNAVAILABLE` or `PRIMARY_STATUS_UNKNOWN` as applicable. V1 has no alternate subscription model list and never substitutes an arbitrary model.

## Auto Decision Matrix

| Exact safe subscription classification | Quota state | `gpt-5.6-sol` proven available | Auto decision |
|---|---|---|---|
| account type is not `chatgpt` | any | any | `openrouter-free` |
| `requiresOpenaiAuth !== true` | any | any | `openrouter-free` |
| PlanType is `free`, `unknown`, any other rejected current value, or any future/unrecognized string | any | any | `openrouter-free` |
| malformed/unknown/error | any | any | `openrouter-free` |
| yes | unavailable | any | `openrouter-free` |
| yes | unknown/error | any | `openrouter-free` |
| yes | available | yes | `primary` + `gpt-5.6-sol` |
| yes | available | no/unknown/error | `openrouter-free` |

`PRIMARY_AVAILABLE` means, without exception: the exact allowlisted authentication predicate passes, every existing machine-readable authoritative quota condition passes, and exact `gpt-5.6-sol` availability passes. Anything else makes primary unavailable. An auto fallback decision is provisional until the existing OpenRouter key, selector, compatibility registry, and fresh zero-price preflight all succeed. If they do not, stop. Never reconsider primary within that invocation and never route elsewhere or to paid execution.

Immediately before execution, print safe metadata only. Primary prints `provider_mode`, `provider_decision=primary`, canonical `phase`, `model=gpt-5.6-sol`, and optionally `quota_available=true`. Fallback prints the same keys with `provider_decision=openrouter-free`, the exact public selected model id, and `verified_free=true`. Do not print auth tokens, API keys, account email/plan, raw quota values/reset times, environment, prompts, task contents, app-server responses, or arbitrary provider errors.

## Phase / Role Contract

Keep the controller's canonical `CODEX_PHASE=general|plan|build|review`; do not add `plan-review` as a controller phase.

Exact fallback mapping:

| Controller phase | Required review target | Selector role | Plan status |
|---|---|---|---|
| `plan` | none | `plan` / PLAN | prospective/resulting `PROPOSED` |
| `build` | none | `build` / BUILD | `APPROVED` |
| `review` | `CODEX_REVIEW_TARGET=plan` | `plan-review` / APPROVAL | `PROPOSED` |
| `review` | `CODEX_REVIEW_TARGET=implementation` | `review` / REVIEW | `APPROVED` |
| `general` | n/a | none | n/a |

In `openrouter-free`, `general` is rejected because it has no selector/provenance role and therefore no bounded compatibility/independence contract. `CODEX_REVIEW_TARGET` is ignored only outside `review`; when fallback phase is `review`, it is required, trimmed, case-insensitive, canonicalized lowercase, and accepts exactly `plan|implementation`. This explicit target avoids inferring semantics from prompt text or plan contents.

`MODEL_PLAN_PATH` is required in all four fallback selector roles. `MODEL_TASK_FILE` remains the strict task input. Existing optional role overrides remain `MODEL_PLAN`, `MODEL_PLAN_REVIEW`, `MODEL_BUILD`, and `MODEL_REVIEW`.

## Primary Mode

Primary is the subscription branch selected automatically or forced manually. It uses `new Codex({ env: primaryEnvironment })`, starts the thread with exact model `gpt-5.6-sol`, and uses only saved ChatGPT authentication proven by the preceding app-server status check. Manual `CODEX_PROVIDER_MODE=primary` applies the identical `PRIMARY_SUBSCRIPTION_PLAN_TYPES` membership, `requiresOpenaiAuth`, quota, and preferred-model predicate as auto; it never bypasses subscription classification. A free, unknown, usage-based-unapproved, otherwise excluded current, or future/unrecognized PlanType stops forced primary and does not route to OpenRouter. `primaryEnvironment` is the existing safe Windows process allowlist from the fallback contract without `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `CODEX_API_KEY`, or any other credential variable. This prevents an environment API key from changing the proven subscription path into paid API inference.

Primary does not require, read, validate, forward, or report `OPENROUTER_API_KEY`; it does not call the OpenRouter catalog, selector, compatibility registry, or pricing preflight. Auto mode must finish primary selection before any OpenRouter module is dynamically imported. Manual `primary` performs the same auth/quota/model safety proof but never falls back.

Primary does not log out, rewrite auth, change the user's global provider, or persist a fallback preference. Existing workingDirectory, sandbox behavior, prompt construction, and controller Git/publication rules remain unchanged.

Primary review remains fresh by default. Primary PLAN/BUILD continuation may use current controller thread behavior only when stored provider and exact model identity also match. Primary mode is not forced into external-model producer provenance; its independence remains the existing fresh Codex review context. Plan-approval tasks in primary must explicitly use `CODEX_PHASE=review`, `CODEX_REVIEW_TARGET=plan`, and fresh thread mode.

## OpenRouter-Free Mode

Before constructing Codex, fallback validates all task/worktree/plan inputs, requires the API key, performs current role selection and producer exclusion, qualifies the selected model against the compatibility registry, and completes the selector's final record preflight. Only then may Codex start.

Fallback constructs a new Codex context for the selected provider/model identity. It never resumes primary state or another fallback model's state. Review and plan approval are always fresh. PLAN/BUILD may resume only a thread whose stored provider mode and exact selected model ID match and whose role/task identity remains eligible; because automatic selection is re-run every invocation, a different selected model forces a fresh thread.

The active fallback path never probes or spawns OpenCode. A Codex/custom-provider failure stops the execution and does not switch backend, provider, or model.

## Zero-Cost Invariant

All OpenRouter inference must use one exact selected `vendor/model:free` record that currently passes the selector's strict zero schema and grammar both in the role catalog and immediate single-model preflight. Registry membership cannot bypass current price validation.

No code path may supply `models`, alternate models, `openrouter/free`, `openrouter/auto`, paid overrides, provider fallbacks to another model, credit purchasing, billing fallback, or BYOK alternatives. The request contains exactly one `model`. OpenRouter may route that exact model among its providers only while the exact `:free` model identity remains the request target and its current pricing is provably zero.

Set Codex provider `request_max_retries=0` and `stream_max_retries=0`. This prevents SDK/runtime transport retries from obscuring the single-attempt failure contract. After final preflight failure or after inference starts, stop.

## OpenRouter Selector Integration

Move the not-yet-implemented selector plan's prospective generic selector modules into `tools/openrouter-free-selector/src/` without changing their algorithms:

- `openrouter.ts`: exact catalog/preflight HTTP, schema, price, free/capability checks;
- `model-selector.ts`: roles, optional overrides, server-order first selection, producer exclusions;
- `provenance.ts`: plan/worktree identities, schemas/storage, atomic writes, execution lock;
- `compatibility.ts`: the additional Codex compatibility registry gate;
- `index.ts`: narrow typed exports only; no executable inference entry point.

The controller consumes these exports. The selector package never imports the controller or model-runner, never runs inference, and never knows about Codex SDK types. This avoids circular dependencies and duplicate selector logic.

## Codex-Compatible Free Model Qualification

Define `CodexCompatibleFreeModel` as a selected exact model ID satisfying all of:

1. the full selector `VerifiedFreeModel` contract: exact specific `:free` ID, strict current zero prices, text input/output, native `tools`, both context fields with effective context at least 32768, valid non-expired record, and current final preflight;
2. for normal fallback only, an exact current entry in the local compatibility registry for the same case-sensitive OpenRouter model ID;
3. registry evidence recorded against Codex SDK/runtime 0.147.0, provider wire `responses`, streamed text output, multi-turn function-tool call/result continuation, and harmless local shell/read tool completion;
4. no registry expiration or version mismatch; and
5. no unsupported requirement discovered by the current Codex request shape.

Catalog metadata is preliminary eligibility only and never proves Codex compatibility. The project does not claim that all OpenRouter Responses-compatible models are Codex compatible. For normal fallback, the selector first chooses the server-ordered verified-free candidate under the preserved selector rules; independence exclusion and the registry gate then apply to that selected exact model. An absent, malformed, expired, future-dated, provider/protocol-mismatched, or SDK-version-mismatched entry stops normal fallback; it does not try candidate two. Failed final preflight likewise stops without candidate two. The sole exception is the dedicated compatibility-probe branch defined below; it omits only the pre-existing-registry requirement.

## Responses API Compatibility

Pin `wire_api="responses"`, SSE streaming (not WebSocket), and `supports_websockets=false`. A compatible registry entry proves the tested free model handled the Codex 0.147.0 request/response subset: text input/output, streamed response events, function tool declarations, tool-call arguments, tool-result continuation, and completion after at least one tool round. Reasoning output is optional, but if emitted it must not break Codex parsing. Web search and OpenRouter server tools are not part of the gate.

OpenRouter's Responses API is beta and stateless. Codex thread continuation is therefore a Codex/client concern; compatibility evidence must cover at least two turns or an equivalent tool round using the full context sent by Codex. Any protocol/schema incompatibility is a hard stop and must be resolved by a new validated registry entry or later plan, not an automatic model switch.

## Provider Configuration

For fallback only, construct the SDK with this exact supported configuration object:

```ts
new Codex({
  config: {
    model_provider: "openrouter",
    model_providers: {
      openrouter: {
        name: "OpenRouter",
        base_url: "https://openrouter.ai/api/v1",
        env_key: "OPENROUTER_API_KEY",
        requires_openai_auth: false,
        wire_api: "responses",
        request_max_retries: 0,
        stream_max_retries: 0,
        supports_websockets: false,
      },
    },
    agents: {
      enabled: false,
    },
  },
  env: fallbackEnvironment,
});
```

Start the thread with `model: selected.modelId`, the existing exact working directory, the controller's current sandbox/approval settings, `networkAccessEnabled` unchanged from existing controller policy, and web search disabled for fallback. Runtime SDK configuration is sufficient. No project or user `config.toml`, profile, login, or one-time persistent setup is required or permitted by this increment.

Build tests must assert the exact object. If 0.147.0 rejects any pinned key during implementation, that is a plan/repository contradiction and Build must stop rather than silently use persistent config or CLI.

## Authentication

`OPENROUTER_API_KEY` is required only for `openrouter-free`. Trim only to validate non-empty; preserve the original value for authentication. The selector receives it as an injected secret and sends `Authorization: Bearer` only over the pinned HTTPS OpenRouter endpoints. Codex receives it only via the SDK child environment because `env_key` resolves there.

Build `fallbackEnvironment` from a copy of `process.env`, remove the existing printer/hardware variables, and include `OPENROUTER_API_KEY`. Because SDK `env` replaces inheritance, preserve only environment needed for normal Windows process/tool execution plus explicitly allowed controller variables; do not forward unrelated credential variables. The exact allowlist is `PATH`, `Path`, `PATHEXT`, `SystemRoot`, `WINDIR`, `COMSPEC`, `TEMP`, `TMP`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `PROGRAMDATA`, `PROGRAMFILES`, `PROGRAMFILES(X86)`, `PROCESSOR_ARCHITECTURE`, `NUMBER_OF_PROCESSORS`, `TERM`, `COLORTERM`, `NO_COLOR`, and `OPENROUTER_API_KEY`, matching keys case-insensitively but emitting the original Windows key spelling. Do not forward `OPENAI_API_KEY`, `CODEX_API_KEY`, Bambu credentials, GitHub tokens, or arbitrary `*_KEY`/`*_TOKEN` variables.

Never print, persist, hash, commit, prompt-inject, task-store, or provenance-store the key. Redact the exact key value from all caught error strings before safe error classification; do not log raw provider bodies, headers, SDK argv, SDK environment, prompts, or arbitrary stderr. Primary never receives this environment object and remains on normal authentication.

The hard cost boundary applies to both branches. Primary means only ChatGPT-managed subscription authentication; it never means OpenAI API billing. Strip API-key variables from both the app-server status child and primary Codex child. Do not call OpenAI inference APIs directly. No paid OpenAI API, paid OpenRouter, cheapest-paid model, `openrouter/auto`, `openrouter/free`, BYOK paid provider, other provider, credits redemption, automatic credit purchase, or billing fallback is permitted.

## Model ID Contract

The selector's canonical OpenRouter ID is exact case-sensitive `vendor/model:free` with one slash and no `openrouter/` prefix. Pass that exact string as SDK `ThreadOptions.model` while `model_provider="openrouter"`.

Do not prepend `openrouter/`; that was an OpenCode provider/model mapping. Do not remove `:free`, normalize case, use an alias, pass multiple models, or permit a second model channel through config/environment. The controller logs only the already-public canonical model ID and safe qualification metadata.

## Thread / Session Contract

Extend persisted task state with `schemaVersion: 2`, `providerMode`, `modelIdentity`, and `role`. Thread reuse requires exact equality of task key, worktree, provider mode, model identity, and compatible role, in addition to `CODEX_THREAD_MODE=resume`.

- Fallback `modelIdentity` is the exact selected OpenRouter ID.
- Primary `modelIdentity` is exact `gpt-5.6-sol`; the controller supplies that exact model override.
- A legacy state record lacking identity is not resumable; preserve its metadata but start fresh and rewrite it only after a successful persistable run.
- `review` and approval always start fresh regardless of `CODEX_THREAD_MODE`; requesting `resume` for either is rejected before execution rather than weakening independence.
- A primary/fallback change or fallback model A/model B change always starts fresh and never calls `resumeThread`.

No transcript, hidden reasoning, rollout file, thread ID, or provider state crosses identities. Durable Git worktree state is the only handoff medium.

## PLAN → Approval Independence

Fallback PLAN maps to selector role `plan` and may use automatic model A or `MODEL_PLAN`. After successful Codex exit, actual valid proposed-plan creation/change, and atomic provenance write, approval is invoked as `CODEX_PHASE=review`, `CODEX_REVIEW_TARGET=plan` and always fresh. It maps to `plan-review`, loads provenance for the exact current plan hash, excludes A, and selects/validates model B. If current producer identity is unknown, approval stops. A and B must differ for that exact plan version.

Primary approval is also a fresh `review/plan` run but is not forced into external-model ID provenance. It remains an independent fresh normal Codex context.

## BUILD → Review Independence

Fallback BUILD maps to selector role `build`. It holds the selector execution lock from pre-state through Codex execution, post-state validation, and provenance persistence. Only successful Codex exit plus actual state change records model C/current plan/current state.

Implementation review is `CODEX_PHASE=review`, `CODEX_REVIEW_TARGET=implementation`, always fresh, maps to selector role `review`, validates exact current plan/state producer provenance, excludes C, and selects/validates D. Unknown producer identity stops. C and D must differ for the exact current implementation state.

## Provenance Integration

Reuse the selector plan's exact root and schemas under `<git-common-dir>/print-engineer/model-runner/selector-v1/`; do not rename it merely because Codex becomes executor. This preserves compatibility with the mature selector contract. Records still contain only schema/kind, canonical plan path, plan/state hashes, and exact model ID.

Executor hooks change only as follows:

- PLAN success predicate: Codex stream completes successfully, selected thread turn succeeds, plan artifact exists/changed, and resulting plan has exactly `Status: PROPOSED`.
- BUILD success predicate: Codex stream completes successfully and exact non-ignored state hash changes.
- Failed/partial/unchanged Codex runs write no producer record.
- Approval/review/select-only never write.

No primary-mode producer record is added. No credential, prompt, response, transcript, task content, absolute path, or timestamp enters provenance.

## Primary → Fallback Handoff

An explicit override or a new auto decision selects `openrouter-free` while retaining the same `CODEX_TASK_KEY`/issue identity and worktree-root convention. The controller resolves the same durable linked worktree. Provider identity mismatch makes the Codex context fresh.

The fallback prompt reconstructs only from `AGENTS.md`, the exact approved/proposed plan appropriate to the role, `git status`, actual targeted diff, named/relevant files, and focused verification evidence. It receives no primary thread ID, transcript, hidden reasoning, or auth state. Any partial work remains visible solely as Git worktree state.

## Fallback → Primary Handoff

An explicit override or a later auto check selects primary while keeping the same task/worktree identity. The controller uses the same durable worktree, sees provider identity mismatch, and starts a fresh subscription-backed `gpt-5.6-sol` thread. It reconstructs from repository state and never migrates the OpenRouter thread/transcript/provider state.

Auto selection runs independently at the beginning of PLAN, plan approval, BUILD, and REVIEW. PLAN may use primary while BUILD later uses OpenRouter, or REVIEW may return to primary after reset. A provider/model change always starts fresh while preserving the same durable task worktree. No transcript, hidden reasoning, or provider session crosses the boundary.

If OpenRouter produced a PLAN or BUILD and later primary performs approval/review, primary must be fresh and inspect the actual durable plan/worktree state; selector producer provenance remains authoritative where required. If primary produced the artifact and later OpenRouter reviews it, the fresh OpenRouter provider/model context plus existing artifact/hash provenance rules establish independence. When both producer and reviewer use OpenRouter, exact producer-model exclusion remains mandatory. Mixed-provider execution never weakens fresh-review, plan-status, artifact-hash, or worktree-state validation.

## Invocation-Level Failure and Reset Behavior

AUTO PROVIDER SELECTION IS INVOCATION-LEVEL, NOT TURN-MIGRATION. Once Codex execution begins, a primary quota/provider failure terminates that invocation with `CODEX_EXECUTION_FAILED`; it never continues the same task, turn, or thread through OpenRouter. Likewise, OpenRouter failure never switches to primary or another model. Partial durable worktree edits remain visible for normal user/reviewer handling, but no transcript/session migration occurs.

The next controller invocation performs a new status check. If subscription quota is then exhausted it starts a fresh OpenRouter-backed context in the same durable worktree. If quota has reset, or becomes usable while an OpenRouter invocation is already running, that running invocation is not interrupted; the next invocation may start a fresh primary `gpt-5.6-sol` context in the same worktree. No process polls for resets between invocations.

## Provider Status Command

Add exact inference-free diagnostic `npm start -- --provider-status`. It is mutually exclusive with `--select-only`, `--compatibility-probe`, and normal execution; unknown or combined flags fail. It parses `CODEX_PROVIDER_MODE`, performs only the status work required by that mode, prints safe lowercase `key=value` lines, and exits without constructing a Codex SDK executor:

```text
provider_mode=auto|primary|openrouter-free
primary_auth_available=true|false|unknown|skipped
primary_plan_supported=true|false|unknown|skipped
primary_quota_available=true|false|unknown|skipped
preferred_model=gpt-5.6-sol
preferred_model_available=true|false|unknown|skipped
auto_decision=primary|openrouter-free|not-applicable
openrouter_config_available=true|false|not-required|skipped
```

For `auto`, it runs the pinned app-server check and uses the same exact `PRIMARY_SUBSCRIPTION_PLAN_TYPES` classification as execution. It checks only whether trimmed `OPENROUTER_API_KEY` is non-empty when the resulting decision requires fallback; it does not call OpenRouter. For manual `primary`, it runs the same primary safety check and reports `not-applicable` for auto decision. For manual `openrouter-free`, all primary fields, including `primary_plan_supported`, are `skipped`, auto decision is `not-applicable`, and it checks key presence only. Exit `0` when the selected/manual branch is presently configurable and safe to attempt; exit `1` for invalid mode, failed forced-primary proof, or required missing OpenRouter key. Status never guarantees later execution success and never prints the full account structure or exact PlanType.

The command performs no Codex/OpenRouter inference, OpenRouter catalog/preflight/compatibility probe, Git command or mutation, worktree creation, thread/state/provenance/registry write, publication, printer access, or hardware access. It does not print account email, plan type, raw percentages, reset timestamps, tokens, environment, credentials, prompts, or task contents. `--compatibility-probe` remains a separate explicit command and may perform one real verified-free inference under its existing contract.

## Select-Only Mode

Preserve `--select-only` as a controller command-line option available only with `CODEX_PROVIDER_MODE=openrouter-free`. It performs strict inputs, role mapping, catalog, free/capability/context/expiration qualification, provenance exclusion, compatibility-registry qualification, and immediate single-model preflight. It prints safe selection metadata and exits.

It performs no Codex SDK construction, inference, thread read/write, OpenCode invocation, lock acquisition, Git mutation, provenance mutation, or publication. Its inference cost is `$0.00`. Reviewer roles still require exact current provenance and fail when producer identity is unknown.

## Compatibility-Probe Bootstrap

Add the exact controller CLI mode `npm start -- --compatibility-probe`. It is a dedicated read-only bootstrap operation, not `general`, PLAN, BUILD, plan approval, or implementation review. `src/index.ts` recognizes the exact flag without a new CLI-parser dependency, gives it precedence over normal phase dispatch, and exits after its report. Unknown options fail clearly. The probe cannot combine with `--select-only` or any normal task execution; it reads or writes no producer provenance and performs no publication. `CODEX_PHASE` is ignored for probe execution semantics and cannot cause normal phase dispatch.

The probe requires `CODEX_PROVIDER_MODE=openrouter-free`; missing, empty, `primary`, or any other value fails before selector or Codex work. It never intentionally consumes the normal Codex subscription. It also requires `OPENROUTER_API_KEY` under the existing secret contract and `CODEX_COMPATIBILITY_MODEL` containing exactly one explicitly supplied OpenRouter ID. Trim surrounding whitespace, preserve case, and require a non-empty exact `vendor/model:free` value. Reject generic routers including `openrouter/free` and `openrouter/auto`, lists, comma-separated alternatives, and any non-`:free` ID. No automatic selection and no candidate-two fallback occur.

Exact probe order:

```text
validate controller/worktree inputs
→ read CODEX_COMPATIBILITY_MODEL
→ current OpenRouter catalog lookup for that exact model
→ verified-free price/expiration qualification
→ hard Codex capability/context metadata qualification
→ fresh single-model zero-cost preflight immediately before execution
→ shared fallback-home resolver
→ create/validate shared CODEX_HOME
→ verify auth.json absent
→ construct shared isolated fallback environment
→ construct Codex custom OpenRouter provider
→ start a fresh Codex thread
→ run the controller-owned read-only task
→ evaluate streamed evidence and post-run worktree state
→ print safe evidence, COMPATIBILITY_PROBE_RATE_LIMITED, or COMPATIBILITY_PROBE_FAILED
```

There is deliberately no `compatibilityRegistry.requireCurrent(...)` before the probe. This is the only OpenRouter-backed Codex path allowed without an existing registry entry, and the only bypass is “compatibility registry entry required.” API key, exact-model syntax, catalog presence, strict zero pricing, expiration, hard capabilities, context minimum, immediate preflight, custom-provider configuration, secrets, isolated-worktree validation, read-only sandbox, no Git mutation/publication, no printer/hardware access, and no paid inference remain mandatory.

Reuse the controller's existing worktree validation and working-directory contract. The target must be an existing isolated linked project worktree. The probe never creates, cleans, resets, stages, commits, pushes, merges, or otherwise publishes a worktree. Capture `git status --short` immediately before Codex and after completion; exact equality is required. Any probe-owned mutation is failure.

The probe starts a fresh thread with the installed SDK's strictest supported `sandboxMode: "read-only"`, `approvalPolicy: "never"`, the validated worktree as `workingDirectory`, `networkAccessEnabled: false`, and `webSearchMode: "disabled"`. These are public `@openai/codex-sdk` 0.147.0 options. The model receives no additional directory and cannot edit the repository.

The exact controller-owned prompt is:

```text
Using repository tools, read AGENTS.md from the current worktree. Do not edit anything. Report the first Markdown heading and finish.
```

No environment variable, task file, CLI argument, user prompt, or normal phase prompt may replace or append to it. The response body is not trusted as tool evidence and is never included in the safe report.

### Pinned SDK Event Evidence

The public 0.147.0 SDK `ThreadEvent` contract proves the local repository interaction through an `ItemCompletedEvent` with `event.type === "item.completed"` and `event.item.type === "command_execution"`. Success additionally requires `event.item.status === "completed"`, `event.item.exit_code === 0`, and a non-empty `event.item.command` that targets the exact `AGENTS.md` token requested by the controller prompt. `item.started`, `item.updated`, final text alone, `mcp_tool_call`, `web_search`, or an item with missing/nonzero exit code does not qualify. `thread.started` proves the fresh thread began; `turn.completed` plus a non-empty final Codex response proves final completion. Any `turn.failed`, stream `error`, failed command item, provider/protocol error, or absent required event makes the turn unsuccessful, while events observed before that failure remain available for partial diagnostics under the terminal-error contract below.

This event contract is observable in the installed public SDK, so the plan remains buildable. If Build discovers the runtime declaration or emitted contract differs, Build must stop as a plan/repository contradiction and must not infer tool success from text.

### Compatibility-Probe Rate-Limit Classification Correction

The valid real compatibility probe for `z-ai/glm-5.2:free` used
`provider=openrouter`, `agents.enabled=false`, an isolated controller-owned
`CODEX_HOME`, a clean linked worktree, and a fresh Codex thread:
`01a01ee2-48ea-7a41-aacc-574f51f872b2`. OpenRouter activity proved an initial
HTTP 200 Responses request for `z-ai/glm-5.2:free` through provider `Decart`,
with 9619 input tokens, 61 output tokens, streaming enabled, finish reason
`tool_calls`, and `$0.00` cost. The next upstream request returned HTTP 429.

The exact rollout proved `thread.started`, a `shell_command` function call
targeting `Get-Content .\\AGENTS.md -TotalCount 20`, a matching
`function_call_output`, and command exit code zero. It did not prove a final
post-tool model completion. Therefore initial Responses generation, streaming,
the valid shell command, AGENTS.md targeting, command success, matching tool
output, and zero cost are proven; final completion and full compatibility are
not proven. The latest terminal outcome is HTTP 429 rate limited/inconclusive;
the model and registry eligibility remain `UNPROVEN`.

The pinned Codex SDK 0.147.0 public `ThreadEvent`/thrown-error boundary exposes
message-only failure information on this path. The richer persisted rollout
structure
`codex_error_info.response_too_many_failed_attempts.http_status_code` is not
reliably surfaced there. Do not assume the thrown JavaScript error contains
`codex_error_info`, and do not compensate by parsing ordinary error messages
such as `429 Too Many Requests`. Plain-text classification is forbidden.

The executor contract must preserve both already-observed legitimate streamed
events and safely available structured terminal failure metadata when the SDK
stream later throws. A terminal failure is never an `ExecutionResult` success;
the typed internal result must be able to express `partial_events` plus
`terminal_error`, including the exact thread ID from `thread.started` when it
was observed. The controller may use preserved partial events for safe
diagnostics such as `thread_started=true`,
`AGENTS_md_command_completed=true`, `command_exit_code=0`,
`tool_output_produced=true`, and `final_completion=false`, but partial tool-loop
evidence can never satisfy probe success. Full success still requires every
existing success condition, including final successful completion.

Add the distinct result `COMPATIBILITY_PROBE_RATE_LIMITED`. It may be emitted
only when the current failed execution has a trustworthy exact thread ID and
the minimum structured terminal metadata from the exact correlated persisted
rollout proves:

```text
event_msg.payload.type = task_complete
event_msg.payload.error.codex_error_info.response_too_many_failed_attempts.http_status_code = 429
```

An equivalent exact structured representation is acceptable. The fallback must
inspect only the validated isolated OpenRouter `CODEX_HOME` at
`%LOCALAPPDATA%\\print-engineer-codex\\openrouter-home-v1`, never the normal
ChatGPT-authenticated home. It must use the exact current thread/session ID
obtained from this execution, including the ID preserved from `thread.started`;
it must never select the newest/latest/arbitrary rollout or discover a session
by model/worktree alone. If session metadata exists, `payload.id` or
`payload.session_id` must equal the current thread ID exactly; filename matching
alone is insufficient.

Before trusting rollout evidence, require the rollout to be under the already
validated isolated home and correlate exact session ID, `model_provider=openrouter`,
the exact current linked-worktree cwd, intended exact model context when
available, and terminal evidence belonging to that session. If any correlation
is missing, conflicting, corrupt, unreadable, or otherwise unproven, fail closed
as `COMPATIBILITY_PROBE_FAILED`. Inspect only the minimum structured terminal
metadata needed for classification. Never log or expose prompts, AGENTS.md
contents, function arguments except safe probe evidence already permitted,
command output, keys, authorization headers, unrelated response contents, or
complete rollout JSON.

Rollout inspection is only a terminal-failure classification fallback. It is
never an independent success engine and cannot declare
`COMPATIBILITY_PROBE_SUCCESS`; normal execution/probe evidence must still prove
final completion. A partial successful tool loop followed by a terminal 429 is
therefore `COMPATIBILITY_PROBE_RATE_LIMITED`, not success. A structured exact
thread 429 leaves compatibility `UNPROVEN`, performs no registry interaction,
does not create validity evidence, performs no automatic retry, never selects
candidate two, never switches models, and has executor invocation count one.
The result is reported with a non-zero CLI exit code. All non-429, missing,
unstructured, malformed, stale, concurrent, wrong-provider, wrong-worktree,
wrong-model, or otherwise uncorrelated evidence remains
`COMPATIBILITY_PROBE_FAILED`; text containing `429` without structured status
must fail rather than become rate limited.

### Probe Success and Safe Report

All of these are mandatory:

1. the exact requested model passed current verified-free qualification;
2. the immediate preflight immediately before Codex still proved `$0.00` pricing;
3. the configured Codex provider was `openrouter`;
4. the exact requested model ID was used;
5. a fresh thread emitted `thread.started`;
6. the Responses stream completed without provider/protocol failure;
7. the pinned repository command event was observed;
8. that command completed with status `completed` and exit code zero;
9. `turn.completed` and a non-empty final response were observed;
10. post-probe `git status --short` exactly matched the pre-probe result;
11. no Git publication occurred; and
12. no printer/hardware access occurred.

Failure of any condition returns `COMPATIBILITY_PROBE_FAILED`, except a
trustworthy structured HTTP 429 provider failure, which returns
`COMPATIBILITY_PROBE_RATE_LIMITED`; neither failure result is compatibility
evidence. The probe performs real inference only after exact `:free` identity,
strict zero pricing, and fresh preflight prove monetary inference cost `$0.00`.
Ambiguity stops before Codex, with no second model attempt.

On success, print only: exact model ID, exact Codex SDK compatibility version, `provider_id=openrouter`, `wire_api=responses`, UTC validation timestamp, proposed valid-until timestamp, tool-loop success, final-completion success, and worktree-unchanged result. Do not print prompts, task contents, response body, API key, credentials, environment, headers, or provider bodies. The probe never writes the compatibility registry.

### Manual Registry Bootstrap

The registry is user-maintained in v1:

1. The user explicitly runs `npm start -- --compatibility-probe` with required environment and one exact model.
2. A successful probe prints only the safe evidence fields.
3. The user reviews that evidence.
4. The user manually creates or updates the exact registry JSON entry at the pinned path and schema.
5. A subsequent normal fallback invocation loads and validates the entry.

No other AI must approve it, no failed probe permits registration, and the controller never writes it automatically.

## Normal Fallback Compatibility Order

Normal PLAN, BUILD, plan approval, and implementation review in `openrouter-free` retain this exact order:

```text
selector → verified-free candidate → current pricing/capability qualification
→ independence exclusion → compatibility registry lookup
→ exact identity match → registry validity check
→ immediate current single-model zero-cost preflight → Codex execution
```

Missing, expired, future-dated, wrong-version, wrong-provider, or wrong-protocol evidence stops normal fallback. A registry entry never skips current selector qualification or immediate preflight and never certifies price, availability, context, capabilities, expiration, or free status.

## Existing OpenCode Treatment

Classification after this increment:

| Component | Classification | Contract |
|---|---|---|
| `tools/model-runner/src/core.ts`, current tests/docs/package | KEEP BUT INACTIVE | Retained for explicit legacy manual OpenCode use; never reached from `CODEX_PROVIDER_MODE=openrouter-free`. |
| `tools/model-runner/src/index.ts` OpenCode spawn/version path | DEPRECATE | Document as legacy; no deletion or large rewrite in this increment. |
| `.opencode/agents/fallback-plan.md` | KEEP BUT INACTIVE | Legacy manual OpenCode only. |
| `.opencode/agents/fallback-build.md` | KEEP BUT INACTIVE | Legacy manual OpenCode only. |
| `.opencode/agents/fallback-review.md` | KEEP BUT INACTIVE | Legacy manual OpenCode only. |
| older `.opencode/agents/plan.md`, `approve.md`, `build.md`, `review.md` | KEEP BUT INACTIVE | Outside active controller path and outside Build scope. |
| OpenCode-specific selector mapping/prospective execution wiring | REMOVE IN LATER CLEANUP | Do not implement it as the new active selector path; clean legacy docs/tooling separately after Codex fallback is proven. |

No component is deleted in this increment. Manual direct invocation of legacy tooling remains possible but is not an automatic or controller fallback.

## Git / Publication Ownership

Provider selection changes no Git ownership. Codex prompts continue to forbid commit/push/merge/PR. Manual mode remains non-publishing. Existing GitHub issue mode may stage controller-run changes, commit, push, and open a draft PR only under its current controller rules after successful agent execution; the OpenRouter model receives no additional Git privileges.

Selector/provenance writes under the Git common directory are internal orchestration metadata, not publication. Failure before successful execution prevents controller publication. No provider failure triggers a commit or checkpoint.

## Secrets / Safety

Every mode continues to obey root `AGENTS.md`, approved plans, focused context, hardware gates, printer/MQTT restrictions, and credential rules. Fallback does not access printer hardware, add MQTT publishing, weaken sandboxing, read secret files, or alter production behavior.

Automated tests use fakes and synthetic keys only. The optional live compatibility probe is separate, explicitly user-run only after Build and independent Review, reads `AGENTS.md`, uses the pinned read-only sandbox, disables network for model tools, and performs no Git publication or hardware access.

## Failure Behavior

Use fixed safe categories/codes and report only provider mode, canonical phase/role, selected public model when selection completed, and category/code:

- `INVALID_PROVIDER_MODE`
- `INVALID_REVIEW_TARGET`
- `PRIMARY_STATUS_START_FAILED`
- `PRIMARY_STATUS_TIMEOUT`
- `PRIMARY_STATUS_PROTOCOL_ERROR`
- `PRIMARY_AUTH_UNAVAILABLE`
- `PRIMARY_QUOTA_UNAVAILABLE`
- `PRIMARY_STATUS_UNKNOWN`
- `PRIMARY_MODEL_UNAVAILABLE`
- `OPENROUTER_AUTH_MISSING`
- existing selector catalog/schema/pricing/provenance/lock codes
- `CODEX_COMPATIBILITY_UNKNOWN`
- `CODEX_COMPATIBILITY_STALE`
- `COMPATIBILITY_PROBE_FAILED`
- `COMPATIBILITY_PROBE_RATE_LIMITED`
- `CODEX_PROVIDER_CONFIG_FAILED`
- `CODEX_EXECUTION_FAILED`
- `PLAN_ARTIFACT_UNCHANGED`
- `BUILD_STATE_UNCHANGED`

In auto, every primary unavailable/unknown category selects the verified-free branch, which must independently pass all fallback gates; failure there stops. In manual primary, every unavailable/unknown category stops. Catalog failure, no compatible verified-free model, missing fallback key, final preflight failure, provider configuration failure, or Codex execution failure stops. Never select candidate two after final preflight, retry a provider/model, parse stderr to switch, switch mid-run, switch to OpenCode/paid, or use a router. Redact secrets and do not echo arbitrary remote/SDK error strings.

## Source / Package Architecture

Create `tools/openrouter-free-selector/` as a private Node >=18 TypeScript ESM package with runtime dependencies `NONE`, `node:test`, exact exports from compiled `dist`, and scripts `build` and `test`. Its package name is `@print-engineer/openrouter-free-selector`. It uses only Node built-ins already required by the architecture (`fetch`, `AbortController`, `crypto`, `fs/path`, and `child_process` where applicable).

The dependency direction is exactly `codex-controller → openrouter-free-selector`; the selector depends on neither controller nor model-runner. The controller adds `"@print-engineer/openrouter-free-selector": "file:../openrouter-free-selector"` and `"@openai/codex-sdk": "0.147.0"`. Both packages pin dev dependencies `"typescript": "7.0.2"` and `"@types/node": "26.2.0"`.

Pin selector scripts to `"build": "tsc -p tsconfig.json"`, `"pretest": "npm run build"`, and `"test": "node --test dist/test/openrouter.test.js dist/test/model-selector.test.js dist/test/provenance.test.js dist/test/compatibility.test.js"`. Pin controller scripts to `"prebuild": "npm --prefix ../openrouter-free-selector run build"`, `"build": "tsc -p tsconfig.json"`, `"pretest": "npm run build"`, `"test": "node --test dist/test/core.test.js dist/test/codex-app-server-client.test.js dist/test/provider-decision.test.js dist/test/provider-flow.test.js dist/test/compatibility-probe.test.js"`, and `"start": "node dist/src/index.js"`. The npm `prebuild`/`pretest` lifecycle is cross-platform and enforces order without shell chaining, a workspace, or bash orchestration. Controller `tsconfig.json` changes `rootDir` to `.` and includes `src/**/*.ts` and `test/**/*.ts`; selector compilation follows the same source/test layout.

Split the currently monolithic controller only where required for hermetic tests:

- `src/core.ts`: provider/phase/review-target parsing, thread eligibility, safe environment and config construction, error classification;
- `src/codex-app-server-client.ts`: exact one-process stdio JSON-RPC handshake, account/rate-limit/model requests, pagination, schema validation, deadline, and shutdown; no generic RPC framework;
- `src/provider-decision.ts`: pure primary classification, exact auto matrix, and safe provider-status report construction;
- `src/codex-executor.ts`: small injected SDK execution interface and production adapter;
- `src/compatibility-probe.ts`: exact bootstrap validation, fixed prompt, streamed-event evaluation, safe report, and post-run mutation check;
- `src/index.ts`: existing orchestration plus selector/provenance transaction wiring;
- `test/core.test.ts`, `test/codex-app-server-client.test.ts`, `test/provider-decision.test.ts`, `test/provider-flow.test.ts`, and `test/compatibility-probe.test.ts`: hermetic controller tests.

Do not create a generic provider interface beyond the minimal `CodexExecutor` test seam. Do not touch `src/print_engineer/**` or existing Python tests.

The selector package owns registry schema, validation, exact identity lookup, and timestamp policy in `src/compatibility.ts`; the controller alone invokes Codex in `src/compatibility-probe.ts`. Compatibility registry path is exact: `tools/openrouter-free-selector/config/codex-compatible-free-models-v1.json`. Schema:

```json
{
  "schema_version": 1,
  "entries": [
    {
      "model_id": "vendor/model:free",
      "codex_sdk_version": "0.147.0",
      "provider_id": "openrouter",
      "wire_api": "responses",
      "validated_at": "2026-08-19T00:00:00.000Z",
      "valid_until": "2026-09-18T00:00:00.000Z"
    }
  ]
}
```

Require plain objects, exact keys, root `schema_version: 1`, unique non-wildcard exact model IDs, `codex_sdk_version: "0.147.0"`, `provider_id: "openrouter"`, and `wire_api: "responses"`. `validated_at` and `valid_until` are exact UTC RFC3339 timestamps with `Z`; no local-time interpretation is allowed. Compatibility validity is a project-owned safety policy, not an OpenAI requirement, OpenRouter requirement, or model-provider guarantee. Its exact duration is 2,592,000 seconds (`30 * 24 * 60 * 60`), never calendar-month arithmetic: `valid_until = validated_at + 2,592,000 seconds` exactly. Accept only `validated_at <= now < valid_until`; `now == valid_until` is expired, and `now < validated_at` is invalid future evidence.

All four identity fields must match current runtime exactly: case-sensitive `model_id`, `provider_id=openrouter`, `wire_api=responses`, and the installed `@openai/codex-sdk` version. A runtime package-version change invalidates old evidence and requires a new probe. The registry means only “this exact model/provider/Codex version previously completed the required project probe.” It never certifies current price, availability, context, capabilities, expiration, or free status. The file is user-maintained; neither selector nor controller writes it. An empty entries array safely blocks normal fallback.

## Lockfile and Install Contract

Both lockfiles are task-owned and TRACKED outputs:

- `tools/openrouter-free-selector/package-lock.json`: CREATE and track.
- `tools/codex-controller/package-lock.json`: MODIFY/adopt the currently untracked local file, normalize it to the exact package manifest, and track it.

First Build workflow is exact. For the new selector package: create exact `package.json`, run `npm install` in `tools/openrouter-free-selector`, and retain the generated lockfile. For the existing controller: modify exact `package.json` first, run `npm install` in `tools/codex-controller`, and retain the matching lockfile. Then run selector build/test followed by controller build/test. Do not run `npm ci` before either matching lockfile exists. Later clean reproducible verification uses `npm ci`, then `npm test`, separately in each package, selector first. No other lockfile may change.

## Testing Strategy

All automated tests are hermetic: injected fetch/clock/filesystem/Git/Codex executor, fake process environments, no OpenRouter/Codex/OpenCode inference, no printer, GitHub, credentials, or hardware.

Preserve every applicable selector-plan pricing/order/provenance test. Add exact controller coverage for:

1. missing/empty `CODEX_PROVIDER_MODE` defaults to `auto`;
2. trimmed case-insensitive `auto`, `primary`, and `openrouter-free` are accepted and canonicalized;
3. every other provider value is rejected before execution;
4. app-server is spawned with the exact pinned Node/script/argv/environment and no shell;
5. exact headerless initialize/initialized/account/read/account/rateLimits/read/model/list request sequence, ids, pinned pagination params, five-second total deadline, and one-second shutdown;
6. malformed JSON/schema, JSON-RPC error, mismatched/duplicate id, repeated cursor, premature exit, missing method, and timeout become status unknown without stderr parsing or inference;
7. ChatGPT account + usable primary/secondary windows + complete `gpt-5.6-sol` entry selects primary;
8. quota reached enum, spend-control reached, or exhausted window selects OpenRouter fallback;
9. unauthenticated, API-key, Bedrock, unknown, or malformed account forbids primary and selects safe fallback in auto;
10. missing/malformed quota status selects safe fallback in auto;
11. missing/malformed/incomplete preferred-model list selects safe fallback in auto;
12. manual primary fails for every auth/quota/model unavailable or unknown state and never falls back;
13. manual OpenRouter skips the app-server decision entirely;
14. auto primary requires no `OPENROUTER_API_KEY`, does not import/call the OpenRouter selector, registry, catalog, or preflight, and passes exact `gpt-5.6-sol`;
15. auto fallback calls the existing selector/registry/preflight chain; missing key or unavailable compatible verified-free model stops;
16. primary environment strips all API keys and proves subscription path rather than paid OpenAI API usage;
17. no inference/thread/turn is created to detect auth, quota, or model availability;
18. no arbitrary stdout/stderr quota text influences selection;
19. provider/model decision is immutable after execution starts;
20. mid-run primary failure stops without OpenRouter continuation;
21. a next invocation can select fallback after exhaustion and primary after reset;
22. provider change and exact fallback-model change force fresh threads while same eligible identity may resume;
23. the same durable worktree is preserved across both handoffs with no transcript, hidden reasoning, session, or auth migration;
24. fresh plan approval/review and mixed-provider provenance rules preserve producer exclusion and actual artifact/state inspection;
25. exact fallback provider object, base URL, Responses wire, retry zeroes, exact model ID, and no `openrouter/` prefix;
26. paid, unknown, malformed, ambiguous, router, and registry-absent/stale/version-mismatch fallback rejection;
27. exact safe environment allowlists; secrets absent from logs/prompts/errors/provenance;
28. `--provider-status` performs no inference, Git operation/mutation, provenance/registry write, compatibility probe, OpenRouter call, publication, printer, or hardware access;
29. provider-status exact output/exit behavior and conditional key-presence check;
30. compatibility-probe remains mutually exclusive and independent;
31. no OpenCode spawn/import and no paid/backend/router/candidate-two switch;
32. safe decision logging contains only provider mode/decision, canonical phase, public model id, optional `verified_free=true`, and optional boolean quota availability—never credentials, environment, raw account response, prompts, or task contents.

Retain the prior exact tests for select-only behavior, PLAN/BUILD success provenance, failure non-replacement, strict plan status/path, Windows spaced paths, package build order, registry schema/freshness, fixed failure metadata, and synthetic-secret redaction.

Add the catalog-filter regression coverage for the corrected OpenRouter request. Hermetic selector tests must prove that the coding/agentic BUILD/PLAN catalog request includes `supported_parameters=tools`, `input_modalities=text`, `output_modalities=text`, `context=32768`, `max_price=0`, `max_output_price=0`, `sort=coding-high-to-low`, `min_coding_index=0`, and `min_agentic_index=0`; omits `category=programming`; never sends `category` together with `supported_parameters`; and retains the existing no-limit/no-offset query shape. They must continue to enforce `data.length === total_count`, server-order authority, first locally qualified candidate selection, exact free/capability/context checks, and all existing strict zero-cost and preflight rules.

Revise the selector pricing tests for the corrected optional-dimension schema. They must prove that prompt-plus-completion lexical zero qualifies with `request` and `image` absent; every recognized optional scalar key is allowed when absent, allowed only when present as a strict lexical-zero string, and rejected when present nonzero; missing prompt or completion, malformed/non-string/null/negative/ambiguous/NaN-like values, unknown keys, non-scalar structures, and `discount` fail closed. Include an explicit passing fixture `{prompt:"0",completion:"0",overrides:[]}`: `prompt` and `completion` exist and are strict lexical zero, `overrides` is present and exactly an empty array, and therefore no conditional pricing rule exists. Require explicit fail-closed cases for any non-empty `overrides` array without interpreting whether an individual override appears zero-priced, including a non-empty object-shaped example, and for malformed/non-array values such as `null`, `{}`, and `"[]"`. Include the live-style `z-ai/glm-5.2:free` fixture with exactly `{prompt:"0",completion:"0"}` and valid text/tools/context metadata, without adding that model as a selector preference, default, registry shortcut, or replacement target. The tests must also prove catalog and exact-model preflight use the same pricing qualification, preserve exact `:free` identity enforcement, and stop before Codex for paid, unknown, or malformed pricing.

Add hermetic failure regressions proving that an HTTP 400 catalog response fails closed, performs no inference, does not automatically attempt another catalog query or a category-only retry, and does not try candidate/model fallback. The compatibility-probe tests must prove that its one exact user-specified candidate must be present in the current corrected qualifying catalog response; an absent candidate, including the previously chosen `nex-agi/nex-n2-pro:free` when absent, fails before Codex execution without grandfathering or hard-coding a replacement model.

The mandatory app-server tests use only an injected fake child process and protocol stream; no real Codex process is permitted. They prove: initialize is exactly `{"id":1,"method":"initialize","params":{"clientInfo":{"name":"print_engineer_codex_controller","title":"Print Engineer Codex Controller","version":"0.1.0"},"capabilities":null}}` with no `jsonrpc`; initialized is exactly `{"method":"initialized"}` with no `jsonrpc`, `id`, or `params`; account/read is exactly `{"id":2,"method":"account/read","params":{"refreshToken":false}}` with no `jsonrpc`; account/rateLimits/read is exactly `{"id":3,"method":"account/rateLimits/read"}` with no `jsonrpc`; and every model/list request has no `jsonrpc` and uses only the pinned `includeHidden`, `limit`, and optional `cursor` pagination fields. Valid initialize, account, rate-limit, and model/list responses without `jsonrpc` succeed; missing `jsonrpc` alone never produces `PRIMARY_STATUS_PROTOCOL_ERROR`; an unexpected top-level `jsonrpc` is tolerated because the pinned serde envelope does not deny unknown fields. They retain strict request/response distinction and prove ID mismatch, result/error non-exclusivity, malformed method-specific response, and malformed notification each fail closed. No `account/read` is sent before a valid successful initialize result with numeric id `1`; mismatched initialize response id, initialize error, or malformed initialize response each fails closed. The existing premature-exit, malformed JSON, timeout, sequence, deadline, and shutdown cases remain mandatory.

Add an explicit provider-status regression test using a hermetic app-server stream in the exact pinned 0.147.0 headerless format: `initialize → initialized → account/read → account/rateLimits/read → model/list` must reach `classifyPrimary` and must not return `PRIMARY_STATUS_PROTOCOL_ERROR` solely because the responses omit `jsonrpc`.

The mandatory model-list tests include: a valid preferred model; absence of the preferred model; a record with missing, non-string, or mismatched `id`; a record with missing, non-string, or mismatched `model`; non-array `inputModalities`; non-string modality members; and a preferred record without exact `"text"`. They also include literal duplicate-ID cases: two structurally identical validated records with the same exact ID in one page are collapsed and produce the same qualification as one record; structurally identical records repeated across two separate pagination pages are likewise collapsed; conflicting records with the same exact ID in one page fail closed; and conflicting records with the same exact ID across two separate pagination pages fail closed. Provider-status and normal provider decision must consume this same client result, so neither may independently reinterpret duplicates or invalid records.

The mandatory PlanType table tests every current pinned value and one future value. With every other auth, quota, and model predicate passing, each allowed literal `go`, `plus`, and `pro` may authorize primary. Each excluded literal `free`, `prolite`, `team`, `self_serve_business_prolite`, `self_serve_business_usage_based`, `business`, `ent26`, `enterprise_cbp_automation`, `enterprise_cbp_usage_based`, `enterprise`, `edu`, and `unknown` is rejected. An unrecognized future string such as `future_subscription_tier` is rejected. Separate cases reject absent account, non-`chatgpt` account, missing PlanType, and `requiresOpenaiAuth` values `false`, `null`, missing, or any non-boolean truthy value. These cases must make an “anything except free,” non-empty-string, substring, truthiness, or recognized-enum implementation fail.

The mandatory routing matrix tests AUTO with `free`, `unknown`, each usage-based-unapproved literal, every other excluded current PlanType, and an unrecognized future string: each makes primary unavailable and attempts only verified-free OpenRouter; if OpenRouter cannot be safely used, execution stops with no paid fallback. The same PlanType cases under manual PRIMARY stop and never attempt OpenRouter. Provider-status repeats these classifications through the shared predicate and reports `primary_plan_supported=false` without exposing the account or PlanType.

Mandatory probe/registry coverage is exact and hermetic. The compatibility
probe is the bootstrap exception: it tests one currently unproven exact model
and therefore bypasses the existing-registry read gate. It must not read,
validate, create a validity window for, write, or otherwise mutate the
compatibility registry on success, rate limiting, or failure. A successful
probe returns safe evidence only; manual registry addition remains a separate
user action. `COMPATIBILITY_PROBE_RATE_LIMITED` leaves compatibility
`UNPROVEN` and produces no registry evidence. Ordinary OpenRouter fallback
continues to require the existing compatibility-registry gate exactly as
before. If the compatibility-probe API carries a `registryPath` parameter
without a genuine non-probe responsibility, the corrective Build must remove
that parameter from the probe interface, call sites, and tests; it must not add
an artificial registry read merely to exercise it.

1. probe executes without a prior registry entry;
2. normal fallback cannot execute without one;
3. probe requires `OPENROUTER_API_KEY`;
4. probe requires exact `CODEX_COMPATIBILITY_MODEL`;
5. paid model is rejected;
6. unknown pricing is rejected;
7. a model without `:free` is rejected;
8. `openrouter/free` is rejected;
9. immediate zero-cost preflight is required;
10. preflight failure prevents Codex start;
11. exactly one requested model is passed;
12. candidate two is never attempted;
13. a fresh Codex thread is used;
14. the exact OpenRouter provider configuration is used;
15. `sandboxMode: "read-only"` is used;
16. the exact controller-owned prompt is used;
17. arbitrary user task text cannot replace or extend it;
18. the pinned completed command event is required for success;
19. a missing command event fails;
20. a failed/nonzero command event fails;
21. provider/stream failure fails;
22. missing/failed final completion fails;
23. changed post-probe Git status fails;
24. probe never writes producer provenance;
25. probe never writes the compatibility registry;
26. safe evidence omits the API key and environment;
27. safe evidence omits prompt, task contents, and full model response;
28. normal-fallback registry validity calculation is exactly `+2,592,000` seconds;
29. `now == valid_until` is expired;
30. future `validated_at` is rejected; and
31. exact SDK-version mismatch rejects normal fallback evidence.

Add mandatory hermetic probe regressions proving: structured `http_status_code=429`
returns `COMPATIBILITY_PROBE_RATE_LIMITED`; the probe performs no registry
read-gate, write, validity-window creation, or mutation and leaves the model
compatibility `UNPROVEN`; no automatic retry occurs; the executor invocation
count is exactly one; candidate two is never selected; 400, 401, 402, 403, and
representative 5xx failures remain `COMPATIBILITY_PROBE_FAILED`; transport and
unknown failures remain failed; text containing `429` without trustworthy
structured HTTP status does not become rate limited; successful compatibility
behavior returns safe evidence only and performs no automatic registry write;
failed compatibility behavior performs no registry write; existing
command-execution requirements remain unchanged; ordinary fallback still uses
the compatibility-registry gate; and `z-ai/glm-5.2:free` is not added to the
registry. Do not create an unrelated temporary registry file merely to assert
that it remains unchanged. No test performs a real OpenRouter call.

Extend those hermetic tests to require: useful SDK events followed by terminal
failure preserve partial events and terminal-error metadata without producing a
successful `ExecutionResult`; preserved AGENTS.md command completion with exit
code zero and tool output but no final completion is not probe success; an
exact current-thread rollout with structured 429 is rate limited; an exact
current-thread rollout with non-429 is failed; a stale different-thread 429, a
newer unrelated 429, a same-model different-thread 429, and a same-worktree
different-thread 429 are ignored and fail closed; newest-rollout selection is
never used; exact thread ID with wrong provider or worktree correlation fails;
missing, corrupt, or unreadable rollout fails; rollout message text containing
`429` without structured status fails; and no prompt, command output, AGENTS.md
contents, secret, header, unrelated response, or complete rollout JSON leaks
through production diagnostics. Prove that normal successful compatibility
behavior is unchanged, no real OpenRouter call occurs, no automatic retry or
model switch occurs, registry interaction remains `NONE`, and executor
invocation count remains one. All rollout fixtures must be hermetic and bound
to the exact current thread; no repository-global or worktree-global session
discovery is permitted.

All use injected catalog/preflight, clock, Git, filesystem, and Codex stream fakes. There is no real network, Codex, inference, printer, or hardware access.

Mandatory Build verification is package-focused: build/test `tools/openrouter-free-selector`, then build/test `tools/codex-controller`. TypeScript/Markdown scope makes Ruff/Mypy inapplicable. Do not run broad repository suites.

## Optional Live Compatibility Probe

No real compatibility probe is performed during this plan correction, approval
review, Build, or implementation review. Only after Build and independent
implementation review, as a separate explicit decision, may the user set the
required environment and run `npm start -- --compatibility-probe` once with one
currently verified-free exact model. This is optional/manual, never CI. A
successful safe report may be used by the user to manually create/update the
registry entry; it never self-registers.

## Exact Build Scope

The revised Build scope is exactly 31 paths: 25 CREATE and 6 MODIFY. No wildcard or discretionary path is permitted.

Create exactly:

1. `tools/openrouter-free-selector/.gitignore`
2. `tools/openrouter-free-selector/package.json`
3. `tools/openrouter-free-selector/package-lock.json`
4. `tools/openrouter-free-selector/tsconfig.json`
5. `tools/openrouter-free-selector/src/openrouter.ts`
6. `tools/openrouter-free-selector/src/model-selector.ts`
7. `tools/openrouter-free-selector/src/provenance.ts`
8. `tools/openrouter-free-selector/src/compatibility.ts`
9. `tools/openrouter-free-selector/src/index.ts`
10. `tools/openrouter-free-selector/test/openrouter.test.ts`
11. `tools/openrouter-free-selector/test/model-selector.test.ts`
12. `tools/openrouter-free-selector/test/provenance.test.ts`
13. `tools/openrouter-free-selector/test/compatibility.test.ts`
14. `tools/openrouter-free-selector/config/codex-compatible-free-models-v1.json`
15. `tools/openrouter-free-selector/README.md`
16. `tools/codex-controller/src/core.ts`
17. `tools/codex-controller/src/codex-app-server-client.ts`
18. `tools/codex-controller/src/provider-decision.ts`
19. `tools/codex-controller/src/codex-executor.ts`
20. `tools/codex-controller/src/compatibility-probe.ts`
21. `tools/codex-controller/test/core.test.ts`
22. `tools/codex-controller/test/codex-app-server-client.test.ts`
23. `tools/codex-controller/test/provider-decision.test.ts`
24. `tools/codex-controller/test/provider-flow.test.ts`
25. `tools/codex-controller/test/compatibility-probe.test.ts`

Modify exactly:

26. `tools/codex-controller/package.json`
27. `tools/codex-controller/package-lock.json`
28. `tools/codex-controller/tsconfig.json`
29. `tools/codex-controller/src/index.ts`
30. `tools/codex-controller/README.md`
31. `tools/codex-controller/SMOKE_TEST.md`

Do not modify this plan during Build, either predecessor plan, `AGENTS.md`, `CODEX_OPTIMIZATION.md`, `.opencode/agents/*`, anything under `tools/model-runner/`, root package/config, `src/print_engineer/**`, Python tests, printer/MQTT/hardware files, or unrelated dirty-tree state. Do not delete, rename, document, or otherwise modify OpenCode components.

Implementation order is fixed: create exact selector manifest; run selector `npm install`; build/test selector; normalize controller manifest; run controller `npm install`; refactor controller into testable core without behavior change; add provider/role/thread parsing; add the pinned app-server client and pure provider decision; integrate invocation-start auto selection; integrate selection/registry/preflight; add the supported SDK execution, provider-status command, and dedicated compatibility probe; add provenance transactions/select-only; update controller docs; run focused builds/tests; inspect the exact 31-path diff.

## Deferred Cleanup

Defer deletion of `tools/model-runner`, `.opencode/agents/*`, OpenCode documentation, legacy dependencies/configuration, and Git-common-dir path renaming. Also defer background quota polling, mid-execution switching/rescue, persistent provider profiles, generic provider abstractions, registry automation, paid models, other routers, model benchmarking, cloud/BYOK fallback, and any printer/product changes.

A later approved cleanup may remove the inactive OpenCode path only after Codex/OpenRouter fallback and independent review are complete.

## Approval Questions

1. One Codex agent/three provider modes with auto default — RESOLVED.
2. SDK versus CLI — RESOLVED: `SDK_SUPPORTED` on pinned 0.147.0.
3. Runtime provider configuration — RESOLVED: public SDK `config`; no persistent config.
4. Provider URL/auth/wire — RESOLVED: `https://openrouter.ai/api/v1`, `OPENROUTER_API_KEY`, `responses`.
5. Model syntax — RESOLVED: exact `vendor/model:free` without OpenCode prefix.
6. Controller phases/approval role — RESOLVED: keep phases; explicit review target.
7. Thread identity/handoffs — RESOLVED: provider+model+role identity; durable worktree only.
8. Selector location — RESOLVED: narrow shared package because selector code is not yet built.
9. Compatibility proof gap — RESOLVED: dedicated exact-model probe bootstraps evidence without weakening the normal fail-closed registry gate.
10. Selector/provenance baseline — RESOLVED: preserved without scoring/order/hash redesign.
11. Primary provenance — RESOLVED: retain fresh primary review; no external-model provenance burden.
12. Existing OpenCode — RESOLVED: inactive/deprecated, not deleted in this increment.
13. Select-only/failure/no retry — RESOLVED.
14. Machine-readable primary status — RESOLVED: pinned 0.147.0 app-server stdio JSON-RPC, exact methods/schema/deadlines/fail-closed behavior.
15. Preferred primary model — RESOLVED: exact account-visible `gpt-5.6-sol`; otherwise verified-free fallback in auto and stop in manual primary.
16. Provider status diagnostic — RESOLVED: inference-free, read-only, no Git/provenance/probe side effects.
17. Exact files/tests/probe/deferred cleanup — RESOLVED: 31 paths, 25 CREATE and 6 MODIFY.

No material Build choice remains apart from re-approval of this OpenRouter
multi-agent compatibility correction. Normal fallback requires one current
manually entered compatibility record. The dedicated probe is the only
bootstrap exception and remains verified-free, single-model, read-only,
non-publishing, and `$0.00`.

## Final Verdict

PROPOSED — OpenRouter multi-agent compatibility correction pending re-approval
