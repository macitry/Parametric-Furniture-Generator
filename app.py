#!/usr/bin/env python3
"""Parametric Furniture Generator — declarative furniture templates to URDF.

A parametric furniture generation platform that takes declarative
furniture templates + user parameters and produces complete 3D
assemblies with URDF export.

Usage:
    python app.py build templates/desk/basic.yaml --width 1200 --depth 600 --height 750
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the project root is on sys.path for local development
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import typer

from cli.build import build_furniture

app = typer.Typer(
    name="furniture",
    help="Parametric Furniture Generator — declarative templates to URDF.",
    no_args_is_help=True,
)

app.command("build")(build_furniture)


@app.callback()
def main_callback(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show version and exit.",
    ),
) -> None:
    """Parametric Furniture Generator: templates + parameters → URDF."""
    if version:
        typer.echo("Parametric Furniture Generator v0.1.0")
        raise typer.Exit()


def main() -> None:
    """Entry point for the CLI application."""
    app()


if __name__ == "__main__":
    main()
