"""Desk solver — implements desk-specific design rules.

Computes all part dimensions, poses, and joint origins for a desk
from the declarative template and user parameters.

Desk Design Rules:
    - Leg length = desk height - tabletop thickness - ½ profile_size
      (legs stop at the beam bottom so the beam rests on top of them)
    - Front/back beam length = desk width - profile_size
      (centre-to-centre span between left and right legs)
    - Left/right beam length = desk depth - profile_size
      (centre-to-centre span between front and back legs)
    - Legs positioned at corners, offset by half the profile size
    - Beams rest on top of the legs, just below the tabletop
    - Tabletop is the root link (attached to virtual base_link)

Coordinate Frame:
    Origin: Center of tabletop top surface.
    X: Left-to-right (positive = right).
    Y: Front-to-back (positive = front/forward).
    Z: Up (positive = upward).
"""

from __future__ import annotations

from loguru import logger

from models.parameter import DeskParameters
from models.pose import Pose
from models.template import FurnitureTemplate

from .furniture_solver import (
    AbstractFurnitureSolver,
    SolvedPart,
    SolverOutput,
    register_solver,
)


class DeskSolver(AbstractFurnitureSolver):
    """Solver for desk furniture.

    Implements desk-specific design rules to compute dimensions,
    positions, and joint origins for all desk parts.

    Usage:
        solver = DeskSolver()
        output = solver.solve(template, parameters)
    """

    @property
    def furniture_type(self) -> str:
        return "desk"

    def solve(
        self,
        template: FurnitureTemplate,
        parameters: DeskParameters,
    ) -> SolverOutput:
        """Compute all desk part dimensions and poses.

        Args:
            template: The desk template (parts + topology).
            parameters: User-specified desk parameters.

        Returns:
            SolverOutput with all parts fully specified.
        """
        self.validate_inputs(template, parameters)

        logger.info(f"Solving desk: {template.name}")
        logger.info(
            f"Parameters: {parameters.width}x{parameters.depth}x{parameters.height} mm, "
            f"tabletop={parameters.tabletop_thickness} mm, "
            f"profile={parameters.profile}"
        )

        solved_parts: list[SolvedPart] = []

        w = parameters.width
        d = parameters.depth
        h = parameters.height
        tt = parameters.tabletop_thickness
        ps = parameters.profile_size

        # ------------------------------------------------------------------
        # Tabletop
        # ------------------------------------------------------------------
        # Symmetric extrusion → mesh centre at z = 0, extends ±tt/2.
        # We offset the link downward by tt/2 so the top surface stays
        # at the assembly origin (z = 0).
        tabletop = SolvedPart(
            name="tabletop",
            part_type="tabletop",
            profile=None,
            board=parameters.board_material,
            material="wood",
            extrusion_length=0.0,
            tabletop_width=w,
            tabletop_depth=d,
            tabletop_thickness=tt,
            pose=Pose.from_translation(0.0, 0.0, -tt / 2.0),
            joint_origin=Pose.from_translation(0.0, 0.0, 0.0),
        )
        solved_parts.append(tabletop)
        logger.debug(
            f"Tabletop: {w}x{d}x{tt} mm, material={parameters.board_material}"
        )

        # ------------------------------------------------------------------
        # Legs — 4 corners, vertical extrusion (symmetric)
        #
        # Beam centre-line is at z = -tt (tabletop bottom).  The beam
        # cross-section extends ±ps/2, so the beam rests entirely below
        # the tabletop.  Legs stop at the beam centre-line so they press
        # against the side of the beam.
        # ------------------------------------------------------------------
        leg_length = h - tt          # leg top = beam centre-line = tabletop bottom
        leg_top_z = -tt
        half_w = w / 2.0 - ps / 2.0
        half_d = d / 2.0 - ps / 2.0

        leg_positions = {
            "leg_front_left":   (-half_w,  half_d, leg_top_z - leg_length / 2.0),
            "leg_front_right":  ( half_w,  half_d, leg_top_z - leg_length / 2.0),
            "leg_back_left":    (-half_w, -half_d, leg_top_z - leg_length / 2.0),
            "leg_back_right":   ( half_w, -half_d, leg_top_z - leg_length / 2.0),
        }

        # The tabletop link is offset by -tt/2 in z, so child joint
        # origins must add tt/2 to compensate (tabletop top = z = 0).
        tz_offset = tt / 2.0

        for leg_name, (lx, ly, lz) in leg_positions.items():
            leg = SolvedPart(
                name=leg_name,
                part_type="leg",
                profile=parameters.profile,
                board=None,
                material="aluminum",
                extrusion_length=leg_length,
                pose=Pose.from_translation(lx, ly, lz + tz_offset),
                joint_origin=Pose.from_translation(0.0, 0.0, leg_length / 2.0),
            )
            solved_parts.append(leg)
            logger.debug(
                f"{leg_name}: length={leg_length:.1f} mm, "
                f"pos=({lx:.1f}, {ly:.1f}, {lz:.1f})"
            )

        # ------------------------------------------------------------------
        # Beams — horizontal, symmetric extrusion
        #
        # Beam centre-line is at z = -tt (tabletop bottom).  The beam sits
        # entirely below the tabletop (top face at z = -tt + ps/2 = -8 mm
        # is still below tabletop bottom z = -tt when … wait.
        #
        # Actually the beam profile extends ±ps/2 = ±10 mm, so with the
        # centre at z = -18 the top face reaches z = -8 mm — which is
        # *inside* the 18 mm tabletop.  We therefore lower the beam
        # centre-line by ps/2 so the beam top sits flush with the
        # tabletop bottom::
        #
        #     beam_centre_z = -tt - ps / 2   →   top at -tt, bottom at -tt - ps
        # ------------------------------------------------------------------
        beam_centre_z = -tt - ps / 2.0

        # Both beam pairs fit face-to-face between the perpendicular beams
        # to eliminate corner interference at all four corners.
        beam_length_fb = w - 2.0 * ps  #  1160 mm — inner face to inner face
        beam_length_lr = d - 2.0 * ps  #  560 mm — inner face to inner face

        beam_specs = {
            "beam_front": {
                "length": beam_length_fb,
                "pose": Pose.from_degrees(
                    x=0.0,
                    y= half_d,
                    z=beam_centre_z + tz_offset,
                    pitch_deg=-90.0,
                ),
            },
            "beam_back": {
                "length": beam_length_fb,
                "pose": Pose.from_degrees(
                    x=0.0,
                    y=-half_d,
                    z=beam_centre_z + tz_offset,
                    pitch_deg=-90.0,
                ),
            },
            "beam_left": {
                "length": beam_length_lr,
                "pose": Pose.from_degrees(
                    x=-half_w,
                    y=0.0,
                    z=beam_centre_z + tz_offset,
                    roll_deg=-90.0,
                ),
            },
            "beam_right": {
                "length": beam_length_lr,
                "pose": Pose.from_degrees(
                    x= half_w,
                    y=0.0,
                    z=beam_centre_z + tz_offset,
                    roll_deg=-90.0,
                ),
            },
        }

        for beam_name, spec in beam_specs.items():
            beam = SolvedPart(
                name=beam_name,
                part_type="beam",
                profile=parameters.profile,
                board=None,
                material="aluminum",
                extrusion_length=spec["length"],
                pose=spec["pose"],
                joint_origin=Pose.origin(),
            )
            solved_parts.append(beam)
            logger.debug(
                f"{beam_name}: length={spec['length']:.1f} mm"
            )

        logger.info(
            f"Desk solved: {len(solved_parts)} parts "
            f"({parameters.leg_count} legs, {parameters.beam_count} beams, 1 tabletop)"
        )

        return SolverOutput(
            furniture_type="desk",
            template_name=template.name,
            parts=solved_parts,
        )


# ---------------------------------------------------------------------------
# Auto-register on import
# ---------------------------------------------------------------------------

register_solver("desk", DeskSolver)
