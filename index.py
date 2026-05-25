"""
Kubernetes AI Agent - Amazon Bedrock + Claude Sonnet
=====================================================

整体架构说明：
  本程序是一个基于 Amazon Bedrock（Claude Sonnet）的 Kubernetes 运维 AI Agent。
  用户用自然语言提问，Claude 自动决定调用哪些工具，Python 执行后将结果返回给 Claude，
  Claude 再生成最终的自然语言回答。

目录结构：
  kubernetes_bedrock_claude.py   ← 本文件：Agent 主循环，不含任何业务逻辑
  skills/                        ← Skill 描述文件（YAML，纯文档，无代码）
    pod_skill.yaml               ← Pod 工具的名称、描述、参数 Schema
    deployment_skill.yaml        ← Deployment 工具描述
    service_skill.yaml           ← Service 工具描述
    configmap_skill.yaml         ← ConfigMap 工具描述
    rag_skill.yaml               ← 知识库检索工具描述
  tools/                         ← 工具实现（Python，无 Schema）
    __init__.py                  ← 读取 skills/*.yaml + 注册所有工具函数
    pod_tools.py                 ← Pod 操作实现
    deployment_tools.py          ← Deployment 操作实现
    service_tools.py             ← Service 操作实现
    configmap_tools.py           ← ConfigMap 操作实现
    rag_tools.py                 ← RAG 知识库检索实现
  docs/                          ← 知识库文档（Markdown）

完整调用流程：
  1. 启动时：tools/__init__.py 扫描 skills/*.yaml → 构建 ALL_TOOLS（Schema 列表）
             tools/__init__.py 导入各 *_tools.py → 构建 ALL_REGISTRY（函数映射）
  2. 用户输入自然语言问题
  3. 主程序将问题 + ALL_TOOLS 发送给 Claude（via Bedrock API）
  4. Claude 返回 tool_use 块，指定要调用的工具名和参数
  5. execute_tool() 从 ALL_REGISTRY 查找函数并执行
  6. 将执行结果（tool_result）回传给 Claude
  7. Claude 生成最终回答（文本）
  8. 重复 3-7 直到 Claude 不再调用工具（最多 max_iterations 轮）

新增 Skill 方法（本文件无需修改）：
  1. 在 skills/ 创建 xxx_skill.yaml（定义工具 Schema）
  2. 在 tools/ 创建 xxx_tools.py（实现函数 + REGISTRY）
  3. 在 tools/__init__.py 的 tool_modules 列表中追加新模块

依赖安装：
  pip install boto3 anthropic kubernetes pyyaml chromadb
"""

# =============================================================================
# 标准库
# =============================================================================

import json
from typing import Any, Dict

# =============================================================================
# AWS / Anthropic SDK
# =============================================================================

import boto3  # noqa: F401  (AnthropicBedrock 内部使用 AWS 凭证链)
from anthropic import AnthropicBedrock

# =============================================================================
# Kubernetes SDK
# =============================================================================

from kubernetes import client, config

# =============================================================================
# Tools 包
# 导入时会自动执行：
#   1. 扫描 skills/*.yaml → ALL_TOOLS（工具 Schema 列表，传给 Claude）
#   2. 合并各模块 REGISTRY → ALL_REGISTRY（工具名 → 函数映射）
# =============================================================================

from tools import ALL_TOOLS, ALL_REGISTRY


# =============================================================================
# Kubernetes 初始化
# =============================================================================


def init_k8s() -> None:
    """
    初始化 Kubernetes 客户端配置。

    加载顺序：
    1. 优先尝试加载本地 ~/.kube/config（本地开发环境）
    2. 失败则加载 Pod 内 ServiceAccount（集群内运行时）

    初始化后，kubernetes.client 模块的所有 API 类（CoreV1Api、AppsV1Api 等）
    都会自动使用此配置连接集群，无需显式传入。
    """
    try:
        config.load_kube_config()
        print("[K8s] Loaded kubeconfig from ~/.kube/config")
    except Exception:
        config.load_incluster_config()
        print("[K8s] Loaded in-cluster config (ServiceAccount)")


