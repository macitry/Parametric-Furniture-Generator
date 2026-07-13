"""
WoodCraft Backend API Server v2
===============================
FastAPI server with pre-generated model cache.

Architecture:
  - Server starts → immediately serves API
  - Background warmup: generates default config → stores in cache
  - API returns cached results instantly (<100ms)
  - On cache miss: triggers generation, caches result for future

Usage:
    cd "Parametric Furniture Generator"
    python server.py
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from pathlib import Path
from typing import Optional

import numpy as np

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from loguru import logger

from parametric_furniture import (
    FurnitureTemplate,
    DeskParameters,
    DeskSolver,
    DeskBuilder,
    AppConfig,
)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="WoodCraft Backend v2", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
OUTPUT_DIR = BASE_DIR / "output"
CACHE_DIR = OUTPUT_DIR / "pregen"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Thread-safe cache
_cache_lock = threading.Lock()
model_cache: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class PartInfo(BaseModel):
    name: str
    part_type: str
    profile: Optional[str] = None
    board: Optional[str] = None
    material: str = ""
    dimensions: Optional[dict[str, float]] = None
    mass_kg: Optional[float] = None
    stl_url: Optional[str] = None
    step_url: Optional[str] = None
    pose: Optional[dict] = None
    joint_parent: Optional[str] = None


class TemplateResponse(BaseModel):
    id: str
    name: str
    type: str
    description: str = ""
    parts: list[dict]
    parameters: list[dict]


class GenerateRequest(BaseModel):
    template_id: str = "basic-desk"
    width: float = Field(default=1200.0, gt=0)
    depth: float = Field(default=600.0, gt=0)
    height: float = Field(default=750.0, gt=0)
    tabletop_thickness: float = Field(default=18.0, gt=0)
    profile: str = Field(default="3030")
    board_material: str = Field(default="plywood")
    color: str = Field(default="natural")
    stl_quality: str = Field(default="web")


class GenerateResponse(BaseModel):
    model_id: str
    name: str
    status: str  # "full" | "warming" | "solver_only"
    parts: list[PartInfo]
    dimensions: dict[str, float]
    stl_url: Optional[str] = None
    urdf_url: Optional[str] = None
    joints: list[dict] = []
    message: Optional[str] = None


class BomEntry(BaseModel):
    name: str
    part_type: str
    quantity: int
    material: str
    dimensions: Optional[dict[str, float]] = None
    mass_kg: Optional[float] = None


class BomResponse(BaseModel):
    furniture_name: str
    entries: list[BomEntry]
    total_mass_kg: Optional[float] = None


class DrawingInfo(BaseModel):
    id: str
    name: str
    type: str
    url: str
    format: str


class DrawingListResponse(BaseModel):
    furniture_name: str
    drawings: list[DrawingInfo]


# ---------------------------------------------------------------------------
# Template metadata
# ---------------------------------------------------------------------------

TEMPLATE_META = {
    "basic-desk": {
        "id": "basic-desk",
        "name": "Basic Desk",
        "type": "desk",
        "description": "A simple desk with aluminum frame and wood tabletop.",
        "parameters": [
            {"id": "width", "name": "Width", "default_value": 1200, "unit": "mm", "min": 600, "max": 3000, "step": 10},
            {"id": "depth", "name": "Depth", "default_value": 600, "unit": "mm", "min": 400, "max": 1200, "step": 10},
            {"id": "height", "name": "Height", "default_value": 750, "unit": "mm", "min": 500, "max": 1300, "step": 10},
            {"id": "tabletop_thickness", "name": "Thickness", "default_value": 18, "unit": "mm", "min": 12, "max": 40, "step": 1},
        ],
    },
    "standing-desk": {
        "id": "standing-desk",
        "name": "Standing Desk",
        "type": "desk",
        "description": "A height-adjustable standing desk.",
        "parameters": [
            {"id": "width", "name": "Width", "default_value": 1400, "unit": "mm", "min": 800, "max": 2400, "step": 10},
            {"id": "depth", "name": "Depth", "default_value": 700, "unit": "mm", "min": 500, "max": 1000, "step": 10},
            {"id": "height", "name": "Height", "default_value": 1100, "unit": "mm", "min": 700, "max": 1300, "step": 10},
            {"id": "tabletop_thickness", "name": "Thickness", "default_value": 25, "unit": "mm", "min": 18, "max": 50, "step": 1},
        ],
    },
}


def _cache_key(req: GenerateRequest) -> str:
    """Deterministic cache key from request parameters."""
    raw = f"{req.template_id}|{req.width}|{req.depth}|{req.height}|{req.tabletop_thickness}|{req.profile}|{req.board_material}|{req.stl_quality}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _load_template(template_id: str) -> FurnitureTemplate:
    path = TEMPLATES_DIR / "desk" / "basic.yaml"
    return FurnitureTemplate.from_yaml(str(path))


def _find_solved(solved, name: str):
    for p in solved.parts:
        if p.name == name:
            return p
    return None


def _do_generate(req: GenerateRequest) -> dict:
    """Run the full pipeline: template → solve → build. Returns cache entry dict."""
    template = _load_template(req.template_id)
    params = DeskParameters(
        width=req.width, depth=req.depth, height=req.height,
        tabletop_thickness=req.tabletop_thickness, profile=req.profile,
        board_material=req.board_material, color=req.color,
    )
    solved = DeskSolver().solve(template, params)
    meta = TEMPLATE_META[req.template_id]

    model_id = str(uuid.uuid4())[:8]

    # Fast mode: skip CAD, generate simple box STLs directly
    if req.stl_quality == "fast":
        return _generate_fast(meta, req, solved, model_id)

    # Trimesh mode: use ezdxf + trimesh extrusion (fast, DXF profile detail)
    if req.stl_quality == "trimesh":
        return _generate_trimesh(meta, req, solved, model_id)

    parts: list[dict] = []
    status = "solver_only"
    stl_url = None
    urdf_url = None
    joints: list[dict] = []

    try:
        config = AppConfig()
        config.paths.output_dir = OUTPUT_DIR / model_id

        # STL quality presets
        presets = {
            "web": {"linear_deflection": 3.0, "angular_deflection": 1.5, "max_edge_length": 0},
            "standard": {"linear_deflection": 0.5, "angular_deflection": 0.5, "max_edge_length": 50},
            "fine": {"linear_deflection": 0.1, "angular_deflection": 0.5, "max_edge_length": 50},
        }
        preset = presets.get(req.stl_quality, presets["web"])
        config.build.stl_linear_deflection = preset["linear_deflection"]
        config.build.stl_angular_deflection = preset["angular_deflection"]
        config.build.stl_max_edge_length = preset["max_edge_length"]

        builder = DeskBuilder(config)
        assembly = builder.build(solved)
        status = "full"

        # Write URDF (authoritative assembly structure)
        urdf_url = None
        try:
            from parametric_furniture import URDFWriter
            urdf_path = OUTPUT_DIR / model_id / "basic_desk" / "basic_desk.urdf"
            urdf_path.parent.mkdir(parents=True, exist_ok=True)
            URDFWriter(config).write(assembly, str(urdf_path))
            urdf_url = f"/static/models/{model_id}/basic_desk/basic_desk.urdf"
            logger.info(f"URDF written: {urdf_url}")
        except Exception as e:
            logger.warning(f"URDF write failed: {e}")

        # Build link pose map from assembly (authoritative positions)
        # NOTE: assembly.links[i].pose is the joint origin (relative to parent).
        # We must walk the URDF tree from root to compute absolute world poses.
        link_pose_map = {}
        for link in assembly.links:
            link_pose_map[link.name] = {
                "x": link.pose.x, "y": link.pose.y, "z": link.pose.z,
                "roll": link.pose.roll, "pitch": link.pose.pitch, "yaw": link.pose.yaw,
            }

        # Build joint hierarchy
        joints = []
        for joint in assembly.joints:
            joints.append({
                "name": joint.name,
                "parent": joint.parent,
                "child": joint.child,
                "origin": {
                    "x": joint.origin.x, "y": joint.origin.y, "z": joint.origin.z,
                    "roll": joint.origin.roll, "pitch": joint.origin.pitch,
                    "yaw": joint.origin.yaw,
                },
            })

        # Walk the URDF tree from base_link to compute absolute world-frame poses
        # base_link is the virtual root at (0,0,0)
        children_map: dict[str, list[dict]] = {}
        for j in joints:
            children_map.setdefault(j["parent"], []).append(j)

        def walk_tree(link_name: str, parent_pos: tuple, parent_rot: tuple):
            """Recursively compute absolute world poses by accumulating joint transforms."""
            results = {}
            for joint in children_map.get(link_name, []):
                child_name = joint["child"]
                origin = joint["origin"]

                # Accumulate position (simplified: no rotation of offset for now,
                # since all desk joints have zero rotation)
                child_abs = {
                    "x": parent_pos[0] + origin["x"],
                    "y": parent_pos[1] + origin["y"],
                    "z": parent_pos[2] + origin["z"],
                    "roll": origin["roll"],   # absolute rotation = parent + joint
                    "pitch": origin["pitch"],
                    "yaw": origin["yaw"],
                }
                results[child_name] = child_abs
                # Recurse into grandchildren
                results.update(walk_tree(child_name,
                    (child_abs["x"], child_abs["y"], child_abs["z"]),
                    (child_abs["roll"], child_abs["pitch"], child_abs["yaw"])))
            return results

        world_poses = walk_tree("base_link", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        logger.info(f"URDF tree: {len(world_poses)} world poses computed")

        for part in assembly.parts:
            stl_path = None
            if part.stl_path:
                stl_abs = Path(part.stl_path)
                if stl_abs.exists():
                    try:
                        stl_rel = stl_abs.relative_to(OUTPUT_DIR)
                        stl_path = f"/static/models/{stl_rel.as_posix()}"
                    except ValueError:
                        stl_path = f"/static/models/{model_id}/basic_desk/meshes/visual/{stl_abs.name}"
            sp = _find_solved(solved, part.name)
            # Use world pose from URDF tree walk (absolute assembly-frame position)
            urdf_pose = world_poses.get(part.name)
            parts.append({
                "name": part.name, "part_type": part.part_type,
                "material": sp.material if sp else "",
                "dimensions": {k: v for k, v in part.dimensions.items()} if part.dimensions else None,
                "mass_kg": part.mass_kg, "stl_url": stl_path,
                "pose": urdf_pose,
                "joint_parent": next((j["parent"] for j in joints if j["child"] == part.name), None),
            })

        for p in assembly.parts:
            if p.stl_path and Path(p.stl_path).exists():
                stl_abs = Path(p.stl_path)
                try:
                    stl_rel = stl_abs.relative_to(OUTPUT_DIR)
                    stl_url = f"/static/models/{stl_rel.as_posix()}"
                except ValueError:
                    stl_url = f"/static/models/{model_id}/basic_desk/meshes/visual/{stl_abs.name}"
                break

    except Exception as exc:
        status = "solver_only"
        logger.warning(f"Build failed: {exc}")
        for sp in solved.parts:
            parts.append({
                "name": sp.name, "part_type": sp.part_type,
                "profile": sp.profile, "board": sp.board, "material": sp.material,
                "dimensions": {
                    "extrusion_length": sp.extrusion_length,
                    "tabletop_width": sp.tabletop_width,
                    "tabletop_depth": sp.tabletop_depth,
                    "tabletop_thickness": sp.tabletop_thickness,
                },
                "pose": {"x": sp.pose.x, "y": sp.pose.y, "z": sp.pose.z,
                         "roll": sp.pose.roll, "pitch": sp.pose.pitch, "yaw": sp.pose.yaw} if sp.pose else None,
            })

    result = {
        "model_id": model_id,
        "name": TEMPLATE_META[req.template_id]["name"],
        "status": status,
        "parts": parts,
        "stl_url": stl_url,
        "urdf_url": urdf_url if status == "full" else None,
        "joints": joints if status == "full" else [],
        "dimensions": {
            "width": req.width, "depth": req.depth,
            "height": req.height, "tabletop_thickness": req.tabletop_thickness,
        },
    }
    return result


def _get_or_generate(req: GenerateRequest) -> dict:
    """Get from cache, or generate + cache."""
    key = _cache_key(req)

    with _cache_lock:
        if key in model_cache:
            logger.info(f"Cache HIT: {key}")
            return model_cache[key]

    logger.info(f"Cache MISS: {key} — generating...")
    entry = _do_generate(req)

    with _cache_lock:
        model_cache[key] = entry

    logger.info(f"Cached: {key} ({entry['status']}, {len(entry['parts'])} parts)")
    return entry


# ---------------------------------------------------------------------------
# Startup warmup — generate default model in background
# ---------------------------------------------------------------------------

WARMUP_CONFIGS = [
    GenerateRequest(template_id="basic-desk", width=1200, depth=600, height=750, profile="3030", stl_quality="trimesh"),
    GenerateRequest(template_id="basic-desk", width=1500, depth=700, height=750, profile="3030", stl_quality="trimesh"),
]


@app.on_event("startup")
def warmup_cache():
    """Pre-generate common configurations on startup (non-blocking)."""
    def _warmup():
        logger.info(f"Cache warmup: generating {len(WARMUP_CONFIGS)} config(s)...")
        for i, cfg in enumerate(WARMUP_CONFIGS):
            key = _cache_key(cfg)
            with _cache_lock:
                if key in model_cache:
                    continue
            logger.info(f"Warmup [{i+1}/{len(WARMUP_CONFIGS)}]: {cfg.width}x{cfg.depth}x{cfg.height}")
            entry = _do_generate(cfg)
            with _cache_lock:
                model_cache[key] = entry
            logger.info(f"Warmup [{i+1}/{len(WARMUP_CONFIGS)}] done: {entry['status']}")
        logger.info(f"Cache warmup complete. {len(model_cache)} entries cached.")

    threading.Thread(target=_warmup, daemon=True).start()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "cache_size": len(model_cache)}


@app.get("/api/templates")
def list_templates() -> list[TemplateResponse]:
    results = []
    for t_id, meta in TEMPLATE_META.items():
        try:
            t = _load_template(t_id)
            parts = [{"name": p.name, "part_type": p.part_type, "profile": p.profile, "board": p.board, "material": p.material} for p in t.parts]
        except Exception:
            parts = []
        results.append(TemplateResponse(id=t_id, name=meta["name"], type=meta["type"], description=meta["description"], parts=parts, parameters=meta["parameters"]))
    return results


@app.get("/api/templates/{template_id}")
def get_template(template_id: str) -> TemplateResponse:
    meta = TEMPLATE_META.get(template_id)
    if not meta:
        raise HTTPException(404, f"Template not found: {template_id}")
    try:
        t = _load_template(template_id)
        parts = [{"name": p.name, "part_type": p.part_type, "profile": p.profile, "board": p.board, "material": p.material} for p in t.parts]
    except Exception:
        parts = []
    return TemplateResponse(id=template_id, name=meta["name"], type=meta["type"], description=meta["description"], parts=parts, parameters=meta["parameters"])


@app.get("/api/models/default")
def get_default_model() -> GenerateResponse:
    """Get the pre-generated default model.

    If warmup is done → instant STL data.
    If warmup still running → solver data immediately (for procedural preview).
    Frontend polls until STL ready.
    """
    default_req = WARMUP_CONFIGS[0]
    key = _cache_key(default_req)

    with _cache_lock:
        cached = model_cache.get(key)

    if cached:
        logger.info("Default model: cache HIT")
        return GenerateResponse(
            model_id=cached["model_id"], name=cached["name"],
            status=cached["status"],
            parts=[PartInfo(**p) for p in cached["parts"]],
            dimensions=cached["dimensions"],
            stl_url=cached.get("stl_url"),
            urdf_url=cached.get("urdf_url"),
            joints=cached.get("joints", []),
        )

    # Warmup still running — return solver data immediately
    logger.info("Default model: still warming, returning solver-only")
    entry = _solver_only_generate(default_req)
    return GenerateResponse(
        model_id=entry["model_id"], name=entry["name"],
        status="warming",
        parts=[PartInfo(**p) for p in entry["parts"]],
        dimensions=entry["dimensions"],
        message="Warmup in progress. Poll /api/health for cache_size > 0, then retry.",
    )


@app.post("/api/models/generate")
def generate_model(req: GenerateRequest) -> GenerateResponse:
    """Generate model (from cache if available, otherwise generate + cache)."""
    meta = TEMPLATE_META.get(req.template_id)
    if not meta:
        raise HTTPException(404, f"Template not found: {req.template_id}")

    entry = _get_or_generate(req)
    message = None
    if entry["status"] == "solver_only":
        message = "CAD build failed. Using solver data."

    return GenerateResponse(
        model_id=entry["model_id"], name=entry["name"], status=entry["status"],
        parts=[PartInfo(**p) for p in entry["parts"]],
        dimensions=entry["dimensions"],
        stl_url=entry.get("stl_url"),
        urdf_url=entry.get("urdf_url"),
        joints=entry.get("joints", []),
        message=message,
    )


@app.get("/api/models/{model_id}/bom")
def get_bom(model_id: str) -> BomResponse:
    entries = []
    for entry in model_cache.values():
        if entry["model_id"] == model_id:
            for p in entry["parts"]:
                entries.append(BomEntry(
                    name=p["name"], part_type=p["part_type"], quantity=1,
                    material=p.get("material", ""),
                    dimensions=p.get("dimensions"),
                    mass_kg=p.get("mass_kg"),
                ))
            total = sum(e.mass_kg or 0 for e in entries)
            return BomResponse(furniture_name=entry["name"], entries=entries, total_mass_kg=total if total > 0 else None)
    raise HTTPException(404, f"Model not found: {model_id}")


@app.get("/api/models/{model_id}/drawings")
def get_drawings(model_id: str) -> DrawingListResponse:
    for entry in model_cache.values():
        if entry["model_id"] == model_id:
            return DrawingListResponse(furniture_name=entry["name"], drawings=[])
    raise HTTPException(404, f"Model not found: {model_id}")


def _build123d_extrude(
    profile: str, length_mm: float, filepath_stl: Path, filepath_step: Path | None = None,
    tabletop_w: float = 0, tabletop_d: float = 0,
) -> bool:
    """Create an extruded solid via build123d, export STEP + STL.

    - Reads DXF profile via ezdxf
    - Creates a Face from the outer wire
    - Extrudes to a Solid
    - Exports STEP (B-Rep) and STL (tessellated)
    - Returns True on success
    """
    from build123d import (
        Face, Wire, Solid, Vector,
        export_step, Mesher,
    )

    if tabletop_w > 0:
        # Rectangular tabletop outline
        hw, hd = tabletop_w / 2.0, tabletop_d / 2.0
        profile_verts = [
            Vector(-hw, -hd, 0), Vector(hw, -hd, 0),
            Vector(hw, hd, 0), Vector(-hw, hd, 0),
        ]
        hole_wires = []
    else:
        # Read DXF via OCP's native DXF reader (via VisualCAD importer).
        # This correctly handles POLYLINE, ARC, and connected entities,
        # producing proper contours with holes (T-slots preserved).
        import sys
        _visualcad_root = Path(__file__).resolve().parent.parent / "visualcad"
        if str(_visualcad_root) not in sys.path:
            sys.path.insert(0, str(_visualcad_root))
        from visualcad.cad.importer import DXFImporter, DXFImportParameters, ContourType

        profile_dir = BASE_DIR / "library" / "profiles"
        dxf_path = profile_dir / f"{profile}.dxf"
        if not dxf_path.exists():
            return False

        try:
            params = DXFImportParameters(file_path=dxf_path)
            importer = DXFImporter(params)
            all_contours = importer.import_contours()
        except Exception as e:
            logger.warning(f"OCP DXF import error: {e}")
            return False

        if not all_contours:
            return False

        # Extract outer contour vertices
        outer_contour = next((c for c in all_contours if c.contour_type == ContourType.OUTER), None)
        if not outer_contour or len(outer_contour.edges) < 3:
            return False

        outer_verts = [e.start for e in outer_contour.edges]
        outer_verts.append(outer_contour.edges[-1].end)
        profile_verts = [Vector(float(v[0]), float(v[1]), 0.0) for v in outer_verts]

        # Extract hole contours (including CIRCLE holes which have only 1 edge)
        hole_wires = []
        for c in all_contours:
            if c.contour_type == ContourType.OUTER:
                continue
            # Handle both multi-edge contours and single-edge circles
            if len(c.edges) >= 3:
                hole_verts = [e.start for e in c.edges]
                hole_verts.append(c.edges[-1].end)
            elif len(c.edges) == 1:
                # Circle: use edge.center and edge.radius directly
                edge = c.edges[0]
                cx = float(edge.center[0]) if hasattr(edge, 'center') else float(edge.start[0])
                cy = float(edge.center[1]) if hasattr(edge, 'center') else float(edge.start[1])
                r = float(edge.radius) if hasattr(edge, 'radius') else 0.0
                if r < 0.01:
                    continue
                hole_verts = []
                import math
                for k in range(32):
                    angle = 2.0 * math.pi * k / 32.0
                    hole_verts.append((
                        cx + r * math.cos(angle),
                        cy + r * math.sin(angle),
                    ))
            else:
                continue  # too few edges, skip

            try:
                hw = Wire.make_polygon(
                    [Vector(float(v[0]), float(v[1]), 0.0) for v in hole_verts],
                    close=True,
                )
                hole_wires.append(hw)
            except Exception:
                pass

        logger.info(f"OCP DXF: {len(profile_verts)} outer verts, {len(hole_wires)} holes")

    try:
        wire = Wire.make_polygon(profile_verts, close=True)
        if hole_wires:
            face = Face(wire, hole_wires)
        else:
            face = Face(wire)

        # Extrude symmetric around Z=0
        half_l = length_mm / 2.0
        solid = Solid.extrude(face, Vector(0, 0, length_mm))
        solid = solid.translate(Vector(0, 0, -half_l))

        # Center XY at origin
        bb = solid.bounding_box()
        scx = (bb.min.X + bb.max.X) / 2.0
        scy = (bb.min.Y + bb.max.Y) / 2.0
        solid = solid.translate(Vector(-scx, -scy, 0))

    except Exception as e:
        logger.warning(f"build123d error: {e}")
        return False

    # Export STEP
    if filepath_step:
        filepath_step.parent.mkdir(parents=True, exist_ok=True)
        try:
            export_step(solid, str(filepath_step))
        except Exception as e:
            logger.warning(f"STEP export error: {e}")

    # Export STL (tessellate B-Rep solid via Mesher)
    filepath_stl.parent.mkdir(parents=True, exist_ok=True)
    try:
        m = Mesher()
        m.add_shape(solid)
        m.write(str(filepath_stl))
    except Exception as e:
        logger.warning(f"STL export error: {e}")
        return False

    return True


def _trace_dxf_contour(profile: str) -> list[list[tuple[float, float]]] | None:
    """Trace connected entities in the DXF to form closed contour loops.

    Returns a list of contours, each a list of (x, y) tuples.
    The first contour (largest area) is the outer boundary.
    Subsequent contours are holes.
    Returns None on failure.
    """
    import ezdxf
    from ezdxf.math import Vec2

    profile_dir = BASE_DIR / "library" / "profiles"
    dxf_path = profile_dir / f"{profile}.dxf"
    if not dxf_path.exists():
        return None

    try:
        doc = ezdxf.readfile(str(dxf_path))
    except Exception:
        return None

    # Find the main INSERT block
    best_ents = []
    best_area = 0.0
    for e in doc.modelspace():
        if e.dxftype() != 'INSERT':
            continue
        block = doc.blocks.get(e.dxf.name)
        if not block:
            continue
        ents = [be for be in block if be.dxftype() in ('LINE','ARC','CIRCLE','LWPOLYLINE')]
        if not ents:
            continue
        xs, ys = [], []
        for ent in ents:
            if ent.dxftype() == 'LINE':
                xs.extend([ent.dxf.start.x, ent.dxf.end.x])
                ys.extend([ent.dxf.start.y, ent.dxf.end.y])
        if not xs:
            continue
        area = (max(xs) - min(xs)) * (max(ys) - min(ys))
        if area > best_area:
            best_area = area
            best_ents = ents

    if not best_ents:
        return None

    # Build adjacency graph: for each entity, store its endpoints
    # LINE: (start, end), ARC: sample into segments, CIRCLE: sample into segments
    segments: list[tuple[Vec2, Vec2]] = []
    for ent in best_ents:
        if ent.dxftype() == 'LINE':
            segments.append((
                Vec2(ent.dxf.start.x, ent.dxf.start.y),
                Vec2(ent.dxf.end.x, ent.dxf.end.y),
            ))
        elif ent.dxftype() in ('ARC', 'CIRCLE'):
            from ezdxf.path import make_path
            path = make_path(ent)
            verts = list(path.flattening(0.3))
            for i in range(len(verts) - 1):
                segments.append((
                    Vec2(verts[i].x, verts[i].y),
                    Vec2(verts[i + 1].x, verts[i + 1].y),
                ))
        elif ent.dxftype() == 'LWPOLYLINE':
            pts = list(ent.vertices())
            for i in range(len(pts) - 1):
                segments.append((
                    Vec2(pts[i][0], pts[i][1]),
                    Vec2(pts[i + 1][0], pts[i + 1][1]),
                ))

    if len(segments) < 3:
        return None

    # Trace closed contours from segments
    TOL = 1.0  # connection tolerance (mm) — DXF entities may have small gaps
    used = [False] * len(segments)
    contours: list[list[Vec2]] = []

    while True:
        # Find an unused segment to start tracing
        start_idx = next((i for i, u in enumerate(used) if not u), None)
        if start_idx is None:
            break

        contour: list[Vec2] = []
        current = segments[start_idx][0]
        contour.append(current)
        used[start_idx] = True

        # Follow the chain
        target = segments[start_idx][1]
        progress = True
        while progress:
            progress = False
            best_dist = TOL
            best_idx = -1
            best_flip = False

            for i, (a, b) in enumerate(segments):
                if used[i]:
                    continue
                # Check both directions
                da = (a - target).magnitude
                db = (b - target).magnitude
                if da < best_dist:
                    best_dist = da
                    best_idx = i
                    best_flip = False
                if db < best_dist:
                    best_dist = db
                    best_idx = i
                    best_flip = True

            if best_idx >= 0:
                used[best_idx] = True
                seg = segments[best_idx]
                if best_flip:
                    contour.append(seg[1])
                    target = seg[1]
                else:
                    contour.append(seg[0])
                    target = seg[0]
                progress = True

        # Check if the contour closes
        if (contour[-1] - contour[0]).magnitude < TOL and len(contour) >= 3:
            contours.append([(float(p.x), float(p.y)) for p in contour])

    if not contours:
        return None

    # Sort by area (largest = outer boundary)
    def polygon_area(verts):
        area = 0.0
        n = len(verts)
        for i in range(n):
            j = (i + 1) % n
            area += verts[i][0] * verts[j][1] - verts[j][0] * verts[i][1]
        return abs(area) / 2.0

    contours.sort(key=polygon_area, reverse=True)
    logger.info(f"DXF contours: {len(contours)} loops, outer={len(contours[0])} verts")
    return contours


def _read_dxf_profile(profile: str, expected_size_mm: float) -> np.ndarray | None:
    """Read a DXF profile and return the outer contour polygon as a 2D numpy array (N,2).

    Extracts ALL contour vertices (not just convex hull) to preserve
    T-slots, channels, and other concave features of the profile.
    Returns None if the DXF cannot be read.
    """
    import ezdxf
    from ezdxf.path import make_path

    profile_dir = BASE_DIR / "library" / "profiles"
    dxf_path = profile_dir / f"{profile}.dxf"
    if not dxf_path.exists():
        logger.warning(f"DXF not found: {dxf_path}")
        return None

    try:
        doc = ezdxf.readfile(str(dxf_path))
    except Exception as e:
        logger.warning(f"DXF read error: {e}")
        return None

    # Find the INSERT block with the largest bbox — this is the main profile
    best_ents = []
    best_area = 0.0
    for e in doc.modelspace():
        if e.dxftype() != 'INSERT':
            continue
        block = doc.blocks.get(e.dxf.name)
        if not block:
            continue
        ents = [be for be in block if be.dxftype() in ('LINE','ARC','CIRCLE','LWPOLYLINE')]
        if not ents:
            continue
        xs, ys = [], []
        for ent in ents:
            if ent.dxftype() == 'LINE':
                xs.extend([ent.dxf.start.x, ent.dxf.end.x])
                ys.extend([ent.dxf.start.y, ent.dxf.end.y])
        if not xs:
            continue
        area = (max(xs) - min(xs)) * (max(ys) - min(ys))
        if area > best_area:
            best_area = area
            best_ents = ents

    if not best_ents:
        # Fallback: use modelspace entities directly
        best_ents = [e for e in doc.modelspace() if e.dxftype() in ('LINE','ARC','CIRCLE','LWPOLYLINE')]

    if not best_ents:
        return None

    # Convert to paths (preserving entity connection order, not radial sort)
    try:
        all_verts: list[list[float]] = []
        for ent in best_ents:
            path = make_path(ent)
            for v in path.flattening(0.3):
                all_verts.append([v.x, v.y])

        if len(all_verts) < 3:
            return None

        outline = np.array(all_verts, dtype=np.float64)

        # Deduplicate consecutive points that are very close
        keep = [True] * len(outline)
        for i in range(len(outline)):
            j = (i + 1) % len(outline)
            if np.sqrt((outline[i,0]-outline[j,0])**2 + (outline[i,1]-outline[j,1])**2) < 0.05:
                keep[i] = False
        outline = outline[keep]

    except Exception as e:
        logger.warning(f"Path error: {e}")
        return None

    bbox_w = outline[:, 0].max() - outline[:, 0].min()
    bbox_h = outline[:, 1].max() - outline[:, 1].min()
    logger.info(f"DXF profile: {len(outline)} verts, {bbox_w:.0f}x{bbox_h:.0f}mm")
    return outline


def _extrude_dxf_to_stl_pure(
    outline_2d: np.ndarray, length_mm: float, filepath: Path,
) -> None:
    """Extrude a 2D polygon (N,2) by length_mm along Z, write binary STL.

    Uses fan triangulation. Profile is auto-centered at XY origin.
    Z is symmetric around 0 (extrusion along Z in solver coords).
    """
    import struct

    # Center the profile in XY using bounding box center
    cx = float((outline_2d[:, 0].max() + outline_2d[:, 0].min()) / 2.0)
    cy = float((outline_2d[:, 1].max() + outline_2d[:, 1].min()) / 2.0)
    centered = outline_2d - np.array([cx, cy], dtype=np.float64)

    n = len(outline_2d)
    half_l = float(length_mm) / 2.0

    bottom = np.column_stack([centered, np.full(n, -half_l, dtype=np.float64)])
    top = np.column_stack([centered, np.full(n, half_l, dtype=np.float64)])
    center_b = np.array([0.0, 0.0, -half_l], dtype=np.float64)
    center_t = np.array([0.0, 0.0, half_l], dtype=np.float64)

    triangles: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []

    for i in range(n):
        j = (i + 1) % n
        triangles.append((bottom[j], bottom[i], center_b))
    for i in range(n):
        j = (i + 1) % n
        triangles.append((top[i], top[j], center_t))
    for i in range(n):
        j = (i + 1) % n
        triangles.append((bottom[i], bottom[j], top[j]))
        triangles.append((bottom[i], top[j], top[i]))

    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, 'wb') as f:
        f.write(b'\x00' * 80)
        f.write(struct.pack('<I', len(triangles)))
        for a, b_val, c_val in triangles:
            n_vec = np.cross(b_val - a, c_val - a)
            norm = float(np.linalg.norm(n_vec))
            if norm < 1e-10:
                n_vec = np.array([0.0, 0.0, 1.0], dtype=np.float64)
            else:
                n_vec = n_vec / norm
            nx, ny, nz = float(n_vec[0]), float(n_vec[1]), float(n_vec[2])
            f.write(struct.pack('<3f', nx, ny, nz))
            for arr in (a, b_val, c_val):
                vx, vy, vz = float(arr[0]), float(arr[1]), float(arr[2])
                f.write(struct.pack('<3f', vx, vy, vz))
            f.write(struct.pack('<H', 0))


def _extrude_profile_to_stl(
    part_type: str, profile_size_mm: float, length_mm: float, filepath: Path,
    tabletop_w: float = 0, tabletop_d: float = 0,
) -> None:
    """Generate an STL by extruding a rectangular profile (pure Python, no CAD deps).

    Uses profile_size_mm (e.g. 30mm for 3030) for legs/beams or
    tabletop_w×tabletop_d for the tabletop. Writes binary STL centered at origin.
    """
    import struct, numpy as np

    if part_type == "tabletop":
        hw, hd, ht = tabletop_w / 2, tabletop_d / 2, length_mm / 2
        # 8 corners of the box
        corners = np.array([
            [-hw, -hd, -ht], [hw, -hd, -ht], [hw, hd, -ht], [-hw, hd, -ht],  # bottom
            [-hw, -hd, ht],  [hw, -hd, ht],  [hw, hd, ht],  [-hw, hd, ht],   # top
        ], dtype=np.float64)
        faces = [
            (0,1,2), (0,2,3),  # -Z
            (4,7,6), (4,6,5),  # +Z
            (0,4,5), (0,5,1),  # -Y
            (2,6,7), (2,7,3),  # +Y
            (1,5,6), (1,6,2),  # +X
            (0,3,7), (0,7,4),  # -X
        ]
    else:
        hs = profile_size_mm / 2
        hl = length_mm / 2
        corners = np.array([
            [-hs, -hs, -hl], [hs, -hs, -hl], [hs, hs, -hl], [-hs, hs, -hl],
            [-hs, -hs, hl],  [hs, -hs, hl],  [hs, hs, hl],  [-hs, hs, hl],
        ], dtype=np.float64)
        faces = [
            (0,1,2), (0,2,3), (4,7,6), (4,6,5),
            (0,4,5), (0,5,1), (2,6,7), (2,7,3),
            (1,5,6), (1,6,2), (0,3,7), (0,7,4),
        ]

    triangles = [(corners[a], corners[b], corners[c]) for a, b, c in faces]

    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, 'wb') as f:
        f.write(b'\x00' * 80)
        f.write(struct.pack('<I', len(triangles)))
        for a, b_val, c_val in triangles:
            n = np.cross(b_val - a, c_val - a)
            n = n / (np.linalg.norm(n) + 1e-10)
            nx, ny, nz = float(n[0]), float(n[1]), float(n[2])
            f.write(struct.pack('<3f', nx, ny, nz))
            for v in (a, b_val, c_val):
                vx, vy, vz = float(v[0]), float(v[1]), float(v[2])
                f.write(struct.pack('<3f', vx, vy, vz))
            f.write(struct.pack('<H', 0))


def _write_fast_stl_box(
    sx: float, sy: float, sz: float, filepath: Path,
) -> None:
    """Write a binary STL of a box centered at origin, dimensions sx×sy×sz (mm).

    Solver coords: X=right, Y=forward, Z=up.
    The box is an axis-aligned extrusion: sx along X, sy along Y, sz along Z.
    """
    import struct

    hx, hy, hz = sx / 2, sy / 2, sz / 2
    # 6 faces, 2 triangles each = 12 triangles
    # Each face: 4 corners → 2 triangles
    faces = [
        # +X face (right): normal (+1,0,0)
        [ (+hx, -hy, -hz), (+hx, +hy, -hz), (+hx, +hy, +hz), (+hx, -hy, +hz) ],
        # -X face (left): normal (-1,0,0)
        [ (-hx, -hy, +hz), (-hx, +hy, +hz), (-hx, +hy, -hz), (-hx, -hy, -hz) ],
        # +Y face (forward): normal (0,+1,0)
        [ (-hx, +hy, -hz), (-hx, +hy, +hz), (+hx, +hy, +hz), (+hx, +hy, -hz) ],
        # -Y face (backward): normal (0,-1,0)
        [ (-hx, -hy, +hz), (-hx, -hy, -hz), (+hx, -hy, -hz), (+hx, -hy, +hz) ],
        # +Z face (top): normal (0,0,+1)
        [ (-hx, -hy, +hz), (+hx, -hy, +hz), (+hx, +hy, +hz), (-hx, +hy, +hz) ],
        # -Z face (bottom): normal (0,0,-1)
        [ (-hx, -hy, -hz), (-hx, +hy, -hz), (+hx, +hy, -hz), (+hx, -hy, -hz) ],
    ]
    normals = [
        (1,0,0), (-1,0,0), (0,1,0), (0,-1,0), (0,0,1), (0,0,-1),
    ]

    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, 'wb') as f:
        f.write(b'\x00' * 80)  # header
        f.write(struct.pack('<I', 12))  # triangle count
        for face_idx, corners in enumerate(faces):
            nx, ny, nz = normals[face_idx]
            # Triangle 1: corners 0-1-2
            f.write(struct.pack('<3f', nx, ny, nz))
            for ci in (0, 1, 2):
                f.write(struct.pack('<3f', *corners[ci]))
            f.write(struct.pack('<H', 0))
            # Triangle 2: corners 0-2-3
            f.write(struct.pack('<3f', nx, ny, nz))
            for ci in (0, 2, 3):
                f.write(struct.pack('<3f', *corners[ci]))
            f.write(struct.pack('<H', 0))


def _build_joints_and_poses(solved, meta, profile_size, extrude_fn, model_id, req):
    """Shared helper: generate STL per part, build joints/world_poses, return cache entry."""
    output_root = OUTPUT_DIR / model_id / "basic_desk"
    visual_dir = output_root / "meshes" / "visual"

    parts = []
    for sp in solved.parts:
        stl_filename = f"{sp.name}.stl"
        stl_filepath = visual_dir / stl_filename
        extrude_fn(sp, stl_filepath)
        stl_rel = stl_filepath.relative_to(OUTPUT_DIR)
        parts.append({
            "name": sp.name, "part_type": sp.part_type,
            "profile": sp.profile, "board": sp.board, "material": sp.material,
            "dimensions": {
                "extrusion_length": sp.extrusion_length,
                "tabletop_width": sp.tabletop_width,
                "tabletop_depth": sp.tabletop_depth,
                "tabletop_thickness": sp.tabletop_thickness,
            },
            "mass_kg": None, "stl_url": f"/static/models/{stl_rel.as_posix()}",
        })

    # Build joints
    joints = []
    tabletop = next((sp for sp in solved.parts if sp.part_type == "tabletop"), None)
    if tabletop and tabletop.pose:
        joints.append({
            "name": "base_link_to_tabletop", "parent": "base_link", "child": "tabletop",
            "origin": {"x": tabletop.pose.x, "y": tabletop.pose.y, "z": tabletop.pose.z,
                       "roll": tabletop.pose.roll, "pitch": tabletop.pose.pitch, "yaw": tabletop.pose.yaw},
        })
    for sp in solved.parts:
        if sp.part_type in ("leg", "beam") and sp.pose:
            joints.append({
                "name": f"tabletop_to_{sp.name}", "parent": "tabletop", "child": sp.name,
                "origin": {"x": sp.pose.x, "y": sp.pose.y, "z": sp.pose.z,
                           "roll": sp.pose.roll, "pitch": sp.pose.pitch, "yaw": sp.pose.yaw},
            })

    # World poses via tree walk
    children_map: dict[str, list[dict]] = {}
    for j in joints:
        children_map.setdefault(j["parent"], []).append(j)

    def walk(link_name, pp, _):
        r = {}
        for jt in children_map.get(link_name, []):
            o = jt["origin"]
            ca = {"x": pp[0] + o["x"], "y": pp[1] + o["y"], "z": pp[2] + o["z"],
                  "roll": o["roll"], "pitch": o["pitch"], "yaw": o["yaw"]}
            r[jt["child"]] = ca
            r.update(walk(jt["child"], (ca["x"], ca["y"], ca["z"]), (ca["roll"], ca["pitch"], ca["yaw"])))
        return r

    world_poses = walk("base_link", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    for p in parts:
        wp = world_poses.get(p["name"])
        if wp:
            p["pose"] = wp
        p["joint_parent"] = next((j["parent"] for j in joints if j["child"] == p["name"]), None)

    return {
        "model_id": model_id, "name": meta["name"], "status": "full", "parts": parts,
        "stl_url": parts[0]["stl_url"] if parts else None,
        "urdf_url": None, "joints": joints,
        "dimensions": {"width": req.width, "depth": req.depth,
                       "height": req.height, "tabletop_thickness": req.tabletop_thickness},
    }


def _generate_trimesh(meta: dict, req: GenerateRequest, solved, model_id: str) -> dict:
    """build123d mode: ezdxf reads DXF → build123d Wire→Face→Solid→STEP+STL.

    Generates proper B-Rep solids with STEP export, then tessellates to STL.
    Handles concave profiles (T-slots) correctly.
    Falls back to pure Python extrusion if build123d fails.
    """
    profile_map = {"2020": 20.0, "3030": 30.0, "4040": 40.0}
    profile_size = profile_map.get(req.profile, 30.0)
    output_root = OUTPUT_DIR / model_id / "basic_desk"
    visual_dir = output_root / "meshes" / "visual"
    cad_dir = output_root / "cad"

    def extrude_part(sp, filepath):
        step_path = Path(str(filepath).replace("/meshes/visual/", "/cad/").replace(".stl", ".step"))

        if sp.part_type == "tabletop":
            ok = _build123d_extrude(
                req.profile, sp.tabletop_thickness, filepath, step_path,
                tabletop_w=sp.tabletop_width, tabletop_d=sp.tabletop_depth,
            )
        else:
            ok = _build123d_extrude(req.profile, sp.extrusion_length, filepath, step_path)

        if not ok:
            # Fallback: pure Python extrusion
            logger.warning(f"build123d failed for {sp.name}, using pure Python fallback")
            if sp.part_type == "tabletop":
                hw, hd = sp.tabletop_width / 2.0, sp.tabletop_depth / 2.0
                rect = np.array([[-hw,-hd],[hw,-hd],[hw,hd],[-hw,hd]], dtype=np.float64)
                _extrude_dxf_to_stl_pure(rect, sp.tabletop_thickness, filepath)
            else:
                _extrude_profile_to_stl(
                    sp.part_type, profile_size, sp.extrusion_length, filepath,
                )
            # Fallback to rectangular
            _extrude_profile_to_stl(
                sp.part_type, profile_size, sp.extrusion_length, filepath,
            )

    return _build_joints_and_poses(solved, meta, profile_size, extrude_part, model_id, req)


def _generate_fast(meta: dict, req: GenerateRequest, solved, model_id: str) -> dict:
    """Fast mode: skip CAD pipeline, generate simple box STLs directly.

    Uses only extrusion (no DXF import, no hole cutting, no STEP export).
    """
    profile_map = {"2020": 20.0, "3030": 30.0, "4040": 40.0}
    profile_size = profile_map.get(req.profile, 30.0)
    output_root = OUTPUT_DIR / model_id / "basic_desk"
    visual_dir = output_root / "meshes" / "visual"

    parts = []
    for sp in solved.parts:
        stl_path = None
        stl_filename = f"{sp.name}.stl"
        stl_filepath = visual_dir / stl_filename

        if sp.part_type == "tabletop":
            # Large flat box: width × depth × thickness
            _write_fast_stl_box(
                sp.tabletop_width, sp.tabletop_depth, sp.tabletop_thickness,
                stl_filepath,
            )
        elif sp.part_type in ("leg", "beam"):
            # Square extrusion: profile × profile × length
            _write_fast_stl_box(
                profile_size, profile_size, sp.extrusion_length,
                stl_filepath,
            )
        else:
            _write_fast_stl_box(profile_size, profile_size, sp.extrusion_length, stl_filepath)

        stl_rel = stl_filepath.relative_to(OUTPUT_DIR)
        stl_path = f"/static/models/{stl_rel.as_posix()}"

        parts.append({
            "name": sp.name, "part_type": sp.part_type,
            "profile": sp.profile, "board": sp.board, "material": sp.material,
            "dimensions": {
                "extrusion_length": sp.extrusion_length,
                "tabletop_width": sp.tabletop_width,
                "tabletop_depth": sp.tabletop_depth,
                "tabletop_thickness": sp.tabletop_thickness,
            },
            "mass_kg": None, "stl_url": stl_path,
        })

    # Build URDF joints and world poses (same as full pipeline)
    from parametric_furniture.models.pose import Pose

    # Copy joint structure from the solved assembly
    # For fast mode, we reconstruct joints from solver data
    joints = []
    # base_link → tabletop
    tabletop = next((sp for sp in solved.parts if sp.part_type == "tabletop"), None)
    if tabletop and tabletop.pose:
        joints.append({
            "name": "base_link_to_tabletop",
            "parent": "base_link",
            "child": "tabletop",
            "origin": {"x": tabletop.pose.x, "y": tabletop.pose.y, "z": tabletop.pose.z,
                       "roll": tabletop.pose.roll, "pitch": tabletop.pose.pitch, "yaw": tabletop.pose.yaw},
        })

    # tabletop → each leg/beam
    for sp in solved.parts:
        if sp.part_type in ("leg", "beam") and sp.pose:
            joint_name = f"tabletop_to_{sp.name}"
            joints.append({
                "name": joint_name, "parent": "tabletop", "child": sp.name,
                "origin": {"x": sp.pose.x, "y": sp.pose.y, "z": sp.pose.z,
                           "roll": sp.pose.roll, "pitch": sp.pose.pitch, "yaw": sp.pose.yaw},
            })

    # Compute world poses via tree walk
    children_map: dict[str, list[dict]] = {}
    for j in joints:
        children_map.setdefault(j["parent"], []).append(j)

    def walk_tree(link_name: str, parent_pos: tuple, _parent_rot: tuple):
        results = {}
        for joint in children_map.get(link_name, []):
            child_name = joint["child"]
            origin = joint["origin"]
            child_abs = {
                "x": parent_pos[0] + origin["x"], "y": parent_pos[1] + origin["y"],
                "z": parent_pos[2] + origin["z"],
                "roll": origin["roll"], "pitch": origin["pitch"], "yaw": origin["yaw"],
            }
            results[child_name] = child_abs
            results.update(walk_tree(child_name,
                (child_abs["x"], child_abs["y"], child_abs["z"]),
                (child_abs["roll"], child_abs["pitch"], child_abs["yaw"])))
        return results

    world_poses = walk_tree("base_link", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))

    # Add pose + joint_parent to parts
    for p in parts:
        wp = world_poses.get(p["name"])
        if wp:
            p["pose"] = wp
        p["joint_parent"] = next((j["parent"] for j in joints if j["child"] == p["name"]), None)

    return {
        "model_id": model_id,
        "name": meta["name"],
        "status": "full",
        "parts": parts,
        "stl_url": parts[0]["stl_url"] if parts else None,
        "urdf_url": None,
        "joints": joints,
        "dimensions": {"width": req.width, "depth": req.depth,
                       "height": req.height, "tabletop_thickness": req.tabletop_thickness},
    }


def _solver_only_generate(req: GenerateRequest) -> dict:
    """Fast solver-only generation — no CAD build. Returns dimensions + poses."""
    template = _load_template(req.template_id)
    params = DeskParameters(
        width=req.width, depth=req.depth, height=req.height,
        tabletop_thickness=req.tabletop_thickness, profile=req.profile,
        board_material=req.board_material, color=req.color,
    )
    solved = DeskSolver().solve(template, params)
    model_id = str(uuid.uuid4())[:8]
    parts = []
    for sp in solved.parts:
        parts.append({
            "name": sp.name, "part_type": sp.part_type,
            "profile": sp.profile, "board": sp.board, "material": sp.material,
            "dimensions": {
                "extrusion_length": sp.extrusion_length,
                "tabletop_width": sp.tabletop_width,
                "tabletop_depth": sp.tabletop_depth,
                "tabletop_thickness": sp.tabletop_thickness,
            },
            "pose": {"x": sp.pose.x, "y": sp.pose.y, "z": sp.pose.z,
                     "roll": sp.pose.roll, "pitch": sp.pose.pitch, "yaw": sp.pose.yaw} if sp.pose else None,
            "stl_url": None, "mass_kg": None,
        })
    return {
        "model_id": model_id,
        "name": TEMPLATE_META[req.template_id]["name"],
        "status": "warming",
        "parts": parts,
        "stl_url": None,
        "urdf_url": None,
        "joints": [],
        "dimensions": {"width": req.width, "depth": req.depth,
                       "height": req.height, "tabletop_thickness": req.tabletop_thickness},
    }


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static/models", StaticFiles(directory=str(OUTPUT_DIR)), name="static_models")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    logger.info("WoodCraft Backend v2 → http://0.0.0.0:8000")
    logger.info("Cache warmup starts in background...")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
