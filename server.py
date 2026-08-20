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
import os
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: write our pid (single-instance guard), launch warmup. Shutdown: clean up pid."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    SERVER_PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    logger.info(f"Cache warmup starts in background... (pid={os.getpid()})")
    threading.Thread(target=_warmup, daemon=True).start()
    try:
        yield
    finally:
        SERVER_PID_FILE.unlink(missing_ok=True)
        logger.info("Server shutting down.")


app = FastAPI(title="WoodCraft Backend v2", version="0.2.0", lifespan=lifespan)
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
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
DIY_BRACKET_LOG = LOGS_DIR / "diy_brackets.jsonl"
SERVER_PID_FILE = LOGS_DIR / "server.pid"

# Thread-safe cache and progress
_cache_lock = threading.Lock()
_log_lock = threading.Lock()
model_cache: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Single-instance guard: detect an already-running backend on second launch
# ---------------------------------------------------------------------------

def _pid_alive(pid: int) -> bool:
    """True if a process with this pid is running (probe only, no kill)."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _find_port_pid(port: int) -> Optional[int]:
    """Best-effort PID of the process LISTENING on `port`."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, timeout=10
            ).stdout
            for line in out.splitlines():
                parts = line.split()
                if (
                    len(parts) >= 5
                    and parts[0] == "TCP"
                    and parts[1].endswith(f":{port}")
                    and parts[3] == "LISTENING"
                ):
                    try:
                        return int(parts[4])
                    except ValueError:
                        continue
        else:
            out = subprocess.run(
                ["lsof", "-nP", "-iTCP:%d" % port, "-sTCP:LISTEN"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            for line in out.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        return int(parts[1])
                    except ValueError:
                        continue
    except Exception:
        pass
    return None


def _kill_command(pid: int) -> str:
    """Shell command that kills the given pid (copy-paste ready)."""
    return f"taskkill /PID {pid} /F" if sys.platform == "win32" else f"kill -9 {pid}"


def running_server_pid() -> Optional[int]:
    """PID of an already-running backend instance, or None.

    Checks the pid file first (fast, reliable), then falls back to the port —
    this also catches instances started before the pid file mechanism existed.
    """
    if SERVER_PID_FILE.exists():
        try:
            pid = int(SERVER_PID_FILE.read_text(encoding="utf-8").strip())
            if _pid_alive(pid):
                return pid
        except (ValueError, OSError):
            pass
    return _find_port_pid(8000)

progress_state: dict = {
    "phase": "idle",        # "idle" | "warming" | "generating"
    "message": "",
    "current": 0,
    "total": 0,
    "config": 0,            # which warmup config
    "config_total": 0,
    "part": "",             # current part name
}

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



def _cleanup_old_models(keep: int = 5):
    """Delete old model dirs, but never delete ones still referenced by cache."""
    cached_ids = {e["model_id"] for e in model_cache.values()}
    dirs = sorted(
        [d for d in OUTPUT_DIR.iterdir() if d.is_dir() and d.name != "pregen"],
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    # Delete dirs beyond `keep`, skipping cached ones
    deleted = 0
    for d in dirs:
        if deleted >= max(len(dirs) - keep, 0):
            break
        if d.name not in cached_ids:
            import shutil
            shutil.rmtree(d, ignore_errors=True)
            deleted += 1


def _normalize_dims(dims: dict | None) -> dict | None:
    """Normalize builder dimension keys to consistent names."""
    if not dims:
        return None
    d = dict(dims)
    # Builder uses "length" for extrusion length; unify to "extrusion_length"
    if "length" in d and "extrusion_length" not in d:
        d["extrusion_length"] = d.pop("length")
    return d


def _load_template(template_id: str) -> FurnitureTemplate:
    path = TEMPLATES_DIR / "desk" / "basic.yaml"
    return FurnitureTemplate.from_yaml(str(path))


def _find_solved(solved, name: str):
    for p in solved.parts:
        if p.name == name:
            return p
    return None


def _do_generate_with_progress(req: GenerateRequest, model_id: str | None = None) -> dict:
    """Wrapper that updates progress_state during generation.

    Monitors the output directory for new STL files to track per-part progress.
    """
    if model_id is None:
        model_id = str(uuid.uuid4())[:8]

    global progress_state
    progress_state["phase"] = "generating"
    progress_state["current"] = 0
    progress_state["total"] = 9
    progress_state["part"] = "solving..."

    # ---- Background thread: watch output dir for new STL files ----
    watch_dir = OUTPUT_DIR / model_id
    watch_dir.mkdir(parents=True, exist_ok=True)

    stop_watch = threading.Event()
    seen_files: set[str] = set()

    def watch_stl_files():
        while not stop_watch.is_set():
            try:
                stls = list(watch_dir.rglob("visual/*.stl"))
                for stl in stls:
                    name = stl.stem
                    if name not in seen_files:
                        seen_files.add(name)
                        progress_state["current"] = len(seen_files)
                        progress_state["part"] = f"{name} ({len(seen_files)}/9)"
            except Exception:
                pass
            stop_watch.wait(0.5)  # poll every 500ms

    watcher = threading.Thread(target=watch_stl_files, daemon=True)
    watcher.start()
    # ----------------------------------------------------------------

    try:
        # Override the output dir so files land in our watched directory
        result = _do_generate(req, model_id=model_id)
        # Final sweep
        stls = list(watch_dir.rglob("visual/*.stl"))
        progress_state["current"] = min(len(stls), 9)
        progress_state["part"] = "done"
        return result
    except Exception:
        progress_state["phase"] = "idle"
        raise
    finally:
        stop_watch.set()
        watcher.join(timeout=2)


def _do_generate(req: GenerateRequest, model_id: str | None = None) -> dict:
    """Run the full pipeline: template → solve → build. Returns cache entry dict."""
    if model_id is None:
        model_id = str(uuid.uuid4())[:8]

    template = _load_template(req.template_id)
    params = DeskParameters(
        width=req.width, depth=req.depth, height=req.height,
        tabletop_thickness=req.tabletop_thickness, profile=req.profile,
        board_material=req.board_material, color=req.color,
    )
    solved = DeskSolver().solve(template, params)
    meta = TEMPLATE_META[req.template_id]

    # All requests go through the full DeskBuilder (VisualCAD) pipeline;
    # the legacy "fast"/"trimesh" quality tiers were removed.
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
                "dimensions": _normalize_dims(part.dimensions),
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
    entry = _do_generate_with_progress(req)

    with _cache_lock:
        model_cache[key] = entry

    # Reset progress to idle
    global progress_state
    progress_state = {
        "phase": "idle", "message": "就绪",
        "current": 0, "total": 0, "config": 0, "config_total": 0, "part": "",
    }

    _cleanup_old_models()
    # Limit cache to 20 entries (evict oldest)
    while len(model_cache) > 20:
        oldest_key = min(model_cache.keys(), key=lambda k: model_cache[k].get("_cached_at", 0))
        del model_cache[oldest_key]
        logger.debug(f"Cache evicted: {oldest_key}")
    entry["_cached_at"] = time.time()
    logger.info(f"Cached: {key} ({entry['status']}, {len(entry['parts'])} parts)")
    return entry


# ---------------------------------------------------------------------------
# Startup warmup — generate default model in background
# ---------------------------------------------------------------------------

WARMUP_CONFIGS = [
    GenerateRequest(template_id="basic-desk", width=1200, depth=600, height=750, profile="3030", stl_quality="web"),
    GenerateRequest(template_id="basic-desk", width=1500, depth=700, height=750, profile="3030", stl_quality="web"),
]


def _warmup() -> None:
    """Pre-generate common configurations in background."""
    global progress_state
    progress_state = {
        "phase": "warming", "message": "预热默认配置...",
        "current": 0, "total": len(WARMUP_CONFIGS) * 9,
        "config": 0, "config_total": len(WARMUP_CONFIGS), "part": "",
    }
    logger.info(f"Cache warmup: generating {len(WARMUP_CONFIGS)} config(s)...")
    for i, cfg in enumerate(WARMUP_CONFIGS):
        key = _cache_key(cfg)
        with _cache_lock:
            if key in model_cache:
                progress_state["current"] = (i + 1) * 9
                continue
        progress_state["config"] = i + 1
        progress_state["part"] = "solving..."
        logger.info(f"Warmup [{i+1}/{len(WARMUP_CONFIGS)}]: {cfg.width}x{cfg.depth}x{cfg.height}")
        entry = _do_generate_with_progress(cfg)
        with _cache_lock:
            model_cache[key] = entry
        progress_state["current"] = (i + 1) * 9
        logger.info(f"Warmup [{i+1}/{len(WARMUP_CONFIGS)}] done: {entry['status']}")
    progress_state = {
        "phase": "idle", "message": "就绪",
        "current": 0, "total": 0, "config": 0, "config_total": 0, "part": "",
    }
    logger.info(f"Cache warmup complete. {len(model_cache)} entries cached.")
    _cleanup_old_models()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "cache_size": len(model_cache)}


@app.get("/api/progress")
def get_progress():
    """Current warmup/generation progress for the frontend progress bar."""
    return progress_state


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
        _cleanup_old_models()
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
    """Generate model (from cache if available, otherwise solve → return immediately, build in background).

    Pattern:
      - Cache hit → instant full response
      - Cache miss → solver data immediately (<100ms), background CAD generation
      - Frontend polls until stl_url is populated
    """
    meta = TEMPLATE_META.get(req.template_id)
    if not meta:
        raise HTTPException(404, f"Template not found: {req.template_id}")

    key = _cache_key(req)

    with _cache_lock:
        cached = model_cache.get(key)

    if cached:
        logger.info(f"Generate: cache HIT {key}")
        return GenerateResponse(
            model_id=cached["model_id"], name=cached["name"], status=cached["status"],
            parts=[PartInfo(**p) for p in cached["parts"]],
            dimensions=cached["dimensions"],
            stl_url=cached.get("stl_url"),
            urdf_url=cached.get("urdf_url"),
            joints=cached.get("joints", []),
        )

    # Cache miss — solver data immediately, then background generation
    logger.info(f"Generate: cache MISS {key} — returning solver data, backgrounding CAD")

    # 1. Solver-only for immediate response
    solver_entry = _solver_only_generate(req)
    model_id = solver_entry["model_id"]

    # 2. Register a placeholder in cache so subsequent polls get solver data
    with _cache_lock:
        model_cache[key] = solver_entry

    # 3. Background thread: generate CAD + STL, then update cache
    def _background_build():
        try:
            full_entry = _do_generate_with_progress(req, model_id=model_id)
            with _cache_lock:
                model_cache[key] = full_entry
            logger.info(f"Background generation complete: {model_id} ({full_entry['status']})")
        except Exception as exc:
            logger.error(f"Background generation failed: {exc}")
            solver_entry["message"] = f"Cad build failed: {exc}"

    threading.Thread(target=_background_build, daemon=True).start()

    return GenerateResponse(
        model_id=model_id, name=meta["name"],
        status="warming",
        parts=[PartInfo(**p) for p in solver_entry["parts"]],
        dimensions=solver_entry["dimensions"],
        message="CAD generation started in background. Poll /api/models/default or retry this endpoint.",
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

# ---------------------------------------------------------------------------
# Bracket Rotation Solver
# ---------------------------------------------------------------------------

class BracketRotationRequest(BaseModel):
    """Two face normals (unit vectors in assembly frame)."""
    face1: list[float]  # [x, y, z] normal of first selected face
    face2: list[float]  # [x, y, z] normal of second selected face


class BracketRotationResponse(BaseModel):
    roll: float   # degrees, ZYX intrinsic Euler
    pitch: float
    yaw: float
    rotation_matrix: list[list[float]]  # 3×3, for debugging


@app.post("/api/bracket/rotation")
def compute_bracket_rotation(req: BracketRotationRequest) -> BracketRotationResponse:
    try:
        return _compute(req)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Bracket rotation failed: {e}", exc_info=True)
        raise HTTPException(500, str(e))

def _compute(req: BracketRotationRequest) -> BracketRotationResponse:
    """Compute the rotation that orients a corner bracket onto two faces.

    The bracket STL is an L-plate in the XY plane whose two mounting faces
    (the inner faces of the two plates) have model-space normals -X and -Y.
    To make those plates press against two target extrusion faces with
    outward normals f1, f2, the rotation must satisfy::

        R . (1,0,0) = f1      R . (0,1,0) = f2

    so R is simply the matrix with columns f1, f2, f1 x f2. The Euler angles
    are extracted in XYZ order (R = Rx*Ry*Rz) to match how the Web viewer
    applies rotations; the client prefers the returned rotation_matrix.
    """
    logger.info(f"Bracket rotation: face1={req.face1}, face2={req.face2}")
    import math

    try:
        f1 = np.array(req.face1, dtype=float)
        f2 = np.array(req.face2, dtype=float)
        f1 /= np.linalg.norm(f1)
        f2 /= np.linalg.norm(f2)

        # A corner bracket joins two *perpendicular* extrusion faces.
        cos_ang = float(np.dot(f1, f2))
        if abs(cos_ang) > 0.05:
            raise HTTPException(
                400,
                f"Faces are not perpendicular (dot={cos_ang:.3f}); "
                "a corner bracket joins two perpendicular faces",
            )
        if np.linalg.norm(np.cross(f1, f2)) < 1e-10:
            raise HTTPException(400, "Face normals are parallel")

        # Make the pair an exact orthonormal basis (Gram-Schmidt).
        f2 = f2 - cos_ang * f1
        f2 /= np.linalg.norm(f2)
        f3 = np.cross(f1, f2)  # unit, since f1 _|_ f2 and both are unit

        # R = [f1 f2 f3]  ->  R.(1,0,0)=f1, R.(0,1,0)=f2.
        R = np.column_stack([f1, f2, f3])
        R_mat = [[float(v) for v in row] for row in R]

        roll, pitch, yaw = _euler_xyz(R)

        r = BracketRotationResponse(
            roll=round(float(math.degrees(roll)), 1),
            pitch=round(float(math.degrees(pitch)), 1),
            yaw=round(float(math.degrees(yaw)), 1),
            rotation_matrix=R_mat,
        )
        logger.info(f"Bracket rotation: roll={r.roll} pitch={r.pitch} yaw={r.yaw}")
        return r
    except HTTPException:
        raise
    except Exception:
        logger.exception("Bracket rotation crashed")
        raise


def _euler_xyz(R):
    """Extract XYZ-order Euler angles from R = Rx(roll)*Ry(pitch)*Rz(yaw)."""
    import math

    if abs(R[0, 2]) < 0.999999:
        pitch = math.asin(R[0, 2])
        yaw = math.atan2(-R[0, 1], R[0, 0])
        roll = math.atan2(-R[1, 2], R[2, 2])
    else:  # pitch = +-90 deg (gimbal lock)
        pitch = math.asin(R[0, 2])
        roll = 0.0
        yaw = math.atan2(-R[1, 0], R[1, 1])
    return roll, pitch, yaw


class DiyBracketLogRequest(BaseModel):
    """One DIY-builder bracket event, persisted as a JSONL line.

    event: "corner_hints" (preview-mode hints) | "bracket_placed" (two-face
    double-click placement). payload carries the browser-side details; the
    server stamps a receive time so multi-browser sessions are comparable.
    """
    event: str
    payload: dict


@app.post("/api/log/bracket")
def log_bracket(req: DiyBracketLogRequest):
    """Append a bracket log entry to logs/diy_brackets.jsonl (one JSON per line)."""
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "event": req.event,
        **req.payload,
    }
    with _log_lock:
        with open(DIY_BRACKET_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static/models", StaticFiles(directory=str(OUTPUT_DIR)), name="static_models")
app.mount("/static/library", StaticFiles(directory=str(BASE_DIR / "library")), name="static_library")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    # Force UTF-8 stdout so the Chinese guard message renders in Git Bash and
    # other UTF-8 terminals (not just the Windows console API).
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    # Second-launch guard: if an instance is already running, print its PID and
    # a copy-paste kill command, then exit WITHOUT starting a second server.
    existing = running_server_pid()
    if existing is not None:
        print("=" * 60)
        print("[server] 检测到后端已在运行,跳过二次启动。")
        print(f"[server] 正在运行的进程 PID = {existing}")
        print("[server] 可杀死该进程的命令:")
        print(f"         {_kill_command(existing)}")
        print("=" * 60)
        sys.exit(0)

    logger.info("WoodCraft Backend v2 → http://0.0.0.0:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
