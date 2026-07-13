"""Shared test fixtures for the Parametric Furniture Generator."""

from __future__ import annotations

from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def project_root() -> Path:
    """Return the project root directory."""
    return _PROJECT_ROOT


@pytest.fixture
def templates_dir() -> Path:
    """Return the templates directory."""
    return _PROJECT_ROOT / "templates"


@pytest.fixture
def desk_template_path() -> Path:
    """Return the path to the basic desk template."""
    return _PROJECT_ROOT / "templates" / "desk" / "basic.yaml"
