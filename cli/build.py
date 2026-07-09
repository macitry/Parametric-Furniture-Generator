"""CLI build command — the main entry point for furniture generation.

Usage:
    python app.py build templates/desk/basic.yaml --width 1200 --depth 600 --height 750
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer
from loguru import logger

from config import AppConfig
from models.parameter import DeskParameters
from models.template import FurnitureTemplate
from solvers.furniture_solver import get_solver
from builders.furniture_builder import get_builder
from exporters.urdf_writer import URDFWriter


def build_furniture(
    template_path: str = typer.Argument(
        ...,
        help="Path to the furniture template YAML file.",
    ),
    width: float = typer.Option(
        1200.0, "--width", "-w", help="Desk width in mm."
    ),
    depth: float = typer.Option(
        600.0, "--depth", "-d", help="Desk depth in mm."
    ),
    height: float = typer.Option(
        750.0, "--height", help="Desk height in mm."
    ),
    tabletop_thickness: float = typer.Option(
        18.0, "--tabletop-thickness", "-t", help="Tabletop thickness in mm."
    ),
    profile: str = typer.Option(
        "2020", "--profile", "-p", help="Aluminum profile: 2020, 3030, 4040."
    ),
    board_material: str = typer.Option(
        "plywood", "--board", "-b", help="Board material: plywood, mdf, oak."
    ),
    output_dir: Optional[str] = typer.Option(
        None, "--output", "-o", help="Output directory for generated files."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable debug logging."
    ),
) -> None:
    """Build a furniture item from a template and parameters.

    The pipeline:
    1. Load Template (YAML)
    2. Load Parameters (CLI options)
    3. Run Solver (compute dimensions and poses)
    4. Run Builder (generate 3D geometry)
    5. Export URDF
    """
    # Configure logging
    logger.remove()
    log_level = "DEBUG" if verbose else "INFO"
    logger.add(
        sys.stderr,
        level=log_level,
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<level>{message}</level>"
        ),
    )

    config = AppConfig()
    if output_dir:
        config.paths.output_dir = Path(output_dir)

    logger.info("=" * 50)
    logger.info("Parametric Furniture Generator")
    logger.info("=" * 50)

    # ------------------------------------------------------------------
    # Step 1: Load Template
    # ------------------------------------------------------------------
    logger.info("Loading Template...")
    try:
        template = FurnitureTemplate.from_yaml(template_path)
        logger.info(f"  Template: {template.name} (type: {template.type})")
        logger.info(f"  Parts: {len(template.parts)}")
    except Exception as exc:
        logger.error(f"Failed to load template: {exc}")
        raise typer.Exit(code=1)

    # ------------------------------------------------------------------
    # Step 2: Load Parameters
    # ------------------------------------------------------------------
    logger.info("Loading Parameters...")
    try:
        parameters = DeskParameters(
            width=width,
            depth=depth,
            height=height,
            tabletop_thickness=tabletop_thickness,
            profile=profile,
            board_material=board_material,
        )
        logger.info(
            f"  {parameters.width}x{parameters.depth}x{parameters.height} mm, "
            f"profile={parameters.profile}, "
            f"board={parameters.board_material}"
        )
    except Exception as exc:
        logger.error(f"Invalid parameters: {exc}")
        raise typer.Exit(code=1)

    # ------------------------------------------------------------------
    # Step 3: Run Solver
    # ------------------------------------------------------------------
    logger.info("Running Solver...")
    try:
        solver = get_solver(template.type)
        solver_output = solver.solve(template, parameters)
        logger.info(f"  Computed {len(solver_output.parts)} parts")
    except Exception as exc:
        logger.error(f"Solver failed: {exc}")
        raise typer.Exit(code=1)

    # ------------------------------------------------------------------
    # Step 4: Run Builder
    # ------------------------------------------------------------------
    logger.info("Building Furniture...")
    try:
        builder = get_builder(template.type, config=config)
        assembly = builder.build(solver_output)
        logger.info(f"  Generated {assembly.part_count} parts")
    except Exception as exc:
        logger.error(f"Builder failed: {exc}")
        raise typer.Exit(code=1)

    # ------------------------------------------------------------------
    # Step 5: Export URDF
    # ------------------------------------------------------------------
    logger.info("Writing URDF...")
    try:
        package_name = template.name.replace(" ", "_").lower()
        urdf_name = package_name + ".urdf"
        urdf_path = config.paths.output_dir / package_name / urdf_name
        writer = URDFWriter(config)
        writer.write(assembly, urdf_path)
        logger.info(f"  URDF: {urdf_path.resolve()}")
    except Exception as exc:
        logger.error(f"URDF export failed: {exc}")
        raise typer.Exit(code=1)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    logger.info("=" * 50)
    logger.info("Build Complete!")
    logger.info(f"  Template:  {template.name}")
    logger.info(f"  Type:      {template.type}")
    logger.info(f"  Parts:     {assembly.part_count}")
    logger.info(f"  Output:    {config.paths.output_dir.resolve()}")
    logger.info("=" * 50)
