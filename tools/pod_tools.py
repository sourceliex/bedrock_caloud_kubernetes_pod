"""
Pod 工具实现
============
提供 Kubernetes Pod 相关的操作函数。
对应 Skill 定义：skills/pod_skill.yaml

函数列表：
  - get_pods(namespace)              → 列出所有 Pod 及健康状态
  - describe_pod(namespace, pod_name) → 获取 Pod 详情和事件
  - get_pod_logs(namespace, pod_name, container, tail_lines) → 获取容器日志
"""

from typing import Any, Dict, List, Optional
from kubernetes import client


# =============================================================================
# get_pods：列出 namespace 下所有 Pod
# =============================================================================

def get_pods(namespace: str) -> Dict[str, Any]:
    """
    列出指定 namespace 下所有 Pod 的状态信息。

    实现逻辑：
    1. 调用 CoreV1Api.list_namespaced_pod() 获取 Pod 列表
    2. 遍历每个 Pod，提取关键状态字段
    3. 分析容器状态，判断是否健康
    4. 返回结构化的 Pod 列表

    健康判断规则：
    - phase != "Running" → 不健康
    - restart_count > 10 → 严重
    - restart_count > 3  → 可疑
    - 容器 waiting.reason == "CrashLoopBackOff" → 严重
    - 容器 terminated.exit_code != 0 → 需要调查

    参数：
      namespace : Kubernetes 命名空间

    返回：
      {
        "namespace": "default",
        "total": 3,
        "pods": [
          {
            "name": "nginx-xxx",
            "phase": "Running",
            "ready": "2/2",
            "restart_count": 0,
            "health": "healthy",
            "containers": [...]
          }
        ]
      }
    """
    # 初始化 Kubernetes CoreV1 API 客户端
    v1 = client.CoreV1Api()

    try:
        # 调用 K8s API 获取 Pod 列表
        pod_list = v1.list_namespaced_pod(namespace=namespace)
    except client.exceptions.ApiException as e:
        # API 调用失败（如 namespace 不存在、权限不足）
        return {"error": f"Failed to list pods in namespace '{namespace}': {e.reason}"}

    pods_info: List[Dict[str, Any]] = []

    for pod in pod_list.items:
        pod_name = pod.metadata.name
        phase = pod.status.phase or "Unknown"

        # ── 统计容器就绪状态 ──────────────────────────────────────────────
        # container_statuses 可能为 None（Pod 还未调度）
        container_statuses = pod.status.container_statuses or []
        ready_count = sum(1 for cs in container_statuses if cs.ready)
        total_count = len(pod.spec.containers)
        ready_str = f"{ready_count}/{total_count}"

        # ── 统计总重启次数（所有容器之和）────────────────────────────────
        total_restarts = sum(cs.restart_count for cs in container_statuses)

        # ── 分析每个容器的详细状态 ────────────────────────────────────────
        containers_detail: List[Dict[str, Any]] = []
        for cs in container_statuses:
            container_info: Dict[str, Any] = {
                "name": cs.name,
                "ready": cs.ready,
                "restart_count": cs.restart_count,
                "state": "unknown",
            }

            # 解析容器当前状态（running / waiting / terminated 三选一）
            if cs.state.running:
                container_info["state"] = "running"
                container_info["started_at"] = str(cs.state.running.started_at)

            elif cs.state.waiting:
                container_info["state"] = "waiting"
                container_info["reason"] = cs.state.waiting.reason or ""
                container_info["message"] = cs.state.waiting.message or ""

            elif cs.state.terminated:
                container_info["state"] = "terminated"
                container_info["reason"] = cs.state.terminated.reason or ""
                container_info["exit_code"] = cs.state.terminated.exit_code

            containers_detail.append(container_info)

        # ── 综合健康状态判断 ──────────────────────────────────────────────
        health = _evaluate_pod_health(phase, total_restarts, containers_detail)

        pods_info.append({
            "name": pod_name,
            "phase": phase,
            "ready": ready_str,
            "restart_count": total_restarts,
            "health": health,
            "node": pod.spec.node_name or "unscheduled",
            "containers": containers_detail,
        })

    return {
        "namespace": namespace,
        "total": len(pods_info),
        "pods": pods_info,
    }


def _evaluate_pod_health(
    phase: str,
    restart_count: int,
    containers: List[Dict[str, Any]],
) -> str:
    """
    根据 Pod 状态综合判断健康等级。

    返回值：
      "healthy"  → 正常运行
      "warning"  → 有轻微问题（重启次数偏多）
      "critical" → 严重问题（CrashLoopBackOff、非零退出码等）
      "unknown"  → 状态无法判断
    """
    # Phase 不是 Running 且不是 Succeeded（Job 完成态）
    if phase not in ("Running", "Succeeded"):
        return "critical"

    # 检查容器级别的异常
    for c in containers:
        reason = c.get("reason", "")
        exit_code = c.get("exit_code")

        # CrashLoopBackOff 是最常见的严重问题
        if reason == "CrashLoopBackOff":
            return "critical"

        # 容器已终止且退出码非 0
        if c.get("state") == "terminated" and exit_code not in (None, 0):
            return "critical"

    # 重启次数判断
    if restart_count > 10:
        return "critical"
    if restart_count > 3:
        return "warning"

    return "healthy"


