# API Reference — Parametric Furniture Generator

> Declarative furniture templates → 3D assemblies → URDF export

---

## Installation

```bash
pip install -e .
```

Requirements: Python ≥ 3.10

Verify:

```bash
furniture --help
```

---

## Quick Start

### CLI (one-liner)

```bash
furniture build templates/desk/basic.yaml --width 1200 --depth 600 --height 750
```

### Python API

```python
from parametric_furniture import (
    FurnitureTemplate,
    DeskParameters,
    DeskSolver,
    DeskBuilder,
    URDFWriter,
    AppConfig,
)

# 1. Load
template = FurnitureTemplate.from_yaml("templates/desk/basic.yaml")

# 2. Parameterize
params = DeskParameters(width=1200, depth=600, height=750, profile="3030")

# 3. Solve (compute dimensions & positions)
solved = DeskSolver().solve(template, params)

# 4. Build (generate 3D geometry)
config = AppConfig()
assembly = DeskBuilder(config).build(solved)

# 5. Export URDF
writer = URDFWriter(config)
writer.write(assembly, "output/my_desk/my_desk.urdf")
```

---

## Architecture

```
User Input                Engine                      Output
───────────    ───────────────────────────────    ──────────────
YAML Template  →  Solver   →  Builder  →  Writer  →  URDF + STL
User Params        (compute)   (3D CAD)    (XML)       STEP
```

| Stage | Module | Responsibility |
|-------|--------|----------------|
| 1. Template | `FurnitureTemplate` | Declare WHAT parts exist and HOW they connect |
| 2. Parameters | `DeskParameters` | User-specified dimensions and materials |
| 3. Solver | `DeskSolver` | Compute every part's size, position, orientation |
| 4. Builder | `DeskBuilder` | Generate 3D solids, export meshes, compute mass |
| 5. Writer | `URDFWriter` | Produce URDF XML with links, joints, inertial |

---

## 1. Templates (`parametric_furniture.models.template`)

Templates are YAML files describing a furniture item's parts and their connections. They are **declarative** — no dimensions, no coordinates, no formulas.

### YAML Format

```yaml
# templates/desk/basic.yaml
name: "Basic Desk"
type: desk

parts:
  - name: leg_front_left
    part_type: leg
    profile: "3030"
    material: aluminum

  - name: beam_front
    part_type: beam
    profile: "3030"
    material: aluminum

  - name: tabletop
    part_type: tabletop
    profile: null
    board: plywood
    material: wood

topology:
  connections:
    - part_a: leg_front_left
      part_b: beam_front
    - part_a: tabletop
      part_b: leg_front_left
```

### `PartTemplate`

A single part definition within a template.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | `str` | ✓ | Unique name (letters, digits, `_`, `-`) |
| `part_type` | `str` | ✓ | Role: `leg`, `beam`, `tabletop`, `shelf` |
| `profile` | `str \| None` | | Profile size: `"2020"`, `"3030"`, `"4040"` |
| `board` | `str \| None` | | Board material: `"plywood"`, `"mdf"`, `"oak"` |
| `material` | `str` | | Material category: `aluminum`, `wood`, `steel` |

### `Topology`

Describes how parts connect. Connections are undirected.

| Field | Type | Description |
|-------|------|-------------|
| `connections` | `list[Connection]` | Part-to-part edge list |

### `Connection`

| Field | Type | Description |
|-------|------|-------------|
| `part_a` | `str` | First part name |
| `part_b` | `str` | Second part name |

### `FurnitureTemplate`

The top-level template object.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Human-readable name |
| `type` | `str` | Furniture type: `desk`, `shelf`, `cabinet` |
| `parts` | `list[PartTemplate]` | All parts (≥ 1) |
| `topology` | `Topology` | Connection graph |

**Methods:**

```python
@classmethod
def from_yaml(path: str | Path) -> FurnitureTemplate:
    """Load and validate a YAML template file.

    Raises:
        FileNotFoundError: Template file missing.
        ValueError: Invalid YAML or validation failure.
    """

def get_parts_by_type(part_type: str) -> list[PartTemplate]:
    """Return all parts of a given type (e.g. 'leg')."""

def get_part(name: str) -> PartTemplate:
    """Return a part by name.

    Raises:
        KeyError: Part name not found.
    """
```

---

