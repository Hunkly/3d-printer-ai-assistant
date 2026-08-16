"""Compact LLM prompt builder for the recommendation engine.

The prompt contains only measured facts (with units), the already-computed
rule-based candidates, and the strict output contract. Raw slicer profile JSON
never enters the prompt. The facts block is written in a fixed phrasing that the
deterministic rules reuse for their ``evidence`` strings, so the LLM can quote
facts verbatim (this is also what the engine's evidence grounding check relies
on).
"""

from __future__ import annotations

from print_engineer.core.recommendation import (
    RECOMMENDABLE_SETTINGS,
    FilamentCandidate,
    ModelFacts,
    ProcessSettings,
    Recommendation,
    RecommendationInput,
    ResolvedPrintContext,
    SlicerSettingsDigest,
)


def candidate_facts(candidate: FilamentCandidate) -> list[str]:
    """Render one filament candidate's profile facts as verbatim lines.

    These lines are the ground truth the LLM may quote; the setup engine's
    grounding check requires at least one full line to appear verbatim.
    """
    lines: list[str] = [f"Filament profile = {candidate.profile_name}"]
    if candidate.vendor:
        lines.append(f"Vendor = {candidate.vendor}")
    if candidate.material_type:
        lines.append(f"Material = {candidate.material_type}")
    if candidate.density_g_cm3 is not None:
        lines.append(f"Density = {candidate.density_g_cm3:.2f} g/cm3")
    if candidate.max_volumetric_speed is not None:
        lines.append(f"Max volumetric speed = {candidate.max_volumetric_speed:.1f} mm3/s")
    if candidate.flow_ratio is not None:
        lines.append(f"Flow ratio = {candidate.flow_ratio:.3f}")
    if candidate.cost_per_kg is not None:
        lines.append(f"Cost = {candidate.cost_per_kg:.2f} per kg")
    if candidate.nozzle_temperature_c is not None:
        lines.append(f"Nozzle temperature = {candidate.nozzle_temperature_c:.0f} C")
    if candidate.nozzle_temperature_range_low_c is not None and (
        candidate.nozzle_temperature_range_high_c is not None
    ):
        lines.append(
            "Nozzle temperature range = "
            f"{candidate.nozzle_temperature_range_low_c:.0f}-"
            f"{candidate.nozzle_temperature_range_high_c:.0f} C"
        )
    if candidate.nozzle_temperature_initial_layer_c is not None:
        lines.append(
            "Initial layer nozzle temperature = "
            f"{candidate.nozzle_temperature_initial_layer_c:.0f} C"
        )
    if candidate.hot_plate_temperature_c is not None:
        lines.append(f"Hot plate temperature = {candidate.hot_plate_temperature_c:.0f} C")
    if candidate.textured_plate_temperature_c is not None:
        lines.append(
            f"Textured plate temperature = {candidate.textured_plate_temperature_c:.0f} C"
        )
    if candidate.cool_plate_temperature_c is not None:
        lines.append(f"Cool plate temperature = {candidate.cool_plate_temperature_c:.0f} C")
    if candidate.fan_max_speed is not None:
        lines.append(f"Max fan speed = {candidate.fan_max_speed:.0f}%")
    if candidate.soluble is not None:
        lines.append(f"Soluble = {candidate.soluble}")
    if candidate.required_nozzle_hrc:
        lines.append(f"Required nozzle hardness = {candidate.required_nozzle_hrc}")
    return lines


def _setup_context_lines(context: ResolvedPrintContext) -> list[str]:
    lines: list[str] = []
    lines.append(f"Slicer = {context.slicer_kind}")
    if context.printer is not None:
        printer = context.printer
        lines.append(f"Printer = {printer.name}")
        if printer.supported_nozzle_mm:
            joined = ", ".join(f"{nozzle:g}" for nozzle in printer.supported_nozzle_mm)
            lines.append(f"Supported nozzles = {joined} mm")
        if printer.printable_height_mm is not None:
            lines.append(f"Printable height = {printer.printable_height_mm:.1f} mm")
        if printer.default_print_profile:
            lines.append(f"Default process profile = {printer.default_print_profile}")
    if context.nozzle_diameter_mm is not None:
        lines.append(f"Nozzle diameter = {context.nozzle_diameter_mm:.2f} mm")
    if context.build_plate:
        lines.append(f"Build plate = {context.build_plate}")
    if context.process is not None:
        lines.append(f"Process profile = {context.process.name}")
        lines.extend(_process_lines(context.process))
    return lines


