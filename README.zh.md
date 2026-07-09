# Parametric Furniture Generator（参数化家具生成器）

一个具有工程级质量的参数化家具生成平台。采用声明式家具模板 + 用户参数 → 通过求解器计算尺寸 → 通过构建器生成三维几何体 → 导出 URDF。

**桌子（Desk）** 是第一个模板。后续可无缝扩展：置物架（Shelf）、柜子（Cabinet）、工作台（Workbench）、电视柜（TV Stand）等。

## 系统架构

```
YAML 模板 + Pydantic 参数
        ↓
   FurnitureSolver  （计算尺寸、位姿、关节原点）
        ↓
   FurnitureBuilder （通过 VisualCAD 生成三维零件，创建装配体）
        ↓
   FurnitureAssembly （零件、连杆、关节、位姿 —— 与 CAD 无关的中间模型）
        ↓
   URDFWriter （XML 导出，不重新计算任何数值）
```

### 核心设计原则

- **模板（Template）**：声明式设计。仅描述有哪些零件以及它们如何连接。不包含任何尺寸、坐标或公式。
- **参数（Parameters）**：仅包含用户需要输入的数值，即用户自然会指定的尺寸。
- **求解器（Solver）**：所有计算逻辑集中于此。每种家具类型有独立的设计规则。
- **构建器（Builder）**：通过 VisualCAD 生成三维几何体，导出 STEP/STL，计算质量和惯性。
- **URDF 导出器（Exporter）**：纯粹的 XML 生成，绝不重新计算任何数值。

### 数据流严格单向

没有任何模块依赖其下游模块。求解器不导入构建器，构建器不导入导出器。

## 快速开始

### 环境要求

- Python 3.12+
- VisualCAD（外部依赖，位于 `../visualcad/`）

### 安装

```bash
pip install -e .
```

### 生成一张桌子

```bash
# 默认参数（1200×600×750mm，2020 型材，胶合板桌面）
python app.py build templates/desk/basic.yaml

# 自定义参数
python app.py build templates/desk/basic.yaml \
    --width 1600 \
    --depth 800 \
    --height 720 \
    --profile 3030 \
    --board oak \
    --tabletop-thickness 25 \
    --output ./my_desk_output

# 详细日志输出
python app.py build templates/desk/basic.yaml --verbose
```

### 输出文件

生成的输出结构采用与 UR5e 等标准机器人模型一致的 URDF 包布局：

```
output/basic_desk/
├── basic_desk.urdf                        # URDF 装配文件（包根目录）
├── meshes/
│   ├── visual/
│   │   ├── tabletop.stl                   # 桌面视觉网格
│   │   ├── leg_front_left.stl             # 左前腿视觉网格
│   │   ├── leg_front_right.stl            # 右前腿视觉网格
│   │   ├── leg_back_left.stl              # 左后腿视觉网格
│   │   ├── leg_back_right.stl             # 右后腿视觉网格
│   │   ├── beam_front.stl                 # 前横梁视觉网格
│   │   ├── beam_back.stl                  # 后横梁视觉网格
│   │   ├── beam_left.stl                  # 左横梁视觉网格
│   │   └── beam_right.stl                 # 右横梁视觉网格
│   └── collision/                         # 碰撞检测网格（与visual相同）
│       └── ...（同上 9 个 .stl 文件）
└── cad/
    ├── tabletop.step                      # 桌面 STEP CAD 文件
    ├── leg_front_left.step                # 左前腿 STEP
    └── ...（同上 9 个 .step 文件）
```

网格引用使用相对路径（如 `filename="meshes/visual/tabletop.stl"`），可直接在 RViz、Gazebo、Foxglove Studio 等工具中可视化。

## 项目结构

