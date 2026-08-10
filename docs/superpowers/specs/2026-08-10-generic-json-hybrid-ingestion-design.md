# 通用 JSON 入库与混合检索设计

日期：2026-08-10

状态：待评审

## 1. 背景

当前系统支持 TXT、Markdown 入库，文本经过切块、向量化后写入 Chroma。检索阶段先从 Chroma 召回候选，再在这批候选内部计算轻量词法分数。因此，现有词法分数不是独立关键词召回：如果精确术语、编号或专有名词没有进入向量候选池，词法打分无法将其找回。

本设计增加两项通用能力：

1. 面向任意 JSON/JSONL 文件的可配置、可复用、流式结构化入库。
2. 保留 Chroma，并增加 SQLite FTS5 独立关键词召回、RRF 融合及可选云端 reranker。

知识星球导出数据只作为首个参考数据集，不在核心模块中写入知识星球专用逻辑。

## 2. 目标与非目标

### 2.1 目标

- 支持 JSON、JSONL，以及根对象、根数组和嵌套记录数组。
- 通过安全 JSONPath 子集配置记录路径和字段映射。
- 自动生成确定性的映射建议，并允许可选 LLM 语义增强；所有建议必须经用户确认。
- 保留父子层级、原始文件和逐记录原始 JSON 快照。
- 将字段分别用于向量、全文、过滤、时间和展示。
- 以版本化模板复用映射方案，并检测输入结构兼容性。
- 在 100 MB 文件限制内流式处理，避免整文件驻留内存。
- 使用 Chroma 与 SQLite FTS5 进行独立双路召回，通过 RRF 融合。
- 提供与供应商无关的云端 reranker 接口，失败时自动回退到 RRF。
- 保证多索引写入失败时旧版本仍可服务，新版本不会部分可见。

### 2.2 非目标

- 不引入 Elasticsearch。
- 首版不支持任意脚本、JSONPath 函数、递归下降和复杂过滤表达式。
- 不强制 embedding 与 reranker 使用同一供应商；二者也不存在维度一致要求。
- 不在本期实现 OCR、图片理解或视频抓取/转写。
- 不改变现有 TXT/Markdown 的快速入库入口。

## 3. 总体架构

```text
JSON / JSONL
  -> 结构探查与采样
  -> 映射建议
  -> 用户确认并保存版本化模板
  -> 流式生成父子 MappedRecord
  -> ChunkBuilder
       embedding_text ------------> Chroma
       title/content/keywords -----> SQLite FTS5
       filters/raw/metadata -------> 处理产物与过滤索引

查询
  -> Chroma dense Top 50
  -> FTS5 BM25 Top 50
  -> 显式元数据过滤
  -> RRF 融合，rank_constant=60
  -> 可选云端 reranker 重排 Top 30
  -> 选取 Top 5~8
  -> 父记录/相邻块上下文扩展
  -> 最多 8 块或约 2000 tokens
```

模块边界：

- `StructuredIngestionModule` 负责探查、预览和流式记录生成，不了解 Chroma 或 FTS。
- `ChunkBuilder` 将标准记录转换为统一索引块，集中处理模型 token 限制。
- `HybridRetrievalModule` 负责过滤、双路召回、融合、重排和上下文扩展，并隐藏在现有 `RetrievalModule` 接口后。
- `RerankModule` 只接收查询与候选文本并返回标量相关度，不暴露供应商协议。

核心接口示意：

```python
class StructuredIngestionModule:
    def inspect(self, source, options) -> Profile: ...
    def preview(self, source, mapping, limit) -> Preview: ...
    def iterate_records(self, source, mapping) -> Iterator[MappedRecord]: ...

class ChunkBuilder:
    def build(self, record: MappedRecord, policy: ChunkPolicy) -> Iterator[IndexChunk]: ...

class HybridRetrievalModule:
    def retrieve(self, query: SearchQuery, options: RetrievalOptions) -> list[RetrievedChunk]: ...

class RerankModule:
    def rerank(self, query: str, candidates: list[RerankCandidate], top_n: int) -> list[RerankResult]: ...
```

## 4. 映射模型

### 4.1 安全路径子集

首版支持：

- 根：`$`
- 对象字段：`$.posts`
- 数组下标：`$.items[0]`
- 数组展开：`$.posts[*]`
- 子记录相对路径：`comments[*]`

首版禁止递归下降、脚本、函数和复杂过滤器。解析器必须是自有的受限语法实现，不能执行用户代码。

### 4.2 映射结构

一个模板包含一个或多个 `record_types`。每种记录声明：

- `name`
- `record_path`
- 字段路径及字段角色
- 子记录定义
- 关系规则
- 转换规则
- 校验及跳过条件
- 切块策略

字段角色：