def setup_grounding_lines(
    context: ResolvedPrintContext, candidates: list[FilamentCandidate]
) -> list[str]:
    """Atomic fact lines used for LLM evidence-grounding checks."""
    lines: list[str] = _setup_context_lines(context)
    for candidate in candidates:
        lines.extend(candidate_facts(candidate))
    return lines


def build_setup_facts_text(
    context: ResolvedPrintContext, candidates: list[FilamentCandidate]
) -> str:
    """Render the grounded facts block for a setup recommendation."""
    sections: list[str] = []
    context_lines = _setup_context_lines(context)
    if context_lines:
        sections.append("\n".join(context_lines))
    for index, candidate in enumerate(candidates, start=1):
        sections.append(f"Candidate {index}: " + " | ".join(candidate_facts(candidate)))
    return "\n".join(sections)


def build_setup_prompt(
    context: ResolvedPrintContext,
    candidates: list[FilamentCandidate],
    goal: str,
) -> str:
    """Build the optional LLM narrative prompt for a setup recommendation."""
    facts = build_setup_facts_text(context, candidates)
    return (
        "You are an expert FDM 3D printing advisor. You explain a setup "
        "recommendation. You are read-only: you never recommend controlling a "
        "printer and never invoke a slicer.\n"
        "\n"
        "GROUNDED FACTS (use ONLY these; never invent values, materials, brands, "
        "or rankings):\n"
        f"{facts}\n"
        "\n"
        f"USER GOAL: {goal}\n"
        "\n"
        "TASK: write a short narrative (at most 4 sentences) that explains the "
        "already-computed recommendation. Keep the deterministic ranking intact. "
        "You may mention tradeoffs between the top-ranked candidates, but every "
        "numeric or material claim must be a verbatim quote of a GROUNDED FACTS "
        "line.\n"
        "\n"
        "OUTPUT JSON SCHEMA (ONLY the JSON object, no prose):\n"
        "{\n"
        '  "summary": "...",\n'
        '  "rationale": "...",\n'
        '  "warnings": ["..."]\n'
        "}\n"
    )


def _model_lines(model: ModelFacts) -> list[str]:
    lines: list[str] = []
    if model.dimensions_mm != (0.0, 0.0, 0.0):
        dims = model.dimensions_mm
        lines.append(f"Model dimensions = {dims[0]:.1f} x {dims[1]:.1f} x {dims[2]:.1f} mm")
    if model.volume_mm3 is not None:
        lines.append(f"Model volume = {model.volume_mm3:.1f} mm3")
    if model.surface_area_mm2:
        lines.append(f"Model surface area = {model.surface_area_mm2:.1f} mm2")
    if model.watertight is not None:
        lines.append(f"Watertight = {model.watertight}")
    if model.component_count is not None:
        lines.append(f"Component count = {model.component_count}")
    if model.height_mm is not None:
        lines.append(f"Model height = {model.height_mm:.1f} mm")
    if model.overhang_area_percent is not None:
        lines.append(f"Overhang area = {model.overhang_area_percent:.1f}% of surface area")
    if model.overhang_face_count is not None:
        lines.append(f"Overhang faces = {model.overhang_face_count}")
    if model.thin_wall_supported is True:
        if model.thin_wall_min_mm is not None:
            lines.append(f"Minimum wall thickness = {model.thin_wall_min_mm:.2f} mm")
        if model.thin_wall_median_mm is not None:
            lines.append(f"Median wall thickness = {model.thin_wall_median_mm:.2f} mm")
    else:
        lines.append("Thin-wall estimate = unavailable")
    return lines


