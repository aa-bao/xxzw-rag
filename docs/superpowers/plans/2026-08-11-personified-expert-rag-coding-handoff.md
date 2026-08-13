# 拟人化专家 RAG Coding Agent 交接计划

日期：2026-08-11

设计基线：[拟人化专家 RAG 总体设计](../specs/2026-08-11-personified-expert-rag-design.md)

领域决策：[ADR-0028](../../adr/0028-expert-assistant-is-rag-not-agent.md)

涉及仓库：

- `E:/dev/project/rag-database`
- `E:/dev/project/knowledge-boll-crawler`

## 0. Coding agent 必读摘要

目标是固定流程的拟人化专家 RAG，不是 Agent，不实现工具调用、任务规划、执行循环或 `SKILL.md` 加载。

最终在线能力分两期：

```text
MVP：原始证据 + voice_profile + 真实混合检索 + 引用
条件扩展：MVP 通过后，再验证 expert_rules 是否有增益
```

不得把仲帅写死到 RAG 核心。RAG 只认识通用 `ExpertProfile`、`ExpertProfileVersion`、`EvidenceRecord` 和可选 `ExpertRule`。

### 当前代码审计

已经存在：

- 通用结构化 JSON/JSONL 模型、映射、产物和相关测试；
- `retrieval/fts.py`、`retrieval/lexical.py`、`retrieval/filters.py`；
- Chroma 向量召回；
- 查询历史改写、上下文扩展、引用和流式回答。

尚未完成或尚未接入生产查询：

- 独立 dense 与 FTS 候选融合；
- RRF；
- `HybridRetrievalModule`；
- 云端 reranker 客户端与配置；
- 迁移 `0006`、`0007`；
- Chat `QueryEngine` 的 hybrid runtime wiring；
- 知识库/对话级专家配置；
- 公开表达画像 Prompt；
- 专家规则线上证据展开。

现有 [混合检索实施计划](2026-08-10-independent-fts-hybrid-reranking.md) 的复选框没有更新，但 Tasks 1–3 已有部分代码。不要按复选框从头重写；先用代码和测试审计实际完成度。

## 全局约束

- 采用测试先行；先添加失败测试，再实现。
- 不修改 raw 数据；所有派生数据写到独立目录并记录源快照。
- 不引入 Elasticsearch，保留 Chroma。
- embedding 和 reranker 不要求同供应商或同维度。
- 检索内容始终是不可信数据，不能覆盖系统指令。
- 专家配置和所有数据必须受用户空间所有权限制。
- 不记录模型密钥或完整付费材料到日志。
- 不覆盖工作区中与本计划无关的现有改动。
- 每个阶段必须通过自己的验收门槛才能进入下一阶段。

---

## Phase 1：完成真实混合检索前置能力

### Task 1.1：审计现有 FTS 和过滤实现

**仓库：** `rag-database`

**重点文件：**

- `rag-service/src/retrieval/fts.py`
- `rag-service/src/retrieval/lexical.py`
- `rag-service/src/retrieval/filters.py`
- `rag-service/tests/test_retrieval/test_fts.py`
- `rag-service/tests/test_retrieval/test_lexical.py`
- `rag-service/tests/test_retrieval/test_filters.py`

**步骤：**

1. 运行现有测试并记录实际通过情况。
2. 对照旧计划 Tasks 1–3，确认 schema、持久化、中文 lexical 分析、BM25、active run 和 EAV filters 是否完整。
3. 只补缺失行为，不重复实现已经存在且有测试覆盖的模块。
4. 更新旧计划的完成状态或写一份简短审计说明，确保后续开发者不再误判。

**验证：**

```powershell
.venv/Scripts/python.exe -m pytest rag-service/tests/test_retrieval/test_fts.py rag-service/tests/test_retrieval/test_lexical.py rag-service/tests/test_retrieval/test_filters.py -q
```

### Task 1.2：实现 independent dense + FTS + RRF

优先执行旧计划 Task 4，建议创建：