| 角色 | 向量文本 | FTS | 元数据/过滤 | 用途 |
| --- | --- | --- | --- | --- |
| `id` | 否 | 否 | 是 | 稳定记录标识 |
| `title` | 是 | 是 | 可选 | 标题与主要语义 |
| `content` | 是 | 是 | 否 | 正文语义 |
| `keyword` | 否 | 是 | 可选 | 标签、术语、编号 |
| `filter` | 否 | 否 | 是 | 精确过滤 |
| `timestamp` | 否 | 否 | 是 | 排序、过滤、展示 |
| `display` | 否 | 否 | 否 | 引用展示 |
| `ignore` | 否 | 否 | 否 | 明确忽略 |

`title` 与 `content` 同时进入 embedding 文本与 FTS；`keyword` 只进入 FTS，避免关键词堆积扭曲向量。

### 4.3 稳定标识与父子关系

若输入存在有效 ID，则使用规范化后的输入 ID。否则生成：

```text
record_id = hash(source_file_hash, source_json_pointer, record_type)
```

子记录包含 `parent_id`。父子关系由模板的嵌套定义产生，不依赖业务字段名。

可选的通用关系规则支持“源字段匹配最近的前序同级记录的目标字段”，适用于回复、聊天、工单等上下文连接，但不内置任何知识星球字段。

### 4.4 转换与条件

允许的固定转换：

- `trim`
- 空格/换行规范化
- `strip_html`
- `join`
- 标量类型转换
- `parse_datetime`
- `deduplicate`
- `remove_child_echo`

`remove_child_echo` 是通用的父子内容去重规则，用于去除父正文中重复拼接的子记录文本。

支持的条件：

- `required`
- `min_length` / `max_length`
- `not_empty`
- `skip_if_only_emoji`
- `skip_if_matches`

转换和条件均使用声明式参数，不允许上传可执行代码。

### 4.5 自动建议与确认

建议流程分两层：

1. 确定性分析：依据路径、字段类型、重复率、长度、时间格式、唯一性和嵌套关系推断。
2. 可选 LLM 增强：只发送采样后的字段摘要和脱敏样例，给出语义角色建议。

LLM 建议永不直接执行。前端必须展示置信度、理由和样例，由用户确认或修改。

## 5. 标准记录、处理产物与切块

### 5.1 MappedRecord

```text
MappedRecord
  record_id
  parent_id
  record_type
  title
  content
  keywords[]
  filters{}
  timestamps{}
  display{}
  raw{}
  source_pointer
  mapping_version_id
```

处理目录保存：

- 原始上传文件
- `records.jsonl`：标准记录流
- `mapping-errors.jsonl`：逐记录错误与警告
- `manifest.json`：源文件、映射版本、结构指纹、数量和哈希

逐记录 `raw` 快照确保后续可以重新映射、审计和定位原始字段。

### 5.2 ChunkPolicy

- `semantic`：按现有 token-aware 语义规则切块。
- `atomic`：整条记录作为一个块，超模型限制时才安全切分。
- `parent-only`：仅父记录建立主体索引，子记录作为扩展上下文。
- `ignore`：不建立索引。

`IndexChunk` 至少包含：

```text
chunk_id
document_id
record_id
parent_id
record_type
content
embedding_text
lexical_title
lexical_content
lexical_keywords
filters
timestamps
source_pointer
mapping_version_id
content_hash
ingest_run_id
index_state
```

现有 worker 中 embedding 前的 500 字符二次硬切分应移除。模型 token 上限、重叠和安全截断统一由 `ChunkBuilder` 处理，否则前端配置的语义块会被再次破坏。

## 6. 流式解析与错误处理

### 6.1 流式策略

- JSONL 按行读取。
- 根数组使用增量 JSON 解析器逐元素读取。
- 嵌套数组在父记录生命周期内流式展开。
- 各阶段使用有界队列施加背压。
- 结构探查默认读取前 1000 条，并从文件后续位置做分布式采样，避免只看到头部形态。
- 100 MB 是上传文件上限，不是允许的进程内存占用。

### 6.2 错误语义

- JSON/JSONL 语法错误：整个文档失败。
- 必填字段缺失或类型不符：隔离该记录并写入错误文件。
- 可选字段错误：记录警告并继续。
- 时间解析失败：保留原始展示值，但不写入时间过滤字段。
- 记录错误数量达到 `max(1, min(ceil(total_records * 0.01), 100))` 时文档失败。
- 未达到失败阈值但存在隔离记录时状态为 `done_with_warnings`。
- 失败运行不得激活任何新索引。

## 7. 模板、版本与结构兼容性

新增概念：

- `MappingTemplate`
- `MappingTemplateVersion`
- `DocumentMapping`

模板版本一旦被文档使用即不可修改；变更会创建新版本。