def _process_lines(process: ProcessSettings) -> list[str]:
    fields = [
        ("Current layer height = {:.2f} mm", "layer_height_mm"),
        ("Initial layer height = {:.2f} mm", "initial_layer_height_mm"),
        ("Line width = {:.2f} mm", "line_width_mm"),
        ("Wall count = {}", "wall_loops"),
        ("Wall generator = {}", "wall_generator"),
        ("Infill density = {:.0f}%", "sparse_infill_percent"),
        ("Infill pattern = {}", "sparse_infill_pattern"),
        ("Top shell layers = {}", "top_shell_layers"),
        ("Bottom shell layers = {}", "bottom_shell_layers"),
        ("Supports enabled = {}", "enable_support"),
        ("Support type = {}", "support_type"),
        ("Support threshold angle = {:.0f} degrees", "support_threshold_angle_deg"),
        ("Support on build plate only = {}", "support_on_build_plate_only"),
        ("Current outer wall speed = {:.0f} mm/s", "outer_wall_speed_mms"),
        ("Inner wall speed = {:.0f} mm/s", "inner_wall_speed_mms"),
        ("Top surface speed = {:.0f} mm/s", "top_surface_speed_mms"),
        ("Sparse infill speed = {:.0f} mm/s", "sparse_infill_speed_mms"),
        ("Initial layer speed = {:.0f} mm/s", "initial_layer_speed_mms"),
        ("Detect thin wall = {}", "detect_thin_wall"),
        ("Spiral mode = {}", "spiral_mode"),
        ("Adaptive layer height = {}", "adaptive_layer_height"),
        ("Brim width = {:.1f} mm", "brim_width_mm"),
    ]
    lines: list[str] = []
    for template, field in fields:
        value = getattr(process, field)
        if value is None:
            continue
        lines.append(template.format(value))
    return lines


def _digest_lines(slicer: SlicerSettingsDigest | None) -> list[str]:
    lines: list[str] = []
    if slicer is None:
        return lines
    if slicer.printer is not None:
        printer = slicer.printer
        lines.append(f"Slicer = {slicer.slicer_kind}")
        lines.append(f"Printer profile = {printer.name}")
        if printer.nozzle_diameter_mm is not None:
            lines.append(f"Nozzle diameter = {printer.nozzle_diameter_mm:.2f} mm")
        if printer.printable_height_mm is not None:
            lines.append(f"Printable height = {printer.printable_height_mm:.1f} mm")
    if slicer.process is not None:
        lines.append(f"Process profile = {slicer.process.name}")
        lines.extend(_process_lines(slicer.process))
    if slicer.filament is not None:
        filament = slicer.filament
        lines.append(f"Filament profile = {filament.name}")
        if filament.material_type is not None:
            lines.append(f"Filament material = {filament.material_type}")
        if filament.density_g_cm3 is not None:
            lines.append(f"Filament density = {filament.density_g_cm3:.2f} g/cm3")
        if filament.max_volumetric_speed is not None:
            lines.append(
                f"Filament max volumetric speed = {filament.max_volumetric_speed:.1f} mm3/s"
            )
    return lines


def _stats_lines(input_: RecommendationInput) -> list[str]:
    lines: list[str] = []
    stats = input_.slice_stats
    if stats is None or not stats.available:
        return lines
    if stats.estimated_time_minutes is not None:
        lines.append(f"Measured print time = {stats.estimated_time_minutes:.1f} min")
    if stats.layer_count is not None:
        lines.append(f"Measured layer count = {stats.layer_count}")
    if stats.filament_used_cm3 is not None:
        lines.append(f"Measured filament volume = {stats.filament_used_cm3:.1f} cm3")
    if stats.filament_weight_g is not None:
        lines.append(f"Measured filament weight = {stats.filament_weight_g:.1f} g")
    return lines


def _constraint_lines(input_: RecommendationInput) -> list[str]:
    lines: list[str] = []
    if input_.max_time_minutes is not None:
        lines.append(f"Requested maximum print time = {input_.max_time_minutes:.1f} min")
    if input_.max_filament_g is not None:
        lines.append(f"Requested maximum filament weight = {input_.max_filament_g:.1f} g")
    return lines