## 2. Parameters

### `DeskParameters`

User-facing input values for a desk. All dimensions in **millimeters**.

```python
from parametric_furniture import DeskParameters

params = DeskParameters(
    width=1200.0,             # Total width (left-right), mm
    depth=600.0,              # Total depth (front-back), mm
    height=750.0,             # Total height (floor to top), mm
    tabletop_thickness=18.0,  # Tabletop board thickness, mm
    profile="3030",           # Profile: "2020" | "3030" | "4040"
    board_material="plywood", # Board: "plywood" | "mdf" | "oak"
    color="natural",          # Visual hint
)
```

| Field | Type | Default | Constraints |
|-------|------|---------|-------------|
| `width` | `float` | `1200.0` | `> 0` |
| `depth` | `float` | `600.0` | `> 0` |
| `height` | `float` | `750.0` | `> 0` |
| `tabletop_thickness` | `float` | `18.0` | `> 0` |
| `profile` | `str` | `"2020"` | `"2020" \| "3030" \| "4040"` |
| `board_material` | `str` | `"plywood"` | `"plywood" \| "mdf" \| "oak"` |
| `color` | `str` | `"natural"` | — |

**Computed properties:**

```python
params.profile_size   # → float  (actual cross-section size in mm)
params.leg_count      # → 4
params.beam_count     # → 4
```

---

## 3. Pose

### `Pose`

An immutable 3-DOF position + 3-DOF orientation. Position in mm, orientation in radians (intrinsic ZYX Euler angles).

```python
from parametric_furniture import Pose

# Default: origin, no rotation
origin = Pose()

# Translation only
p = Pose.from_translation(x=100.0, y=200.0, z=50.0)

# With orientation in degrees
p = Pose.from_degrees(x=0, y=0, z=0, roll_deg=90, pitch_deg=0, yaw_deg=0)

# Create a copy offset by a delta
shifted = p.translated(dx=10.0, dy=0.0, dz=0.0)
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `x`, `y`, `z` | `float` | `0.0` | Position in mm |
| `roll` | `float` | `0.0` | Rotation about X (rad) |
| `pitch` | `float` | `0.0` | Rotation about Y (rad) |
| `yaw` | `float` | `0.0` | Rotation about Z (rad) |

**Class methods:**

```python
Pose.origin()                              # → Pose(0,0,0, 0,0,0)
Pose.from_translation(x, y, z)             # → Pose with zero rotation
Pose.from_degrees(x, y, z, roll, pitch, yaw)  # → Pose with degrees input
```

**Instance methods:**

```python
pose.translated(dx=0, dy=0, dz=0)  # → new Pose offset by (dx, dy, dz)
pose.to_urdf_origin()              # → "x y z roll pitch yaw" string
pose.to_xyz_rpy()                  # → (x, y, z, roll, pitch, yaw) tuple
```

---

## 4. Solvers (`parametric_furniture.solvers`)

Solver = the computational core. Takes Template + Parameters → computes every part's dimensions, position, and orientation.

### `DeskSolver`

Implements desk-specific design rules.

```python
from parametric_furniture import DeskSolver

solver = DeskSolver()
assert solver.furniture_type == "desk"

output: SolverOutput = solver.solve(template, parameters)
```

**Design rules:**
- Leg length = desk height − tabletop thickness
- Legs positioned at the four corners
- Beam lengths = desk width/depth minus profile clearances
- Tabletop is the root link, centered at origin
- Coordinate frame: origin = center of tabletop top surface; X = right, Y = forward, Z = up

### `SolverOutput`

| Field | Type | Description |
|-------|------|-------------|
| `furniture_type` | `str` | `"desk"` |
| `template_name` | `str` | Template name |
| `parts` | `list[SolvedPart]` | All computed parts |

**Methods:**

```python
output.get_parts_by_type("leg")   # → list[SolvedPart]
```

### `SolvedPart`

One fully-specified part after solving.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Part name |
| `part_type` | `str` | Role (`leg`, `beam`, `tabletop`) |
| `profile` | `str \| None` | Profile reference |
| `board` | `str \| None` | Board material |
| `material` | `str` | Material category |
| `extrusion_length` | `float` | Extrusion length (mm) |
| `tabletop_width` | `float` | Tabletop width (mm) |
| `tabletop_depth` | `float` | Tabletop depth (mm) |
| `tabletop_thickness` | `float` | Tabletop thickness (mm) |
| `pose` | `Pose` | Position in assembly frame |
| `joint_origin` | `Pose` | Joint origin relative to parent |

### Solver Registry

```python
from parametric_furniture import get_solver

