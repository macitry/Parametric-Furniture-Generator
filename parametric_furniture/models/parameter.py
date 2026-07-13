"""Furniture parameter models.

Parameters are the user-facing inputs. They contain only the values
a user would reasonably specify — the Solver derives everything else.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DeskParameters(BaseModel):
    """User-specified parameters for a desk.

    All dimensions are in millimeters. These are the values a user
    would naturally specify — the Solver computes derived dimensions.

    Attributes:
        width: Total desk width (left-to-right) in mm.
        depth: Total desk depth (front-to-back) in mm.
        height: Total desk height (floor to tabletop top surface) in mm.
        tabletop_thickness: Thickness of the tabletop board in mm.
        profile: Aluminum profile size for legs and beams ('2020', '3030', '4040').
        board_material: Tabletop board material ('plywood', 'mdf', 'oak').
        color: Visual color hint for rendering.
    """

    width: float = Field(
        default=1200.0,
        gt=0.0,
        description="Total desk width in mm.",
    )
    depth: float = Field(
        default=600.0,
        gt=0.0,
        description="Total desk depth in mm.",
    )
    height: float = Field(
        default=750.0,
        gt=0.0,
        description="Total desk height in mm (floor to tabletop top).",
    )
    tabletop_thickness: float = Field(
        default=18.0,
        gt=0.0,
        description="Thickness of the tabletop board in mm.",
    )
    profile: str = Field(
        default="2020",
        pattern=r"^(2020|3030|4040)$",
        description="Aluminum profile size for legs and beams.",
    )
    board_material: str = Field(
        default="plywood",
        pattern=r"^(plywood|mdf|oak)$",
        description="Tabletop board material.",
    )
    color: str = Field(
        default="natural",
        description="Visual color hint.",
    )

    # Actual profile face dimensions measured from the STL output.
    # The MJ-8-3030 DXF extracts to 60×60 mm (two merged contours);
    # 2020 / 4040 are generated centred at their nominal size.
    _PROFILE_SIZES: dict[str, float] = {"2020": 20.0, "3030": 60.0, "4040": 40.0}

    @property
    def profile_size(self) -> float:
        """Return the actual profile cross-section size in mm."""
        return self._PROFILE_SIZES.get(
            self.profile, float(self.profile[:2])
        )

    @property
    def leg_count(self) -> int:
        """Return the number of legs for this desk."""
        return 4

    @property
    def beam_count(self) -> int:
        """Return the number of beams for this desk."""
        return 4