- `rag-service/src/retrieval/fusion.py`
- `rag-service/src/retrieval/hybrid.py`
- `rag-service/tests/test_retrieval/test_fusion.py`
- `rag-service/tests/test_retrieval/test_hybrid.py`

`HybridRetrievalModule` 隐藏双路候选、过滤、RRF 和降级逻辑，继续满足现有 `RetrievalModule` 接口。FTS-only 候选必须能够进入结果，不能只对 Chroma 候选做词法打分。

默认行为按既有设计：dense Top 50、FTS Top 50、`rank_constant=60`、融合后 Top 30。

### Task 1.3：完成跨索引激活

执行旧计划 Task 5：

- 创建迁移 `0006_hybrid_retrieval_metadata.py`；
- FTS 与 Chroma 使用同一 `ingest_run_id` 和 staging/active 协议；
- MySQL 活动运行是最终可见性门控；
- 崩溃和重试不暴露半套索引。

必须覆盖 crash matrix，不允许以“最终一致”替代原子可见性。

### Task 1.4：实现 provider-neutral reranker

执行旧计划 Task 6：

- `rag-service/src/models/rerank.py`
- `rag-service/src/retrieval/rerank.py`
- 迁移 `0007_reranker_settings.py`
- 设置 API 与密钥脱敏
- timeout、429、5xx、非法响应时回退 RRF

reranker 输入稳定候选 ID 和文本，输出 ID 与标量分数；不得传 embedding 向量。

### Task 1.5：接入 QueryEngine

执行旧计划 Task 7：

- 应用启动时构造并共享 `FtsIndex`、Chroma adapter、可选 reranker 和 `HybridRetrievalModule`；
- `router_chat.py` 不再直接构造或取得裸 `ChromaRetrieval` 作为线上实现；
- 重排后再做父记录/相邻块扩展；
- `RetrievedChunk` 保留 dense、lexical、RRF、rerank 诊断字段；
- 引用兼容 FTS-only 结果。

**Phase 1 验收：**

- FTS-only 精确术语可以进入聊天答案来源；
- reranker 失败时聊天仍使用 RRF 正常回答；
- 现有 TXT/Markdown/JSON 入库与聊天回归测试通过；
- 全量后端测试、前端类型检查与构建通过。

```powershell
.venv/Scripts/python.exe -m pytest -q
Set-Location web
npx vue-tsc --noEmit
npm run build
```

**STOP：Phase 1 未通过，不开始专家能力。**

---

## Phase 2：生成高质量原始证据数据

### Task 2.1：定义离线证据模型

**仓库：** `knowledge-boll-crawler`

**建议创建：**

- `src/evidence_models.py`
- `src/evidence_export.py`
- `src/run_prepare_rag.py`
- `tests/test_evidence_export.py`
- `tests/fixtures/evidence/`

外部接口保持简单：

```text
prepare_evidence(raw_posts_dir, raw_questions_dir, expert_identity, split_config)
  -> EvidenceExportResult
```

返回结果包含记录流、转换问题、源快照和集合划分；调用方不需要了解帖子/问答/评论的内部解析细节。

### Task 2.2：作者身份与稳定 ID

- 问答使用平台 `topic_id`、`comment_id` 和仲帅平台 `user_id`。
- 精选帖优先回填平台 ID；回填前使用内容哈希 legacy ID。
- 无评论 ID 使用父记录、作者、时间和文本哈希。
- 相同输入重复执行必须产生相同 ID 和相同记录顺序。
- 作者无法可靠确定时标记 `identity_confidence`，不得默认视为专家。

### Task 2.3：上下文重建

必须覆盖：

1. 普通长帖语义段落；
2. 帖内多个“问/答”的原子拆分；
3. 问答频道的完整“用户问题 + 仲帅回答”；
4. “帖子主题 + 被回复内容 + 仲帅回复”；
5. 多轮追问的最短必要上下文；
6. 普通成员评论保留 raw，但默认不独立索引；
7. 图片/视频无文本时输出媒体缺失记录而不建立文本块。

同一正文不得同时以全文块和多个子问答重复占据索引。

