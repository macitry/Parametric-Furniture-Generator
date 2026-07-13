"""Abstract furniture solver and solver registry.

The Solver is the computational core of the system. It takes a
declarative Template + user Parameters and computes:
- Every part's dimensions (length, profile, etc.)
- Every part's Pose in the assembly frame
- Every joint's origin

This module provides the abstract base and a registry for solver
implementations. New furniture types register their solver here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol

from loguru import logger
from pydantic import BaseModel, Field

from ..models.pose import Pose
from ..models.template import FurnitureTemplate


class HasFurnitureType(Protocol):
    """Protocol for parameter objects that expose a furniture type."""

    @property
    def furniture_type(self) -> str: ...


class SolvedPart(BaseModel):
    """The output of the Solver for a single part.

    Contains all computed values needed by the Builder to generate
    3D geometry and place it in the assembly.

    Attributes:
        name: Part name (from template).
        part_type: Part role (leg, beam, tabletop, etc.).
        profile: Profile reference for extrusion (e.g. '2020').
        board: Board material reference (for tabletop-type parts).
        material: Material category.
        extrusion_length: Length to extrude the profile (mm).
        tabletop_width: Width of tabletop (mm) — only for tabletop parts.
        tabletop_depth: Depth of tabletop (mm) — only for tabletop parts.
        tabletop_thickness: Thickness of tabletop (mm) — only for tabletop parts.
        pose: Pose of this part in the assembly frame.
        joint_origin: Origin for the URDF joint connecting this part.
    """

    name: str = Field(..., description="Part name.")
    part_type: str = Field(..., description="Part role.")
    profile: str | None = Field(default=None, description="Profile reference.")
    board: str | None = Field(default=None, description="Board material reference.")
    material: str = Field(default="aluminum", description="Material category.")
    extrusion_length: float = Field(default=0.0, ge=0.0, description="Extrusion length in mm.")
    tabletop_width: float = Field(default=0.0, ge=0.0, description="Tabletop width in mm.")
    tabletop_depth: float = Field(default=0.0, ge=0.0, description="Tabletop depth in mm.")
    tabletop_thickness: float = Field(default=0.0, ge=0.0, description="Tabletop thickness in mm.")
    pose: Pose = Field(default_factory=Pose.origin, description="Part pose in assembly frame.")
    joint_origin: Pose = Field(
        default_factory=Pose.origin, description="Joint origin relative to parent link."
    )


class SolverOutput(BaseModel):
    """Complete solver output — all solved parts for one furniture item.

    Attributes:
        furniture_type: The furniture type that was solved.
        template_name: The template name that was solved.
        parts: All solved parts with computed dimensions and poses.
    """

    furniture_type: str = Field(..., description="Furniture type.")
    template_name: str = Field(..., description="Template name.")
    parts: list[SolvedPart] = Field(
        default_factory=list, description="All solved parts."
    )

    def get_parts_by_type(self, part_type: str) -> list[SolvedPart]:
        """Return all solved parts of a given type."""
        return [p for p in self.parts if p.part_type == part_type]


class AbstractFurnitureSolver(ABC):
    """Abstract base for all furniture solvers.

    Each furniture type (desk, shelf, cabinet, etc.) has its own
    Solver subclass that implements the design rules for that type.

    The Solver computes:
    - Part dimensions from template topology + user parameters
    - Part poses in the assembly coordinate frame
    - Joint origins for connection points

    Subclasses must implement `solve()`.

    Usage:
        solver = DeskSolver()
        output = solver.solve(template, parameters)
    """

    @property
    @abstractmethod
    def furniture_type(self) -> str:
        """Return the furniture type this solver handles (e.g. 'desk')."""
        ...

    @abstractmethod
    def solve(
        self,
        template: FurnitureTemplate,
        parameters: "HasFurnitureType",
    ) -> SolverOutput:
        """Compute all part dimensions, poses, and joint origins.

        Args:
            template: The declarative furniture template.
            parameters: User-specified furniture parameters.

        Returns:
            SolverOutput with all parts fully specified.
        """
        ...

    def validate_inputs(
        self,
        template: FurnitureTemplate,
        parameters: "HasFurnitureType",
    ) -> None:
        """Validate that the template and parameters are compatible.

        Called before solve(). Override to add custom validation.

        Args:
            template: The furniture template.
            parameters: The user parameters.

        Raises:
            ValueError: If inputs are incompatible.
        """
        if template.type != self.furniture_type:
            raise ValueError(
                f"Solver for '{self.furniture_type}' cannot handle "
                f"template of type '{template.type}'. "
                f"Use the appropriate solver."
            )


# ---------------------------------------------------------------------------
# Solver Registry
# ---------------------------------------------------------------------------

_registry: dict[str, type[AbstractFurnitureSolver]] = {}


def register_solver(furniture_type: str, solver_cls: type[AbstractFurnitureSolver]) -> None:
    """Register a solver class for a furniture type.

    Args:
        furniture_type: The furniture type (e.g. 'desk').
        solver_cls: The solver class to register.

    Raises:
        ValueError: If a solver is already registered for this type.
    """
    if furniture_type in _registry:
        raise ValueError(
            f"Solver already registered for '{furniture_type}': "
            f"{_registry[furniture_type].__name__}"
        )
    _registry[furniture_type] = solver_cls
    logger.debug(f"Registered solver '{solver_cls.__name__}' for type '{furniture_type}'")


def get_solver(furniture_type: str) -> AbstractFurnitureSolver:
    """Get a solver instance for a furniture type.

    Args:
        furniture_type: The furniture type.

    Returns:
        An instance of the registered solver.

    Raises:
        KeyError: If no solver is registered for this type.
    """
    solver_cls = _registry.get(furniture_type)
    if solver_cls is None:
        available = list(_registry.keys()) if _registry else ["(none)"]
        raise KeyError(
            f"No solver registered for furniture type '{furniture_type}'. "
            f"Available types: {available}"
        )
    return solver_cls()


def list_solvers() -> list[str]:
    """Return the list of registered furniture types."""
    return sorted(_registry.keys())
