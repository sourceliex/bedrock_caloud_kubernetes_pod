# Kubernetes + Amazon Bedrock + Claude Sonnet 工具调用示例（逐行注释版）

本文将完整解释一个 Python Agent：

- 使用 Amazon Bedrock 调用 Anthropic Claude Sonnet。
- 通过 Tool Use（工具调用）访问 Kubernetes。
- 支持：
  - 查询 Pod。
  - 修改 Deployment 的 CPU / Memory Resources。
- 从架构角度理解 MCP（Model Context Protocol）的核心思想。

---

# 一、整体架构

```text
User
  ↓
KubernetesAgent.run()
  ↓
Claude Sonnet (Bedrock)
  ↓ tool_use
execute_tool()
  ↓
Kubernetes Python Client
  ↓
Kubernetes API Server
  ↓
tool_result
  ↓
Claude Sonnet
  ↓
Final Answer
```

---

# 二、完整代码（深度注释）

```python
"""
requirements:
  pip install boto3 anthropic kubernetes pyyaml

本程序实现：
1. 调用 Amazon Bedrock 上的 Claude Sonnet。
2. 注册 Kubernetes 工具。
3. 让 Claude 自动决定何时调用工具。
4. 返回工具结果，再由 Claude 生成最终自然语言回答。
"""

# =========================
# 1. 导入标准库
# =========================

import json
# 用于 Python 对象与 JSON 字符串之间转换。
# Claude 的 tool_result content 通常是字符串，因此需要 json.dumps()。

from typing import Dict, Any, List
# 类型提示：
# Dict[str, Any] 表示字典。
# List[Dict[str, Any]] 表示字典列表。


# =========================
# 2. 导入 Bedrock SDK
# =========================

import boto3
# AWS 官方 Python SDK。
# 虽然本例中没有直接使用 boto3 client，
# 但 AnthropicBedrock 内部依赖 AWS 凭证。

from anthropic import AnthropicBedrock
# Anthropic 官方 SDK 中用于访问 Amazon Bedrock 的客户端。


# =========================
# 3. 导入 Kubernetes SDK
# =========================

from kubernetes import client, config
# config: 加载 kubeconfig 或 in-cluster 配置。
# client: 各类 API 对象，例如 CoreV1Api、AppsV1Api。


# =========================================================
# Kubernetes 初始化
# =========================================================

def init_k8s():
    """
    初始化 Kubernetes 客户端配置。

    优先尝试本地 ~/.kube/config。
    如果失败，则尝试 Pod 内部的 ServiceAccount 配置。
    """

    try:
        config.load_kube_config()
        # 读取 ~/.kube/config
        # 适合本地开发。

        print("Loaded kubeconfig")

    except Exception:
        # 如果本地没有 kubeconfig，进入 except。

        config.load_incluster_config()
        # 读取 Pod 内的 ServiceAccount Token。

        print("Loaded in-cluster config")


# =========================================================
# Tool 1: 查询 Pod
# =========================================================

def get_pods(namespace: str = "default") -> List[Dict[str, Any]]:
    """
    获取指定 namespace 下所有 Pod 信息。

    返回示例：
    [
      {
        "name": "nginx-123",
        "phase": "Running",
        "node": "ip-10-0-0-1",
        "pod_ip": "10.244.1.5"
      }
    ]
    """

    v1 = client.CoreV1Api()
    # Kubernetes Core API 客户端。

    pods = v1.list_namespaced_pod(namespace=namespace)
    # 调用 Kubernetes API:
    # GET /api/v1/namespaces/{namespace}/pods

    result = []
    # 最终返回的数据列表。

    for pod in pods.items:
        # pods.items 是 Pod 对象列表。

        result.append({
            "name": pod.metadata.name,
            # Pod 名称。

            "phase": pod.status.phase,
            # Pod 状态，例如 Running/Pending。

            "node": pod.spec.node_name,
            # 所在节点。

            "pod_ip": pod.status.pod_ip,
            # Pod IP。
        })

    return result
    # 返回给 Claude 使用。


# =========================================================
# Tool 2: 修改 Deployment Resources
# =========================================================

def update_deployment_resources(
    namespace: str,
    deployment_name: str,
    cpu_request: str,
    memory_request: str,
    cpu_limit: str,
    memory_limit: str,
) -> Dict[str, Any]:
    """
    修改 Deployment 第一个容器的 resources。
    """

    apps = client.AppsV1Api()
    # Deployment 属于 apps/v1 API。

    deployment = apps.read_namespaced_deployment(
        name=deployment_name,
        namespace=namespace
    )
    # 读取现有 Deployment。

    container = deployment.spec.template.spec.containers[0]
    # 取第一个 container。

    # 下面这段只是修改本地对象，真正生效要 patch。
    container.resources = client.V1ResourceRequirements(
        requests={
            "cpu": cpu_request,
            "memory": memory_request,
        },
        limits={
            "cpu": cpu_limit,
            "memory": memory_limit,
        }
    )

    # 构造 patch body。
    body = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": container.name,
                            "resources": {
                                "requests": {
                                    "cpu": cpu_request,
                                    "memory": memory_request,
                                },
                                "limits": {
                                    "cpu": cpu_limit,
                                    "memory": memory_limit,
                                }
                            }
                        }
                    ]
                }
            }
        }
    }

    apps.patch_namespaced_deployment(
        name=deployment_name,
        namespace=namespace,
        body=body
    )
    # PATCH Deployment。
    # 更新 Pod Template，触发 Rolling Update。

    return {
        "status": "success",
        "deployment": deployment_name,
        "namespace": namespace,
        "resources": body["spec"]["template"]["spec"]["containers"][0]["resources"],
    }


# =========================================================
# Tool Schema
# =========================================================

TOOLS = [
    {
        "name": "get_pods",
        # 工具名称。

        "description": "Get all pods in a Kubernetes namespace",
        # 给 Claude 的说明。

        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "Kubernetes namespace"
                }
            },
            "required": ["namespace"]
        },
    },

    {
        "name": "update_deployment_resources",
        "description": "Update deployment CPU and memory requests/limits",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "deployment_name": {"type": "string"},
                "cpu_request": {"type": "string"},
                "memory_request": {"type": "string"},
                "cpu_limit": {"type": "string"},
                "memory_limit": {"type": "string"},
            },
            "required": [
                "namespace",
                "deployment_name",
                "cpu_request",
                "memory_request",
                "cpu_limit",
                "memory_limit",
            ],
        },
    },
]

# TOOLS 的作用：告诉 Claude 有哪些工具可用。
# Claude 根据 description 和 schema 决定是否调用。


# =========================================================
# Tool Dispatcher
# =========================================================

def execute_tool(tool_name: str, tool_input: Dict[str, Any]) -> Any:
    """
    根据 tool_name 调用对应 Python 函数。
    """

    if tool_name == "get_pods":
        return get_pods(**tool_input)
        # **tool_input 等价于:
        # get_pods(namespace="default")

    elif tool_name == "update_deployment_resources":
        return update_deployment_resources(**tool_input)

    else:
        raise ValueError(f"Unknown tool: {tool_name}")


# =========================================================
# Agent 类
# =========================================================

class KubernetesAgent:
    """
    核心 Agent。

    职责：
    1. 调用 Claude。
    2. 处理 tool_use。
    3. 执行工具。
    4. 将 tool_result 回传给 Claude。
    5. 输出最终结果。
    """

    def __init__(self):
        """初始化 Bedrock 客户端。"""

        self.client = AnthropicBedrock(
            aws_region="ap-northeast-1"
        )
        # AWS 东京区域。

        self.model = "us.anthropic.claude-sonnet-4-6-20260115-v1:0"
        # 模型 ID。

    def run(self, user_input: str):
        """
        处理一次用户请求。
        """

        messages = [
            {
                "role": "user",
                "content": user_input
            }
        ]
        # 初始消息。

        while True:
            # 因为 Claude 可能多次调用工具。

            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                tools=TOOLS,
                messages=messages,
                system="""
You are a Kubernetes assistant.

You can:
1. Query pods
2. Update deployment CPU and memory resources

Always use tools when Kubernetes information is needed.

When checking pod crashes:
- Look for CrashLoopBackOff in reasons.
- Consider restart_count > 3 as suspicious.
- Explain which pods are unhealthy.
- If restart_count > 10, mark it as critical.
- Pods not in Running phase should also be considered unhealthy.
- Suggest useful kubectl commands for further troubleshooting.
"""
            )
            # 向 Claude 发起请求。

            assistant_content = []
            # 保存 Claude 本次返回的全部 blocks。

            for block in response.content:
                assistant_content.append(block)

                if block.type == "tool_use":
                    # Claude 决定调用工具。

                    print(f"\nCalling tool: {block.name}")
                    print(f"Input: {block.input}\n")

                    result = execute_tool(
                        block.name,
                        block.input
                    )
                    # 执行 Python 函数。

                    messages.append({
                        "role": "assistant",
                        "content": assistant_content
                    })
                    # 保存 assistant tool_use。

                    messages.append({
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(
                                    result,
                                    ensure_ascii=False,
                                    indent=2
                                )
                            }
                        ]
                    })
                    # 把工具结果传回 Claude。

                    break
                    # 跳出 for，重新进入 while。

            else:
                # for 没有遇到 break。
                # 表示 Claude 未调用工具，而是直接回答。

                print("\nClaude Response:")

                for block in response.content:
                    if block.type == "text":
                        print(block.text)

                return
                # 完成本次请求。


# =========================================================
# 程序入口
# =========================================================

if __name__ == "__main__":
    # 只有直接运行脚本时执行。

    init_k8s()
    # 初始化 Kubernetes 配置。

    agent = KubernetesAgent()
    # 创建 Agent。

    print("Kubernetes AI Agent")
    print("Examples:")
    print("- List pods in kube-system")
    print("- Show pods in default namespace")
    print("- Set nginx deployment CPU to 500m and memory to 1Gi")
    print()

    while True:
        query = input("You> ").strip()
        # 读取用户输入。

        if query.lower() in ("exit", "quit"):
            break
        # 退出程序。

        agent.run(query)
        # 执行一次对话。
```