solver = get_solver("desk")          # → DeskSolver instance

from parametric_furniture.solvers import list_solvers
list_solvers()                       # → ["desk"]
```

To add a new furniture type, subclass `AbstractFurnitureSolver` — it auto-registers on import:

```python
from parametric_furniture.solvers.furniture_solver import (
    AbstractFurnitureSolver, register_solver, SolvedPart, SolverOutput
)

class ShelfSolver(AbstractFurnitureSolver):
    @property
    def furniture_type(self) -> str:
        return "shelf"

    def solve(self, template, parameters) -> SolverOutput:
        self.validate_inputs(template, parameters)
        # ... compute parts ...
        return SolverOutput(
            furniture_type="shelf",
            template_name=template.name,
            parts=[...],
        )
```

---

## 5. Builders (`parametric_furniture.builders`)

Builder = the 3D geometry engine. Takes SolverOutput → generates CAD solids, exports mesh files, computes mass & inertia, assembles FurnitureAssembly.

### `DeskBuilder`

```python
from parametric_furniture import DeskBuilder, AppConfig

config = AppConfig()
builder = DeskBuilder(config)
assert builder.furniture_type == "desk"

assembly: FurnitureAssembly = builder.build(solver_output)
```

### `MaterialDensity`

Material density lookup (kg/m³). Built-in: aluminum (2700), steel (7850), wood (700), plywood (700), mdf (750), oak (900), birch (670), walnut (650).

```python
from parametric_furniture.builders.furniture_builder import MaterialDensity

density = MaterialDensity.get("plywood")     # → 700.0
density = MaterialDensity.get("aluminum")    # → 2700.0

# Register custom material
MaterialDensity.register("bamboo", 800.0)
```

| Method | Description |
|--------|-------------|
| `get(material)` | Density in kg/m³ (case-insensitive). Raises `ValueError` if empty. Unknown material → 1000.0 |
| `register(material, density)` | Add a custom material |

### Builder Registry

```python
from parametric_furniture import get_builder

builder = get_builder("desk", config=AppConfig())  # → DeskBuilder instance

from parametric_furniture.builders import list_builders
list_builders()                                      # → ["desk"]
```

To add a new builder, subclass `AbstractFurnitureBuilder`:

```python
from parametric_furniture.builders.furniture_builder import (
    AbstractFurnitureBuilder, register_builder
)

class ShelfBuilder(AbstractFurnitureBuilder):
    @property
    def furniture_type(self) -> str:
        return "shelf"

    def build(self, solver_output) -> FurnitureAssembly:
        # Generate 3D geometry, export meshes, compute mass...
        return FurnitureAssembly(name="...", furniture_type="shelf", parts=[...])
```

---

## 6. Assembly Models (`parametric_furniture.models.furniture`)

These are the data types that flow from Builder → URDFWriter.

### `FurnitureAssembly`

Top-level container. Output of Builder, input to URDFWriter.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Assembly name |
| `furniture_type` | `str` | Type (`desk`) |
| `parts` | `list[FurniturePart]` | All generated parts |
| `links` | `list[FurnitureLink]` | URDF links |
| `joints` | `list[FurnitureJoint]` | URDF joints |

**Properties:** `assembly.part_count` → `int`

### `FurniturePart`

One physical component after 3D generation.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Part name |
| `part_type` | `str` | Role |
| `step_path` | `Path \| None` | STEP file path |
| `stl_path` | `Path \| None` | STL file path |
| `mass_kg` | `float` | Mass in kg (≥ 0) |
| `dimensions` | `dict[str, float]` | Key dims (length, width, …) |

### `FurnitureLink`

A URDF `<link>` — one per part.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Link name |
| `part` | `FurniturePart` | Associated part |
| `visual_mesh` | `Path \| None` | Visual STL path |
| `collision_mesh` | `Path \| None` | Collision STL path |
| `inertial` | `InertialData` | Mass + inertia tensor |
| `pose` | `Pose` | Assembly-frame pose |

### `FurnitureJoint`

A URDF `<joint type="fixed">` connecting two links.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | — | Joint name |
| `joint_type` | `str` | `"fixed"` | Always fixed for furniture |
| `parent` | `str` | — | Parent link name |
| `child` | `str` | — | Child link name |
| `origin` | `Pose` | `origin` | Joint origin in parent frame |

### `InertialData`

Rigid-body inertial properties for URDF `<inertial>`.

| Field | Type | Description |
|-------|------|-------------|
| `mass` | `float` | Mass in kg (> 0) |
| `ixx`, `iyy`, `izz` | `float` | Moments of inertia (kg·m²) |
| `ixy`, `ixz`, `iyz` | `float` | Products of inertia (kg·m², default 0) |
| `origin` | `Pose` | Inertial frame origin (default: identity) |

---

## 7. URDF Writer (`parametric_furniture.exporters`)

### `URDFWriter`

Converts a `FurnitureAssembly` → URDF XML file.

```python
from parametric_furniture import URDFWriter, AppConfig

