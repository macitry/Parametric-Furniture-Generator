"""Tests for furniture template loading and validation."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from models.template import (
    Connection,
    FurnitureTemplate,
    PartTemplate,
    Topology,
)


class TestPartTemplate:
    """Tests for PartTemplate validation."""

    def test_valid_part(self) -> None:
        """A valid part template should be created without errors."""
        part = PartTemplate(
            name="leg_front_left",
            part_type="leg",
            profile="2020",
            material="aluminum",
        )
        assert part.name == "leg_front_left"
        assert part.part_type == "leg"
        assert part.profile == "2020"

    def test_empty_name_raises(self) -> None:
        """Empty part name should raise ValueError."""
        with pytest.raises(ValueError):
            PartTemplate(name="", part_type="leg", profile="2020")

    def test_invalid_name_characters(self) -> None:
        """Part names with special characters should raise ValueError."""
        with pytest.raises(ValueError):
            PartTemplate(name="leg with spaces", part_type="leg", profile="2020")

    def test_part_with_board(self) -> None:
        """A tabletop part should accept a board material."""
        part = PartTemplate(
            name="tabletop",
            part_type="tabletop",
            board="plywood",
            material="wood",
            profile=None,
        )
        assert part.board == "plywood"
        assert part.profile is None

    def test_optional_profile(self) -> None:
        """Profile should be optional for tabletop-type parts."""
        part = PartTemplate(
            name="tabletop",
            part_type="tabletop",
            board="mdf",
            material="wood",
        )
        assert part.profile is None


class TestFurnitureTemplate:
    """Tests for FurnitureTemplate loading and validation."""

    def test_load_from_yaml(self, desk_template_path: Path) -> None:
        """Should load and validate the basic desk template."""
        template = FurnitureTemplate.from_yaml(desk_template_path)

        assert template.name == "Basic Desk"
        assert template.type == "desk"
        assert len(template.parts) == 9  # 4 legs + 4 beams + 1 tabletop

    def test_get_parts_by_type(self, desk_template_path: Path) -> None:
        """Should filter parts by type."""
        template = FurnitureTemplate.from_yaml(desk_template_path)

        legs = template.get_parts_by_type("leg")
        assert len(legs) == 4

        beams = template.get_parts_by_type("beam")
        assert len(beams) == 4

        tabletops = template.get_parts_by_type("tabletop")
        assert len(tabletops) == 1

    def test_get_part_by_name(self, desk_template_path: Path) -> None:
        """Should retrieve a specific part by name."""
        template = FurnitureTemplate.from_yaml(desk_template_path)

        part = template.get_part("leg_front_left")
        assert part.name == "leg_front_left"
        assert part.part_type == "leg"

    def test_get_part_missing_raises_key_error(self, desk_template_path: Path) -> None:
        """Should raise KeyError for unknown part names."""
        template = FurnitureTemplate.from_yaml(desk_template_path)

        with pytest.raises(KeyError):
            template.get_part("nonexistent_part")

    def test_topology_validation(self, desk_template_path: Path) -> None:
        """Topology should reference valid part names."""
        template = FurnitureTemplate.from_yaml(desk_template_path)

        assert len(template.topology.connections) == 12
        for conn in template.topology.connections:
            assert conn.part_a in [p.name for p in template.parts]
            assert conn.part_b in [p.name for p in template.parts]

    def test_nonexistent_yaml_raises(self) -> None:
        """Loading a non-existent YAML file should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            FurnitureTemplate.from_yaml("nonexistent.yaml")

    def test_empty_yaml_raises(self) -> None:
        """An empty YAML file should raise ValueError."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write("")

        try:
            with pytest.raises(ValueError):
                FurnitureTemplate.from_yaml(f.name)
        finally:
            Path(f.name).unlink()

    def test_invalid_topology_references(self) -> None:
        """A topology referencing unknown parts should raise ValueError."""
        import yaml

        data = {
            "name": "Test",
            "type": "desk",
            "parts": [
                {"name": "part_a", "part_type": "leg", "profile": "2020", "material": "aluminum"},
            ],
            "topology": {
                "connections": [
                    {"part_a": "part_a", "part_b": "nonexistent"},
                ]
            },
        }

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(data, f)

        try:
            with pytest.raises(ValueError):
                FurnitureTemplate.from_yaml(f.name)
        finally:
            Path(f.name).unlink()

    def test_missing_parts_field_raises(self) -> None:
        """A template without parts should raise validation error."""
        import yaml

        data = {"name": "Test", "type": "desk"}

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(data, f)

        try:
            with pytest.raises(ValueError):
                FurnitureTemplate.from_yaml(f.name)
        finally:
            Path(f.name).unlink()