结构指纹基于路径、容器形态和字段类型，不包含实际值。复用判定：

- 指纹一致：自动选中原模板版本，仍允许用户预览。
- 仅新增可选字段：标记兼容，可直接复用。
- 路径消失、类型变化或层级变化：标记破坏性变化，必须重新确认。

`DocumentMapping` 固定文档实际采用的模板版本、确认者和确认时间，保证可追溯。

## 8. 双路索引

### 8.1 Chroma

保留 Chroma 作为 dense 向量索引。metadata 增加：

- `document_id`
- `record_id`
- `parent_id`
- `record_type`
- `mapping_version_id`
- `source_pointer`
- `timestamp`
- `content_hash`
- `ingest_run_id`
- `index_state`

### 8.2 SQLite FTS5

每个部署使用持久化 SQLite 文件，随服务挂载独立 Docker volume，不增加新服务。

FTS 字段与默认权重：

- title：5
- keywords：3
- content：1

应用层词法分析器先执行 NFKC、转小写、保留数字/标识符/字母数字组合，并为中文生成 unigram 与 bigram，再写入使用 `unicode61` tokenizer 的 FTS5 表。首版不直接使用 trigram tokenizer，因为两字中文词会受限。

动态过滤字段写入通用 EAV sidecar，并至少为下列组合建立索引：

```text
(collection_id, field_name, normalized_value)
(document_id, field_name, normalized_value)
```

FTS 行和过滤行都携带 `chunk_id`、`ingest_run_id` 与 `index_state`。

## 9. 检索、融合与重排

### 9.1 查询模型

```text
SearchQuery
  text
  filters{}
```

首版只接受调用方显式过滤条件，不让 LLM 从自然语言自动生成过滤器。

### 9.2 双路召回

默认召回：

- Chroma dense Top 50
- FTS5 BM25 Top 50

过滤条件在各召回通道尽早执行。原 cosine 阈值只适用于 dense 通道；FTS 独立候选即使没有 dense 分数也可以进入融合。

### 9.3 RRF

以 `rank_constant=60` 做 Reciprocal Rank Fusion：

```text
rrf_score(d) = sum(1 / (60 + rank_lane(d)))
```

RRF 依赖排名而非不同模型间不可比的原始分数。融合后默认保留前 30 条作为 reranker 输入。

### 9.4 云端 reranker

`RerankModule` 采用 provider-neutral 适配器：

- 输入：查询、候选文本和稳定候选 ID。
- 输出：候选 ID 与标量相关度。
- 配置：provider、model、endpoint、timeout、batch size、top_n。
- 超时、限流、服务错误或无效响应：记录降级原因并直接使用 RRF 排序。

reranker 对文本对进行交叉相关性评分，不消费 embedding 向量，因此其“维度”无需与 embedding 模型一致，也无需沿用豆包供应商。默认模型应由真实评测集比较后决定，而不是按供应商绑定。

### 9.5 返回分数兼容性

现有 `score` 继续表示原始 cosine，但改为 nullable，以兼容只由 FTS 召回的结果。新增：

```text
dense_score
lexical_score
dense_rank
lexical_rank
rrf_score
rerank_score
retrieval_sources[]
```

这样保留 API 兼容语义，同时支持检索调试和离线评测。

### 9.6 上下文扩展

重排后再扩展上下文：

- 命中子记录时补充父记录标题或摘要。
- 命中长记录分块时补充相邻块。
- 不默认加载某父记录的全部子记录。
- 按 `chunk_id` 和内容哈希去重。
- 最终限制为最多 8 块或约 2000 tokens。

## 10. 状态机与跨索引一致性

文档状态：

```text
uploaded
  -> profiling
  -> awaiting_mapping
  -> previewing
  -> queued
  -> mapping
  -> chunking
  -> embedding
  -> indexing_lexical
  -> indexing_dense
  -> activating
  -> done | done_with_warnings | failed
```

TXT/Markdown 可继续走现有快速路径，不必进入 `awaiting_mapping`。

每次入库创建唯一 `ingest_run_id`。激活协议：

1. Chroma、FTS 和过滤 sidecar 全部以 `staging` 写入。
2. 校验三处的 `chunk_id` 集合、数量和内容哈希。
3. 将两类外部索引标记为 `active`，但此时 MySQL 仍指向旧 `active_ingest_run_id`。
4. 在 MySQL 事务内将文档的 `active_ingest_run_id` 切换到新运行，并更新文档状态。
5. 检索必须同时要求外部 `index_state=active`，并且 `ingest_run_id` 等于 MySQL 当前活动运行，因此第 3 步的短暂窗口不会暴露新数据。
6. 切换成功后异步清理旧运行。

