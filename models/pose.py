"""Pose representation for part positioning in 3D space.

A Pose defines the position (x, y, z in mm) and orientation
(roll, pitch, yaw in radians) of a part relative to the
assembly coordinate frame.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Pose:
    """An immutable 3D pose with position and Euler-angle orientation.

    Position is in millimeters. Orientation uses intrinsic ZYX Euler angles
    (roll about X, pitch about Y, yaw about Z) in radians.

    Attributes:
        x: X position in mm.
        y: Y position in mm.
        z: Z position in mm.
        roll: Rotation about X axis (radians).
        pitch: Rotation about Y axis (radians).
        yaw: Rotation about Z axis (radians).
    """

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0

    @classmethod
    def origin(cls) -> Pose:
        """Return the identity pose at the origin with zero rotation."""
        return cls()

    @classmethod
    def from_translation(cls, x: float, y: float, z: float) -> Pose:
        """Create a pose with only translation, no rotation."""
        return cls(x=x, y=y, z=z)

    @classmethod
    def from_degrees(
        cls,
        x: float = 0.0,
        y: float = 0.0,
        z: float = 0.0,
        roll_deg: float = 0.0,
        pitch_deg: float = 0.0,
        yaw_deg: float = 0.0,
    ) -> Pose:
        """Create a pose with orientation specified in degrees."""
        return cls(
            x=x,
            y=y,
            z=z,
            roll=math.radians(roll_deg),
            pitch=math.radians(pitch_deg),
            yaw=math.radians(yaw_deg),
        )

    def translated(self, dx: float = 0.0, dy: float = 0.0, dz: float = 0.0) -> Pose:
        """Return a new Pose offset by the given translation."""
        return Pose(
            x=self.x + dx,
            y=self.y + dy,
            z=self.z + dz,
            roll=self.roll,
            pitch=self.pitch,
            yaw=self.yaw,
        )

    def to_urdf_origin(self) -> str:
        """Format as a URDF <origin> attribute string.

        Returns:
            String in format 'x y z roll pitch yaw' for URDF.
        """
        return f"{self.x:.6f} {self.y:.6f} {self.z:.6f} {self.roll:.6f} {self.pitch:.6f} {self.yaw:.6f}"

    def to_xyz_rpy(self) -> tuple[float, float, float, float, float, float]:
        """Return the pose as a flat (x, y, z, roll, pitch, yaw) tuple."""
        return (self.x, self.y, self.z, self.roll, self.pitch, self.yaw)