### Task 2.4：平台标记和文本投影

实现确定性转换：

- 解码 `<e type="text_bold" ...>`；
- 网页标签保留可读标题和 URL；
- 规范化空格与换行；
- 跳过纯表情；
- 保留 9610、9810、SKU、ROI、FBA、金额、比例和日期；
- 不用 LLM 润色原文。

分别生成 `content`、`embedding_text`、`lexical_title`、`lexical_content` 和 `lexical_keywords`。

### Task 2.5：主题级数据集划分

在任何画像或规则生成前，以完整主题为单位生成 train/dev/holdout 清单。同一主题的正文、回答和全部评论不可跨集合。

建议产物：

```text
data/processed/evidence_records.jsonl
data/processed/evidence_report.json
data/processed/manifest.json
data/eval/train_topics.json
data/eval/dev_topics.json
data/eval/holdout_topics.json
```

具体比例可配置，默认 70/15/15；人工黄金集从 holdout 中选择，不把 holdout 内容用于画像或规则提炼。

### Task 2.6：通用 JSON 映射与入库回归

生成一个知识星球参考映射文件，但不得在 RAG 解析器中硬编码。使用现有 JSON/JSONL MappingDefinition 将 `EvidenceRecord` 映射为 `MappedRecord` 和 `IndexChunk`。

验证：

- 每个索引记录能回到 `source_pointer`；
- QA 和回复上下文使用 `atomic`；
- 精确术语进入 FTS；
- 普通成员评论不作为专家事实独立命中；
- 空文本不入索引；
- 无 ID 冲突和重复内容块。

**Phase 2 验收：**

- 格式校验 100% 通过；
- 所有可索引记录有稳定 ID、来源和作者角色；
- 转换错误均进入报告，无静默丢失；
- raw 文件一个字节不改。

---

## Phase 3：建立普通 RAG 基线

### Task 3.1：专家数据评测集

**仓库：** `rag-database`

复用 ADR-0024 的质量验收框架，新增独立专家数据集，不改变原有通用验收集。至少包含：

- 25 条事实题；
- 25 条条件判断题；
- 20 条新场景迁移题；
- 10 条材料不足题；
- 10 条冲突/时效题；
- 10 条多轮追问题。

每题记录：问题、允许的答案要点、禁止推断、期望证据 `record_id`、日期要求和拒答预期。

### Task 3.2：记录检索与生成基线

记录：

- dense-only；
- dense + FTS + RRF；
- dense + FTS + RRF + reranker。

只有真实混合检索相对 dense-only 稳定提升，才能作为后续 A/B/C 基线。

**Phase 3 门槛：**

- 事实与引用准确率 ≥95%；
- 关键证据 Recall@5 ≥90%；
- 精确术语 Recall@5 ≥95%；
- 无依据问题正确拒答率 ≥90%；
- 严重虚构和错误作者归属均为 0。

**STOP：普通 RAG 基线未通过，不实现 voice_profile。**

---

## Phase 4：通用 ExpertProfile 与公开表达画像

### Task 4.1：数据库模型与迁移

**仓库：** `rag-database`

**建议文件：**

- 修改 `rag-service/src/db/models.py`
- 创建 `rag-service/src/db/migrations/versions/0008_expert_profile.py`
- 创建 `rag-service/src/expert/models.py`
- 创建 `rag-service/src/expert/profiles.py`
- 创建 `rag-service/tests/test_expert/test_profiles.py`
- 修改 `rag-service/tests/test_db/test_migration.py`

模型：

```text
ExpertProfile
  id, owner_user_id, key, display_name, status, timestamps

ExpertProfileVersion
  id, profile_id, version, identity_notice, domain_scope,
  voice_profile_json, source_snapshot, sample_count,
  generation_model, prompt_version, review_status, created_at

Conversation
  expert_profile_version_id nullable
```

版本一旦被对话绑定即不可修改；修改画像创建新版本。专家配置独立于知识库，对话同时选择 profile version 与 KB 集合。

### Task 4.2：专家配置导入与查询接口

