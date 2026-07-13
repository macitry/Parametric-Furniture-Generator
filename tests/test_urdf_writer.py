"""Tests for the URDF writer."""

from __future__ import annotations

import tempfile
from pathlib import Path
from xml.etree.ElementTree import parse as parse_xml

import pytest

from parametric_furniture.exporters.urdf_writer import URDFWriter
from parametric_furniture.models.furniture import (
    FurnitureAssembly,
    FurnitureJoint,
    FurnitureLink,
    FurniturePart,
    InertialData,
)
from parametric_furniture.models.pose import Pose


class TestURDFWriter:
    """Tests for URDF XML generation."""

    @pytest.fixture
    def writer(self) -> URDFWriter:
        """Create a URDFWriter instance."""
        return URDFWriter()

    @pytest.fixture
    def sample_assembly(self) -> FurnitureAssembly:
        """Create a minimal furniture assembly for testing."""
        inertial = InertialData(
            mass=10.0,
            ixx=0.1,
            iyy=0.1,
            izz=0.1,
        )

        part = FurniturePart(
            name="test_leg",
            part_type="leg",
            mass_kg=10.0,
            dimensions={"length": 732.0},
        )

        link = FurnitureLink(
            name="test_leg",
            part=part,
            inertial=inertial,
            pose=Pose.from_translation(0.5, 0.3, -0.375),
        )

        joint = FurnitureJoint(
            name="base_to_test_leg",
            joint_type="fixed",
            parent="base_link",
            child="test_leg",
            origin=Pose.from_translation(0.5, 0.3, -0.009),
        )

        return FurnitureAssembly(
            name="Test Desk",
            furniture_type="desk",
            parts=[part],
            links=[link],
            joints=[joint],
        )

    def test_write_to_string(self, writer: URDFWriter, sample_assembly: FurnitureAssembly) -> None:
        """Should produce valid XML string output."""
        xml_str = writer.write_to_string(sample_assembly)

        assert '<?xml version="1.0" ?>' in xml_str or '<?xml version="1.0"?>' in xml_str
        assert '<robot name="test_desk">' in xml_str
        assert '<link name="base_link"/>' in xml_str
        assert '<link name="test_leg">' in xml_str
        assert '<joint name="base_to_test_leg"' in xml_str

    def test_write_to_file(self, writer: URDFWriter, sample_assembly: FurnitureAssembly) -> None:
        """Should write a valid URDF file to disk."""
        with tempfile.NamedTemporaryFile(
            suffix=".urdf", delete=False
        ) as f:
            output_path = Path(f.name)

        try:
            result = writer.write(sample_assembly, output_path)
            assert result.exists()
            assert result.stat().st_size > 0

            # Parse and validate XML structure
            tree = parse_xml(str(result))
            root = tree.getroot()
            assert root.tag == "robot"
            assert root.get("name") == "test_desk"

            # Check links
            links = root.findall("link")
            link_names = {link.get("name") for link in links}
            assert "base_link" in link_names
            assert "test_leg" in link_names

            # Check joints
            joints = root.findall("joint")
            assert len(joints) == 1
            joint = joints[0]
            assert joint.get("name") == "base_to_test_leg"
            assert joint.get("type") == "fixed"

            parent = joint.find("parent")
            assert parent.get("link") == "base_link"

            child = joint.find("child")
            assert child.get("link") == "test_leg"

        finally:
            if output_path.exists():
                output_path.unlink()

    def test_empty_assembly_raises(self, writer: URDFWriter) -> None:
        """Writing an assembly with no links should raise ValueError."""
        empty = FurnitureAssembly(
            name="Empty",
            furniture_type="desk",
            parts=[],
            links=[],
            joints=[],
        )

        with pytest.raises(ValueError):
            writer.write_to_string(empty)

    def test_inertial_values(self, writer: URDFWriter, sample_assembly: FurnitureAssembly) -> None:
        """Inertial block should contain correct mass and inertia values."""
        xml_str = writer.write_to_string(sample_assembly)

        assert "<mass" in xml_str
        assert 'value="10"' in xml_str
        assert "<inertia" in xml_str
        assert 'ixx="0.1"' in xml_str
        assert 'iyy="0.1"' in xml_str
        assert 'izz="0.1"' in xml_str

    def test_joint_origin_format(self, writer: URDFWriter) -> None:
        """Joint origin should use proper xyz and rpy attributes."""
        assembly = FurnitureAssembly(
            name="Test",
            furniture_type="desk",
            links=[
                FurnitureLink(
                    name="part_a",
                    part=FurniturePart(name="part_a", part_type="leg"),
                    inertial=InertialData(mass=1.0, ixx=0.1, iyy=0.1, izz=0.1),
                    pose=Pose.origin(),
                ),
            ],
            joints=[
                FurnitureJoint(
                    name="joint_1",
                    parent="base_link",
                    child="part_a",
                    origin=Pose(x=1.0, y=2.0, z=3.0, roll=0.1, pitch=0.2, yaw=0.3),
                ),
            ],
        )

        xml_str = writer.write_to_string(assembly)
        # xyz converted from mm to m (URDF standard)
        assert 'xyz="0.001 0.002 0.003"' in xml_str
        assert 'rpy="0.1 0.2 0.3"' in xml_str

    def test_multiple_joints(self, writer: URDFWriter) -> None:
        """Assembly with multiple parts should generate correct joints."""
        parts = []
        links = []
        joints = []

        for i, name in enumerate(["leg_fl", "leg_fr", "leg_bl", "leg_br"]):
            part = FurniturePart(name=name, part_type="leg")
            link = FurnitureLink(
                name=name,
                part=part,
                inertial=InertialData(mass=1.0, ixx=0.1, iyy=0.1, izz=0.1),
                pose=Pose.from_translation(float(i), 0.0, 0.0),
            )
            links.append(link)
            parts.append(part)
            joints.append(
                FurnitureJoint(
                    name=f"tabletop_to_{name}",
                    parent="tabletop",
                    child=name,
                )
            )

        assembly = FurnitureAssembly(
            name="Four Leg Desk",
            furniture_type="desk",
            parts=parts,
            links=links,
            joints=joints,
        )

        xml_str = writer.write_to_string(assembly)

        # Should have base_link + 4 furniture links = 5 links
        assert xml_str.count('<link name=') == 5
        # Should have 4 joints
        assert xml_str.count('<joint name=') == 4
