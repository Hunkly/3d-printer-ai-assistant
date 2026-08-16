# Print Engineer

Local AI-powered assistant for the Bambu Lab A1 (and compatible) 3D printer.

The assistant is designed to help with the full print workflow: pick a model, analyze it,
slice it with Bambu Studio / OrcaSlicer, verify the settings against policy, send the print to
the printer over the local network, and learn from the outcome to make better recommendations.

## Status

Phase 0 is in place: project skeleton, configuration, logging, core interfaces, policy
stub, and an MCP server foundation.

Phase 1 (Slicer Gateway) is implemented: a common `Slicer` interface with two adapters
(OrcaSlicer — full slicing; Bambu Studio — detection/validation, slicing disabled until an
upgrade is verified), profile discovery + materialization, structured errors, hard process
timeouts, and `slicer.*` MCP tools.

- Printer integration (LAN MQTT, Bambu Lab A1): **Phase 2+**
- Slicer integration (Bambu Studio / OrcaSlicer CLI): **Phase 1 (done)**
- Model analysis (trimesh): **Phase 3**
- Print history + recommendations: **Phase 3+**

## Slicer support

Detected on this machine and reported by `print-engineer --info`:

| Slicer | Version | Slicing |
| --- | --- | --- |
| OrcaSlicer | 2.3.2 (registry/DLL scan) | Working |
| Bambu Studio | 02.06.00.51 (CLI banner) | Unavailable |

Known limitations (Phase 1):

- Bambu Studio `--slice` crashes on this machine (access violation in `BambuStudio.dll`
  during printer extruder setup, confirmed on 2.6.0.51), so `slicer.slice` on
  `bambu_studio` reports a structured `slicer_unavailable` error instead of launching a
  broken operation. Detection, profile discovery, and model validation still work. Slicing
  is enabled only after upgrading to 2.7.1.62 (the version your own 3mf projects use) and
  re-verifying.
- OrcaSlicer `--version` is not a supported flag and `--help` crashes on this machine, so
  its version is read from the registry and the DLL; Bambu Studio's version is parsed from
  the `--help` banner.
- 3mf projects saved by a newer slicer (e.g. Bambu Studio 2.7.1.62) are rejected by the
  installed CLIs and surface as `version_mismatch` rather than a raw CLI error.

Slicing artifacts (gcode, `.gcode.3mf`, intermediate profiles) are written to the runtime
workspace and parsed for estimated time, layer count, max Z, filament length/volume, density,
and weight. User profile stores (`%APPDATA%\OrcaSlicer`, `%APPDATA%\BambuStudio`) are read
only; user deltas are materialized in-memory against their inheritance chain.

## MCP slicer tools

`system.info`, `system.health`, `slicer.list`, `slicer.info`, `slicer.validate`,
`slicer.slice`. Every `slicer.*` tool returns `{ok: true, ...}` on success and
`{ok: false, error: {code, message, details}}` on failure, with stable error codes
(`slicer_not_installed`, `unsupported_slicer_version`, `slicer_unavailable`,
`invalid_model`, `invalid_profile`, `slice_failed`, `slice_timeout`, `version_mismatch`).

## Prerequisites

- Windows 10/11
- [uv](https://docs.astral.sh/uv/) (Python 3.12 is provisioned automatically by `uv sync`)

## Development

```powershell
uv sync --extra dev   # create .venv + install project (dev extras)
uv run pytest         # run the test suite (hermetic + real-slicer integration)
uv run ruff check .   # lint
uv run mypy           # type check
```

Tests in `tests/unit/test_orca_adapter.py` and friends are hermetic (fake executables and
processes, no real slicer). `tests/integration/test_slicer_cli_probe.py` and
`tests/integration/test_orca_slice.py` probe the real installed slicers and skip cleanly
when a slicer is absent; `test_orca_slice.py` slices a generated 20 mm cube end to end and
asserts the resulting `plate_1.gcode` and `.gcode.3mf` are parsed correctly.

## Usage

```powershell
uv run print-engineer --info              # print environment / settings summary
uv run print-engineer-mcp --check         # verify the MCP server starts (non-blocking)
uv run print-engineer-mcp                 # run the MCP server (stdio transport)
```

## Configuration

Copy `config/config.example.yaml` to `config/config.yaml` (runtime) or
`config/config.local.yaml` (gitignored) and adjust values. Secrets go into `.env`
(see `.env.example`).

Runtime data (databases, logs, workspaces) is written under `runtime/`, which is
gitignored.

## Architecture

```
src/print_engineer/
├── core/
│   ├── types.py                 # shared dataclasses + enums
│   ├── interfaces/              # Printer, Slicer, ModelAnalyzer, PrintHistory ABCs
│   └── policy.py                # SafetyPolicy: gates printer actions
├── adapters/
│   ├── printer/                 # Bambu Lab LAN MQTT adapter (Phase 2+)
│   └── slicer/                  # Phase 1: base + OrcaSlicer/BambuStudio adapters,
│                                #   profile discovery/materialization, gcode parsing,
│                                #   version detection, process/timeout handling
├── mcp/                         # MCP server exposed to the AI (OpenCode)
├── config.py                    # settings loading (YAML + .env)
├── logging_setup.py             # stderr logging + JSON audit log
└── errors.py                    # typed error hierarchy
```

The AI talks to the assistant over MCP (`system.*`, later `printer.*`, `slicer.*`,
`analysis.*`, `history.*`). All physical printer actions go through `SafetyPolicy`
so the model can never send commands to the printer unchecked.