```
Parametric Furniture Generator/
├── app.py                    # CLI 入口（Typer）
├── config.py                 # 应用配置（Pydantic Settings）
├── pyproject.toml            # 依赖与构建配置
├── models/                   # 数据模型（Pydantic）
│   ├── pose.py               # 位姿（位置 + 欧拉角）
│   ├── template.py           # FurnitureTemplate、PartTemplate
│   ├── parameter.py          # DeskParameters
│   └── furniture.py          # FurnitureAssembly、Part、Link、Joint
├── solvers/                  # 尺寸与位姿计算
│   ├── furniture_solver.py   # 抽象基类 + 求解器注册表
│   └── desk_solver.py        # 桌子设计规则
├── builders/                 # 三维几何体生成
│   ├── furniture_builder.py  # 抽象基类 + 构建器注册表
│   └── desk_builder.py       # 桌子构建器（集成 VisualCAD）
├── exporters/                # URDF 导出
│   └── urdf_writer.py        # 装配体 → URDF XML
├── cli/                      # 命令行界面
│   └── build.py              # build 命令
├── templates/                # 声明式家具模板
│   └── desk/
│       └── basic.yaml        # 基础四腿工作桌
├── library/                  # 家具资源库
│   ├── profiles/             # DXF 截面型材
│   ├── boards/               # 板材规格
│   └── materials/            # 材料密度表
├── output/                   # 生成的 STEP/STL/URDF 文件
└── tests/                    # 测试套件
    ├── test_template_loader.py
    ├── test_desk_solver.py
    ├── test_desk_builder.py
    └── test_urdf_writer.py
```

## 模板编写规范

模板是描述家具结构的 YAML 文件，采用声明式设计。

### 规则

1. **只描述零件和拓扑关系** —— 绝不包含尺寸、坐标或公式
2. **零件名称必须唯一**，仅允许使用 `[a-zA-Z0-9_-]`
3. **每个零件有一个类型**（`leg`、`beam`、`tabletop`、`shelf`、`door` 等）
4. **拓扑关系列出哪些零件相连** —— 无向配对

### 示例：基础桌子

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

### 零件类型参考

| 零件类型 | 说明 | 必填字段 |
|---------|------|---------|
| `leg` | 垂直支撑 | `profile` |
| `beam` | 水平连接 | `profile` |
| `tabletop` | 平面工作台面 | `board` |
| `shelf` | 水平储物层板 | `board` |

## 求解器开发规范

新增家具类型时，实现一个 Solver 子类：

```python
from solvers.furniture_solver import AbstractFurnitureSolver, SolvedPart, SolverOutput, register_solver

class ShelfSolver(AbstractFurnitureSolver):
    @property
    def furniture_type(self) -> str:
        return "shelf"

    def solve(self, template, parameters) -> SolverOutput:
        self.validate_inputs(template, parameters)
        parts = []
        # ... 计算置物架的尺寸与位姿 ...
        return SolverOutput(
            furniture_type="shelf",
            template_name=template.name,
            parts=parts,
        )

# 自动注册
register_solver("shelf", ShelfSolver)
```

### 求解器职责

- 计算每个零件的**拉伸长度**
- 计算每个零件在装配坐标系中的**位姿**
- 计算每个零件的**关节原点**
- 遵循该家具类型的**设计规则**

## 构建器开发规范

```python
from builders.furniture_builder import AbstractFurnitureBuilder, register_builder

class ShelfBuilder(AbstractFurnitureBuilder):
    @property
    def furniture_type(self) -> str:
        return "shelf"

    def build(self, solver_output):
        # 1. 通过 VisualCAD 生成三维实体
        # 2. 导出 STEP/STL
        # 3. 计算质量与惯性
        # 4. 创建 FurnitureAssembly
        return assembly

register_builder("shelf", ShelfBuilder)
```

## 新增家具类型完整流程

1. **模板**：创建 `templates/<type>/<variant>.yaml`
2. **参数**：在 `models/parameter.py` 中新增参数模型（或复用已有）
3. **求解器**：在 `solvers/<type>_solver.py` 中实现 `<Type>Solver`
4. **构建器**：在 `builders/<type>_builder.py` 中实现 `<Type>Builder`
5. **注册**：求解器和构建器在 import 时自动注册
6. **测试**：在 `tests/` 中添加对应测试

**无需修改任何已有代码。**

## 测试

```bash
pytest tests/ -v
```

## 依赖项

- **VisualCAD**：DXF → 三维实体流水线（外部依赖，位于 `../visualcad/`）
- **Pydantic**：数据校验
- **Typer**：CLI 框架
- **Loguru**：结构化日志
- **PyYAML**：模板解析
- **ezdxf**：DXF 型材生成
- **lxml**：URDF XML 生成

## License

MIT
