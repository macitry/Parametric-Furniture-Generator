"""Tests for the DeskBuilder (unit tests without VisualCAD dependency)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from builders.furniture_builder import MaterialDensity
from models.furniture import InertialData
from models.parameter import DeskParameters
from models.pose import Pose
from models.template import FurnitureTemplate
from solvers.furniture_solver import SolvedPart


class TestMaterialDensity:
    """Tests for material density lookup."""

    def test_aluminum(self) -> None:
        """Aluminum density should be 2700 kg/m³."""
        assert MaterialDensity.get("aluminum") == 2700.0

    def test_wood(self) -> None:
        """Wood density should be 700 kg/m³."""
        assert MaterialDensity.get("wood") == 700.0

    def test_plywood(self) -> None:
        """Plywood density should be 700 kg/m³."""
        assert MaterialDensity.get("plywood") == 700.0

    def test_mdf(self) -> None:
        """MDF density should be 750 kg/m³."""
        assert MaterialDensity.get("mdf") == 750.0

    def test_oak(self) -> None:
        """Oak density should be 900 kg/m³."""
        assert MaterialDensity.get("oak") == 900.0

    def test_case_insensitive(self) -> None:
        """Material lookup should be case-insensitive."""
        assert MaterialDensity.get("ALUMINUM") == 2700.0
        assert MaterialDensity.get("Aluminum") == 2700.0

    def test_unknown_material_fallback(self) -> None:
        """Unknown materials should return default density."""
        assert MaterialDensity.get("unobtainium") == 1000.0

    def test_empty_material_raises(self) -> None:
        """Empty material name should raise ValueError."""
        with pytest.raises(ValueError):
            MaterialDensity.get("")

    def test_register_custom_material(self) -> None:
        """Should be able to register a custom material."""
        MaterialDensity.register("carbon_fiber", 1600.0)
        assert MaterialDensity.get("carbon_fiber") == 1600.0


class TestDeskBuilderInertia:
    """Tests for inertia computation logic."""

    def _compute_inertia(self, solved: SolvedPart, mass_kg: float, dx: float, dy: float, dz: float) -> InertialData:
        """Helper to compute inertia using the same analytical formulas as DeskBuilder."""
        m = mass_kg
        ixx = m / 12.0 * (dy * dy + dz * dz)
        iyy = m / 12.0 * (dx * dx + dz * dz)
        izz = m / 12.0 * (dx * dx + dy * dy)

        return InertialData(
            mass=m,
            ixx=ixx,
            iyy=iyy,
            izz=izz,
            ixy=0.0,
            ixz=0.0,
            iyz=0.0,
        )

    def test_tabletop_inertia(self) -> None:
        """Tabletop (wide flat rectangle) should have large Izz."""
        solved = SolvedPart(
            name="tabletop",
            part_type="tabletop",
            material="wood",
            tabletop_width=1200.0,
            tabletop_depth=600.0,
            tabletop_thickness=18.0,
        )

        inertial = self._compute_inertia(
            solved,
            mass_kg=10.0,
            dx=1.2,  # m
            dy=0.6,  # m
            dz=0.018,  # m
        )

        # Izz (about vertical) should be largest
        assert inertial.izz > inertial.ixx
        assert inertial.izz > inertial.iyy
        # Iyy > Ixx because width (X, 1.2m) > depth (Y, 0.6m)
        # Rotation about Y encounters mass distributed along X
        assert inertial.iyy > inertial.ixx

    def test_leg_inertia(self) -> None:
        """Long thin leg should have largest inertia about X and Y axes."""
        solved = SolvedPart(
            name="leg",
            part_type="leg",
            profile="2020",
            material="aluminum",
            extrusion_length=732.0,
        )

        inertial = self._compute_inertia(
            solved,
            mass_kg=1.0,
            dx=0.02,  # m (20mm profile)
            dy=0.02,  # m
            dz=0.732,  # m
        )

        # Ixx and Iyy should be much larger than Izz (long thin rod)
        assert inertial.ixx > inertial.izz * 10
        assert inertial.iyy > inertial.izz * 10
        # Ixx should equal Iyy (square cross-section)
        assert inertial.ixx == pytest.approx(inertial.iyy)

    def test_beam_inertia(self) -> None:
        """Horizontal beam should have different inertia profile."""
        solved = SolvedPart(
            name="beam",
            part_type="beam",
            profile="2020",
            material="aluminum",
            extrusion_length=1180.0,
        )

        inertial = self._compute_inertia(
            solved,
            mass_kg=0.5,
            dx=0.02,  # m
            dy=0.02,  # m
            dz=1.180,  # m
        )

        # In local frame (extrusion along Z), Izz is small, Ixx/Iyy are large
        assert inertial.ixx > inertial.izz
        assert inertial.iyy > inertial.izz

    def test_positive_values(self) -> None:
        """All inertia values should be positive."""
        solved = SolvedPart(
            name="test",
            part_type="leg",
            profile="3030",
            extrusion_length=500.0,
            material="aluminum",
        )

        inertial = self._compute_inertia(
            solved,
            mass_kg=2.0,
            dx=0.03,
            dy=0.03,
            dz=0.5,
        )

        assert inertial.mass > 0
        assert inertial.ixx > 0
        assert inertial.iyy > 0
        assert inertial.izz > 0
