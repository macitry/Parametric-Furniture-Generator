"""Material properties for furniture construction.

This module provides material reference data used by the Builder
to compute physical properties (mass, density, appearance).
"""

from __future__ import annotations


# Material densities in kg/m³
MATERIAL_DENSITIES: dict[str, float] = {
    # Metals
    "aluminum": 2700.0,
    "steel": 7850.0,
    "stainless_steel": 8000.0,
    # Wood products
    "plywood": 700.0,
    "mdf": 750.0,
    "oak": 900.0,
    "birch": 670.0,
    "walnut": 650.0,
    "pine": 550.0,
    "maple": 720.0,
    "cherry": 630.0,
    # Engineered
    "particle_board": 680.0,
    "osb": 640.0,
    "bamboo": 700.0,
}

# Board materials with thickness presets (mm)
BOARD_PRESETS: dict[str, dict[str, float]] = {
    "plywood": {
        "standard_thickness": 18.0,
        "min_thickness": 6.0,
        "max_thickness": 25.0,
        "density": 700.0,
    },
    "mdf": {
        "standard_thickness": 18.0,
        "min_thickness": 9.0,
        "max_thickness": 30.0,
        "density": 750.0,
    },
    "oak": {
        "standard_thickness": 20.0,
        "min_thickness": 12.0,
        "max_thickness": 40.0,
        "density": 900.0,
    },
}

# Aluminum profile cross-section sizes
PROFILE_SIZES: dict[str, float] = {
    "2020": 20.0,
    "3030": 30.0,
    "4040": 40.0,
}
