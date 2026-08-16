"""CLI entry point (``print-engineer``)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from print_engineer import __version__
from print_engineer.adapters.llm.ollama import build_llm_client
from print_engineer.adapters.model.analyzer import TrimeshModelAnalyzer, model_analysis_to_dict
from print_engineer.adapters.slicer.registry import SlicerRegistry
from print_engineer.config import Settings
from print_engineer.core.recommendation import (
    PrintContextIntent,
    RecommendationGoal,
    RecommendationRequest,
    SetupRequest,
)
from print_engineer.errors import LLMError, SlicerError
from print_engineer.recommendation.context import PrintContextResolver
from print_engineer.recommendation.engine import RecommendationEngine
from print_engineer.recommendation.filament import FilamentMatrixBuilder
from print_engineer.recommendation.setup import SetupEngine


def _detect_slicers(settings: Settings) -> list[dict[str, object]]:
    registry = SlicerRegistry(
        settings=settings,
        workdir=settings.storage.workspace_dir,
        timeout_seconds=settings.slicer.timeout_seconds,
    )
    detected: list[dict[str, object]] = []
    for info in registry.detect_all().values():
        detected.append(
            {
                "kind": info.kind.value,
                "name": info.name,
                "executable": str(info.executable),
                "version": info.version,
                "version_source": info.version_source,
                "slicing_supported": info.slicing_supported,
                "notes": list(info.notes),
            }
        )
    return detected


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="print-engineer", description="Local AI 3D printing assistant"
    )
    parser.add_argument("--version", action="store_true", help="Print version and exit.")
    parser.add_argument(
        "--info", action="store_true", help="Print environment/settings summary as JSON."
    )
    parser.add_argument("--config", help="Path to a config YAML file.")

    subparsers = parser.add_subparsers(dest="command")
    analyze_parser = subparsers.add_parser(
        "analyze", help="Analyze an STL or 3MF model (deterministic geometry)."
    )
    analyze_parser.add_argument("model", help="Path to the STL or 3MF model file.")
    analyze_parser.add_argument(
        "--overhang-threshold",
        type=float,
        default=None,
        help="Overhang threshold in degrees from vertical (default from config, 45).",
    )

    recommend_parser = subparsers.add_parser(
        "recommend",
        help=(
            "Recommend print settings for a model from measured geometry, current "
            "slicer settings, and optional LLM reasoning (read-only)."
        ),
    )
    recommend_parser.add_argument("model", help="Path to the STL or 3MF model file.")
    recommend_parser.add_argument(
        "--slicer", default=None, help="Slicer kind (default from config, orca_slicer)."
    )
    recommend_parser.add_argument("--process", default=None, help="Process profile name.")
    recommend_parser.add_argument("--filament", default=None, help="Filament profile name.")
    recommend_parser.add_argument("--printer", default=None, help="Printer (machine) profile name.")
    recommend_parser.add_argument(
        "--goal",
        default=None,
        help=(
            "Optimization goal: surface_quality, strength, print_time, "
            "filament_usage, or balanced (default from config)."
        ),
    )
    recommend_parser.add_argument(
        "--max-time", type=float, default=None, help="Hard constraint: max print time (minutes)."
    )
    recommend_parser.add_argument(
        "--max-filament", type=float, default=None, help="Hard constraint: max filament (grams)."
    )
    recommend_parser.add_argument(
        "--overhang-threshold",
        type=float,
        default=None,
        help="Overhang threshold in degrees from vertical (default from config, 45).",
    )
    recommend_parser.add_argument(
        "--slice",
        action="store_true",
        help="Slice on demand to gather real statistics (requires process, filament, printer).",
    )
    recommend_parser.add_argument(
        "--no-llm", action="store_true", help="Skip LLM reasoning; use deterministic rules only."
    )

    filaments_parser = subparsers.add_parser(
        "filaments",
        help=(
            "Enumerate and rank locally-installed filament profiles for a resolved print "
            "context (read-only, never slices)."
        ),
    )
    _add_context_args(filaments_parser)
    filaments_parser.add_argument(
        "--vendor", default=None, help="Only keep filaments from this vendor."
    )
    filaments_parser.add_argument(
        "--material", default=None, help="Only keep filaments of this material type (e.g. PLA)."
    )

    setup_parser = subparsers.add_parser(
        "setup",
        help=(
            "Four-layer setup recommendation: material, filament, nozzle, and process. "
            "Read-only: never slices, applies nothing, and never touches the printer."
        ),
    )
    _add_context_args(setup_parser)
    setup_parser.add_argument(
        "--vendor", default=None, help="Only consider filaments from this vendor."
    )
    setup_parser.add_argument(
        "--material", default=None, help="Only consider filaments of this material type (e.g. PLA)."
    )
    setup_parser.add_argument(
        "--process", default=None, help="Process profile name to anchor the process layer."
    )
    setup_parser.add_argument(
        "--filament", default=None, help="Filament profile name to anchor the filament context."
    )
    setup_parser.add_argument(
        "--no-llm", action="store_true", help="Skip LLM narrative; use the deterministic result."
    )
    return parser


def _add_context_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--slicer", default=None, help="Slicer kind (default from config, orca_slicer)."
    )
    parser.add_argument(
        "--printer",
        default=None,
        help=(
            "Printer to use: an exact machine profile name (e.g. 'Bambu Lab A1 0.4 nozzle') "
            "or a printer model name (e.g. 'Bambu Lab A1')."
        ),
    )
    parser.add_argument(
        "--nozzle",
        type=float,
        default=None,
        help="Nozzle diameter in mm (acts as a compatibility filter; for setup, a dimension).",
    )
    parser.add_argument(
        "--plate", default=None, help="Build plate type, e.g. 'cool plate' or 'textured plate'."
    )
    parser.add_argument(
        "--goal",
        default=None,
        help=(
            "Optimization goal: surface_quality, strength, print_time, "
            "filament_usage, or balanced (default from config)."
        ),
    )
    parser.add_argument(
        "--use-defaults",
        action="store_true",
        help=(
            "Explicitly allow configured default printer/nozzle/plate to be used when the "
            "caller did not select one (never silent)."
        ),
    )


def _run_analyze(args: argparse.Namespace, settings: Settings) -> int:
    threshold = (
        args.overhang_threshold
        if args.overhang_threshold is not None
        else settings.analysis.default_overhang_threshold_degrees
    )
    try:
        analysis = TrimeshModelAnalyzer().analyze(Path(args.model), threshold)
    except SlicerError as exc:
        print(json.dumps({"ok": False, "error": exc.to_dict()}, indent=2))
        return 1
    print(json.dumps({"ok": True, "analysis": model_analysis_to_dict(analysis)}, indent=2))
    return 0


def _run_recommend(args: argparse.Namespace, settings: Settings) -> int:
    try:
        request = RecommendationRequest(
            model_path=Path(args.model),
            slicer_kind=args.slicer or settings.recommend.default_slicer,
            process_profile=args.process,
            filament_profile=args.filament,
            printer_profile=args.printer,
            goal=cast(RecommendationGoal, args.goal or settings.recommend.default_goal),
            overhang_threshold_degrees=(
                args.overhang_threshold
                if args.overhang_threshold is not None
                else settings.analysis.default_overhang_threshold_degrees
            ),
            max_time_minutes=args.max_time,
            max_filament_g=args.max_filament,
            slice_on_demand=args.slice,
        )
        llm = None if args.no_llm else build_llm_client(settings.llm)
        engine = RecommendationEngine(settings, llm=llm)
        result = engine.recommend(request)
    except ValidationError as exc:
        error = SlicerError(
            "invalid recommendation request",
            details={"validation_errors": str(exc)[:500]},
        )
        print(json.dumps({"ok": False, "error": error.to_dict()}, indent=2))
        return 1
    except (SlicerError, LLMError) as exc:
        print(json.dumps({"ok": False, "error": exc.to_dict()}, indent=2))
        return 1
    print(json.dumps({"ok": True, "recommendations": result.model_dump(mode="json")}, indent=2))
    return 0


def _run_filaments(args: argparse.Namespace, settings: Settings) -> int:
    try:
        intent = PrintContextIntent(
            slicer_kind=args.slicer or settings.recommend.default_slicer,
            printer=args.printer,
            nozzle_diameter_mm=args.nozzle,
            build_plate=args.plate,
            use_defaults=args.use_defaults,
        )
        resolver = PrintContextResolver(settings)
        resolved = resolver.resolve(intent)
        adapter = resolver.adapter(intent.slicer_kind)
        matrix = FilamentMatrixBuilder(settings, adapter).build(
            resolved,
            goal=cast(RecommendationGoal, args.goal or settings.recommend.default_goal),
            vendor=args.vendor,
            material=args.material,
        )
    except ValidationError as exc:
        error = SlicerError(
            "invalid filaments request",
            details={"validation_errors": str(exc)[:500]},
        )
        print(json.dumps({"ok": False, "error": error.to_dict()}, indent=2))
        return 1
    except (SlicerError, LLMError) as exc:
        print(json.dumps({"ok": False, "error": exc.to_dict()}, indent=2))
        return 1
    print(json.dumps({"ok": True, "matrix": matrix.model_dump(mode="json")}, indent=2))
    return 0


def _run_setup(args: argparse.Namespace, settings: Settings) -> int:
    try:
        request = SetupRequest(
            slicer_kind=args.slicer or settings.recommend.default_slicer,
            printer=args.printer,
            nozzle_diameter_mm=args.nozzle,
            build_plate=args.plate,
            process_profile=args.process,
            filament_profile=args.filament,
            use_defaults=args.use_defaults,
            goal=cast(RecommendationGoal, args.goal or settings.recommend.default_goal),
            vendor=args.vendor,
            material=args.material,
            use_llm=not args.no_llm,
        )
        llm = None if args.no_llm else build_llm_client(settings.llm)
        engine = SetupEngine(settings, llm=llm)
        result = engine.recommend(request)
    except ValidationError as exc:
        error = SlicerError(
            "invalid setup request",
            details={"validation_errors": str(exc)[:500]},
        )
        print(json.dumps({"ok": False, "error": error.to_dict()}, indent=2))
        return 1
    except (SlicerError, LLMError) as exc:
        print(json.dumps({"ok": False, "error": exc.to_dict()}, indent=2))
        return 1
    print(json.dumps({"ok": True, "setup": result.model_dump(mode="json")}, indent=2))
    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.version:
        print(__version__)
        return 0

    settings = Settings.load(args.config)

    if args.command == "analyze":
        return _run_analyze(args, settings)

    if args.command == "recommend":
        return _run_recommend(args, settings)

    if args.command == "filaments":
        return _run_filaments(args, settings)

    if args.command == "setup":
        return _run_setup(args, settings)

    if args.info:
        summary: dict[str, object] = {
            "version": __version__,
            "root": str(settings.root),
            "storage": {key: str(value) for key, value in settings.storage.model_dump().items()},
            "logging": {key: str(value) for key, value in settings.logging.model_dump().items()},
            "mcp": settings.mcp.model_dump(),
            "log_level": settings.app.log_level,
            "slicer": settings.slicer.model_dump(),
        }
        try:
            summary["slicers"] = _detect_slicers(settings)
        except Exception as exc:  # pragma: no cover - defensive
            summary["slicers"] = [{"error": str(exc)}]
        print(json.dumps(summary, indent=2))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
