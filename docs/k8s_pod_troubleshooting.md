# Kubernetes Pod 故障排查指南

## 概述

本文档涵盖 Kubernetes Pod 常见故障的原因分析、排查步骤和解决方案。

---

## CrashLoopBackOff

### 含义
容器启动后立即崩溃，Kubernetes 反复重启，重启间隔指数级增长（10s → 20s → 40s → ...）。

### 常见原因
1. 应用程序启动时抛出未捕获异常
2. 配置文件缺失或格式错误（如 ConfigMap/Secret 挂载路径错误）
3. 依赖服务（数据库、Redis）无法连接
4. 端口冲突
5. 内存不足导致 OOM（会显示 OOMKilled，但有时也表现为 CrashLoopBackOff）
6. 启动命令错误（command/args 配置有误）

### 排查步骤
```bash
# 1. 查看 Pod 状态和重启次数
kubectl get pod <pod-name> -n <namespace>

# 2. 查看最近的日志（关键：加 --previous 查看上一次崩溃的日志）
kubectl logs <pod-name> -n <namespace> --previous

# 3. 查看 Pod 详细事件
kubectl describe pod <pod-name> -n <namespace>

# 4. 查看容器退出码
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.status.containerStatuses[0].lastState.terminated.exitCode}'
```

### 退出码含义
| 退出码 | 含义 |
|--------|------|
| 0 | 正常退出（不应该 CrashLoop） |
| 1 | 应用程序错误 |
| 137 | OOMKilled（内存不足，128+9） |
| 139 | 段错误（Segmentation Fault） |
| 143 | 收到 SIGTERM 后正常退出 |

### 解决方案
- 查看 `--previous` 日志找到具体错误信息
- 检查 ConfigMap/Secret 是否正确挂载：`kubectl exec <pod> -- cat /path/to/config`
- 检查环境变量：`kubectl exec <pod> -- env`
- 临时增大内存限制排查是否 OOM

---

## OOMKilled

### 含义
容器使用内存超过 `resources.limits.memory` 限制，被内核强制杀死。

### 常见原因
1. 内存限制设置过低
2. 应用存在内存泄漏
3. 流量突增导致内存使用量超出预期
4. JVM 堆内存配置与容器限制不匹配（Java 应用常见）

### 排查步骤
```bash
# 1. 确认是 OOMKilled
kubectl describe pod <pod-name> -n <namespace> | grep -A5 "Last State"
# 输出示例：
# Last State: Terminated
#   Reason: OOMKilled
#   Exit Code: 137

# 2. 查看当前内存限制
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.spec.containers[0].resources}'

# 3. 查看实时内存使用（需要 metrics-server）
kubectl top pod <pod-name> -n <namespace>

# 4. 查看历史内存使用趋势（如有 Prometheus）
# 查询：container_memory_working_set_bytes{pod="<pod-name>"}
```

### 解决方案
```yaml
# 增大内存限制
resources:
  requests:
    memory: "256Mi"
  limits:
    memory: "512Mi"   # 适当增大

# Java 应用：设置 JVM 堆内存与容器限制匹配
env:
  - name: JAVA_OPTS
    value: "-Xmx400m -Xms256m"  # 堆内存上限 < 容器内存限制
```

---

## ImagePullBackOff / ErrImagePull

### 含义
Kubernetes 无法从镜像仓库拉取容器镜像。

### 常见原因
1. 镜像名称或 Tag 错误（拼写错误、Tag 不存在）
2. 镜像仓库需要认证，但未配置 imagePullSecret
3. 网络问题（节点无法访问镜像仓库）
4. 镜像仓库限流（Docker Hub 免费账户有拉取限制）

### 排查步骤
```bash
# 1. 查看具体错误信息
kubectl describe pod <pod-name> -n <namespace> | grep -A10 "Events"

# 2. 确认镜像是否存在
docker pull <image-name>:<tag>

# 3. 检查 imagePullSecret 是否配置
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.spec.imagePullSecrets}'

# 4. 检查 Secret 是否存在
kubectl get secret -n <namespace>
```

### 解决方案
```bash
# 创建镜像仓库认证 Secret
kubectl create secret docker-registry regcred \
  --docker-server=<registry-url> \
  --docker-username=<username> \
  --docker-password=<password> \
  -n <namespace>
```

```yaml
# 在 Pod spec 中引用 Secret
spec:
  imagePullSecrets:
    - name: regcred
  containers:
    - name: app
      image: <registry-url>/app:latest
```

---

## Pending（Pod 一直处于 Pending 状态）

### 含义
Pod 已创建但尚未被调度到任何节点上运行。

### 常见原因
1. **资源不足**：集群中没有节点满足 CPU/内存 requests
2. **节点选择器不匹配**：`nodeSelector` 或 `nodeAffinity` 找不到匹配节点
3. **污点与容忍**：节点有 Taint，但 Pod 没有对应 Toleration
4. **PVC 未绑定**：Pod 依赖的 PersistentVolumeClaim 处于 Pending 状态
5. **调度器问题**：自定义调度器未运行

### 排查步骤
```bash
# 1. 查看 Pod 事件（最重要）
kubectl describe pod <pod-name> -n <namespace>
# 关注 Events 部分，通常会有明确的原因

# 2. 查看节点资源使用情况
kubectl describe nodes | grep -A5 "Allocated resources"

# 3. 查看节点标签（排查 nodeSelector 问题）
kubectl get nodes --show-labels

# 4. 查看 PVC 状态
kubectl get pvc -n <namespace>
```

### 常见 Events 及解决方案