**建议修改/创建：**

- `rag-service/src/api/router_expert.py`
- `rag-service/src/api/app.py`
- `rag-service/tests/test_api/test_expert.py`
- `rag-service/tests/test_api/test_chat.py`

建议端点：

```text
GET  /api/expert-profiles
POST /api/expert-profiles
GET  /api/expert-profiles/{id}/versions
POST /api/expert-profiles/{id}/versions
```

创建对话请求增加可选 `expert_profile_version_id`。必须校验 profile version 与所有 KB 都属于当前用户；历史对话返回绑定的 profile 名称与版本。

### Task 4.3：PromptComposer

**建议修改/创建：**

- 创建 `rag-service/src/expert/prompt.py`
- 修改 `rag-service/src/engine/prompt.py`
- 修改 `rag-service/src/engine/query.py`
- 修改 `rag-service/tests/test_engine/test_prompt.py`
- 修改 `rag-service/tests/test_engine/test_query.py`

小接口：

```text
render_expert_prompt(profile_version) -> ExpertPromptFragment
```

片段只能增加身份、领域、公开表达和禁止事项，不能移除基础安全、来源边界、引用和拒答规则。检索内容继续放在 `<untrusted-source>` 中。

身份声明示例：

```text
你是“仲帅AI助手”，基于已授权的公开教学与答疑材料生成，不是仲帅本人。
```

不得在没有检索证据时用模型常识补成“仲帅的观点”。

### Task 4.4：前端专家选择

**建议修改：**

- `web/src/api/chat.ts`
- `web/src/views/ChatView.vue`
- 新建或复用 ExpertProfile API 类型文件
- 相应前端测试

功能：

- 创建对话时可选“普通助手”或某个专家配置版本；
- 已有对话显示绑定的专家名称；
- 专家对话显示 AI 身份说明；
- 不在消息气泡中假装真人账号；
- 切换专家创建新对话，不悄悄修改已有对话版本。

### Task 4.5：离线生成 voice_profile 草稿

**仓库：** `knowledge-boll-crawler`

**建议创建：**

- `src/distillation/models.py`
- `src/distillation/client.py`
- `src/distillation/voice_profile.py`
- `src/run_voice_profile.py`
- `tests/test_voice_profile.py`
- `third_party/colleague-skill-LICENSE.txt`（仅在实际复制上游内容时）

流程：

```text
train 集第一人称回答
  -> 按场景和长度分层采样
  -> 统计特征
  -> 云端 LLM 生成有证据的画像草稿
  -> JSON schema 校验
  -> 人工审核
  -> voice_profile.json
```

LLM 客户端通过 provider-neutral 接口与测试 fake 隔离；密钥来自环境变量，不写文件或日志。可以参考固定提交的 `colleague-skill` 提示词，但输出必须符合本项目 schema。

画像只使用 train 集；不得读取 dev/holdout 答案。每条画像结论至少绑定多个样本 ID，并通过人工审核后才能导入 RAG。

### Task 4.6：A/B 评测

```text
A：普通 RAG
B：普通 RAG + voice_profile
```

使用同模型、温度、检索结果和上下文预算。人工盲评公开答疑风格，不做欺骗性真人识别测试。

**Phase 4 门槛：**

- B 风格评分显著高于 A；
- B 事实准确率相对 A 下降 ≤2 个百分点；
- 严重虚构、身份冒充、私人人格补全均为 0；
- “刻意模仿/夸张口癖”比例 <5%。

通过后即可发布不含专家规则的“仲帅AI助手”MVP。

---

## Phase 5：专家规则小规模试点（条件执行）

### Gate

只有 Phase 4 通过，且用户确认需要进一步提升“判断方法”时才执行。不得因为已有早期 `work.md` 就跳过 Gate。

### Task 5.1：离线规则候选

**仓库：** `knowledge-boll-crawler`

从 train 集提取 30–50 条高价值候选，覆盖店铺诊断、选品、新品、定价、活动、广告、物流、侵权和税务。

候选类型：

