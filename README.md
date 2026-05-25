# Kubernetes AI Agent

基于 **Amazon Bedrock + Claude** 的 Kubernetes 运维 AI Agent。用自然语言描述需求，Claude 自动调用工具完成操作并给出分析结论。

---

## 功能特性

- 🤖 **自然语言交互**：用中文或英文描述需求，AI 自动调用对应工具
- 🔧 **Pod 管理**：列出 Pod、查看详情、获取日志、健康状态分析
- 🚀 **Deployment 管理**：扩缩容、滚动重启、更新资源配置
- 🌐 **Service 管理**：查看服务类型、端口映射、Pod 选择器
- ⚙️ **ConfigMap 管理**：查看和更新配置项
- 📚 **RAG 知识库**：内置 Kubernetes 故障排查文档，支持语义检索
- 🔌 **可扩展**：新增工具只需添加 YAML + Python 文件，无需修改主程序

---

## 目录结构

```
.
├── kubernetes_bedrock_claude.py   # Agent 主程序入口
├── requirements.txt               # Python 依赖
├── .env.example                   # 环境变量模板
│
├── skills/                        # 工具描述（YAML，纯文档，无代码）
│   ├── pod_skill.yaml             # Pod 工具 Schema
│   ├── deployment_skill.yaml      # Deployment 工具 Schema
│   ├── service_skill.yaml         # Service 工具 Schema
│   ├── configmap_skill.yaml       # ConfigMap 工具 Schema
│   └── rag_skill.yaml             # 知识库检索工具 Schema
│
├── tools/                         # 工具实现（Python）
│   ├── __init__.py                # 自动加载 skills/*.yaml + 注册工具函数
│   ├── pod_tools.py               # Pod 操作实现
│   ├── deployment_tools.py        # Deployment 操作实现
│   ├── service_tools.py           # Service 操作实现
│   ├── configmap_tools.py         # ConfigMap 操作实现
│   └── rag_tools.py               # RAG 知识库检索实现
│
└── docs/                          # 知识库文档（Markdown）
    └── k8s_pod_troubleshooting.md # Pod 故障排查指南
```

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 AWS 凭证

```bash
# 方式一：AWS CLI 配置（推荐）
aws configure

# 方式二：环境变量
export AWS_ACCESS_KEY_ID=your_key
export AWS_SECRET_ACCESS_KEY=your_secret
export AWS_REGION=ap-northeast-1
```

> **前置条件**：需在 AWS Bedrock 控制台申请 Claude 模型访问权限。

### 3. 配置 kubeconfig

```bash
# 确认 kubectl 可以连接到集群
kubectl get nodes

# EKS 集群示例
aws eks update-kubeconfig --name <cluster-name> --region <region>
```

### 4. 启动 Agent

```bash
python kubernetes_bedrock_claude.py
```

---

## 使用示例

```
You> 列出 default namespace 下所有 Pod
You> production 环境有没有不健康的 Pod？
You> 查看 nginx-xxx Pod 的日志
You> 把 nginx deployment 扩容到 3 个副本
You> 重启 staging 环境的 api deployment
You> CrashLoopBackOff 怎么排查？
You> 查看 app-config 这个 ConfigMap 的内容
You> 更新 my-config 的 log_level 为 debug
```

---

## 架构说明

### 调用流程

```
用户输入（自然语言）
       ↓
  发送给 Claude（携带所有工具 Schema）
       ↓
  Claude 返回 tool_use（工具名 + 参数）
       ↓
  execute_tool() 执行 Python 函数
       ↓
  将结果（tool_result）回传给 Claude
       ↓
  Claude 生成最终回答（文本）
       ↓（循环，最多 10 轮）
  输出最终回答
```

### 工具加载机制

启动时 `tools/__init__.py` 自动完成两件事：

1. **扫描 `skills/*.yaml`** → 构建工具 Schema 列表（告诉 Claude 有哪些工具）
2. **导入各 `*_tools.py`** → 构建函数注册表（实际执行工具调用）

### 新增工具（无需修改主程序）

1. 在 `skills/` 创建 `xxx_skill.yaml`（定义工具名称、描述、参数 Schema）
2. 在 `tools/` 创建 `xxx_tools.py`（实现函数 + `REGISTRY` 字典）
3. 在 `tools/__init__.py` 的导入列表中追加新模块

---

## 可用工具列表