| Event 信息 | 原因 | 解决方案 |
|-----------|------|---------|
| `Insufficient cpu` | CPU 资源不足 | 降低 requests 或扩容节点 |
| `Insufficient memory` | 内存资源不足 | 降低 requests 或扩容节点 |
| `no nodes available to schedule pods` | 所有节点不可用 | 检查节点状态 |
| `didn't match node selector` | nodeSelector 不匹配 | 检查节点标签 |
| `had untolerated taint` | 节点有 Taint | 添加 Toleration 或移除 Taint |

---

## Evicted（Pod 被驱逐）

### 含义
节点资源（磁盘、内存）不足时，kubelet 主动驱逐 Pod 以释放资源。

### 常见原因
1. 节点磁盘空间不足（最常见）
2. 节点内存压力（MemoryPressure）
3. 节点 PID 压力（PIDPressure）

### 排查步骤
```bash
# 1. 查看驱逐原因
kubectl describe pod <pod-name> -n <namespace> | grep -A5 "Message"
# 示例：The node was low on resource: ephemeral-storage

# 2. 查看节点状态
kubectl describe node <node-name> | grep -A10 "Conditions"

# 3. 查看节点磁盘使用
kubectl get node <node-name> -o jsonpath='{.status.conditions[?(@.type=="DiskPressure")].status}'

# 4. 清理已完成的 Pod
kubectl delete pod --field-selector=status.phase==Succeeded -n <namespace>
kubectl delete pod --field-selector=status.phase==Failed -n <namespace>
```

### 解决方案
```bash
# 清理节点上的无用镜像
docker system prune -a

# 设置 ephemeral-storage 限制，防止单个 Pod 占用过多磁盘
resources:
  limits:
    ephemeral-storage: "1Gi"
```

---

## Init Container 失败

### 含义
Pod 的初始化容器（Init Container）执行失败，主容器无法启动。

### 常见原因
1. Init Container 中的命令执行失败（如数据库迁移脚本报错）
2. 依赖服务未就绪（Init Container 等待超时）
3. 权限问题

### 排查步骤
```bash
# 1. 查看 Init Container 状态
kubectl describe pod <pod-name> -n <namespace> | grep -A20 "Init Containers"

# 2. 查看 Init Container 日志
kubectl logs <pod-name> -c <init-container-name> -n <namespace>

# 3. 查看上一次失败的 Init Container 日志
kubectl logs <pod-name> -c <init-container-name> -n <namespace> --previous
```

---

## Liveness / Readiness Probe 失败

### 含义
- **Liveness Probe 失败**：容器被认为不健康，Kubernetes 重启容器
- **Readiness Probe 失败**：容器被从 Service 的 Endpoints 中移除，不再接收流量

### 常见原因
1. 探针配置的路径/端口错误
2. 应用启动慢，但 `initialDelaySeconds` 设置过短
3. 应用真的不健康（内存泄漏、死锁等）
4. 探针超时时间设置过短

### 排查步骤
```bash
# 1. 查看探针配置和失败事件
kubectl describe pod <pod-name> -n <namespace>
# 关注：Liveness probe failed / Readiness probe failed

# 2. 手动测试探针
kubectl exec <pod-name> -n <namespace> -- curl -f http://localhost:8080/health

# 3. 查看应用日志
kubectl logs <pod-name> -n <namespace>
```

### 推荐配置
```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8080
  initialDelaySeconds: 30    # 给应用足够的启动时间
  periodSeconds: 10
  failureThreshold: 3
  timeoutSeconds: 5

readinessProbe:
  httpGet:
    path: /ready
    port: 8080
  initialDelaySeconds: 10
  periodSeconds: 5
  failureThreshold: 3
```

---

## Pod 处于 Terminating 状态无法删除

### 含义
Pod 被删除后长时间停留在 Terminating 状态。

### 常见原因
1. 应用没有正确处理 SIGTERM 信号
2. `terminationGracePeriodSeconds` 超时
3. Finalizer 未清除
4. 节点宕机，Pod 无法正常终止

### 解决方案
```bash
# 强制删除 Pod（谨慎使用）
kubectl delete pod <pod-name> -n <namespace> --force --grace-period=0

# 查看是否有 Finalizer 阻止删除
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.metadata.finalizers}'
```

---

## 常用排查命令速查表

```bash
# 查看所有 Pod 状态
kubectl get pods -n <namespace> -o wide

# 查看所有不健康的 Pod
kubectl get pods -n <namespace> | grep -v Running | grep -v Completed

# 查看 Pod 日志（最近100行）
kubectl logs <pod-name> -n <namespace> --tail=100

# 查看上一次崩溃的日志
kubectl logs <pod-name> -n <namespace> --previous

# 进入容器调试
kubectl exec -it <pod-name> -n <namespace> -- /bin/sh

# 查看 Pod 详细信息和事件
kubectl describe pod <pod-name> -n <namespace>

# 查看 Pod 资源使用
kubectl top pod <pod-name> -n <namespace>

# 查看 Pod 的 YAML 定义
kubectl get pod <pod-name> -n <namespace> -o yaml

# 监控 Pod 状态变化
kubectl get pods -n <namespace> -w

# 查看节点资源
kubectl top nodes

# 查看所有命名空间的 Pod
kubectl get pods -A
```

---

## 健康检查标准

| 指标 | 正常 | 警告 | 严重 |
|------|------|------|------|
| 重启次数 | 0-3 | 3-10 | >10 |
| Pod 状态 | Running | Pending | CrashLoopBackOff / Error |
| CPU 使用率 | <70% limit | 70-90% limit | >90% limit |
| 内存使用率 | <70% limit | 70-90% limit | >90% limit |
