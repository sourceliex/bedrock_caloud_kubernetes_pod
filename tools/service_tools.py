"""
Service 工具实现
================
提供 Kubernetes Service 相关的操作函数。
对应 Skill 定义：skills/service_skill.yaml

函数列表：
  - get_services(namespace)                        → 列出所有 Service
  - get_service_detail(namespace, service_name)    → 获取 Service 详情
"""

from typing import Any, Dict, List
from kubernetes import client


# =============================================================================
# get_services：列出所有 Service
# =============================================================================

def get_services(namespace: str) -> Dict[str, Any]:
    """
    列出指定 namespace 下所有 Service。

    实现逻辑：
    1. 调用 CoreV1Api.list_namespaced_service() 获取列表
    2. 提取 Service 类型、ClusterIP、端口映射、外部 IP

    Service 类型说明：
    - ClusterIP    : 仅集群内部访问（默认类型）
    - NodePort     : 通过节点 IP + NodePort 访问
    - LoadBalancer : 通过云厂商负载均衡器访问（有外部 IP）
    - ExternalName : 映射到外部 DNS 名称

    参数：
      namespace : Kubernetes 命名空间

    返回：
      {
        "namespace": "default",
        "total": 2,
        "services": [
          {
            "name": "nginx-svc",
            "type": "ClusterIP",
            "cluster_ip": "10.96.0.1",
            "ports": [{"port": 80, "target_port": "8080", "protocol": "TCP"}],
            "external_ips": []
          }
        ]
      }
    """
    v1 = client.CoreV1Api()

    try:
        svc_list = v1.list_namespaced_service(namespace=namespace)
    except client.exceptions.ApiException as e:
        return {"error": f"Failed to list services in namespace '{namespace}': {e.reason}"}

    services_info: List[Dict[str, Any]] = []

    for svc in svc_list.items:
        # 提取端口映射列表
        ports: List[Dict[str, Any]] = []
        if svc.spec.ports:
            for p in svc.spec.ports:
                port_info: Dict[str, Any] = {
                    "port": p.port,
                    "target_port": str(p.target_port) if p.target_port else "",
                    "protocol": p.protocol or "TCP",
                    "name": p.name or "",
                }
                # NodePort 类型才有 node_port 字段
                if p.node_port:
                    port_info["node_port"] = p.node_port
                ports.append(port_info)

        # 提取外部 IP（LoadBalancer 类型）
        external_ips: List[str] = []
        if svc.status.load_balancer and svc.status.load_balancer.ingress:
            for ingress in svc.status.load_balancer.ingress:
                # 可能是 IP 或 hostname（取决于云厂商）
                external_ips.append(ingress.ip or ingress.hostname or "")

        services_info.append({
            "name": svc.metadata.name,
            "type": svc.spec.type or "ClusterIP",
            "cluster_ip": svc.spec.cluster_ip or "",
            "ports": ports,
            "external_ips": external_ips,
            "selector": svc.spec.selector or {},  # 用于关联 Pod 的标签选择器
        })

    return {
        "namespace": namespace,
        "total": len(services_info),
        "services": services_info,
    }


# =============================================================================
# get_service_detail：获取 Service 详情
# =============================================================================

def get_service_detail(namespace: str, service_name: str) -> Dict[str, Any]:
    """
    获取单个 Service 的详细信息。

    实现逻辑：
    1. 调用 CoreV1Api.read_namespaced_service() 获取对象
    2. 提取完整端口配置（含 nodePort）
    3. 提取 Pod 选择器（selector）
    4. 提取标签和注解

    参数：
      namespace    : Kubernetes 命名空间
      service_name : Service 名称

    返回：
      包含完整端口配置、Pod 选择器、标签、注解的字典
    """
    v1 = client.CoreV1Api()

    try:
        svc = v1.read_namespaced_service(name=service_name, namespace=namespace)
    except client.exceptions.ApiException as e:
        return {"error": f"Service '{service_name}' not found in namespace '{namespace}': {e.reason}"}

    # ── 提取完整端口配置 ──────────────────────────────────────────────────
    ports: List[Dict[str, Any]] = []
    if svc.spec.ports:
        for p in svc.spec.ports:
            port_info: Dict[str, Any] = {
                "name": p.name or "",
                "port": p.port,
                "target_port": str(p.target_port) if p.target_port else "",
                "protocol": p.protocol or "TCP",
            }
            if p.node_port:
                port_info["node_port"] = p.node_port
            ports.append(port_info)

    # ── 提取外部访问信息 ──────────────────────────────────────────────────
    external_ips: List[str] = []
    if svc.status.load_balancer and svc.status.load_balancer.ingress:
        for ingress in svc.status.load_balancer.ingress:
            external_ips.append(ingress.ip or ingress.hostname or "")

    return {
        "name": service_name,
        "namespace": namespace,
        "type": svc.spec.type or "ClusterIP",
        "cluster_ip": svc.spec.cluster_ip or "",
        "external_ips": external_ips,
        "ports": ports,
        "selector": svc.spec.selector or {},
        "labels": svc.metadata.labels or {},
        "annotations": svc.metadata.annotations or {},
        "created_at": str(svc.metadata.creation_timestamp),
    }


# =============================================================================
# REGISTRY：工具名 → 函数映射
# =============================================================================

REGISTRY = {
    "get_services": get_services,
    "get_service_detail": get_service_detail,
}
