"""
ConfigMap 工具实现
==================
提供 Kubernetes ConfigMap 相关的操作函数。
对应 Skill 定义：skills/configmap_skill.yaml

函数列表：
  - get_configmaps(namespace)                          → 列出所有 ConfigMap
  - get_configmap_detail(namespace, configmap_name)    → 获取 ConfigMap 完整内容
  - update_configmap(namespace, configmap_name, data)  → 更新 ConfigMap 数据
"""

from typing import Any, Dict, List
from kubernetes import client


# =============================================================================
# get_configmaps：列出所有 ConfigMap
# =============================================================================

def get_configmaps(namespace: str) -> Dict[str, Any]:
    """
    列出指定 namespace 下所有 ConfigMap。

    实现逻辑：
    1. 调用 CoreV1Api.list_namespaced_config_map() 获取列表
    2. 只返回名称和 key 列表（不含 value，避免数据量过大）
    3. 过滤掉系统自动创建的 ConfigMap（kube-root-ca.crt 等）

    参数：
      namespace : Kubernetes 命名空间

    返回：
      {
        "namespace": "default",
        "total": 2,
        "configmaps": [
          {
            "name": "app-config",
            "keys": ["log_level", "timeout", "database_url"],
            "created_at": "2024-01-01T00:00:00"
          }
        ]
      }
    """
    v1 = client.CoreV1Api()

    try:
        cm_list = v1.list_namespaced_config_map(namespace=namespace)
    except client.exceptions.ApiException as e:
        return {"error": f"Failed to list configmaps in namespace '{namespace}': {e.reason}"}

    configmaps_info: List[Dict[str, Any]] = []

    for cm in cm_list.items:
        # data 字段可能为 None（空 ConfigMap）
        keys = list(cm.data.keys()) if cm.data else []

        configmaps_info.append({
            "name": cm.metadata.name,
            "keys": keys,
            "key_count": len(keys),
            "created_at": str(cm.metadata.creation_timestamp),
        })

    return {
        "namespace": namespace,
        "total": len(configmaps_info),
        "configmaps": configmaps_info,
    }


# =============================================================================
# get_configmap_detail：获取 ConfigMap 完整内容
# =============================================================================

def get_configmap_detail(namespace: str, configmap_name: str) -> Dict[str, Any]:
    """
    获取单个 ConfigMap 的完整内容（所有 key-value 对）。

    实现逻辑：
    1. 调用 CoreV1Api.read_namespaced_config_map() 获取对象
    2. 返回完整的 data 字典（所有 key-value）
    3. 同时返回 binaryData 的 key 列表（不返回二进制内容）

    参数：
      namespace      : Kubernetes 命名空间
      configmap_name : ConfigMap 名称

    返回：
      {
        "name": "app-config",
        "namespace": "default",
        "data": {
          "log_level": "info",
          "timeout": "30s"
        },
        "binary_keys": []
      }
    """
    v1 = client.CoreV1Api()

    try:
        cm = v1.read_namespaced_config_map(name=configmap_name, namespace=namespace)
    except client.exceptions.ApiException as e:
        return {"error": f"ConfigMap '{configmap_name}' not found in namespace '{namespace}': {e.reason}"}

    # binaryData 只返回 key 名，不返回二进制内容（避免编码问题）
    binary_keys = list(cm.binary_data.keys()) if cm.binary_data else []

    return {
        "name": configmap_name,
        "namespace": namespace,
        "data": cm.data or {},
        "binary_keys": binary_keys,
        "labels": cm.metadata.labels or {},
        "created_at": str(cm.metadata.creation_timestamp),
    }


# =============================================================================
# update_configmap：更新 ConfigMap 数据
# =============================================================================

def update_configmap(
    namespace: str,
    configmap_name: str,
    data: Dict[str, str],
) -> Dict[str, Any]:
    """
    更新 ConfigMap 中的 key-value 数据（合并更新）。

    实现逻辑：
    1. 先读取现有 ConfigMap 获取当前 data
    2. 将新的 data 合并到现有 data 中（已有 key 覆盖，新 key 追加）
    3. 调用 patch_namespaced_config_map() 应用变更
    4. 返回更新后的完整 data

    注意：
    - 这是合并更新，不是全量替换
    - 未在 data 参数中指定的 key 保持不变
    - 如需删除某个 key，需要直接调用 K8s API

    参数：
      namespace      : Kubernetes 命名空间
      configmap_name : ConfigMap 名称
      data           : 要更新的 key-value 字典（所有值必须为字符串）

    返回：
      {
        "success": True,
        "configmap": "app-config",
        "updated_keys": ["log_level"],
        "full_data": {"log_level": "debug", "timeout": "30s"}
      }
    """
    v1 = client.CoreV1Api()

    # 先读取现有 ConfigMap
    try:
        cm = v1.read_namespaced_config_map(name=configmap_name, namespace=namespace)
    except client.exceptions.ApiException as e:
        return {"error": f"ConfigMap '{configmap_name}' not found in namespace '{namespace}': {e.reason}"}

    # 合并数据：现有 data + 新 data（新 data 覆盖同名 key）
    current_data = cm.data or {}
    merged_data = {**current_data, **data}  # data 中的 key 会覆盖 current_data 中的同名 key

    # 构建 patch body（只更新 data 字段）
    patch_body = {"data": merged_data}

    try:
        result = v1.patch_namespaced_config_map(
            name=configmap_name,
            namespace=namespace,
            body=patch_body,
        )
    except client.exceptions.ApiException as e:
        return {"error": f"Failed to update configmap '{configmap_name}': {e.reason}"}

    return {
        "success": True,
        "configmap": configmap_name,
        "namespace": namespace,
        "updated_keys": list(data.keys()),   # 本次更新的 key 列表
        "full_data": result.data or {},       # 更新后的完整 data
        "message": f"ConfigMap '{configmap_name}' updated successfully. Keys modified: {list(data.keys())}",
    }


# =============================================================================
# REGISTRY：工具名 → 函数映射
# =============================================================================

REGISTRY = {
    "get_configmaps": get_configmaps,
    "get_configmap_detail": get_configmap_detail,
    "update_configmap": update_configmap,
}
