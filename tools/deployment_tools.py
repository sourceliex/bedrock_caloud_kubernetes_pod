"""
Deployment 工具实现
===================
提供 Kubernetes Deployment 相关的操作函数。
对应 Skill 定义：skills/deployment_skill.yaml

函数列表：
  - get_deployments(namespace)                          → 列出所有 Deployment
  - get_deployment_detail(namespace, deployment_name)   → 获取 Deployment 详情
  - scale_deployment(namespace, deployment_name, replicas) → 扩缩容
  - restart_deployment(namespace, deployment_name)      → 滚动重启
  - update_deployment_resources(...)                    → 修改资源配置
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from kubernetes import client


# =============================================================================
# get_deployments：列出所有 Deployment
# =============================================================================

def get_deployments(namespace: str) -> Dict[str, Any]:
    """
    列出指定 namespace 下所有 Deployment 的状态。

    实现逻辑：
    1. 调用 AppsV1Api.list_namespaced_deployment() 获取列表
    2. 提取副本状态、镜像信息、创建时间
    3. 判断 Deployment 是否就绪（ready_replicas == replicas）

    参数：
      namespace : Kubernetes 命名空间

    返回：
      {
        "namespace": "default",
        "total": 2,
        "deployments": [
          {
            "name": "nginx",
            "replicas": 3,
            "ready_replicas": 3,
            "available_replicas": 3,
            "status": "ready",
            "images": ["nginx:1.21"],
            "created_at": "2024-01-01T00:00:00"
          }
        ]
      }
    """
    apps_v1 = client.AppsV1Api()

    try:
        dep_list = apps_v1.list_namespaced_deployment(namespace=namespace)
    except client.exceptions.ApiException as e:
        return {"error": f"Failed to list deployments in namespace '{namespace}': {e.reason}"}

    deployments_info: List[Dict[str, Any]] = []

    for dep in dep_list.items:
        # 期望副本数（spec.replicas 可能为 None，表示默认 1）
        desired = dep.spec.replicas or 1
        # 实际就绪副本数（status 字段可能为 None）
        ready = dep.status.ready_replicas or 0
        available = dep.status.available_replicas or 0

        # 提取所有容器的镜像列表
        images = [c.image for c in dep.spec.template.spec.containers]

        # 判断 Deployment 整体状态
        if ready == desired:
            status = "ready"
        elif ready == 0:
            status = "unavailable"
        else:
            status = "degraded"  # 部分就绪

        deployments_info.append({
            "name": dep.metadata.name,
            "replicas": desired,
            "ready_replicas": ready,
            "available_replicas": available,
            "status": status,
            "images": images,
            "created_at": str(dep.metadata.creation_timestamp),
        })

    return {
        "namespace": namespace,
        "total": len(deployments_info),
        "deployments": deployments_info,
    }


# =============================================================================
# get_deployment_detail：获取 Deployment 详情
# =============================================================================

def get_deployment_detail(namespace: str, deployment_name: str) -> Dict[str, Any]:
    """
    获取单个 Deployment 的详细信息。

    实现逻辑：
    1. 调用 AppsV1Api.read_namespaced_deployment() 获取对象
    2. 提取容器资源配置（requests/limits）
    3. 提取更新策略（RollingUpdate 参数）
    4. 提取 Conditions（Available、Progressing 等）

    参数：
      namespace       : Kubernetes 命名空间
      deployment_name : Deployment 名称

    返回：
      包含副本状态、容器资源、更新策略、Conditions 的字典
    """
    apps_v1 = client.AppsV1Api()

    try:
        dep = apps_v1.read_namespaced_deployment(name=deployment_name, namespace=namespace)
    except client.exceptions.ApiException as e:
        return {"error": f"Deployment '{deployment_name}' not found in namespace '{namespace}': {e.reason}"}

    # ── 提取容器详情 ──────────────────────────────────────────────────────
    containers_detail: List[Dict[str, Any]] = []
    for c in dep.spec.template.spec.containers:
        resources: Dict[str, Any] = {}
        if c.resources:
            if c.resources.requests:
                resources["requests"] = dict(c.resources.requests)
            if c.resources.limits:
                resources["limits"] = dict(c.resources.limits)

        containers_detail.append({
            "name": c.name,
            "image": c.image,
            "resources": resources,
        })

    # ── 提取更新策略 ──────────────────────────────────────────────────────
    strategy: Dict[str, Any] = {"type": dep.spec.strategy.type}
    if dep.spec.strategy.rolling_update:
        ru = dep.spec.strategy.rolling_update
        strategy["max_surge"] = str(ru.max_surge) if ru.max_surge else None
        strategy["max_unavailable"] = str(ru.max_unavailable) if ru.max_unavailable else None

    # ── 提取 Conditions ───────────────────────────────────────────────────
    conditions: List[Dict[str, Any]] = []
    if dep.status.conditions:
        for cond in dep.status.conditions:
            conditions.append({
                "type": cond.type,
                "status": cond.status,
                "reason": cond.reason,
                "message": cond.message,
            })

    return {
        "name": deployment_name,
        "namespace": namespace,
        "replicas": {
            "desired": dep.spec.replicas or 1,
            "ready": dep.status.ready_replicas or 0,
            "available": dep.status.available_replicas or 0,
            "updated": dep.status.updated_replicas or 0,
        },
        "containers": containers_detail,
        "strategy": strategy,
        "conditions": conditions,
        "labels": dep.metadata.labels or {},
        "created_at": str(dep.metadata.creation_timestamp),
    }


# =============================================================================
# scale_deployment：扩缩容
# =============================================================================

def scale_deployment(
    namespace: str,
    deployment_name: str,
    replicas: int,
) -> Dict[str, Any]:
    """
    修改 Deployment 的副本数。

    实现逻辑：
    1. 调用 AppsV1Api.patch_namespaced_deployment_scale()
    2. 使用 JSON Patch 格式更新 spec.replicas
    3. 返回操作结果（新的副本数）

    等价命令：
      kubectl scale deployment/<name> --replicas=N -n <namespace>

    参数：
      namespace       : Kubernetes 命名空间
      deployment_name : Deployment 名称
      replicas        : 目标副本数（>= 0）

    返回：
      {"success": True, "deployment": "nginx", "new_replicas": 3}
    """
    if replicas < 0:
        return {"error": "replicas must be >= 0"}

    apps_v1 = client.AppsV1Api()

    # 使用 patch 更新 spec.replicas（只修改这一个字段）
    patch_body = {"spec": {"replicas": replicas}}

    try:
        result = apps_v1.patch_namespaced_deployment_scale(
            name=deployment_name,
            namespace=namespace,
            body=patch_body,
        )
    except client.exceptions.ApiException as e:
        return {"error": f"Failed to scale deployment '{deployment_name}': {e.reason}"}

    return {
        "success": True,
        "deployment": deployment_name,
        "namespace": namespace,
        "new_replicas": result.spec.replicas,
        "message": f"Deployment '{deployment_name}' scaled to {replicas} replicas.",
    }


# =============================================================================
# restart_deployment：滚动重启
# =============================================================================

def restart_deployment(namespace: str, deployment_name: str) -> Dict[str, Any]:
    """
    触发 Deployment 的滚动重启。

    实现逻辑：
    通过在 Pod Template 的 annotations 中添加 kubectl.kubernetes.io/restartedAt
    时间戳来触发滚动更新。这是 kubectl rollout restart 的底层实现方式。

    等价命令：
      kubectl rollout restart deployment/<name> -n <namespace>

    参数：
      namespace       : Kubernetes 命名空间
      deployment_name : Deployment 名称

    返回：
      {"success": True, "deployment": "nginx", "restarted_at": "2024-01-01T00:00:00Z"}
    """
    apps_v1 = client.AppsV1Api()

    # 使用 UTC 时间戳作为重启标记
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 通过更新 Pod Template 的 annotation 触发滚动更新
    # K8s 检测到 template 变化后会自动触发 RollingUpdate
    patch_body = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": now
                    }
                }
            }
        }
    }

    try:
        apps_v1.patch_namespaced_deployment(
            name=deployment_name,
            namespace=namespace,
            body=patch_body,
        )
    except client.exceptions.ApiException as e:
        return {"error": f"Failed to restart deployment '{deployment_name}': {e.reason}"}

    return {
        "success": True,
        "deployment": deployment_name,
        "namespace": namespace,
        "restarted_at": now,
        "message": f"Deployment '{deployment_name}' rolling restart triggered at {now}.",
    }


# =============================================================================
# update_deployment_resources：修改资源配置
# =============================================================================

def update_deployment_resources(
    namespace: str,
    deployment_name: str,
    cpu_request: str,
    memory_request: str,
    cpu_limit: str,
    memory_limit: str,
) -> Dict[str, Any]:
    """
    修改 Deployment 第一个容器的 CPU 和内存资源配置。

    实现逻辑：
    1. 先读取 Deployment 获取第一个容器名称
    2. 构建 patch body，只修改 resources 字段
    3. 调用 patch_namespaced_deployment() 应用变更
    4. 变更会触发 RollingUpdate（逐步替换 Pod）

    资源格式说明：
    - CPU: "100m"（0.1核）、"500m"（0.5核）、"1"（1核）
    - 内存: "128Mi"（128MB）、"512Mi"（512MB）、"1Gi"（1GB）

    参数：
      namespace       : Kubernetes 命名空间
      deployment_name : Deployment 名称
      cpu_request     : CPU 请求量
      memory_request  : 内存请求量
      cpu_limit       : CPU 上限
      memory_limit    : 内存上限

    返回：
      {"success": True, "container": "nginx", "resources": {...}}
    """
    apps_v1 = client.AppsV1Api()

    # 先读取 Deployment，获取第一个容器的名称
    try:
        dep = apps_v1.read_namespaced_deployment(name=deployment_name, namespace=namespace)
    except client.exceptions.ApiException as e:
        return {"error": f"Deployment '{deployment_name}' not found: {e.reason}"}

    # 获取第一个容器名称（patch 时需要通过名称定位容器）
    first_container_name = dep.spec.template.spec.containers[0].name

    # 构建资源配置 patch
    # 注意：containers 是数组，需要通过 name 匹配
    patch_body = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": first_container_name,
                            "resources": {
                                "requests": {
                                    "cpu": cpu_request,
                                    "memory": memory_request,
                                },
                                "limits": {
                                    "cpu": cpu_limit,
                                    "memory": memory_limit,
                                },
                            },
                        }
                    ]
                }
            }
        }
    }

    try:
        apps_v1.patch_namespaced_deployment(
            name=deployment_name,
            namespace=namespace,
            body=patch_body,
        )
    except client.exceptions.ApiException as e:
        return {"error": f"Failed to update resources for deployment '{deployment_name}': {e.reason}"}

    return {
        "success": True,
        "deployment": deployment_name,
        "namespace": namespace,
        "container": first_container_name,
        "resources": {
            "requests": {"cpu": cpu_request, "memory": memory_request},
            "limits": {"cpu": cpu_limit, "memory": memory_limit},
        },
        "message": f"Resources updated for container '{first_container_name}'. Rolling update triggered.",
    }


# =============================================================================
# REGISTRY：工具名 → 函数映射
# =============================================================================

REGISTRY = {
    "get_deployments": get_deployments,
    "get_deployment_detail": get_deployment_detail,
    "scale_deployment": scale_deployment,
    "restart_deployment": restart_deployment,
    "update_deployment_resources": update_deployment_resources,
}
