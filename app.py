#!/usr/bin/env python3
"""Parametric Furniture Generator — thin wrapper for development.

For production use, install the package and run::

    pip install -e .
    furniture build templates/desk/basic.yaml --width 1200 --depth 600 --height 750

This wrapper is kept for backward compatibility during development.
"""

from __future__ import annotations

from parametric_furniture.cli import main

if __name__ == "__main__":
    main()
