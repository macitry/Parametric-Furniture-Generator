"""CLI module for the Parametric Furniture Generator.

Usage:
    furniture build templates/desk/basic.yaml --width 1200 --depth 600 --height 750
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from .build import build_furniture

app = typer.Typer(
    name="furniture",
    help="Parametric Furniture Generator — declarative templates to URDF.",
    no_args_is_help=True,
)

app.command("build")(build_furniture)


@app.callback()
def _main_callback(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show version and exit.",
    ),
) -> None:
    """Parametric Furniture Generator: templates + parameters → URDF."""
    if version:
        from parametric_furniture import __version__
        typer.echo(f"Parametric Furniture Generator v{__version__}")
        raise typer.Exit()


def main() -> None:
    """Entry point for the CLI application."""
    app()


__all__ = ["app", "main", "build_furniture"]
