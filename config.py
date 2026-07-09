"""Global configuration for the Parametric Furniture Generator.

This module provides centralized configuration management using Pydantic's
BaseSettings, supporting environment variable overrides and .env files.

Configuration categories:
- Paths: Template, library, and output directories.
- Build: Default build parameters.
- Export: URDF and mesh export settings.
- Logging: Log level and format.
"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PathsConfig(BaseSettings):
    """Filesystem path configuration."""

    model_config = SettingsConfigDict(env_prefix="FURNITURE_PATHS_")

    templates_dir: Path = Field(
        default=Path("templates"),
        description="Directory containing furniture template YAML files.",
    )
    library_dir: Path = Field(
        default=Path("library"),
        description="Directory containing furniture library resources.",
    )
    output_dir: Path = Field(
        default=Path("output"),
        description="Default output directory for generated files.",
    )
    profiles_dir: Path = Field(
        default=Path("library/profiles"),
        description="Directory containing profile DXF files.",
    )


class BuildConfig(BaseSettings):
    """Build operation defaults."""

    model_config = SettingsConfigDict(env_prefix="FURNITURE_BUILD_")

    export_step: bool = Field(
        default=True,
        description="Export STEP files for each part.",
    )
    export_stl: bool = Field(
        default=True,
        description="Export STL files for each part.",
    )
    stl_linear_deflection: float = Field(
        default=0.1,
        gt=0.0,
        description="STL mesh linear deflection in mm.",
    )
    stl_angular_deflection: float = Field(
        default=0.5,
        gt=0.0,
        description="STL mesh angular deflection in radians.",
    )
    stl_max_edge_length: float = Field(
        default=50.0,
        ge=0.0,
        description="Max triangle edge length in mm. 0 = no subdivision.",
    )


class ExportConfig(BaseSettings):
    """Export settings."""

    model_config = SettingsConfigDict(env_prefix="FURNITURE_EXPORT_")

    urdf_robot_name: str = Field(
        default="furniture",
        description="Default robot name in URDF output.",
    )


class LoggingConfig(BaseSettings):
    """Logging configuration."""

    model_config = SettingsConfigDict(env_prefix="FURNITURE_LOG_")

    level: str = Field(
        default="INFO",
        pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$",
        description="Default log level.",
    )


class AppConfig(BaseSettings):
    """Top-level application configuration.

    Aggregates all configuration categories. Settings can be overridden
    via environment variables with the FURNITURE_ prefix.

    Usage:
        config = AppConfig()
        print(config.paths.output_dir)
    """

    model_config = SettingsConfigDict(
        env_prefix="FURNITURE_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    paths: PathsConfig = Field(default_factory=PathsConfig)
    build: BuildConfig = Field(default_factory=BuildConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
