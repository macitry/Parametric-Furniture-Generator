# Parametric Furniture Generator

A production-quality parametric furniture generation platform. Takes declarative furniture templates + user parameters → computes dimensions via a Solver → generates 3D geometry via a Builder → exports URDF.

**Desk** is the first template. Shelf, Cabinet, Workbench, TV Stand follow without architecture changes.

## Architecture

```
YAML Template + Pydantic Parameters
        ↓
   FurnitureSolver  (computes dimensions, poses, joint origins)
        ↓
   FurnitureBuilder (generates 3D parts via VisualCAD, creates assembly)
        ↓
   FurnitureAssembly (Parts, Links, Joints, Poses — CAD-independent model)
        ↓
   URDFWriter (XML export, no recalculation)
```

### Key Principles

- **Template**: Declarative. Describes WHAT parts exist and HOW they connect. No dimensions, coordinates, or formulas.
- **Parameters**: User-facing inputs only. Dimensions the user would naturally specify.
- **Solver**: All computation lives here. Design rules for each furniture type.
- **Builder**: Generates 3D geometry via VisualCAD, exports STEP/STL, computes mass/inertia.
- **URDF Exporter**: Pure XML generation. Never recomputes values.

### Data Flow is Strictly Unidirectional

No module imports or depends on modules downstream of it. Solver never imports Builder. Builder never imports Exporter.

## Quick Start

### Prerequisites

- Python 3.12+
- VisualCAD (external dependency at `../visualcad/`)

### Install

```bash
pip install -e .
```

### Build a Desk

```bash
# Default desk (1200×600×750mm, 2020 profile, plywood top)
python app.py build templates/desk/basic.yaml

# Custom desk
python app.py build templates/desk/basic.yaml \
    --width 1600 \
    --depth 800 \
    --height 720 \
    --profile 3030 \
    --board oak \
    --tabletop-thickness 25 \
    --output ./my_desk_output

# Verbose output
python app.py build templates/desk/basic.yaml --verbose
```

### Output

```
output/
├── tabletop.step
├── tabletop.stl
├── leg_front_left.step
├── leg_front_left.stl
├── ... (all other parts)
├── beam_front.step
├── beam_front.stl
├── ...
└── basic_desk.urdf
```

## Project Structure

```
Parametric Furniture Generator/
├── app.py                    # CLI entry point (Typer)
├── config.py                 # AppConfig via Pydantic Settings
├── pyproject.toml            # Dependencies and build config
├── models/                   # Data models (Pydantic)
│   ├── pose.py               # Pose (position + Euler orientation)
│   ├── template.py           # FurnitureTemplate, PartTemplate
│   ├── parameter.py          # DeskParameters
│   └── furniture.py          # FurnitureAssembly, Part, Link, Joint
├── solvers/                  # Dimension and pose computation
│   ├── furniture_solver.py   # Abstract base + solver registry
│   └── desk_solver.py        # Desk design rules
├── builders/                 # 3D geometry generation
│   ├── furniture_builder.py  # Abstract base + builder registry
│   └── desk_builder.py       # Desk builder (VisualCAD integration)
├── exporters/                # URDF export
│   └── urdf_writer.py        # Assembly → URDF XML
├── cli/                      # Command-line interface
│   └── build.py              # Build command
├── templates/                # Declarative furniture templates
│   └── desk/
│       └── basic.yaml        # Basic 4-leg work desk
├── library/                  # Furniture resources
│   ├── profiles/             # DXF cross-section profiles
│   ├── boards/               # Board material specs
│   └── materials/            # Material density tables
├── output/                   # Generated STEP/STL/URDF files
└── tests/                    # Test suite
    ├── test_template_loader.py
    ├── test_desk_solver.py
    ├── test_desk_builder.py
    └── test_urdf_writer.py
```

## Template Writing Guide

Templates are YAML files that describe furniture structure declaratively.

### Rules

1. **Describe only parts and topology** — never dimensions, coordinates, or formulas
2. **Part names must be unique** and use only `[a-zA-Z0-9_-]`
3. **Each part has a type** (`leg`, `beam`, `tabletop`, `shelf`, `door`, etc.)
4. **Topology lists which parts connect** — undirected pairs

### Example: Basic Desk

```yaml
name: "Basic Desk"
type: desk

parts:
  - name: leg_front_left
    part_type: leg
    profile: "2020"
    material: aluminum

  - name: tabletop
    part_type: tabletop
    board: plywood
    material: wood

topology:
  connections:
    - part_a: tabletop
      part_b: leg_front_left
```

### Part Types Reference

| Part Type | Description | Required Fields |
|-----------|-------------|-----------------|
| `leg` | Vertical support | `profile` |
| `beam` | Horizontal connector | `profile` |
| `tabletop` | Flat work surface | `board` |
| `shelf` | Horizontal storage | `board` |

## Solver Development Guide

To add a new furniture type, implement a Solver subclass:

```python
from solvers.furniture_solver import AbstractFurnitureSolver, SolvedPart, SolverOutput, register_solver

class ShelfSolver(AbstractFurnitureSolver):
    @property
    def furniture_type(self) -> str:
        return "shelf"

    def solve(self, template, parameters) -> SolverOutput:
        self.validate_inputs(template, parameters)
        parts = []
        # ... compute shelf dimensions and poses ...
        return SolverOutput(
            furniture_type="shelf",
            template_name=template.name,
            parts=parts,
        )

# Auto-register
register_solver("shelf", ShelfSolver)
```

### Solver Responsibilities

- Compute every part's **extrusion length**
- Compute every part's **pose** in the assembly frame
- Compute every part's **joint origin**
- Follow the **design rules** for the furniture type

## Builder Development Guide

```python
from builders.furniture_builder import AbstractFurnitureBuilder, register_builder

class ShelfBuilder(AbstractFurnitureBuilder):
    @property
    def furniture_type(self) -> str:
        return "shelf"

    def build(self, solver_output):
        # 1. Generate 3D solids via VisualCAD
        # 2. Export STEP/STL
        # 3. Compute mass and inertia
        # 4. Create FurnitureAssembly
        return assembly

register_builder("shelf", ShelfBuilder)
```

## Adding a New Furniture Type

1. **Template**: Create `templates/<type>/<variant>.yaml`
2. **Parameters**: Add a new parameter model in `models/parameter.py` (or reuse existing)
3. **Solver**: Implement `<Type>Solver` in `solvers/<type>_solver.py`
4. **Builder**: Implement `<Type>Builder` in `builders/<type>_builder.py`
5. **Register**: Both Solver and Builder auto-register on import
6. **Test**: Add tests in `tests/`

No existing code needs modification.

## Testing

```bash
pytest tests/ -v
```

## Dependencies

- **VisualCAD**: DXF → 3D solid pipeline (external, at `../visualcad/`)
- **Pydantic**: Data validation
- **Typer**: CLI framework
- **Loguru**: Structured logging
- **PyYAML**: Template parsing
- **ezdxf**: DXF profile generation
- **lxml**: URDF XML generation

## License

MIT
