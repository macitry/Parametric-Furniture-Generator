"""Parametric Furniture Generator — declarative furniture templates to URDF.

A parametric furniture generation platform that takes declarative
furniture templates + user parameters and produces complete 3D
assemblies with URDF export.

Quick usage::

    from parametric_furniture import (
        FurnitureTemplate,
        DeskParameters,
        DeskSolver,
        DeskBuilder,
    )

    template = FurnitureTemplate.from_yaml("templates/desk/basic.yaml")
    params = DeskParameters(width=1200, depth=600, height=750)
    solver = DeskSolver()
    solved = solver.solve(template, params)

CLI usage::

    furniture build templates/desk/basic.yaml --width 1200 --depth 600 --height 750
"""

from .config import AppConfig, PathsConfig, BuildConfig, ExportConfig
from .models import (
    FurnitureAssembly,
    FurnitureJoint,
    FurnitureLink,
    FurniturePart,
    FurnitureTemplate,
    DeskParameters,
    PartTemplate,
    Pose,
    Topology,
)
from .solvers import (
    DeskSolver,
    SolvedPart,
    SolverOutput,
    get_solver,
)
from .builders import (
    DeskBuilder,
    get_builder,
)
from .exporters import URDFWriter
from .cli.build import build_furniture

__version__ = "0.1.0"
__all__ = [
    # Config
    "AppConfig",
    "PathsConfig",
    "BuildConfig",
    "ExportConfig",
    # Models
    "FurnitureTemplate",
    "PartTemplate",
    "Topology",
    "DeskParameters",
    "Pose",
    "FurnitureAssembly",
    "FurniturePart",
    "FurnitureLink",
    "FurnitureJoint",
    # Solvers
    "DeskSolver",
    "SolvedPart",
    "SolverOutput",
    "get_solver",
    # Builders
    "DeskBuilder",
    "get_builder",
    # Exporters
    "URDFWriter",
    # CLI
    "build_furniture",
]
