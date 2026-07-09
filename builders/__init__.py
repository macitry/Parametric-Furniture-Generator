"""Furniture builders — generate 3D geometry and create assemblies."""

from .furniture_builder import (
    AbstractFurnitureBuilder,
    MaterialDensity,
    register_builder,
    get_builder,
    list_builders,
)
from .desk_builder import DeskBuilder

__all__ = [
    "AbstractFurnitureBuilder",
    "MaterialDensity",
    "register_builder",
    "get_builder",
    "list_builders",
    "DeskBuilder",
]