config = AppConfig()
writer = URDFWriter(config)

# Write to file
path = writer.write(assembly, "output/basic_desk/basic_desk.urdf")

# Write to string (for testing / in-memory)
xml_string = writer.write_to_string(assembly)
```

**Output layout:**

```
output/basic_desk/
├── basic_desk.urdf
├── meshes/
│   ├── visual/
│   │   ├── tabletop.stl
│   │   ├── leg_front_left.stl
│   │   └── ...
│   └── collision/
│       └── ...
└── cad/
    ├── tabletop.step
    └── ...
```

The URDF uses relative mesh paths (`meshes/visual/tabletop.stl`) so the package is portable across machines. A virtual `base_link` is added as the kinematic tree root.

---

## 8. Configuration (`parametric_furniture.config`)

### `AppConfig`

Centralized config using Pydantic Settings. Overridable via environment variables (prefix `FURNITURE_`) or `.env` file.

```python
from parametric_furniture import AppConfig

config = AppConfig()

# Paths
config.paths.output_dir     # → Path("output")
config.paths.templates_dir  # → Path("templates")
config.paths.profiles_dir   # → Path("library/profiles")

# Build options
config.build.export_step     # → True
config.build.export_stl      # → True
config.build.stl_linear_deflection  # → 0.1
```

### `PathsConfig`

| Field | Default | Env Variable |
|-------|---------|-------------|
| `templates_dir` | `Path("templates")` | `FURNITURE_PATHS_TEMPLATES_DIR` |
| `library_dir` | `Path("library")` | `FURNITURE_PATHS_LIBRARY_DIR` |
| `output_dir` | `Path("output")` | `FURNITURE_PATHS_OUTPUT_DIR` |
| `profiles_dir` | `Path("library/profiles")` | `FURNITURE_PATHS_PROFILES_DIR` |

### `BuildConfig`

| Field | Default | Description |
|-------|---------|-------------|
| `export_step` | `True` | Export STEP CAD files |
| `export_stl` | `True` | Export STL mesh files |
| `stl_linear_deflection` | `0.1` | Mesh linear deflection (mm) |
| `stl_angular_deflection` | `0.5` | Mesh angular deflection (rad) |
| `stl_max_edge_length` | `50.0` | Max triangle edge (mm, 0=off) |

### `ExportConfig`

| Field | Default | Description |
|-------|---------|-------------|
| `urdf_robot_name` | `"furniture"` | Default URDF robot name |

### `LoggingConfig`

| Field | Default | Constraints |
|-------|---------|-------------|
| `level` | `"INFO"` | `DEBUG \| INFO \| WARNING \| ERROR \| CRITICAL` |

---

## CLI Reference

```
furniture build TEMPLATE_PATH [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `TEMPLATE_PATH` | (arg) | *required* | Path to YAML template |
| `--width` | `-w` | `1200.0` | Desk width in mm |
| `--depth` | `-d` | `600.0` | Desk depth in mm |
| `--height` | — | `750.0` | Desk height in mm |
| `--tabletop-thickness` | `-t` | `18.0` | Tabletop thickness in mm |
| `--profile` | `-p` | `"2020"` | Profile: `2020`, `3030`, `4040` |
| `--board` | `-b` | `"plywood"` | Board: `plywood`, `mdf`, `oak` |
| `--output` | `-o` | `"output"` | Output directory |
| `--verbose` | `-v` | `False` | Debug logging |
| `--version` | `-V` | — | Show version |
| `--help` | — | — | Show help |

**Examples:**

```bash
# Basic desk (wide)
furniture build templates/desk/basic.yaml -w 1200 -d 600

# Standing desk with 3030 profile
furniture build templates/desk/basic.yaml --width 1400 --depth 700 --height 1100 --profile 3030

# Custom output directory
furniture build templates/desk/basic.yaml -w 800 -d 500 -o ./my_output

# Debug mode
furniture build templates/desk/basic.yaml -v
```

---

## Extending: Adding a New Furniture Type

To add a new furniture type (e.g. `shelf`), implement three components:

### 1. Parameter model

```python
# parametric_furniture/models/parameter.py (or new file)
class ShelfParameters(BaseModel):
    width: float = Field(default=800.0, gt=0)
    height: float = Field(default=1200.0, gt=0)
    depth: float = Field(default=300.0, gt=0)
    shelf_count: int = Field(default=4, ge=1)
    profile: str = Field(default="2020")
```

### 2. Solver

```python
# parametric_furniture/solvers/shelf_solver.py
class ShelfSolver(AbstractFurnitureSolver):
    furniture_type = "shelf"

    def solve(self, template, params):
        self.validate_inputs(template, params)
        # Compute shelf dimensions, positions...
        return SolverOutput(...)
```

### 3. Builder

```python
# parametric_furniture/builders/shelf_builder.py
class ShelfBuilder(AbstractFurnitureBuilder):
    furniture_type = "shelf"

    def build(self, solver_output):
        # Generate 3D geometry...
        return FurnitureAssembly(...)
```

Then create a `templates/shelf/basic.yaml` and users can run:

```bash
furniture build templates/shelf/basic.yaml --width 800 --height 1200
```

---

## Appendix: Template File Reference

### `templates/desk/basic.yaml`

```yaml
name: "Basic Desk"
type: desk

parts:
  - name: leg_front_left
    part_type: leg
    profile: "3030"
    material: aluminum

  - name: leg_front_right
    part_type: leg
    profile: "3030"
    material: aluminum

  - name: leg_back_left
    part_type: leg
    profile: "3030"
    material: aluminum

  - name: leg_back_right
    part_type: leg
    profile: "3030"
    material: aluminum

  - name: beam_front
    part_type: beam
    profile: "3030"
    material: aluminum

  - name: beam_back
    part_type: beam
    profile: "3030"
    material: aluminum

  - name: beam_left
    part_type: beam
    profile: "3030"
    material: aluminum

  - name: beam_right
    part_type: beam
    profile: "3030"
    material: aluminum

  - name: tabletop
    part_type: tabletop
    profile: null
    board: plywood
    material: wood

topology:
  connections:
    - part_a: leg_front_left
      part_b: beam_front
    - part_a: leg_front_left
      part_b: beam_left
    - part_a: leg_front_right
      part_b: beam_front
    - part_a: leg_front_right
      part_b: beam_right
    - part_a: leg_back_left
      part_b: beam_back
    - part_a: leg_back_left
      part_b: beam_left
    - part_a: leg_back_right
      part_b: beam_back
    - part_a: leg_back_right
      part_b: beam_right
    - part_a: tabletop
      part_b: leg_front_left
    - part_a: tabletop
      part_b: leg_front_right
    - part_a: tabletop
      part_b: leg_back_left
    - part_a: tabletop
      part_b: leg_back_right
```

### Profile Library

Profiles are DXF cross-section files in `library/profiles/`:

| File | Dimensions |
|------|-----------|
| `2020.dxf` | 20×20 mm |
| `3030.dxf` | 30×30 mm |
| `4040.dxf` | 40×40 mm |

---

## Migration Notes (v0.2.0)

If migrating from the flat-module structure (pre v0.2.0):

**Old imports:**
```python
from models.template import FurnitureTemplate
from solvers.desk_solver import DeskSolver
```

**New imports:**
```python
from parametric_furniture import FurnitureTemplate, DeskSolver
# or
from parametric_furniture.models.template import FurnitureTemplate
from parametric_furniture.solvers.desk_solver import DeskSolver
```