| 工具名 | 功能 |
|--------|------|
| `get_pods` | 列出 namespace 下所有 Pod 及健康状态 |
| `describe_pod` | 获取 Pod 详情和事件（类似 kubectl describe） |
| `get_pod_logs` | 获取容器日志 |
| `get_deployments` | 列出 Deployment 及副本状态 |
| `scale_deployment` | 扩缩容 Deployment |
| `restart_deployment` | 触发滚动重启 |
| `update_deployment_resources` | 更新 CPU/内存资源配置 |
| `get_services` | 列出 Service 及端口映射 |
| `describe_service` | 获取 Service 详情 |
| `get_configmaps` | 列出 ConfigMap |
| `get_configmap` | 查看 ConfigMap 内容 |
| `update_configmap` | 更新 ConfigMap 键值 |
| `search_knowledge_base` | 搜索 Kubernetes 故障排查知识库 |

---

## RAG 知识库

Agent 内置了 Kubernetes 故障排查知识库，使用 AWS Bedrock Titan Embedding 进行语义检索。

**工作原理：**
- 启动时自动加载 `docs/` 目录下所有 `.md` / `.txt` 文件
- 按 Markdown 二级标题（`##`）切片，每个章节独立向量化
- 存入 ChromaDB 内存数据库（重启后自动重建）
- 当用户询问故障排查问题时，Claude 自动调用 `search_knowledge_base` 工具

**添加自定义文档：**

```bash
# 将 Markdown 文档放入 docs/ 目录，重启 Agent 即可自动加载
cp my_runbook.md docs/
```

---

## 部署到 EKS

在 EKS 内部运行时，需要配置两层权限：**AWS IAM**（调用 Bedrock）和 **Kubernetes RBAC**（操作集群资源）。

### 第一步：创建 IAM Policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:*::foundation-model/anthropic.claude-*"
      ]
    }
  ]
}
```

```bash
aws iam create-policy \
  --policy-name K8sAgentBedrockPolicy \
  --policy-document file://bedrock-policy.json
```

### 第二步：配置 IRSA（IAM Roles for Service Accounts）

```bash
# 1. 为 EKS 集群开启 OIDC Provider（如未开启）
eksctl utils associate-iam-oidc-provider \
  --cluster <cluster-name> \
  --region <region> \
  --approve

# 2. 创建 IAM Role 并绑定到 Kubernetes ServiceAccount
eksctl create iamserviceaccount \
  --cluster <cluster-name> \
  --region <region> \
  --namespace <namespace> \
  --name k8s-agent-sa \
  --attach-policy-arn arn:aws:iam::<account-id>:policy/K8sAgentBedrockPolicy \
  --approve
```

### 第三步：配置 Kubernetes RBAC

```yaml
# rbac.yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: k8s-agent-role
rules:
  # Pod：查看状态、获取日志
  - apiGroups: [""]
    resources: ["pods", "pods/log"]
    verbs: ["get", "list", "watch"]
  # Deployment：查看 + 扩缩容 + 重启 + 更新资源
  - apiGroups: ["apps"]
    resources: ["deployments"]
    verbs: ["get", "list", "watch", "patch", "update"]
  # Service：只读
  - apiGroups: [""]
    resources: ["services"]
    verbs: ["get", "list", "watch"]
  # ConfigMap：查看 + 更新
  - apiGroups: [""]
    resources: ["configmaps"]
    verbs: ["get", "list", "watch", "patch", "update"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: k8s-agent-rolebinding
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: k8s-agent-role
subjects:
  - kind: ServiceAccount
    name: k8s-agent-sa
    namespace: <namespace>
```

```bash
kubectl apply -f rbac.yaml
```

### 第四步：部署 Agent

```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: k8s-agent
  namespace: <namespace>
spec:
  replicas: 1
  selector:
    matchLabels:
      app: k8s-agent
  template:
    metadata:
      labels:
        app: k8s-agent
    spec:
      serviceAccountName: k8s-agent-sa   # 绑定 IRSA ServiceAccount
      containers:
        - name: k8s-agent
          image: <your-ecr-image>
          command: ["python", "index.py"]
          env:
            - name: AWS_REGION
              value: "ap-northeast-1"
```

```bash
kubectl apply -f deployment.yaml
```

### 权限总结

| 权限类型 | 内容 | 配置方式 |
|---------|------|---------|
| AWS IAM | `bedrock:InvokeModel` | IRSA（IAM Role → ServiceAccount） |
| K8s RBAC（读） | pods / services 的 get/list/watch | ClusterRole |
| K8s RBAC（写） | deployments / configmaps 的 patch/update | ClusterRole |
| kubeconfig | **不需要** | 自动使用 in-cluster config |

> **注意**：程序启动时会自动检测运行环境。本地开发时使用 `~/.kube/config`，部署到 EKS 后自动切换为 in-cluster config（读取 Pod 内的 ServiceAccount Token），无需任何代码修改。
