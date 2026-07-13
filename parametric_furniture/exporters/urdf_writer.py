"""URDF writer — converts a FurnitureAssembly to URDF XML.

Generates a complete URDF file with:
- <link> elements with visual, collision, and inertial sections
- <joint type="fixed"> elements connecting all furniture parts
- Relative mesh paths for portability (e.g. ``meshes/visual/part.stl``)

Standard output layout::

    <output>/<package_name>/
        <name>.urdf
        meshes/visual/<part>.stl
        meshes/collision/<part>.stl
        cad/<part>.step

The URDFWriter does NOT recompute any poses or dimensions.
All data comes from the FurnitureAssembly as computed by the Builder.
"""

from __future__ import annotations

from pathlib import Path
from xml.dom import minidom
from xml.etree.ElementTree import Element, SubElement, ElementTree

from loguru import logger

from ..config import AppConfig
from ..models.furniture import FurnitureAssembly, FurnitureJoint, FurnitureLink


class URDFWriter:
    """Writes a FurnitureAssembly to a URDF XML file.

    Mesh references are written as relative paths from the URDF
    file location (e.g. ``../meshes/visual/leg.stl``). This makes
    the output package portable — it can be relocated without
    breaking mesh references.

    Usage:
        writer = URDFWriter(config)
        writer.write(assembly, output_path)   # output_path points into urdf/
    """

    def __init__(self, config: AppConfig | None = None) -> None:
        """Initialize the URDF writer.

        Args:
            config: Application configuration.
        """
        self._config = config or AppConfig()

    def write(self, assembly: FurnitureAssembly, output_path: str | Path) -> Path:
        """Write the assembly to a URDF file.

        The output_path should point to the package root
        (e.g. ``output/basic_desk/basic_desk.urdf``).
        Mesh references are resolved relative to the URDF file's directory.

        Args:
            assembly: The furniture assembly to export.
            output_path: Destination path for the URDF file.

        Returns:
            Path to the written URDF file.

        Raises:
            ValueError: If the assembly is empty.
        """
        output_path = Path(output_path)

        if not assembly.links:
            raise ValueError(
                "Cannot write URDF: assembly has no links. "
                "Run the Builder first to generate parts."
            )

        logger.info(f"Writing URDF: {output_path}")

        robot_name = assembly.name.replace(" ", "_").lower()
        root = Element("robot", {"name": robot_name})

        # URDF file directory — used to resolve relative mesh paths
        urdf_dir = output_path.resolve().parent

        # Add virtual base_link
        self._add_base_link(root)

        # Add all furniture links
        for link in assembly.links:
            self._add_link(root, link, urdf_dir)

        # Add all joints
        for joint in assembly.joints:
            self._add_joint(root, joint)

        # Format and write
        tree = ElementTree(root)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        xml_str = minidom.parseString(
            self._element_to_string(root)
        ).toprettyxml(indent="  ", encoding="utf-8")

        with open(output_path, "wb") as f:
            f.write(xml_str)

        logger.info(
            f"URDF written: {output_path} "
            f"({len(assembly.links)} links, {len(assembly.joints)} joints)"
        )
        return output_path

    def write_to_string(self, assembly: FurnitureAssembly) -> str:
        """Write the assembly to a URDF XML string.

        Uses a dummy URDF directory for relative path resolution.

        Args:
            assembly: The furniture assembly to export.

        Returns:
            URDF XML as a formatted string.

        Raises:
            ValueError: If the assembly has no links.
        """
        if not assembly.links:
            raise ValueError(
                "Cannot write URDF: assembly has no links. "
                "Run the Builder first to generate parts."
            )

        robot_name = assembly.name.replace(" ", "_").lower()
        root = Element("robot", {"name": robot_name})

        self._add_base_link(root)
        for link in assembly.links:
            self._add_link(root, link, urdf_dir=Path("."))
        for joint in assembly.joints:
            self._add_joint(root, joint)

        xml_str = self._element_to_string(root)
        return minidom.parseString(xml_str).toprettyxml(indent="  ")

    # URDF uses **metres** for all positions. The rest of the pipeline
    # works in millimetres, so we convert xyz values here.
    _MM_TO_M = 0.001

    # ------------------------------------------------------------------
    # Float formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _ff(val: float) -> str:
        """Format a float for URDF output, stripping redundant trailing zeros.

        ``0.0`` → ``"0"``, ``1.570796`` → ``"1.570796"``,
        ``9.072`` → ``"9.072"``.

        Args:
            val: The float value to format.

        Returns:
            A clean string representation.
        """
        s = f"{val:.6f}"
        s = s.rstrip("0").rstrip(".")
        return "0" if not s or s == "-" else s

    @classmethod
    def _fmt_origin(cls, pose: object) -> dict[str, str]:
        """Build origin attributes (rpy-before-xyz, matching UR5e convention).

        Converts xyz from millimetres (internal representation) to metres
        (URDF standard). Rotation angles are already in radians and pass
        through unchanged.

        Args:
            pose: Object with x, y, z, roll, pitch, yaw attributes.

        Returns:
            Dict with ``"rpy"`` and ``"xyz"`` keys.
        """
        s = cls._MM_TO_M
        return {
            "rpy": f"{cls._ff(pose.roll)} {cls._ff(pose.pitch)} {cls._ff(pose.yaw)}",
            "xyz": f"{cls._ff(pose.x * s)} {cls._ff(pose.y * s)} {cls._ff(pose.z * s)}",
        }

    # ------------------------------------------------------------------
    # Internal XML builders
    # ------------------------------------------------------------------

    @staticmethod
    def _add_base_link(root: Element) -> None:
        """Add the virtual base_link to the URDF.

        base_link is the root of the kinematic tree. All furniture
        assemblies attach to it via fixed joints.

        Args:
            root: The <robot> root element.
        """
        SubElement(root, "link", {"name": "base_link"})

    @staticmethod
    def _add_link(root: Element, link: FurnitureLink, urdf_dir: Path) -> None:
        """Add a <link> element to the URDF.

        Each link contains:
        - <visual>: mesh reference for rendering
        - <collision>: mesh reference for collision detection
        - <inertial>: mass and inertia tensor

        Mesh filenames are written as relative paths from the URDF file
        (e.g. ``meshes/visual/tabletop.stl``).

        The output format matches reference robot models (e.g. UR5e):
        ``rpy`` before ``xyz`` in origins, no redundant ``scale``
        attribute on meshes.

        Args:
            root: The <robot> root element.
            link: The furniture link to add.
            urdf_dir: Absolute path to the directory containing the URDF file.
        """
        elem = SubElement(root, "link", {"name": link.name})

        # Visual
        if link.visual_mesh is not None:
            mesh_path = urdf_dir / link.visual_mesh
            if mesh_path.exists():
                visual = SubElement(elem, "visual")
                SubElement(
                    visual, "origin", {"rpy": "0 0 0", "xyz": "0 0 0"}
                )
                geometry = SubElement(visual, "geometry")
                SubElement(
                    geometry,
                    "mesh",
                    {
                        "filename": str(link.visual_mesh.as_posix()),
                        "scale": "0.001 0.001 0.001",
                    },
                )

        # Collision
        if link.collision_mesh is not None:
            mesh_path = urdf_dir / link.collision_mesh
            if mesh_path.exists():
                collision = SubElement(elem, "collision")
                SubElement(
                    collision, "origin", {"rpy": "0 0 0", "xyz": "0 0 0"}
                )
                geometry = SubElement(collision, "geometry")
                SubElement(
                    geometry,
                    "mesh",
                    {
                        "filename": str(link.collision_mesh.as_posix()),
                        "scale": "0.001 0.001 0.001",
                    },
                )

        # Inertial
        URDFWriter._add_inertial(elem, link)

    @staticmethod
    def _add_inertial(elem: Element, link: FurnitureLink) -> None:
        """Add the <inertial> block to a link element.

        Origin attributes use ``rpy``-before-``xyz`` ordering to match
        common reference robot models (e.g. UR5e).

        Args:
            elem: The <link> element to add inertial to.
            link: The furniture link with inertial data.
        """
        inertial_data = link.inertial
        inertial = SubElement(elem, "inertial")

        # Origin (rpy before xyz)
        SubElement(inertial, "origin", URDFWriter._fmt_origin(inertial_data.origin))

        # Mass
        SubElement(
            inertial,
            "mass",
            {"value": URDFWriter._ff(inertial_data.mass)},
        )

        # Inertia tensor
        SubElement(
            inertial,
            "inertia",
            {
                "ixx": URDFWriter._ff(inertial_data.ixx),
                "iyy": URDFWriter._ff(inertial_data.iyy),
                "izz": URDFWriter._ff(inertial_data.izz),
                "ixy": URDFWriter._ff(inertial_data.ixy),
                "ixz": URDFWriter._ff(inertial_data.ixz),
                "iyz": URDFWriter._ff(inertial_data.iyz),
            },
        )

    @staticmethod
    def _add_joint(root: Element, joint: FurnitureJoint) -> None:
        """Add a <joint> element to the URDF.

        All furniture joints are fixed type since parts don't move.

        Origin is placed before parent/child, matching the element
        ordering used by reference robot models (e.g. UR5e).

        Args:
            root: The <robot> root element.
            joint: The furniture joint to add.
        """
        elem = SubElement(
            root,
            "joint",
            {
                "name": joint.name,
                "type": joint.joint_type,
            },
        )

        # Origin before parent/child (matching UR5e convention)
        SubElement(elem, "origin", URDFWriter._fmt_origin(joint.origin))

        SubElement(elem, "parent", {"link": joint.parent})
        SubElement(elem, "child", {"link": joint.child})

    @staticmethod
    def _element_to_string(element: Element) -> str:
        """Convert an ElementTree Element to a byte string for minidom.

        Args:
            element: The root XML element.

        Returns:
            XML string representation.
        """
        from xml.etree.ElementTree import tostring

        return tostring(element, encoding="utf-8")