def _candidate_lines(candidates: list[Recommendation]) -> list[str]:
    lines: list[str] = []
    for index, candidate in enumerate(candidates, start=1):
        current = candidate.current_value
        recommended = candidate.recommended_value
        current_text = (
            f"{current:.3g}" if isinstance(current, float) else str(current)
        )
        recommended_text = (
            f"{recommended:.3g}" if isinstance(recommended, float) else str(recommended)
        )
        evidence = "; ".join(candidate.evidence)
        lines.append(
            f"{index}. setting={candidate.setting} change={candidate.change.value} "
            f"current={current_text} recommended={recommended_text} "
            f"evidence=[{evidence}]"
        )
    return lines


def build_facts_text(input_: RecommendationInput) -> str:
    """Render the measured-facts block (also used for evidence grounding)."""
    sections: list[str] = []
    model_lines = _model_lines(input_.model)
    if model_lines:
        sections.append("\n".join(model_lines))
    if input_.slicer is not None:
        digest_lines = _digest_lines(input_.slicer)
        if digest_lines:
            sections.append("\n".join(digest_lines))
        if input_.slicer.unavailable:
            sections.append("Unavailable = " + ", ".join(sorted(set(input_.slicer.unavailable))))
    stats_lines = _stats_lines(input_)
    if stats_lines:
        sections.append("\n".join(stats_lines))
    constraint_lines = _constraint_lines(input_)
    if constraint_lines:
        sections.append("\n".join(constraint_lines))
    return "\n".join(sections)


def build_prompt(input_: RecommendationInput, candidates: list[Recommendation]) -> str:
    """Build the full recommendation prompt for the LLM."""
    facts = build_facts_text(input_)
    allowed = ", ".join(sorted(RECOMMENDABLE_SETTINGS))
    candidates_block = _candidate_lines(candidates)

    return (
        "You are an expert FDM 3D printing advisor. You recommend slicer settings only.\n"
        "\n"
        "MEASURED FACTS (use ONLY these; never invent measurements):\n"
        f"{facts}\n"
        "\n"
        f"USER GOAL: {input_.goal.value}\n"
        "\n"
        "RULE-BASED CANDIDATE RECOMMENDATIONS (already computed from the facts above; "
        "you MUST keep each of these - you may reorder them and improve the narrative "
        "fields, but not change the values):\n"
        f"{candidates_block}\n"
        "\n"
        "YOUR TASK: return a JSON object with:\n"
        '- "goal": the user goal, exactly "' + input_.goal.value + '"\n'
        '- "summary": one or two sentences prioritizing the recommendations\n'
        '- "warnings": list of strings (may be empty)\n'
        '- "recommendations": an array of recommendation objects ordered by priority.\n'
        "  For each rule-based candidate include it exactly once with the same "
        '"setting", "change", "current_value", "recommended_value". You may add at '
        "most 3 additional recommendations, each from this allowlist only: "
        f"{allowed}.\n"
        "\n"
        "RULES:\n"
        "- Do NOT recommend temperatures, bed/nozzle temperatures, flow ratio, or any "
        "hardware/calibration setting.\n"
        "- If a setting has no 'Current ...' line in MEASURED FACTS, set both "
        "'current_value' and 'recommended_value' to null; never invent baseline "
        "numbers.\n"
        '- Every recommendation must set "evidence" to quoted measured facts from the '
        "MEASURED FACTS block (copy them verbatim).\n"
        '- "confidence" must be a number between 0 and 1, or null. Do not assign a '
        "confidence to rule-based candidates.\n"
        '- Do not make up numbers; if a fact is not present, treat it as unavailable.\n'
        "\n"
        "OUTPUT JSON SCHEMA:\n"
        "{\n"
        '  "goal": "' + input_.goal.value + '",\n'
        '  "summary": "...",\n'
        '  "warnings": ["..."],\n'
        '  "recommendations": [\n'
        "    {\n"
        '      "setting": "layer_height_mm",\n'
        '      "change": "decrease",\n'
        '      "current_value": 0.2,\n'
        '      "recommended_value": 0.16,\n'
        '      "reason": "...",\n'
        '      "expected_benefit": "...",\n'
        '      "tradeoff": "...",\n'
        '      "confidence": null,\n'
        '      "evidence": ["Current layer height = 0.20 mm"]\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "\n"
        "Respond with ONLY the JSON object, no prose before or after.\n"
    )