# =============================================================================
# 工具分发器
# =============================================================================


def execute_tool(tool_name: str, tool_input: Dict[str, Any]) -> Any:
    """
    根据工具名称从 ALL_REGISTRY 查找并执行对应函数。

    调用时机：
    - Claude 返回 tool_use 类型的 content block 时
    - block.name 是工具名，block.input 是参数字典

    执行方式：
    - 直接将 tool_input 字典解包（**kwargs）传入函数
    - 函数签名必须与 skills/*.yaml 中的 input_schema 一致

    参数：
      tool_name  : 工具名称（如 "get_pods"、"scale_deployment"）
      tool_input : Claude 生成的参数字典（如 {"namespace": "default"}）

    返回：
      工具函数的返回值（通常是字典），会被序列化为 JSON 回传给 Claude
    """
    if tool_name not in ALL_REGISTRY:
        return {
            "error": f"Unknown tool: '{tool_name}'",
            "available_tools": list(ALL_REGISTRY.keys()),
        }

    func = ALL_REGISTRY[tool_name]

    try:
        # 将 Claude 生成的参数字典解包传入函数
        return func(**tool_input)
    except TypeError as e:
        # 参数不匹配（如缺少必填参数、参数名错误）
        return {
            "error": f"Tool '{tool_name}' called with invalid arguments: {str(e)}",
            "input_received": tool_input,
        }
    except Exception as e:
        # 工具执行过程中的其他异常
        return {
            "error": f"Tool '{tool_name}' execution failed: {str(e)}",
        }


# =============================================================================
# System Prompt（定义 Claude 的角色、能力和行为规范）
# =============================================================================

SYSTEM_PROMPT = """
You are an expert Kubernetes operations assistant powered by Amazon Bedrock.

## Your Skills

### Pod Management
- List pods and check their health status across namespaces
- Detect unhealthy pods: CrashLoopBackOff, OOMKilled, Pending, Failed
- Describe pods to get events and container details
- Fetch pod logs to diagnose application errors

### Deployment Management
- List and inspect deployments with replica status
- Scale deployments up or down
- Trigger rolling restarts without service interruption
- Update CPU and memory resource requests/limits

### Service Management
- List services and their types (ClusterIP, NodePort, LoadBalancer)
- Inspect service port mappings and pod selectors

### ConfigMap Management
- List and view ConfigMap contents
- Update ConfigMap key-value pairs (merge update, non-destructive)

### Knowledge Base (RAG)
- Search internal Kubernetes troubleshooting guides and best practices
- Look up how to diagnose specific Pod statuses and error codes

## Behavior Rules

1. **Always use tools** when Kubernetes information is needed. Never guess.
2. **Diagnose proactively**: When listing pods, identify unhealthy ones and explain why.
3. **Pod health criteria**:
   - Phase not "Running" → unhealthy
   - restart_count > 3 → suspicious (⚠️)
   - restart_count > 10 → critical (❌)
   - Container waiting with reason "CrashLoopBackOff" → critical (❌)
   - Container terminated with non-zero exit_code → investigate
4. **Search knowledge base** when asked about troubleshooting procedures or error meanings.
5. **Suggest kubectl commands** for further manual investigation when relevant.
6. **Be concise but complete**: Summarize findings clearly, highlight issues first.
7. **Confirm after write operations**: For scale/restart/update, report the new state.

## Uncertainty Rules (Anti-Hallucination)

- **Never guess** Pod names, namespace names, replica counts, or any resource values — always call a tool to get real data.
- **If a tool returns insufficient data**, say "I need more information" rather than speculating.
- **If you are unsure** about a Kubernetes fact or behavior, say "I'm not certain — let me check" and call the appropriate tool.
- **If asked about something outside Kubernetes operations**, say "This is outside my scope" rather than making up an answer.
- **Do not invent** kubectl commands with flags or options you are not certain exist.

## Write Operation Safety Rules

Before executing any of the following tools, **always summarize the action and ask for user confirmation**:
- `scale_deployment` — state the deployment name, namespace, and new replica count
- `restart_deployment` — state the deployment name and namespace
- `update_deployment_resources` — state the resource changes
- `update_configmap` — state the key and new value

Example confirmation message:
> "I am about to scale **nginx** in namespace **production** from 2 → 0 replicas. Please confirm to proceed."

Only proceed after the user explicitly confirms (e.g., "yes", "confirm", "go ahead").

## Response Format
- Use markdown for readability
- Use tables for listing multiple resources
- Use code blocks for kubectl commands
- Highlight ⚠️ warnings and ❌ critical issues prominently
"""