# =============================================================================
# describe_pod：获取 Pod 详情
# =============================================================================

def describe_pod(namespace: str, pod_name: str) -> Dict[str, Any]:
    """
    获取单个 Pod 的详细信息，类似 kubectl describe pod。

    实现逻辑：
    1. 调用 CoreV1Api.read_namespaced_pod() 获取 Pod 对象
    2. 提取容器配置（镜像、资源、端口、环境变量）
    3. 调用 CoreV1Api.list_namespaced_event() 获取相关事件
    4. 返回结构化详情

    参数：
      namespace : Kubernetes 命名空间
      pod_name  : Pod 名称

    返回：
      包含容器配置、资源限制、挂载卷、最近事件的字典
    """
    v1 = client.CoreV1Api()

    try:
        pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)
    except client.exceptions.ApiException as e:
        return {"error": f"Pod '{pod_name}' not found in namespace '{namespace}': {e.reason}"}

    # ── 提取容器配置 ──────────────────────────────────────────────────────
    containers_config: List[Dict[str, Any]] = []
    for c in pod.spec.containers:
        # 解析资源配置（requests 和 limits）
        resources: Dict[str, Any] = {}
        if c.resources:
            if c.resources.requests:
                resources["requests"] = dict(c.resources.requests)
            if c.resources.limits:
                resources["limits"] = dict(c.resources.limits)

        # 解析端口配置
        ports = []
        if c.ports:
            for p in c.ports:
                ports.append({
                    "container_port": p.container_port,
                    "protocol": p.protocol or "TCP",
                    "name": p.name or "",
                })

        # 解析环境变量（只取 name，不暴露 value 中的敏感信息）
        env_names = []
        if c.env:
            env_names = [e.name for e in c.env]

        containers_config.append({
            "name": c.name,
            "image": c.image,
            "resources": resources,
            "ports": ports,
            "env_keys": env_names,  # 只返回 key 名，不返回 value
        })

    # ── 获取 Pod 相关事件（最近 10 条）────────────────────────────────────
    # 通过 field_selector 过滤出属于该 Pod 的事件
    events_raw = v1.list_namespaced_event(
        namespace=namespace,
        field_selector=f"involvedObject.name={pod_name}",
    )

    events: List[Dict[str, Any]] = []
    for e in events_raw.items[-10:]:  # 只取最近 10 条
        events.append({
            "type": e.type,           # Normal / Warning
            "reason": e.reason,
            "message": e.message,
            "count": e.count,
            "first_time": str(e.first_timestamp),
            "last_time": str(e.last_timestamp),
        })

    return {
        "name": pod_name,
        "namespace": namespace,
        "phase": pod.status.phase,
        "node": pod.spec.node_name,
        "start_time": str(pod.status.start_time),
        "containers": containers_config,
        "recent_events": events,
    }


# =============================================================================
# get_pod_logs：获取容器日志
# =============================================================================

def get_pod_logs(
    namespace: str,
    pod_name: str,
    container: Optional[str] = None,
    tail_lines: int = 50,
) -> Dict[str, Any]:
    """
    获取 Pod 容器的最近日志。

    实现逻辑：
    1. 调用 CoreV1Api.read_namespaced_pod_log()
    2. tail_lines 控制返回行数（默认 50，避免日志过大）
    3. 若 Pod 有多个容器，需指定 container 参数

    参数：
      namespace   : Kubernetes 命名空间
      pod_name    : Pod 名称
      container   : 容器名称（可选，多容器 Pod 时必填）
      tail_lines  : 返回最后 N 行，默认 50

    返回：
      {
        "pod": "nginx-xxx",
        "container": "nginx",
        "tail_lines": 50,
        "logs": "..."
      }
    """
    v1 = client.CoreV1Api()

    # 构建 API 调用参数
    kwargs: Dict[str, Any] = {
        "name": pod_name,
        "namespace": namespace,
        "tail_lines": tail_lines,
    }
    # container 参数只在指定时传入（不传则 K8s 自动选择第一个容器）
    if container:
        kwargs["container"] = container

    try:
        logs = v1.read_namespaced_pod_log(**kwargs)
    except client.exceptions.ApiException as e:
        return {
            "error": f"Failed to get logs for pod '{pod_name}': {e.reason}",
            "hint": "If the pod has multiple containers, specify the 'container' parameter.",
        }

    return {
        "pod": pod_name,
        "namespace": namespace,
        "container": container or "(default)",
        "tail_lines": tail_lines,
        "logs": logs,
    }


# =============================================================================
# REGISTRY：工具名 → 函数映射（供 tools/__init__.py 自动注册）
# =============================================================================

REGISTRY = {
    "get_pods": get_pods,
    "describe_pod": describe_pod,
    "get_pod_logs": get_pod_logs,
}
