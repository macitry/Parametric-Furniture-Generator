"""Desk builder — generates 3D geometry for desk furniture.

Uses the VisualCAD pipeline to create 3D solids from DXF profiles,
exports mesh files, computes mass/inertia, and assembles the
FurnitureAssembly.

Output follows the standard URDF package layout (matching UR5e and
other reference robot models)::

    <output>/<package_name>/
        <package_name>.urdf
        meshes/
            visual/
                <part>.stl
            collision/
                <part>.stl
        cad/
            <part>.step

All mesh references in the URDF use relative paths (e.g.
``meshes/visual/tabletop.stl``) so the package is portable.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from loguru import logger

from ..config import AppConfig
from ..models.furniture import (
    FurnitureAssembly,
    FurnitureJoint,
    FurnitureLink,
    FurniturePart,
    InertialData,
)
from ..models.pose import Pose
from ..solvers.furniture_solver import SolvedPart, SolverOutput

from .furniture_builder import (
    AbstractFurnitureBuilder,
    MaterialDensity,
    register_builder,
)

# VisualCAD is an external dependency at a known path.
_VISUALCAD_ROOT = Path(__file__).resolve().parent.parent.parent / "visualcad"
if str(_VISUALCAD_ROOT) not in sys.path:
    sys.path.insert(0, str(_VISUALCAD_ROOT))


class DeskBuilder(AbstractFurnitureBuilder):
    """Builder for desk furniture.

    Generates 3D parts using VisualCAD, exports STEP/STL files
    into the standard URDF package layout, computes mass and inertia,
    and assembles the FurnitureAssembly.

    Usage:
        builder = DeskBuilder(config)
        assembly = builder.build(solver_output)
    """

    @property
    def furniture_type(self) -> str:
        return "desk"

    def build(self, solver_output: SolverOutput) -> FurnitureAssembly:
        """Build the complete desk assembly.

        Args:
            solver_output: Computed desk dimensions and poses.

        Returns:
            FurnitureAssembly ready for URDF export.
        """
        from visualcad import export_solid, pipeline

        package_name = solver_output.template_name.replace(" ", "_").lower()
        logger.info(f"Building desk assembly: {solver_output.template_name}")

        # ------------------------------------------------------------------
        # Create standard URDF package directory layout
        # ------------------------------------------------------------------
        pkg = self.create_package_dirs(package_name)

        profile_dir = self.config.paths.profiles_dir
        profile_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_profiles_exist(profile_dir)

        parts: list[FurniturePart] = []
        links: list[FurnitureLink] = []
        joints: list[FurnitureJoint] = []

        # Temp directory for DXF generation (cleaned up after build)
        tmp_dir = pkg["root"] / ".tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # ------------------------------------------------------------------
        # Build each part
        # ------------------------------------------------------------------
        for solved in solver_output.parts:
            logger.info(f"Generating part: {solved.name} ({solved.part_type})")

            # Determine DXF path and extrusion height
            if solved.part_type == "tabletop":
                dxf_path = self._generate_tabletop_dxf(
                    tmp_dir,
                    solved.name,
                    solved.tabletop_width,
                    solved.tabletop_depth,
                )
                extrude_height = solved.tabletop_thickness
            else:
                dxf_path = profile_dir / f"{solved.profile}.dxf"
                extrude_height = solved.extrusion_length

            # Generate 3D solid via VisualCAD (symmetric extrusion so
            # the mesh origin is at the geometric centre of every part).
            try:
                solid = pipeline(
                    str(dxf_path),
                    height=extrude_height,
                    direction="symmetric",
                )
                # Some profile DXFs have geometry offset from origin;
                # translate so the profile centre lands at (0, 0).
                cx, cy = self._PROFILE_OFFSETS.get(solved.profile, (0, 0))
                if cx or cy:
                    from build123d import Vector
                    solid = solid.translate(Vector(-cx, -cy, 0))
            except Exception as exc:
                logger.error(f"VisualCAD pipeline failed for {solved.name}: {exc}")
                raise

            # Export paths within the package structure
            step_path: Path | None = None
            visual_stl_path: Path | None = None
            collision_stl_path: Path | None = None

            if self.config.build.export_step:
                step_path = pkg["cad"] / f"{solved.name}.step"
            if self.config.build.export_stl:
                visual_stl_path = pkg["meshes_visual"] / f"{solved.name}.stl"
                collision_stl_path = pkg["meshes_collision"] / f"{solved.name}.stl"

            # Export via VisualCAD (primary STL goes to visual/)
            try:
                export_solid(
                    solid,
                    step_path=str(step_path) if step_path else None,
                    stl_path=str(visual_stl_path) if visual_stl_path else None,
                    linear_deflection=self.config.build.stl_linear_deflection,
                    angular_deflection=self.config.build.stl_angular_deflection,
                    max_edge_length=self.config.build.stl_max_edge_length,
                )
            except Exception as exc:
                logger.error(f"Export failed for {solved.name}: {exc}")
                raise

            # Duplicate visual STL as collision STL (furniture is static,
            # so the same high-resolution mesh works for both)
            if visual_stl_path and collision_stl_path:
                shutil.copy2(str(visual_stl_path), str(collision_stl_path))

            # ------------------------------------------------------------------
            # Mesh paths stored relative to the URDF file location.
            # URDF is at <pkg>/<name>.urdf, meshes at <pkg>/meshes/...
            # So the relative path is ``meshes/visual/<part>.stl``.
            # ------------------------------------------------------------------
            visual_mesh_rel: Path | None = None
            collision_mesh_rel: Path | None = None

            if visual_stl_path:
                visual_mesh_rel = Path("meshes") / "visual" / f"{solved.name}.stl"
            if collision_stl_path:
                collision_mesh_rel = Path("meshes") / "collision" / f"{solved.name}.stl"

            # Compute mass
            volume_mm3 = solid.volume
            density = MaterialDensity.get(solved.material)
            mass_kg = volume_mm3 * 1e-9 * density

            # Compute inertia (analytical approximation)
            inertial = self._compute_inertia(
                solved=solved,
                mass_kg=mass_kg,
                solid=solid,
            )

            # Build dimensions dict
            if solved.part_type == "tabletop":
                dimensions = {
                    "width": solved.tabletop_width,
                    "depth": solved.tabletop_depth,
                    "thickness": solved.tabletop_thickness,
                }
            else:
                dimensions = {
                    "length": solved.extrusion_length,
                    "profile_size": float(solved.profile[:2]) if solved.profile else 0.0,
                }

            part = FurniturePart(
                name=solved.name,
                part_type=solved.part_type,
                step_path=step_path,
                stl_path=visual_stl_path,
                mass_kg=mass_kg,
                dimensions=dimensions,
            )
            parts.append(part)

            link = FurnitureLink(
                name=solved.name,
                part=part,
                visual_mesh=visual_mesh_rel,
                collision_mesh=collision_mesh_rel,
                inertial=inertial,
                pose=solved.pose,
            )
            links.append(link)

            logger.info(
                f"  {solved.name}: volume={volume_mm3:.1f} mm³, "
                f"mass={mass_kg:.3f} kg"
            )

        # Clean up temp directory
        shutil.rmtree(tmp_dir, ignore_errors=True)

        # ------------------------------------------------------------------
        # Create joints
        # ------------------------------------------------------------------
        base_link_name = "base_link"

        # Find the tabletop link so we can use its computed pose
        tabletop_link = next(lnk for lnk in links if lnk.name == "tabletop")

        # Tabletop fixed to base_link.
        # The tabletop link is shifted down by tt/2 so its top surface
        # stays at the assembly origin (z = 0).
        joints.append(
            FurnitureJoint(
                name="base_to_tabletop",
                joint_type="fixed",
                parent=base_link_name,
                child="tabletop",
                origin=tabletop_link.pose,
            )
        )

        # Each leg and beam fixed to tabletop
        for solved in solver_output.parts:
            if solved.part_type == "tabletop":
                continue

            joints.append(
                FurnitureJoint(
                    name=f"tabletop_to_{solved.name}",
                    joint_type="fixed",
                    parent="tabletop",
                    child=solved.name,
                    origin=solved.pose,
                )
            )

        logger.info(
            f"Assembly complete: {len(parts)} parts, "
            f"{len(links)} links, {len(joints)} joints"
        )

        return FurnitureAssembly(
            name=solver_output.template_name,
            furniture_type="desk",
            parts=parts,
            links=links,
            joints=joints,
        )

    # ------------------------------------------------------------------
    # Profile DXF Generation
    # ------------------------------------------------------------------

    # T-slot geometry parameters for each profile series.
    #
    #   face    — outer square side length (mm)
    #   slot_w  — opening width at the face
    #   neck_w  — narrowest neck width (just inside the face)
    #   int_w   — interior channel width (where the T-nut head sits)
    #   neck_d  — depth of the neck constriction from the face
    #   total_d — total slot depth from the face
    #   hole_r  — centre hole radius (for weight reduction / wiring)
    _PROFILE_SPECS: dict[str, dict[str, float]] = {
        "2020": dict(face=20, slot_w=6, neck_w=4.5, int_w=8,
                      neck_d=1.0, total_d=6.0, hole_r=2.1),
        "3030": dict(face=30, slot_w=8, neck_w=6.0, int_w=10,
                      neck_d=1.5, total_d=8.0, hole_r=3.4),
        "4040": dict(face=40, slot_w=8, neck_w=6.0, int_w=10,
                      neck_d=1.5, total_d=8.0, hole_r=4.2),
    }

    # DXF files whose geometry is not centred at (0, 0).  After the
    # VisualCAD pipeline produces the solid we translate it so the
    # profile lands at the origin.  Values are the (cx, cy) centre
    # of the extruded solid as reported by ``solid.center()``.
    _PROFILE_OFFSETS: dict[str, tuple[float, float]] = {
        # Measured from MJ-8-3030.dxf STL output (60×60 mm bounding box)
        "3030": (378.06, 170.27),
    }

    def _ensure_profiles_exist(self, profile_dir: Path) -> None:
        """Generate aluminium extrusion profile DXF files.

        Creates proper T-slot profiles with centre holes for common
        aluminium extrusion sizes (2020, 3030, 4040).  Previously
        generated files are left untouched.

        For 3030 the original MJ-8-3030.dxf engineering drawing is
        copied as-is (never modified).  The resulting solid is centred
        in XY using a post-extrusion translation.

        Args:
            profile_dir: Directory to store profile DXF files.
        """
        import ezdxf

        for name, spec in self._PROFILE_SPECS.items():
            dxf_path = profile_dir / f"{name}.dxf"
            if dxf_path.exists():
                logger.debug(f"Profile DXF already exists: {dxf_path}")
                continue

            # ---- 3030: copy original engineering DXF (do NOT modify) ----
            if name == "3030":
                source = (
                    Path(__file__).resolve().parent.parent.parent
                    / "visualcad" / "examples" / "MJ-8-3030.dxf"
                )
                if source.exists():
                    import shutil
                    shutil.copy2(str(source), str(dxf_path))
                    logger.info(f"Copied {source.name} → {dxf_path}")
                    continue

            # ---- 2020 / 4040: generate programmatically ----
            logger.info(
                f"Generating {name} profile DXF "
                f"({spec['face']}×{spec['face']} mm, T-slot)"
            )
            doc = ezdxf.new("R2010")
            msp = doc.modelspace()

            outer = self._build_profile_contour(spec)
            msp.add_lwpolyline(outer, close=True, dxfattribs={"layer": "OUTER"})

            msp.add_circle(
                (0, 0),
                spec["hole_r"],
                dxfattribs={"layer": "INNER"},
            )

            doc.saveas(str(dxf_path))
            logger.debug(f"Saved profile DXF: {dxf_path}")

    @staticmethod
    def _build_profile_contour(spec: dict[str, float]) -> list[tuple[float, float]]:
        """Build the outer-contour vertex list for one extrusion profile.

        The contour traces clockwise around the square, dipping into
        the T-slot on each face.  All four slots are identical;
        this function generates the full loop in one pass.

        Args:
            spec: Profile geometry dict from ``_PROFILE_SPECS``.

        Returns:
            List of (x, y) vertex tuples forming a closed polygon.
        """
        h = spec["face"] / 2.0          # half face width  (e.g. 15)
        ow = spec["slot_w"] / 2.0       # half opening      (e.g.  4)
        iw = spec["int_w"] / 2.0        # half interior     (e.g.  5)
        nd = spec["neck_d"]             # neck depth        (e.g.  1.5)
        td = spec["total_d"]            # total slot depth  (e.g.  8.0)

        pts: list[tuple[float, float]] = []

        # ------- top face (y = +h), left → right ----------
        pts.append((-h, h))                   # corner
        pts.append((-ow, h))                  # → opening
        pts.append((-ow, h - nd))             # ↓ neck
        # slot interior (cw): step-left, down, across-right, up
        pts.append((-iw, h - nd))
        pts.append((-iw, h - td))
        pts.append((iw, h - td))
        pts.append((iw, h - nd))
        pts.append((ow, h - nd))              # ↑ neck (right)
        pts.append((ow, h))                   # → face

        # ------- right face (x = +h), top → bottom ---------
        pts.append((h, ow))                   # → opening
        pts.append((h - nd, ow))              # ← neck
        # slot interior (cw): step-down, left, up, right
        pts.append((h - nd, iw))
        pts.append((h - td, iw))
        pts.append((h - td, -iw))
        pts.append((h - nd, -iw))
        pts.append((h - nd, -ow))             # → neck (bottom)
        pts.append((h, -ow))                  # → face

        # ------- bottom face (y = -h), right → left --------
        pts.append((ow, -h))                  # → opening
        pts.append((ow, -h + nd))             # ↑ neck
        # slot interior (cw): step-right, up, across-left, down
        pts.append((iw, -h + nd))
        pts.append((iw, -h + td))
        pts.append((-iw, -h + td))
        pts.append((-iw, -h + nd))
        pts.append((-ow, -h + nd))            # ↓ neck (left)
        pts.append((-ow, -h))                 # → face

        # ------- left face (x = -h), bottom → top ----------
        pts.append((-h, -ow))                 # → opening
        pts.append((-h + nd, -ow))            # → neck
        # slot interior (cw): step-up, right, down, left
        pts.append((-h + nd, -iw))
        pts.append((-h + td, -iw))
        pts.append((-h + td, iw))
        pts.append((-h + nd, iw))
        pts.append((-h + nd, ow))             # ← neck (top)
        pts.append((-h, ow))                  # → face

        return pts

    @staticmethod
    def _generate_tabletop_dxf(
        output_dir: Path,
        name: str,
        width: float,
        depth: float,
    ) -> Path:
        """Generate a rectangular DXF for the tabletop.

        Since tabletop dimensions vary per instance, we generate
        a fresh DXF each time.

        Args:
            output_dir: Directory to save the DXF.
            name: Part name for the filename.
            width: Tabletop width in mm.
            depth: Tabletop depth in mm.

        Returns:
            Path to the generated DXF file.
        """
        import ezdxf

        dxf_path = output_dir / f"{name}_profile.dxf"

        doc = ezdxf.new("R2010")
        msp = doc.modelspace()

        msp.add_lwpolyline(
            [
                (-width / 2.0, -depth / 2.0),
                (width / 2.0, -depth / 2.0),
                (width / 2.0, depth / 2.0),
                (-width / 2.0, depth / 2.0),
            ],
            close=True,
            dxfattribs={"layer": "OUTER"},
        )

        doc.saveas(str(dxf_path))
        logger.debug(f"Generated tabletop DXF: {dxf_path} ({width}×{depth} mm)")
        return dxf_path

    # ------------------------------------------------------------------
    # Inertia Computation
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_inertia(
        solved: SolvedPart,
        mass_kg: float,
        solid: object,
    ) -> InertialData:
        """Compute the inertia tensor for a part using analytical formulas.

        Uses box/rectangular-prism formulas for all parts. For aluminum
        profiles, this approximates them as solid square bars — acceptable
        for URDF dynamics since furniture is static (all fixed joints).

        Axes in the part's local frame:
        - X, Y: cross-section plane of the extrusion
        - Z: extrusion direction

        Args:
            solved: The solved part with dimensions.
            mass_kg: Mass in kilograms.
            solid: The build123d Solid (for bounding box fallback).

        Returns:
            InertialData with the computed inertia tensor.
        """
        if solved.part_type == "tabletop":
            dx = solved.tabletop_width * 1e-3  # m
            dy = solved.tabletop_depth * 1e-3
            dz = solved.tabletop_thickness * 1e-3
        elif solved.profile:
            profile_size = float(solved.profile[:2]) * 1e-3  # m
            dx = profile_size
            dy = profile_size
            dz = solved.extrusion_length * 1e-3  # m
        else:
            # Fallback: use bounding box of the solid
            try:
                bbox = solid.bounding_box()
                dx = (bbox.max.X - bbox.min.X) * 1e-3
                dy = (bbox.max.Y - bbox.min.Y) * 1e-3
                dz = (bbox.max.Z - bbox.min.Z) * 1e-3
            except Exception:
                dx = dy = dz = 0.001  # 1 mm fallback

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


# ---------------------------------------------------------------------------
# Auto-register on import
# ---------------------------------------------------------------------------

register_builder("desk", DeskBuilder)
