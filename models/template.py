"""Furniture template models.

Templates are declarative descriptions of furniture structure.
They describe WHAT parts exist and HOW they connect — never dimensions,
coordinates, or formulas.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from loguru import logger
from pydantic import BaseModel, Field, field_validator


class PartTemplate(BaseModel):
    """A single part definition within a furniture template.

    Describes the identity and role of one part. Does NOT contain
    dimensions, positions, or material properties — those come from
    Parameters and the Solver.

    Attributes:
        name: Unique name for this part within the template.
        part_type: The role this part plays (leg, beam, tabletop, etc.).
        profile: Profile reference for extrusion (e.g. '2020', '3030').
            Used to look up the DXF cross-section.
        board: Board material reference for flat parts (e.g. 'plywood').
            Only used for tabletop-type parts.
        material: Material category (aluminum, wood, steel).
    """

    name: str = Field(..., description="Unique part name within the template.")
    part_type: str = Field(..., description="Part role: leg, beam, tabletop, shelf, etc.")
    profile: Optional[str] = Field(
        default=None,
        description="Profile cross-section reference (e.g. '2020', '3030').",
    )
    board: Optional[str] = Field(
        default=None,
        description="Board material reference for flat parts (e.g. 'plywood').",
    )
    material: str = Field(
        default="aluminum",
        description="Material category: aluminum, wood, steel.",
    )

    @field_validator("name")
    @classmethod
    def name_must_be_valid(cls, v: str) -> str:
        """Validate that the part name is non-empty and uses valid characters."""
        if not v.strip():
            raise ValueError("Part name must not be empty.")
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError(
                f"Part name '{v}' contains invalid characters. "
                "Use only letters, digits, underscores, and hyphens."
            )
        return v.strip()


class Connection(BaseModel):
    """A connection between two parts in the furniture topology.

    Attributes:
        part_a: Name of the first part.
        part_b: Name of the second part.
    """

    part_a: str = Field(..., description="First part name.")
    part_b: str = Field(..., description="Second part name.")


class Topology(BaseModel):
    """Describes how parts connect to each other.

    Connections are undirected — [A, B] is the same as [B, A].
    """

    connections: list[Connection] = Field(
        default_factory=list,
        description="List of part-to-part connections.",
    )

    @field_validator("connections")
    @classmethod
    def connections_must_reference_valid_parts(
        cls, v: list[Connection]
    ) -> list[Connection]:
        """Validate connections. Full cross-reference validation is done
        at the FurnitureTemplate level since parts aren't accessible here."""
        return v


class FurnitureTemplate(BaseModel):
    """A declarative description of a furniture item's structure.

    Templates are the starting point of the pipeline. They define:
    - Which parts exist (identity, type, profile)
    - How parts connect (topology)

    They do NOT define:
    - Dimensions (these come from Parameters)
    - Positions (these come from the Solver)
    - Material properties (these come from the library)

    Usage:
        template = FurnitureTemplate.from_yaml("templates/desk/basic.yaml")
    """

    name: str = Field(..., description="Human-readable template name.")
    type: str = Field(..., description="Furniture type: desk, shelf, cabinet, etc.")
    parts: list[PartTemplate] = Field(
        ..., min_length=1, description="Parts that make up this furniture item."
    )
    topology: Topology = Field(
        default_factory=Topology,
        description="How parts connect to each other.",
    )

    @field_validator("type")
    @classmethod
    def type_must_be_valid(cls, v: str) -> str:
        """Validate the furniture type is a known category."""
        if not v.strip():
            raise ValueError("Furniture type must not be empty.")
        return v.strip().lower()

    @field_validator("topology")
    @classmethod
    def validate_topology_references(
        cls, v: Topology, info: "pydantic.ValidationInfo"
    ) -> Topology:
        """Validate that all topology connections reference existing parts."""
        parts_data = info.data.get("parts", [])
        part_names = {p.name if isinstance(p, PartTemplate) else p.get("name") for p in parts_data}

        for conn in v.connections:
            if conn.part_a not in part_names:
                raise ValueError(
                    f"Topology connection references unknown part '{conn.part_a}'. "
                    f"Known parts: {sorted(part_names)}"
                )
            if conn.part_b not in part_names:
                raise ValueError(
                    f"Topology connection references unknown part '{conn.part_b}'. "
                    f"Known parts: {sorted(part_names)}"
                )
        return v

    def get_parts_by_type(self, part_type: str) -> list[PartTemplate]:
        """Return all parts of a given type."""
        return [p for p in self.parts if p.part_type == part_type]

    def get_part(self, name: str) -> PartTemplate:
        """Return a part by name.

        Raises:
            KeyError: If the part name is not found.
        """
        for part in self.parts:
            if part.name == name:
                return part
        raise KeyError(f"Part '{name}' not found in template '{self.name}'.")

    @classmethod
    def from_yaml(cls, path: str | Path) -> "FurnitureTemplate":
        """Load a furniture template from a YAML file.

        Args:
            path: Path to the YAML template file.

        Returns:
            A validated FurnitureTemplate instance.

        Raises:
            FileNotFoundError: If the YAML file does not exist.
            ValueError: If the YAML is invalid or fails validation.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Template file not found: {path}")

        logger.info(f"Loading template: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if data is None:
            raise ValueError(f"Template file is empty: {path}")

        return cls.model_validate(data)
