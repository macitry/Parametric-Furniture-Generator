"""Abstract furniture builder and builder registry.

The Builder takes SolverOutput (computed dimensions and poses) and
generates 3D geometry using the VisualCAD pipeline. It produces a
FurnitureAssembly containing Parts, Links, and Joints ready for
URDF export.

The Builder is responsible for:
- Calling VisualCAD to generate 3D solids from DXF profiles
- Exporting STEP and STL files
- Computing mass from volume × material density
- Computing inertia tensors (analytical approximations)
- Creating Link and Joint objects for the assembly

The Builder does NOT:
- Compute dimensions or positions (that's the Solver's job)
- Generate URDF XML (that's the URDFWriter's job)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from loguru import logger

from ..config import AppConfig
from ..models.furniture import FurnitureAssembly
from ..solvers.furniture_solver import SolverOutput


class MaterialDensity:
    """Material density lookup table.

    Densities in kg/m³. Used to compute mass from volume.
    """

    _densities: dict[str, float] = {
        "aluminum": 2700.0,
        "steel": 7850.0,
        "wood": 700.0,
        "plywood": 700.0,
        "mdf": 750.0,
        "oak": 900.0,
        "birch": 670.0,
        "walnut": 650.0,
    }

    @classmethod
    def get(cls, material: str) -> float:
        """Return density in kg/m³ for a material.

        Args:
            material: Material name (case-insensitive).

        Returns:
            Density in kg/m³. Falls back to 1000.0 for unknown materials.

        Raises:
            ValueError: If material is empty.
        """
        if not material or not material.strip():
            raise ValueError("Material name must not be empty.")
        return cls._densities.get(material.lower().strip(), 1000.0)

    @classmethod
    def register(cls, material: str, density: float) -> None:
        """Register a custom material density.

        Args:
            material: Material name.
            density: Density in kg/m³.
        """
        cls._densities[material.lower().strip()] = density
        logger.debug(f"Registered material '{material}': {density} kg/m³")


class AbstractFurnitureBuilder(ABC):
    """Abstract base for all furniture builders.

    Each furniture type has its own Builder subclass that knows how
    to generate the 3D geometry specific to that type.

    Subclasses must implement `build()`.

    Usage:
        builder = DeskBuilder(config)
        assembly = builder.build(solver_output)
    """

    def __init__(self, config: AppConfig | None = None) -> None:
        """Initialize the builder.

        Args:
            config: Application configuration. Uses defaults if None.
        """
        self._config = config or AppConfig()

    @property
    def config(self) -> AppConfig:
        """Return the application configuration."""
        return self._config

    @property
    @abstractmethod
    def furniture_type(self) -> str:
        """Return the furniture type this builder handles (e.g. 'desk')."""
        ...

    @abstractmethod
    def build(self, solver_output: SolverOutput) -> FurnitureAssembly:
        """Generate 3D geometry and create the furniture assembly.

        Args:
            solver_output: Computed part dimensions and poses from the Solver.

        Returns:
            A complete FurnitureAssembly with parts, links, and joints.
        """
        ...

    def ensure_output_dir(self) -> Path:
        """Ensure the output directory exists and return its path.

        Returns:
            Path to the output directory.
        """
        out = self._config.paths.output_dir
        out.mkdir(parents=True, exist_ok=True)
        return out

    def create_package_dirs(self, package_name: str) -> dict[str, Path]:
        """Create the URDF package directory layout.

        The URDF file is placed at the package root (same level as the
        meshes/ directory), matching the standard convention used by
        reference robot models (e.g. UR5e).

        Creates::

            <output_dir>/<package_name>/
                <package_name>.urdf
                meshes/visual/    — visual STL meshes
                meshes/collision/ — collision STL meshes
                cad/              — STEP CAD files

        This layout is compatible with RViz, Gazebo, Foxglove Studio,
        and other ROS/robotics tools.

        Args:
            package_name: The package directory name.

        Returns:
            Dict with keys 'root', 'meshes_visual',
            'meshes_collision', 'cad' mapping to Path objects.
        """
        root = self._config.paths.output_dir / package_name
        dirs = {
            "root": root,
            "meshes_visual": root / "meshes" / "visual",
            "meshes_collision": root / "meshes" / "collision",
            "cad": root / "cad",
        }
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        return dirs


# ---------------------------------------------------------------------------
# Builder Registry
# ---------------------------------------------------------------------------

_registry: dict[str, type[AbstractFurnitureBuilder]] = {}


def register_builder(
    furniture_type: str, builder_cls: type[AbstractFurnitureBuilder]
) -> None:
    """Register a builder class for a furniture type.

    Args:
        furniture_type: The furniture type (e.g. 'desk').
        builder_cls: The builder class to register.

    Raises:
        ValueError: If a builder is already registered for this type.
    """
    if furniture_type in _registry:
        raise ValueError(
            f"Builder already registered for '{furniture_type}': "
            f"{_registry[furniture_type].__name__}"
        )
    _registry[furniture_type] = builder_cls
    logger.debug(
        f"Registered builder '{builder_cls.__name__}' for type '{furniture_type}'"
    )


def get_builder(
    furniture_type: str, config: AppConfig | None = None
) -> AbstractFurnitureBuilder:
    """Get a builder instance for a furniture type.

    Args:
        furniture_type: The furniture type.
        config: Application configuration.

    Returns:
        An instance of the registered builder.

    Raises:
        KeyError: If no builder is registered for this type.
    """
    builder_cls = _registry.get(furniture_type)
    if builder_cls is None:
        available = list(_registry.keys()) if _registry else ["(none)"]
        raise KeyError(
            f"No builder registered for furniture type '{furniture_type}'. "
            f"Available types: {available}"
        )
    return builder_cls(config=config)


def list_builders() -> list[str]:
    """Return the list of registered furniture types."""
    return sorted(_registry.keys())