---

# 三、运行流程详解

## 用户输入

```text
List pods in kube-system
```

## Claude 返回

```json
{
  "type": "tool_use",
  "name": "get_pods",
  "input": {
    "namespace": "kube-system"
  }
}
```

## Python 执行

```python
get_pods(namespace="kube-system")
```

## 返回 tool_result

```json
[
  {
    "name": "coredns-xxx",
    "phase": "Running"
  }
]
```

## Claude 最终回答

```text
There are 12 running pods in kube-system.
```

---

# 四、为什么要用 while True

Claude 可能连续调用多个工具。

例如：

1. `get_pods`
2. `update_deployment_resources`
3. 最终总结

因此需要循环。

---

# 五、MCP 对应关系

| MCP 概念 | 本代码 |
|------|------|
| Model | Claude Sonnet |
| MCP Client | `messages.create()` |
| MCP Server | `execute_tool()` |
| Tools | `TOOLS` |
| Tool Result | `tool_result` |

---

# 六、关键知识点

## `**tool_input`

```python
{"namespace": "default"}
```

等价于：

```python
get_pods(namespace="default")
```

---

## `for ... else`

如果 `for` 没有执行 `break`，则进入 `else`。

---

## `json.dumps()`

把 Python 对象转成 JSON 字符串。

---

# 七、建议你继续深入研究

1. Claude Tool Use。
2. MCP 协议。
3. Kubernetes Python Client。
4. Amazon Bedrock。
5. Agent 架构设计。

---

# 八、你当前代码的本质

> 这是一个手写版 MCP Server。

Claude 不直接访问 Kubernetes。
Claude 只负责推理。
Python 程序负责执行实际操作。

---

# 九、下一步建议

你可以继续扩展更多工具： 

- `get_deployments()`
- `scale_deployment()`
- `restart_deployment()`
- `get_logs()`
- `exec_command()`
- `describe_pod()`

这样就能构建完整的 Kubernetes AI 运维助手。
