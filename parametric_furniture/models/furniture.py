"""Furniture assembly models.

The FurnitureAssembly is the output of the Builder and the input to the
URDF Exporter. It is CAD-independent: it contains paths to generated
mesh files, not CAD solids.

Data flow: Builder → FurnitureAssembly → URDFWriter
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from .pose import Pose


class InertialData(BaseModel):
    """Inertial properties for a rigid body.

    Attributes:
        mass: Mass in kilograms.
        ixx: Moment of inertia about X axis (kg·m²).
        iyy: Moment of inertia about Y axis (kg·m²).
        izz: Moment of inertia about Z axis (kg·m²).
        ixy: Product of inertia (kg·m²).
        ixz: Product of inertia (kg·m²).
        iyz: Product of inertia (kg·m²).
        origin: Origin of the inertial frame relative to the link frame.
    """

    mass: float = Field(..., gt=0.0, description="Mass in kg.")
    ixx: float = Field(..., description="Moment of inertia about X axis (kg·m²).")
    iyy: float = Field(..., description="Moment of inertia about Y axis (kg·m²).")
    izz: float = Field(..., description="Moment of inertia about Z axis (kg·m²).")
    ixy: float = Field(default=0.0, description="Product of inertia (kg·m²).")
    ixz: float = Field(default=0.0, description="Product of inertia (kg·m²).")
    iyz: float = Field(default=0.0, description="Product of inertia (kg·m²).")
    origin: Pose = Field(
        default_factory=Pose.origin,
        description="Origin of the inertial frame relative to the link frame.",
    )


class FurniturePart(BaseModel):
    """A generated 3D part with exported mesh files.

    Represents one physical component of the furniture item after
    the Builder has generated its 3D geometry.

    Attributes:
        name: Part name matching the template.
        part_type: Role (leg, beam, tabletop, etc.).
        step_path: Path to the generated STEP file.
        stl_path: Path to the generated STL file.
        mass_kg: Mass in kilograms.
        dimensions: Dict of key dimensions (length, width, etc.) in mm.
    """

    name: str = Field(..., description="Part name.")
    part_type: str = Field(..., description="Part role.")
    step_path: Optional[Path] = Field(
        default=None, description="Path to STEP file."
    )
    stl_path: Optional[Path] = Field(
        default=None, description="Path to STL file."
    )
    mass_kg: float = Field(default=0.0, ge=0.0, description="Mass in kg.")
    dimensions: dict[str, float] = Field(
        default_factory=dict,
        description="Key dimensions in mm (length, width, height, etc.).",
    )


class FurnitureLink(BaseModel):
    """A URDF link corresponding to one furniture part.

    Attributes:
        name: Link name (matches part name).
        part: The associated furniture part.
        visual_mesh: Path to the STL file for visual geometry.
        collision_mesh: Path to the STL file for collision geometry.
        inertial: Inertial properties.
        pose: Link pose in the assembly frame.
    """

    name: str = Field(..., description="Link name.")
    part: FurniturePart = Field(..., description="Associated part.")
    visual_mesh: Optional[Path] = Field(
        default=None, description="Path to visual mesh (STL)."
    )
    collision_mesh: Optional[Path] = Field(
        default=None, description="Path to collision mesh (STL)."
    )
    inertial: InertialData = Field(..., description="Inertial properties.")
    pose: Pose = Field(
        default_factory=Pose.origin,
        description="Link pose in the assembly frame.",
    )


class FurnitureJoint(BaseModel):
    """A URDF joint connecting two links.

    All furniture joints are fixed (type='fixed') since furniture
    parts do not move relative to each other.

    Attributes:
        name: Joint name.
        joint_type: Always 'fixed' for furniture.
        parent: Parent link name.
        child: Child link name.
        origin: Joint origin relative to the parent link frame.
    """

    name: str = Field(..., description="Joint name.")
    joint_type: str = Field(
        default="fixed",
        description="Joint type. Always 'fixed' for furniture.",
    )
    parent: str = Field(..., description="Parent link name.")
    child: str = Field(..., description="Child link name.")
    origin: Pose = Field(
        default_factory=Pose.origin,
        description="Joint origin in the parent link frame.",
    )


class FurnitureAssembly(BaseModel):
    """The complete furniture assembly — output of the Builder.

    Contains all parts, links, and joints needed to generate URDF.
    This is the intermediate representation between CAD generation
    and URDF export.

    Attributes:
        name: Assembly name (from template).
        furniture_type: Furniture type (desk, shelf, etc.).
        parts: All generated parts.
        links: All URDF links.
        joints: All URDF joints.
    """

    name: str = Field(..., description="Assembly name.")
    furniture_type: str = Field(..., description="Furniture type.")
    parts: list[FurniturePart] = Field(
        default_factory=list, description="All parts in the assembly."
    )
    links: list[FurnitureLink] = Field(
        default_factory=list, description="All URDF links."
    )
    joints: list[FurnitureJoint] = Field(
        default_factory=list, description="All URDF joints."
    )

    @property
    def part_count(self) -> int:
        """Return the total number of parts."""
        return len(self.parts)