若第 3 或第 4 步崩溃，旧运行仍由 MySQL 活动清单控制并正常服务；重试根据运行清单恢复或清除部分激活数据。知识库级重建采用同样的蓝绿 generation 机制，完整验证后一次切换。

## 11. 后端与前端改造

### 11.1 后端建议目录

```text
rag-service/src/structured/
  models.py
  stream.py
  paths.py
  profiler.py
  mapping.py
  transforms.py
  artifacts.py
  chunks.py

rag-service/src/retrieval/
  lexical.py
  fts.py
  filters.py
  fusion.py
  rerank.py
  hybrid.py
```

需要修改现有文档上传路由、文档模型、worker、retrieval factory、Chroma adapter、query/prompt 组装、共享配置、Docker volume 和运行时配置。

数据库迁移建议拆为：

- `0005_structured_json_ingestion.py`
- `0006_hybrid_retrieval_metadata.py`
- `0007_reranker_settings.py`

### 11.2 前端映射向导

1. 上传文件。
2. 展示结构检测和候选记录数组。
3. 映射字段角色。
4. 配置父子、上下文及可选关系规则。
5. 请求后端真实预览，展示 raw、content、embedding text、lexical fields、metadata。
6. 保存模板版本并开始入库。

新增页面或组件还应支持模板列表、版本查看、兼容性提示和逐记录错误下载。

### 11.3 API 草案

- `POST /api/kb/{kb_id}/json/profile`
- `POST /api/kb/{kb_id}/json/preview`
- `GET|POST /api/mapping-templates`
- `POST /api/kb/{kb_id}/json/ingest`
- `GET /api/docs/{doc_id}/mapping-errors`

所有预览和正式入库使用同一套解析、转换和切块实现，避免“预览正确、入库不同”。

## 12. 分阶段交付

1. 结构探查、映射模板、版本管理和后端预览。
2. 流式映射、标准产物和统一 ChunkBuilder。
3. JSON worker、Chroma staging/active 与恢复机制。
4. FTS5、过滤 sidecar 和 RRF 双路召回。
5. 云端 reranker 适配器、配置、降级与日志。
6. 前端向导、模板管理及知识星球参考模板。

每一阶段应保持已有 TXT/Markdown 入库和 dense 检索可运行。

## 13. 测试与验收

### 13.1 数据集

- 至少 100 个可回答问题。
- 至少 30 个不可回答问题。
- 至少 30 个依赖精确术语、编号或专有名词的问题。
- 至少 20 个依赖父子上下文的问题。
- 包含 JSON、JSONL、根对象、根数组、嵌套数组、异常记录和近 100 MB 文件。

### 13.2 指标

- 正确来源 Top 5 命中率不低于 90%。
- 精确术语 Recall@5 不低于 95%。
- 父子上下文引用正确率不低于 95%。
- 不可回答问题拒答率不低于 90%。
- 跨知识库泄漏为 0。
- 不产生重复块。
- 失败的 staging 数据对查询不可见。

离线评测必须对比：

1. dense only
2. dense + FTS5 + RRF
3. dense + FTS5 + RRF + reranker

只有在真实数据上相对基线稳定提升，才默认启用 reranker。

### 13.3 知识星球参考数据预期

参考映射预计生成：

- 136 条父帖子记录。
- 1163 条评论子记录。
- 使用 `remove_child_echo` 清理 16 条父正文中的评论回显。
- 短回复检索结果带上对应问题或父帖摘要。
- 图片和视频只保留引用与待处理标记，后续单独实现媒体管线。

这些数字用于首个回归数据集，不成为通用解析器的硬编码约束。

## 14. 风险与缓解

- **双索引一致性**：使用 `ingest_run_id`、外部状态和 MySQL 活动清单三重门控。
- **中文 FTS 召回不足**：应用层生成 unigram/bigram，并通过精确术语集评测。
- **映射误判**：建议必须确认，正式入库前使用相同代码真实预览。
- **大文件内存膨胀**：增量解析、有界队列和逐行错误产物。
- **reranker 不稳定或昂贵**：限制 Top 30、批处理、超时和 RRF 降级。
- **动态过滤字段膨胀**：EAV 索引限定作用域，模板层限制可过滤字段数量和类型。
- **父子扩展挤占上下文**：重排后按规则扩展，并执行严格块数/token 预算。

## 15. 决策摘要

- 通用 JSON 入库采用“版本化声明式映射 + 流式标准记录”的深模块边界。
- Chroma 继续负责语义向量召回；SQLite FTS5 提供真正独立的关键词/BM25 召回。
- 双路候选先以 RRF 融合，再由可选云端 reranker 重排。
- embedding 与 reranker 不要求同厂商或同维度，最终默认模型由评测决定。
- 原始文件、逐记录快照、映射版本和运行版本全部保留，以支持审计、重建和错误恢复。