```text
diagnostic_workflow
decision_rule
action_sequence
checklist
risk_boundary
exception
clarifying_question
```

单条候选必须返回适用问题、required inputs、条件、步骤、禁止动作、例外、时效和 evidence IDs。

### Task 5.2：证据独立性、冲突和审核

- 同一帖子及其评论只算一个证据家族；
- 重复保存的同一平台主题只算一次；
- 普通成员评论不能单独支持规则；
- 对支持、限定和反驳证据分别记录；
- 政策类记录观察日期和复核日期；
- 所有发布规则人工审核。

输出：

```text
data/derived/expert_rules.jsonl
data/derived/rule_provenance.jsonl
data/derived/rule_manifest.json
```

### Task 5.3：离线 B/C 评测

在尚未实现线上规则 lane 前，通过评测 harness 将 Top 3 规则和对应证据显式注入上下文：

```text
B：证据 + voice_profile
C：证据 + voice_profile + expert_rules
```

只有 C 的新场景判断路径和关键条件保留显著优于 B，且事实准确率不下降，才进入 Phase 6。

**STOP：C 未显著优于 B，保留 MVP，不建设线上规则模块。**

---

## Phase 6：线上规则 lane 与证据展开（条件执行）

本阶段必须先补一份细化设计，明确 RuleEvidence 的持久化与按 `record_id` 精确取证接口。不得简单把 `evidence_ids` 拼进可见文本并假设模型会正确引用。

最低要求：

- 规则与证据多对多持久化；
- `RetrievedChunk` 暴露稳定 `record_id`；
- 规则 lane 只检索 `supported + approved + fresh + published`；
- 命中规则后按 ID 展开支持证据；
- 无支持证据时丢弃规则；
- 规则上下文不进入用户可见引用列表；
- 最终引用只指向原始证据；
- 过期、争议和被替代规则不能提供确定性建议。

本阶段仍然是固定两路 RAG，不得引入 Agent。

---

## 最终验收清单

### 功能

- [ ] 普通知识库行为保持兼容。
- [ ] 对话可选择普通模式或 ExpertProfileVersion。
- [ ] 专家身份说明始终可见。
- [ ] 专家回答事实均有原始证据引用。
- [ ] 无材料时明确拒答，不补写“仲帅会怎么想”。
- [ ] FTS-only 和 dense-only 候选都能进入融合。
- [ ] reranker 不可用时安全降级。
- [ ] 旧对话保持绑定的专家版本。

### 数据

- [ ] raw 数据未被修改。
- [ ] EvidenceRecord ID 稳定且无冲突。
- [ ] 每条证据有来源指针和作者角色。
- [ ] QA 和短回复不丢上下文。
- [ ] 普通成员评论不冒充专家证据。
- [ ] 媒体缺失明确记录。
- [ ] train/dev/holdout 无主题泄漏。

### 安全

- [ ] 跨用户、跨知识库和跨 ExpertProfile 泄漏为 0。
- [ ] 检索内容不能覆盖系统 Prompt。
- [ ] API 不返回模型密钥。
- [ ] 日志不包含完整付费材料。
- [ ] 没有 Agent、工具调用或 Skill 加载入口。

### 质量

- [ ] 事实与引用准确率 ≥95%。
- [ ] 精确术语 Recall@5 ≥95%。
- [ ] 关键条件保留率 ≥90%。
- [ ] 无依据问题拒答率 ≥90%。
- [ ] 严重虚构、错误作者归属和身份冒充均为 0。
- [ ] voice_profile 带来显著风格提升且准确率下降 ≤2 个百分点。
- [ ] expert_rules 仅在 C 显著优于 B 时上线。

## 明确不执行

- 不实现 `SKILL.md` 或 `work.md` 加载。
- 不把 `colleague-skill` 作为生产依赖。
- 不实现工具调用、自动店铺操作或多步 Agent。
- 不把完整 Persona 或私人人格字段加入 ExpertProfile。
- 不删除或覆盖原始付费材料。
- 不在没有评测证据时一次性全量提炼全部规则。
