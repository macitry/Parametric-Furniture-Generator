"""Furniture solvers — compute dimensions, poses, and joint origins."""

from .furniture_solver import (
    AbstractFurnitureSolver,
    SolvedPart,
    SolverOutput,
    register_solver,
    get_solver,
    list_solvers,
)
from .desk_solver import DeskSolver

__all__ = [
    "AbstractFurnitureSolver",
    "SolvedPart",
    "SolverOutput",
    "register_solver",
    "get_solver",
    "list_solvers",
    "DeskSolver",
]