# =============================================================================
# KubernetesAgent 类
# =============================================================================


class KubernetesAgent:
    """
    Kubernetes AI Agent 主类。

    工作流程（多轮工具调用循环）：
    ┌─────────────────────────────────────────────────────────┐
    │  用户输入                                                │
    │     ↓                                                   │
    │  发送给 Claude（携带 ALL_TOOLS Schema）                  │
    │     ↓                                                   │
    │  Claude 返回 tool_use（工具名 + 参数）                   │
    │     ↓                                                   │
    │  execute_tool() 执行工具函数                             │
    │     ↓                                                   │
    │  将 tool_result 回传给 Claude                            │
    │     ↓                                                   │
    │  Claude 返回文本（最终回答）或继续调用工具               │
    │     ↓（循环，最多 max_iterations 轮）                    │
    │  输出最终回答                                            │
    └─────────────────────────────────────────────────────────┘
    """

    def __init__(
        self,
        aws_region: str = "ap-northeast-1",
        model_id: str = "us.anthropic.claude-sonnet-4-6-20260115-v1:0",
        max_tokens: int = 4096,
        max_iterations: int = 10,
    ):
        """
        初始化 Agent。

        参数：
          aws_region     : AWS 区域（默认东京 ap-northeast-1）
          model_id       : Claude 模型 ID（使用 Bedrock 跨区域推理前缀 us.）
          max_tokens     : 单次响应最大 token 数
          max_iterations : 最大工具调用轮数（防止无限循环）
        """
        # AnthropicBedrock 会自动使用 AWS 凭证链（~/.aws/credentials 或环境变量）
        self.bedrock_client = AnthropicBedrock(aws_region=aws_region)
        self.model = model_id
        self.max_tokens = max_tokens
        self.max_iterations = max_iterations

    def run(self, user_input: str) -> None:
        """
        处理一次用户请求，支持多轮工具调用。

        对话历史格式（OpenAI/Anthropic 标准）：
          messages = [
            {"role": "user",      "content": "用户问题"},
            {"role": "assistant", "content": [tool_use_block, ...]},
            {"role": "user",      "content": [tool_result_block, ...]},
            {"role": "assistant", "content": [text_block]},  ← 最终回答
          ]

        参数：
          user_input : 用户的自然语言问题或指令
        """
        # 初始化对话历史（每次 run() 调用都是独立的对话）
        messages = [{"role": "user", "content": user_input}]

        print(f"\n{'='*60}")
        print(f"User: {user_input}")
        print(f"{'='*60}")

        for iteration in range(1, self.max_iterations + 1):

            # ── 调用 Claude API ────────────────────────────────────────────
            # tools 参数告诉 Claude 有哪些工具可用（从 skills/*.yaml 加载）
            # system 改为列表格式以支持 Prompt Caching：
            #   - cache_control: {"type": "ephemeral"} 标记缓存断点
            #   - 缓存范围：System Prompt + Tools Schema（约 2900 token）
            #   - 缓存命中时该部分按 10% 价格计费，首 token 延迟降低 85%
            #   - 缓存有效期：至少 5 分钟（ephemeral 模式）
            response = self.bedrock_client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=0,          # 运维场景：关闭随机性，确保输出确定、准确，减少幻觉
                system=[{
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},  # 缓存 System Prompt + Tools
                }],
                tools=ALL_TOOLS,        # 工具 Schema 列表（Claude 据此决定调用哪个工具）
                messages=messages,
                extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
            )

            # ── 收集本轮 Claude 返回的所有 content blocks ─────────────────
            assistant_content = list(response.content)

            # ── 检查是否有 tool_use 块 ─────────────────────────────────────
            # stop_reason == "tool_use" 表示 Claude 想调用工具
            # stop_reason == "end_turn" 表示 Claude 已生成最终回答
            tool_use_blocks = [b for b in assistant_content if b.type == "tool_use"]

            if not tool_use_blocks:
                # Claude 没有调用工具，输出最终文本回答并结束
                print("\n[Claude Response]")
                for block in assistant_content:
                    if block.type == "text":
                        print(block.text)
                return

            # ── 将 assistant 消息加入对话历史 ─────────────────────────────
            # 必须先保存 assistant 消息，再追加 tool_result
            # （Anthropic API 要求 tool_result 必须紧跟在对应的 tool_use 之后）
            messages.append({"role": "assistant", "content": assistant_content})

            # ── 执行本轮所有工具调用 ───────────────────────────────────────
            tool_results = []
            for block in tool_use_blocks:
                print(f"\n[Tool Call #{iteration}] {block.name}")
                print(f"  Input : {json.dumps(block.input, ensure_ascii=False)}")

                # 执行工具函数
                result = execute_tool(block.name, block.input)

                # 打印结果预览（只显示前 300 字符，避免日志过长）
                result_str = json.dumps(result, ensure_ascii=False)
                preview = result_str[:300] + ("..." if len(result_str) > 300 else "")
                print(f"  Result: {preview}")

                # 构建 tool_result 块（Anthropic API 格式）
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,   # 必须与对应的 tool_use block id 匹配
                    "content": json.dumps(result, ensure_ascii=False, indent=2),
                })

            # ── 将工具结果回传给 Claude，进入下一轮 ───────────────────────
            messages.append({"role": "user", "content": tool_results})

        # 超过最大迭代次数
        print(f"\n[Warning] Reached max iterations ({self.max_iterations}). "
              "The conversation may be incomplete.")


