"""
Tools 包初始化
==============
负责将 skills/*.yaml（工具描述）和 tools/*.py（工具实现）整合在一起。

整合流程：
  1. 扫描 skills/ 目录，读取所有 *.yaml 文件
     → 每个 YAML 文件包含一个 skill 的 tools 列表（Schema）
     → 将所有 tools 合并为 ALL_TOOLS 列表（传给 Claude）

  2. 从各 tools/*.py 模块导入 REGISTRY 字典
     → 每个 REGISTRY 是 {工具名: 函数} 的映射
     → 将所有 REGISTRY 合并为 ALL_REGISTRY（用于工具分发）

  3. 启动时打印已加载的 Skill 和工具数量，方便调试

新增 Skill 步骤：
  1. 在 skills/ 目录创建 xxx_skill.yaml（定义工具 Schema）
  2. 在 tools/ 目录创建 xxx_tools.py（实现工具函数 + REGISTRY）
  3. 在本文件末尾的 _TOOL_MODULES 列表中追加 xxx_tools 模块

使用方式：
  from tools import ALL_TOOLS, ALL_REGISTRY
"""

import os
import glob
from pathlib import Path
from typing import Any, Dict, List

import yaml  # pip install pyyaml

# 导入各工具模块（每个模块提供一个 REGISTRY 字典）
from . import pod_tools
from . import deployment_tools
from . import service_tools
from . import configmap_tools
from . import rag_tools


# =============================================================================
# 第一步：从 skills/*.yaml 加载工具 Schema（ALL_TOOLS）
# =============================================================================

def _load_tools_from_skills() -> List[Dict[str, Any]]:
    """
    扫描 skills/ 目录，读取所有 *_skill.yaml 文件，
    提取每个文件中的 tools 列表，合并为一个大列表。

    YAML 文件结构：
      skill_name: pod_management
      description: "..."
      tools:
        - name: get_pods
          description: "..."
          input_schema:
            type: object
            properties: {...}
            required: [...]

    返回：
      [
        {"name": "get_pods", "description": "...", "input_schema": {...}},
        {"name": "describe_pod", "description": "...", "input_schema": {...}},
        ...
      ]
    """
    all_tools: List[Dict[str, Any]] = []

    # 计算 skills/ 目录的绝对路径
    # __file__ 是本文件（tools/__init__.py）的路径
    # 向上一级是项目根目录，再进入 skills/
    project_root = Path(__file__).parent.parent
    skills_dir = project_root / "skills"

    # 查找所有 *_skill.yaml 文件（按文件名排序，保证加载顺序一致）
    yaml_files = sorted(skills_dir.glob("*_skill.yaml"))

    if not yaml_files:
        print(f"[Tools] Warning: No skill YAML files found in {skills_dir}")
        return all_tools

    for yaml_path in yaml_files:
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                skill_data = yaml.safe_load(f)

            # 提取 skill 名称和工具列表
            skill_name = skill_data.get("skill_name", yaml_path.stem)
            tools_in_skill = skill_data.get("tools", [])

            if not tools_in_skill:
                print(f"[Tools] Warning: No tools defined in {yaml_path.name}")
                continue

            # 将每个工具的 description 中的多行文本合并为单行
            # （YAML 的 > 折叠块会保留换行，需要清理）
            for tool in tools_in_skill:
                if "description" in tool:
                    # 将多个空白字符（含换行）替换为单个空格
                    tool["description"] = " ".join(tool["description"].split())

            all_tools.extend(tools_in_skill)
            print(f"[Tools] Loaded skill '{skill_name}' from {yaml_path.name} "
                  f"({len(tools_in_skill)} tools)")

        except yaml.YAMLError as e:
            print(f"[Tools] Error parsing {yaml_path.name}: {e}")
        except Exception as e:
            print(f"[Tools] Error loading {yaml_path.name}: {e}")

    return all_tools


# =============================================================================
# 第二步：从各 tools/*.py 模块合并函数注册表（ALL_REGISTRY）
# =============================================================================

def _build_registry() -> Dict[str, Any]:
    """
    从各工具模块的 REGISTRY 字典合并为统一的函数注册表。

    每个工具模块（如 pod_tools.py）都定义了一个 REGISTRY：
      REGISTRY = {
          "get_pods": get_pods,
          "describe_pod": describe_pod,
          ...
      }

    本函数将所有模块的 REGISTRY 合并为一个大字典，
    供主程序的 execute_tool() 函数查找和调用。

    注意：如果不同模块定义了同名工具，后加载的会覆盖先加载的。
    建议确保所有工具名称全局唯一。

    返回：
      {
        "get_pods": <function get_pods>,
        "describe_pod": <function describe_pod>,
        "get_deployments": <function get_deployments>,
        ...
      }
    """
    # 所有需要注册的工具模块列表
    # 新增工具模块时，在此列表追加即可
    tool_modules = [
        pod_tools,
        deployment_tools,
        service_tools,
        configmap_tools,
        rag_tools,      # RAG 知识库检索工具
    ]

    registry: Dict[str, Any] = {}

    for module in tool_modules:
        module_registry = getattr(module, "REGISTRY", {})

        if not module_registry:
            print(f"[Tools] Warning: Module '{module.__name__}' has no REGISTRY")
            continue

        # 检查是否有重名工具（提前发现配置错误）
        for tool_name in module_registry:
            if tool_name in registry:
                print(f"[Tools] Warning: Tool '{tool_name}' in '{module.__name__}' "
                      f"overrides existing registration!")

        registry.update(module_registry)

    return registry


# =============================================================================
# 第三步：执行加载，导出 ALL_TOOLS 和 ALL_REGISTRY
# =============================================================================

# 加载所有 Skill YAML → 工具 Schema 列表（传给 Claude）
ALL_TOOLS: List[Dict[str, Any]] = _load_tools_from_skills()

# 合并所有工具模块的函数注册表（用于工具分发）
ALL_REGISTRY: Dict[str, Any] = _build_registry()

# ── 启动时打印汇总信息 ────────────────────────────────────────────────────────
print(f"[Tools] Total: {len(ALL_TOOLS)} tools loaded from YAML, "
      f"{len(ALL_REGISTRY)} functions registered.")

# 检查 YAML 中定义的工具是否都有对应的函数实现
_missing = [t["name"] for t in ALL_TOOLS if t["name"] not in ALL_REGISTRY]
if _missing:
    print(f"[Tools] WARNING: The following tools are defined in YAML but have NO implementation: {_missing}")

# 检查是否有函数实现但没有对应 YAML 定义（不影响运行，但可能是遗漏）
_yaml_names = {t["name"] for t in ALL_TOOLS}
_extra = [name for name in ALL_REGISTRY if name not in _yaml_names]
if _extra:
    print(f"[Tools] Info: The following functions are registered but NOT in any YAML: {_extra}")


__all__ = ["ALL_TOOLS", "ALL_REGISTRY"]
