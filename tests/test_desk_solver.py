"""Tests for the DeskSolver."""

from __future__ import annotations

import pytest

from parametric_furniture.models.parameter import DeskParameters
from parametric_furniture.models.template import FurnitureTemplate, PartTemplate
from parametric_furniture.solvers.desk_solver import DeskSolver
from parametric_furniture.solvers.furniture_solver import SolvedPart, SolverOutput


class TestDeskSolver:
    """Tests for the DeskSolver design rules."""

    @pytest.fixture
    def solver(self) -> DeskSolver:
        """Create a DeskSolver instance."""
        return DeskSolver()

    @pytest.fixture
    def template(self, desk_template_path) -> FurnitureTemplate:
        """Load the basic desk template."""
        return FurnitureTemplate.from_yaml(desk_template_path)

    @pytest.fixture
    def default_params(self) -> DeskParameters:
        """Default desk parameters."""
        return DeskParameters(
            width=1200.0,
            depth=600.0,
            height=750.0,
            tabletop_thickness=18.0,
            profile="2020",
            board_material="plywood",
        )

    def test_solver_type(self, solver: DeskSolver) -> None:
        """The solver should report 'desk' as its furniture type."""
        assert solver.furniture_type == "desk"

    def test_solve_returns_correct_count(
        self,
        solver: DeskSolver,
        template: FurnitureTemplate,
        default_params: DeskParameters,
    ) -> None:
        """Should return exactly 9 solved parts (4 legs + 4 beams + 1 tabletop)."""
        output = solver.solve(template, default_params)

        assert len(output.parts) == 9
        assert output.furniture_type == "desk"

    def test_leg_length_calculation(
        self,
        solver: DeskSolver,
        template: FurnitureTemplate,
        default_params: DeskParameters,
    ) -> None:
        """Leg length = height - tabletop thickness (leg top at beam centreline)."""
        output = solver.solve(template, default_params)

        legs = output.get_parts_by_type("leg")
        expected_length = (
            default_params.height
            - default_params.tabletop_thickness
        )
        # 750 - 18 = 732

        for leg in legs:
            assert leg.extrusion_length == pytest.approx(expected_length)
            assert leg.part_type == "leg"
            assert leg.profile == "2020"
            assert leg.material == "aluminum"

    def test_tabletop_dimensions(
        self,
        solver: DeskSolver,
        template: FurnitureTemplate,
        default_params: DeskParameters,
    ) -> None:
        """Tabletop should have the user-specified dimensions."""
        output = solver.solve(template, default_params)

        tabletops = output.get_parts_by_type("tabletop")
        assert len(tabletops) == 1

        tt = tabletops[0]
        assert tt.tabletop_width == default_params.width
        assert tt.tabletop_depth == default_params.depth
        assert tt.tabletop_thickness == default_params.tabletop_thickness
        assert tt.board == "plywood"

    def test_beam_lengths(
        self,
        solver: DeskSolver,
        template: FurnitureTemplate,
        default_params: DeskParameters,
    ) -> None:
        """All beams fit face-to-face between opposite legs: w - 2*ps or d - 2*ps."""
        output = solver.solve(template, default_params)

        ps = default_params.profile_size  # 20
        w = default_params.width  # 1200
        d = default_params.depth  # 600

        beam_front = next(p for p in output.parts if p.name == "beam_front")
        beam_back = next(p for p in output.parts if p.name == "beam_back")
        beam_left = next(p for p in output.parts if p.name == "beam_left")
        beam_right = next(p for p in output.parts if p.name == "beam_right")

        assert beam_front.extrusion_length == pytest.approx(w - 2 * ps)
        assert beam_back.extrusion_length == pytest.approx(w - 2 * ps)
        assert beam_left.extrusion_length == pytest.approx(d - 2 * ps)
        assert beam_right.extrusion_length == pytest.approx(d - 2 * ps)

    def test_leg_positions_are_symmetric(
        self,
        solver: DeskSolver,
        template: FurnitureTemplate,
        default_params: DeskParameters,
    ) -> None:
        """Leg positions should be symmetric about the origin."""
        output = solver.solve(template, default_params)

        leg_fl = next(p for p in output.parts if p.name == "leg_front_left")
        leg_fr = next(p for p in output.parts if p.name == "leg_front_right")
        leg_bl = next(p for p in output.parts if p.name == "leg_back_left")
        leg_br = next(p for p in output.parts if p.name == "leg_back_right")

        # Symmetric in X: left vs right
        assert leg_fl.pose.x == pytest.approx(-leg_fr.pose.x)
        assert leg_bl.pose.x == pytest.approx(-leg_br.pose.x)

        # Symmetric in Y: front vs back
        assert leg_fl.pose.y == pytest.approx(-leg_bl.pose.y)
        assert leg_fr.pose.y == pytest.approx(-leg_br.pose.y)

        # All legs at same Z
        assert leg_fl.pose.z == pytest.approx(leg_fr.pose.z)
        assert leg_fl.pose.z == pytest.approx(leg_bl.pose.z)

    def test_leg_top_is_at_beam_centre(
        self,
        solver: DeskSolver,
        template: FurnitureTemplate,
        default_params: DeskParameters,
    ) -> None:
        """Leg top at beam centreline = tabletop bottom = -tt."""
        output = solver.solve(template, default_params)

        # Leg top in PHYSICAL frame (before tz_offset compensation):
        # leg.pose.z - tz_offset + joint_origin.z = physical_top_z
        tt = default_params.tabletop_thickness
        tz_offset = tt / 2.0
        expected_top = -tt
        for leg in output.get_parts_by_type("leg"):
            leg_top_z = (leg.pose.z - tz_offset) + leg.joint_origin.z
            assert leg_top_z == pytest.approx(expected_top)

    def test_beam_orientations(
        self,
        solver: DeskSolver,
        template: FurnitureTemplate,
        default_params: DeskParameters,
    ) -> None:
        """Front/back beams should have pitch=-90° (rotate to X axis).
        Left/right beams should have roll=-90° (rotate to Y axis)."""
        import math

        output = solver.solve(template, default_params)

        beam_front = next(p for p in output.parts if p.name == "beam_front")
        beam_left = next(p for p in output.parts if p.name == "beam_left")

        # Front beam pitch should be -90 degrees
        assert beam_front.pose.pitch == pytest.approx(math.radians(-90.0))

        # Left beam roll should be -90 degrees
        assert beam_left.pose.roll == pytest.approx(math.radians(-90.0))

    def test_custom_parameters(
        self,
        solver: DeskSolver,
        template: FurnitureTemplate,
    ) -> None:
        """Should handle custom parameter values correctly."""
        params = DeskParameters(
            width=1600.0,
            depth=800.0,
            height=720.0,
            tabletop_thickness=25.0,
            profile="4040",
            board_material="oak",
        )

        output = solver.solve(template, params)

        legs = output.get_parts_by_type("leg")
        assert len(legs) == 4

        # Leg length = 720 - 25 = 695
        for leg in legs:
            assert leg.extrusion_length == pytest.approx(695.0)
            assert leg.profile == "4040"

        # Tabletop
        tt = output.get_parts_by_type("tabletop")[0]
        assert tt.tabletop_thickness == 25.0
        assert tt.board == "oak"

    def test_rejects_wrong_template_type(self, solver: DeskSolver) -> None:
        """Should raise ValueError if template type is not 'desk'."""
        from parametric_furniture.models.template import FurnitureTemplate

        template = FurnitureTemplate(
            name="Test",
            type="shelf",
            parts=[
                PartTemplate(
                    name="shelf_board",
                    part_type="shelf",
                    material="wood",
                    board="plywood",
                )
            ],
        )

        with pytest.raises(ValueError, match="cannot handle"):
            solver.solve(template, DeskParameters())