# =============================================================================
# 程序入口
# =============================================================================


def print_banner() -> None:
    """打印欢迎信息，显示已加载的工具列表。"""
    tool_names = [t["name"] for t in ALL_TOOLS]

    print("""
╔══════════════════════════════════════════════════════════════╗
║         Kubernetes AI Agent (Amazon Bedrock + Claude)        ║
╠══════════════════════════════════════════════════════════════╣
║  Architecture:                                               ║
║    skills/*.yaml  → Tool Schema (what Claude can call)       ║
║    tools/*.py     → Tool Implementation (Python functions)   ║
╠══════════════════════════════════════════════════════════════╣
║  Example queries:                                            ║
║    > List all pods in default namespace                      ║
║    > Are there any unhealthy pods in production?             ║
║    > Show logs for pod nginx-xxx in default                  ║
║    > Scale nginx deployment to 3 replicas in default         ║
║    > Restart the api deployment in staging                   ║
║    > How to fix CrashLoopBackOff?                            ║
║    > Show configmap app-config in default                    ║
║    > Update configmap my-config: set log_level=debug         ║
║                                                              ║
║  Type 'exit' or 'quit' to stop.                              ║
╚══════════════════════════════════════════════════════════════╝
""")
    print(f"[Skills] {len(ALL_TOOLS)} tools loaded: {tool_names}\n")


if __name__ == "__main__":
    # ── 第一步：初始化 Kubernetes 客户端 ──────────────────────────────────
    # 必须在使用任何 K8s API 之前调用
    init_k8s()

    # ── 第二步：打印欢迎信息（tools/__init__.py 已在 import 时打印加载日志）──
    print_banner()

    # ── 第三步：创建 Agent 实例 ────────────────────────────────────────────
    agent = KubernetesAgent(
        aws_region="ap-northeast-1",
        model_id="us.anthropic.claude-sonnet-4-6-20260115-v1:0",
        max_tokens=4096,
        max_iterations=10,
    )

    # ── 第四步：交互式命令行循环 ───────────────────────────────────────────
    while True:
        try:
            user_query = input("\nYou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_query:
            continue

        if user_query.lower() in ("exit", "quit", "q"):
            print("Goodbye!")
            break

        agent.run(user_query)
