"""
RAG 工具实现
============
使用 AWS Bedrock Titan Embedding + ChromaDB（内存模式）实现知识库检索。

工作流程：
  1. 程序启动时，扫描 docs/ 目录下所有 .md 文件
  2. 按 Markdown 标题（## 二级标题）切片，每个章节为一个独立片段
  3. 调用 AWS Bedrock Titan Embedding 将每个片段向量化
  4. 存入 ChromaDB 内存数据库
  5. 查询时：将用户问题向量化 → 余弦相似度搜索 → 返回最相关片段

依赖：
  pip install chromadb
  （boto3 已安装）

注意：
  - 使用内存模式（chromadb.Client()），程序重启后自动重建，无需持久化
  - Embedding 模型：amazon.titan-embed-text-v2:0（AWS Bedrock）
  - 文档放在项目根目录的 docs/ 文件夹下，支持 .md 和 .txt 格式

环境变量：
  AWS_REGION : AWS 区域（默认 ap-northeast-1）
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List

import boto3
import chromadb

# =============================================================================
# 配置
# =============================================================================

# AWS Bedrock Embedding 模型
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
AWS_REGION = "ap-northeast-1"

# 知识库文档目录（相对于项目根目录）
DOCS_DIR = Path(__file__).parent.parent / "docs"

# ChromaDB 集合名称
COLLECTION_NAME = "k8s_knowledge_base"

# 每个文档片段的最大字符数（超过则截断）
MAX_CHUNK_SIZE = 1500

# =============================================================================
# 初始化 AWS Bedrock 客户端
# =============================================================================

_bedrock_client = boto3.client("bedrock-runtime", region_name=AWS_REGION)


def _get_embedding(text: str) -> List[float]:
    """
    调用 AWS Bedrock Titan Embedding 模型，将文本转换为向量。

    参数：
      text : 要向量化的文本（最大 8192 token）

    返回：
      512 维的浮点数列表
    """
    # 截断过长的文本（Titan v2 最大支持 8192 token，约 32000 字符）
    text = text[:8000]

    response = _bedrock_client.invoke_model(
        modelId=EMBEDDING_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "inputText": text,
            "dimensions": 512,        # 使用 512 维，平衡性能和精度
            "normalize": True,        # 归一化，适合余弦相似度计算
        }),
    )

    result = json.loads(response["body"].read())
    return result["embedding"]


# =============================================================================
# 文档加载与切片
# =============================================================================

def _split_markdown_by_heading(content: str, source_file: str) -> List[Dict[str, str]]:
    """
    按 Markdown 二级标题（##）切片文档。
    每个 ## 章节作为一个独立的知识片段。

    参数：
      content     : Markdown 文件内容
      source_file : 文件名（用于元数据）

    返回：
      [{"text": "...", "source": "...", "section": "..."}, ...]
    """
    chunks = []

    # 按 ## 标题分割（保留标题行）
    # 匹配 ## 开头的行（不匹配 ###）
    sections = re.split(r'\n(?=## )', content)

    for section in sections:
        section = section.strip()
        if not section:
            continue

        # 提取标题
        lines = section.split('\n')
        title = lines[0].strip('#').strip() if lines[0].startswith('#') else "概述"

        # 跳过太短的片段（少于 50 字符，可能是空章节）
        if len(section) < 50:
            continue

        # 截断过长的片段
        if len(section) > MAX_CHUNK_SIZE:
            # 尝试在段落边界截断
            truncated = section[:MAX_CHUNK_SIZE]
            last_newline = truncated.rfind('\n\n')
            if last_newline > MAX_CHUNK_SIZE // 2:
                section = truncated[:last_newline]
            else:
                section = truncated

        chunks.append({
            "text": section,
            "source": source_file,
            "section": title,
        })

    return chunks


def _load_documents() -> List[Dict[str, str]]:
    """
    扫描 docs/ 目录，加载所有 .md 和 .txt 文件，切片后返回。

    返回：
      所有文档片段的列表
    """
    all_chunks = []

    if not DOCS_DIR.exists():
        print(f"[RAG] Warning: docs directory not found: {DOCS_DIR}")
        return all_chunks

    # 支持 .md 和 .txt 格式
    doc_files = list(DOCS_DIR.glob("**/*.md")) + list(DOCS_DIR.glob("**/*.txt"))
    doc_files = sorted(doc_files)

    if not doc_files:
        print(f"[RAG] Warning: No documents found in {DOCS_DIR}")
        return all_chunks

    for doc_path in doc_files:
        try:
            content = doc_path.read_text(encoding="utf-8")
            source_name = doc_path.name

            if doc_path.suffix == ".md":
                chunks = _split_markdown_by_heading(content, source_name)
            else:
                # txt 文件：按段落切片
                paragraphs = [p.strip() for p in content.split('\n\n') if len(p.strip()) > 50]
                chunks = [{"text": p, "source": source_name, "section": f"段落{i+1}"}
                          for i, p in enumerate(paragraphs)]

            all_chunks.extend(chunks)
            print(f"[RAG] Loaded '{source_name}': {len(chunks)} chunks")

        except Exception as e:
            print(f"[RAG] Error loading {doc_path}: {e}")

    return all_chunks


# =============================================================================
# ChromaDB 初始化（内存模式）
# =============================================================================

def _build_vector_store(chunks: List[Dict[str, str]]) -> chromadb.Collection:
    """
    将文档片段向量化并存入 ChromaDB 内存数据库。

    参数：
      chunks : 文档片段列表

    返回：
      ChromaDB Collection 对象
    """
    # 内存模式：程序退出后数据消失，每次启动重建
    client = chromadb.Client()
    collection = client.create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},  # 使用余弦相似度
    )

    if not chunks:
        print("[RAG] Warning: No chunks to index")
        return collection

    print(f"[RAG] Building vector store: {len(chunks)} chunks...")

    # 批量向量化（每批 10 个，避免 API 限流）
    batch_size = 10
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]

        ids = [f"chunk_{i + j}" for j in range(len(batch))]
        texts = [c["text"] for c in batch]
        metadatas = [{"source": c["source"], "section": c["section"]} for c in batch]

        # 生成向量
        embeddings = [_get_embedding(text) for text in texts]

        # 存入 ChromaDB
        collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
        )

        print(f"[RAG] Indexed {min(i + batch_size, len(chunks))}/{len(chunks)} chunks")

    print(f"[RAG] Vector store ready: {collection.count()} chunks indexed")
    return collection


# =============================================================================
# 全局初始化（模块导入时执行）
# =============================================================================

print("[RAG] Initializing knowledge base...")

try:
    _chunks = _load_documents()
    _collection = _build_vector_store(_chunks)
    _rag_ready = True
    print("[RAG] Knowledge base ready ✓")
except Exception as e:
    print(f"[RAG] Warning: Failed to initialize knowledge base: {e}")
    print("[RAG] search_knowledge_base will return empty results")
    _collection = None
    _rag_ready = False


# =============================================================================
# 工具函数
# =============================================================================

def search_knowledge_base(query: str, top_k: int = 3) -> Dict[str, Any]:
    """
    在知识库中搜索与查询最相关的文档片段。

    调用时机：
    - 用户询问 Kubernetes 故障排查方法时
    - 需要了解某种 Pod 状态的含义和处理方式时
    - 需要查找 kubectl 命令或最佳实践时

    参数：
      query  : 搜索查询（自然语言，如 "CrashLoopBackOff 怎么排查"）
      top_k  : 返回最相关的片段数量（默认 3）

    返回：
      {
        "results": [
          {
            "content": "...",    # 文档片段内容
            "source": "...",     # 来源文件名
            "section": "...",    # 章节标题
            "relevance_score": 0.95  # 相关度分数（0-1，越高越相关）
          },
          ...
        ],
        "total_found": 3,
        "query": "..."
      }
    """
    if not _rag_ready or _collection is None:
        return {
            "error": "Knowledge base is not available",
            "results": [],
            "total_found": 0,
            "query": query,
        }

    try:
        # 将查询向量化
        query_embedding = _get_embedding(query)

        # 在 ChromaDB 中搜索最相似的片段
        results = _collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, _collection.count()),
            include=["documents", "metadatas", "distances"],
        )

        # 格式化结果
        formatted_results = []
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        for doc, meta, dist in zip(documents, metadatas, distances):
            # ChromaDB 余弦距离：0 = 完全相同，2 = 完全相反
            # 转换为相关度分数：1 - distance/2（范围 0-1）
            relevance_score = round(1 - dist / 2, 4)

            formatted_results.append({
                "content": doc,
                "source": meta.get("source", "unknown"),
                "section": meta.get("section", "unknown"),
                "relevance_score": relevance_score,
            })

        return {
            "results": formatted_results,
            "total_found": len(formatted_results),
            "query": query,
        }

    except Exception as e:
        return {
            "error": f"Search failed: {str(e)}",
            "results": [],
            "total_found": 0,
            "query": query,
        }


# =============================================================================
# 工具注册表（供 tools/__init__.py 使用）
# =============================================================================

REGISTRY = {
    "search_knowledge_base": search_knowledge_base,
}
