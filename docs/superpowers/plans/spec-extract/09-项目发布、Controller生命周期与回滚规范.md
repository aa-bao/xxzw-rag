# 规范契约提取报告：项目发布、Controller 生命周期与回滚规范

- **源文档**：`E:\dev\project\project-development-specification\项目开发\09-项目发布、Controller生命周期与回滚规范.md`
- **提取日期**：2026-08-12
- **提取范围**：100% 覆盖全部强制要求（必须 / 禁止 / 不得 / 应当 / 不应 / MUST 等），全部具体值逐字保留
- **适用对象**：独立项目应用的测试发布、生产发布、升级、回滚、停用、卸载和数据清除

---

## 1. 目的（第 1 章）

- 本规范定义独立项目应用如何由**主系统**和 **Project Platform Controller** 完成：测试发布、生产发布、升级、回滚、停用、卸载和数据清除。
- 目标强制要求：项目发布**必须**可恢复、可审计、可重放验证，**不得**依赖服务器现场命令和个人记忆。

## 2. 唯一事实来源（第 2 章）

逐字事实来源分层：

```text
主系统数据库
  项目期望版本、组件版本、环境、启停状态、配置版本、操作和审计

制品库
  不可变镜像、Windows 执行器制品、Digest、签名、SBOM 和 CI 证明

Controller
  对账期望状态与服务器实际状态，不产生新的业务期望
```

强制要求：

- **MUST-2.1**：服务器现场容器、目录、Nginx 文件和人工启动进程**都不是**事实来源。
- **MUST-2.2**：人工修改**不得**反向覆盖主系统期望状态。
- **MUST-2.3**：Controller 的职责是"对账期望状态与服务器实际状态"，**不产生新的业务期望**。

## 3. 发布输入（第 3 章）

- **MUST-3.1**：一次发布**必须冻结**以下全部输入（逐字字段清单）：

```text
appKey
environment: TEST | PRODUCTION
sourceCommitSha
rpaApplicationManifestHash
component releases
  componentKey
  componentType
  version
  imageDigest 或 artifactDigest
  sha256
  signature
  sbomDigest
  ciEvidenceDigest
contract hashes
migration checksum
secret configuration version
expectedVersion
idempotencyKey
```

- **MUST-3.2**（禁止）：**禁止**使用分支、Tag、远端 HEAD、浮动镜像标签或服务器现有目录替代不可变 Digest。

## 4. CI 交付（第 4 章）

- **MUST-4.1**：Pull Request 合入受保护 `main` 后，CI **必须**依次执行 7 步：
  1. 校验根清单和所有组件清单。
  2. 执行单元、集成、契约和安全测试。
  3. 构建前端镜像、后端镜像和各 Windows 执行器制品。
  4. 生成组件 SHA256、签名、SBOM 和依赖扫描结果。
  5. 生成 OpenAPI、JSON Schema 和权限摘要。
  6. 把不可变制品推送到公司制品库。
  7. 向主系统登记候选 Release，**不直接部署生产**。
- **MUST-4.2**（禁止）：项目开发机**不得**成为生产构建来源。

### 4.1 CI 全绿不等于已上线

- **MUST-4.1.1**：第 7 步登记完成后，Release 停在 `REGISTERED`，**不会自己变成运行版本**。
- **MUST-4.1.2**：要上线**必须**由平台侧**显式发起**一次 `RELEASE` 操作，且需要 `rpa:project-application:deploy` 权限；走完 **12 步部署状态机**之后，应用的实际版本才会跟上。
- **MUST-4.1.3**：**没有任何东西会提醒你有 Release 在排队**。登记成功和部署成功都是"成功"，但它们是两件事；积压的候选版本可以在 `REGISTERED` 上停留几天而不产生任何告警。
- **MUST-4.1.4**：验收或联调之前，**先确认实际运行版本**，不要用 CI 结论替代。看「独立应用列表」的期望版本与实际版本是否都等于要验的那个 commit，或查 `GET /api/rpa/project-applications/{id}` 的 `expectedRelease` / `actualRelease`。
- **MUST-4.1.5**：项目侧完成 CI 后**应当**明确说明版本尚未发布，把发布动作交给平台侧，而不是默认它已经发生。
- **MUST-4.1.6**：平台侧发起 `RELEASE` 前先看一眼有没有更早的候选被跳过；跳过通常意味着某次交接漏了。

## 5. Controller 持久化对象（第 5 章）

- **MUST-5.1**：Controller **至少**依赖以下主系统对象（逐字清单）：

```text
ProjectApplication
ComponentRelease
DeploymentOperation
DeploymentStep
MigrationExecution
SecretVersion
RouteBinding
ControllerLease
ReconciliationResult
```

- **MUST-5.2**：操作状态**统一为**（逐字枚举）：

```text
PENDING / CLAIMED / RUNNING / SUCCEEDED / FAILED / UNKNOWN / CANCELLED
```

- **MUST-5.3**：每个操作和步骤**必须**有：稳定幂等键、期望版本、租约、输入摘要、开始/结束时间和脱敏结果。

## 6. TEST 发布流程（第 6 章）

完整 12 步流程（逐字）：

```text
管理员点击"发布测试"
  -> 主系统校验候选 Release、权限和环境
  -> Controller 获取操作租约
  -> 校验 Digest、签名、SBOM、CI 证明和契约 Hash
  -> 创建或确认 TEST Schema、账号和密钥版本
  -> 备份目标 Schema
  -> 在隔离迁移容器执行迁移
  -> 部署未变化组件复用、变化组件替换
  -> 生成 TEST 路由和证书绑定
  -> readiness、health、version 检查
  -> 同步项目权限、任务类型和动态入口
  -> 运行统一黑盒测试
  -> 标记 TEST Release 可验收
```

强制要求：

- **MUST-6.1**：任何步骤失败时**停止后续步骤**。
- **MUST-6.2**：数据库迁移结果未知时进入 `UNKNOWN`。
- **MUST-6.3**（禁止）：**禁止**重新执行整次发布（迁移结果未知场景）。

## 7. PRODUCTION 发布流程（第 7 章）

生产发布前**必须**满足（前置门禁，逐字）：

- 同一不可变候选已通过 TEST。
- TEST 黑盒门禁和项目业务 Smoke 已通过。
- 没有未处置的 `UNKNOWN` 发布、迁移或数据清除操作。
- 生产环境容量、Schema、密钥和节点能力满足清单要求。
- 变更窗口内没有与发布冲突的迁移或项目生命周期操作。

生产发布顺序（逐字 9 步）：

1. 冻结生产目标 Release 和当前回滚点。
2. 备份生产 Schema 并保存校验摘要。
3. 执行向后兼容迁移。
4. 部署后端并通过 readiness。
5. 部署前端和路由。
6. 登记 Windows 执行器不可变制品和兼容节点要求。
7. 校验动态入口、SSO、权限和只读接口。
8. 使用无副作用任务完成 Smoke。
9. 更新主系统期望 Release。

强制要求：

- **MUST-7.1**：以上 5 项前置条件**必须全部满足**才能开始生产发布。
- **MUST-7.2**：生产发布**必须**按上述 9 步顺序执行。
- **MUST-7.3**：发布期间旧版本**必须保留**到新版本健康和 Smoke 完成。

## 8. 组件级发布单元（第 8 章）

- **MUST-8.1**：前端、后端和各执行器**独立**构建、部署和回滚。
- **MUST-8.2**：未变化组件**复用**已有不可变制品。
- **MUST-8.3**：根清单、共享代码、权限、Task 契约或跨组件接口变化时，**必须执行整个项目门禁**。
- **MUST-8.4**：一个组件失败**不能**把其他组件伪装为成功；项目 Release **记录**每个组件实际状态。
- **MUST-8.5**：Windows 执行器升级只**登记新制品**，不要求普通项目升级 Agent。

## 9. 对账和恢复（第 9 章）

Controller 每次启动后**必须**依次执行（逐字 6 步）：

1. 领取有效租约。
2. 恢复未完成 `DeploymentOperation`。
3. 从最后成功 `DeploymentStep` 继续。
4. 检查容器、路由、版本、健康、密钥和迁移事实。
5. 只重试明确无副作用或可验证已完成情况的步骤。
6. 无法确认结果时保持 `UNKNOWN` 并等待人工处置。

强制要求：

- **MUST-9.1**：上述 6 步为 Controller 每次启动后的强制流程。
- **MUST-9.2**（禁止）：**禁止**使用"重新执行整次发布"替代步骤级恢复。
- **MUST-9.3**：Controller 暂停只影响发布和生命周期操作，**不应**停止已运行项目或 Windows Task。

## 10. 回滚（第 10 章）

- **MUST-10.1**：回滚**必须**选择**历史成功的完整 Release**，并冻结以下内容（逐字）：

```text
目标组件 Digest
目标 Commit 和根清单 Hash
兼容的数据库 Schema 版本范围
权限和任务契约版本
回滚原因和操作人
```

回滚规则：

- **MUST-10.2**：代码和容器**可以**回滚，数据库**默认不执行降级脚本**。
- **MUST-10.3**：数据库变更**必须**提前采用 Expand/Contract 保持旧版本兼容。
- **MUST-10.4**：回滚后**重新执行** health、readiness、version、SSO 和最小业务 Smoke。
- **MUST-10.5**（禁止）：被 Task、重试窗口、审计或回滚引用的制品**不得删除**。

## 11. 环境隔离（第 11 章）

- **MUST-11.1**：TEST 和 PRODUCTION **必须分别拥有**（逐字清单）：

```text
容器和路由
Schema 和数据库运行账号
服务身份和 HMAC 密钥
项目会话密钥
配置和 SecretVersion
Windows Task、节点池和执行器制品登记
回调地址和 Task JWT audience
日志、指标和告警标签
```

- **MUST-11.2**（禁止）：测试服务身份**不能**创建生产 Task，测试 Task **不能**进入生产节点，两个环境的 JWT 和回调**必须互相拒绝**。

## 12. 资源限制（第 12 章）

- **MUST-12.1**：每个服务端组件**必须声明并由容器运行时强制执行**（逐字 5 项）：

```text
CPU
内存且不额外使用宿主机 Swap
PID 上限
临时可写盘大小
只读根文件系统
```

- **MUST-12.2**（禁止）：项目**不得**写死服务器地址，也**不得**要求宿主机安装项目专用全局依赖。

## 13. 生命周期操作（第 13 章）

### 13.1 停用 `DISABLE`

- 关闭项目入口和新任务。
- 保留服务、Schema、配置、制品和审计。
- **不强制终止**已运行 Task。
- **可以恢复**。

### 13.2 卸载 `UNINSTALL`

- 停止并删除在线容器、路由和在线运行密钥。
- 保留 Schema、备份、Release、Task 和审计。
- **必须先停止新任务**并等待活动 Task、Attempt 和租约清零。

### 13.3 数据清除 `PURGE`

- **独立于**普通删除按钮。
- **只有**停用和卸载完成后才能发起。
- 输入项目名称**二次确认**并展示影响摘要。
- 经过**冷静期**后清除 Schema、项目密钥和无引用制品。
- 操作**不可恢复**。
- **MUST-13.1**（禁止）：存在**活动 Task、待提交业务结果、有效租约、未知操作或缺少最终备份**时必须**拒绝清除**。

## 14. 制品保留（第 14 章）

- **MUST-14.1**：每个组件保留**最近 5 个**成功生产版本。
- **MUST-14.2**：未发布或失败候选保留**至少 14 天**。
- **MUST-14.3**：被历史 Task、人工重跑、回滚窗口或审计引用的制品**继续保留**。
- **MUST-14.4**（禁止）：清理**必须**由主系统计算引用关系，**禁止**服务器目录脚本按时间直接删除。

## 15. 项目开发者不得执行（第 15 章）

全部为禁止性强制要求（逐字）：

- **MUST-15.1**：**不得**登录生产服务器手工替换容器或目录。
- **MUST-15.2**：**不得**手工修改主系统项目版本、路由或数据库迁移状态。
- **MUST-15.3**：**不得**强推或重打已经发布的 Commit、Tag 或制品版本。
- **MUST-15.4**：**不得**使用 `latest` 等浮动镜像标签发布。
- **MUST-15.5**：**不得**把生产 `.env`、数据库密码或服务身份放入仓库。
- **MUST-15.6**：**不得**通过直接调用 Agent 或节点脚本绕过主系统发布。

## 16. 发布完成定义（第 16 章）

发布**只有同时满足以下条件才成功**（逐字 6 条）：

- 所有预期组件运行版本与主系统期望一致。
- 迁移、路由、密钥和权限同步均有成功记录。
- health、readiness、version 和通用黑盒门禁通过。
- 项目动态入口可见且 SSO、权限、部门数据范围正确。
- Windows 执行器制品可由符合能力的 Agent 校验，但未被发布流程擅自启动。
- 回滚点、审计、日志和制品引用完整。

---

## 附录 A：全部"禁止/不得/不能"类规则（汇总）

1. **§3**：禁止使用分支、Tag、远端 HEAD、浮动镜像标签或服务器现有目录替代不可变 Digest。
2. **§4**：CI 向主系统登记候选 Release，不直接部署生产。
3. **§4**：项目开发机不得成为生产构建来源。
4. **§6**：数据库迁移结果未知时进入 `UNKNOWN`，禁止重新执行整次发布。
5. **§9**：禁止使用"重新执行整次发布"替代步骤级恢复。
6. **§9**：Controller 暂停不应停止已运行项目或 Windows Task。
7. **§10**：数据库默认不执行降级脚本。
8. **§10**：被 Task、重试窗口、审计或回滚引用的制品不得删除。
9. **§11**：测试服务身份不能创建生产 Task，测试 Task 不能进入生产节点，两个环境的 JWT 和回调必须互相拒绝。
10. **§12**：项目不得写死服务器地址，也不得要求宿主机安装项目专用全局依赖。
11. **§12**：内存限制且不额外使用宿主机 Swap（不得借 Swap 逃避内存限制）。
12. **§13.1**：DISABLE 不强制终止已运行 Task。
13. **§13.2**：UNINSTALL 必须先停止新任务并等待活动 Task、Attempt 和租约清零（此前不得进入卸载）。
14. **§13.3**：PURGE 只有停用和卸载完成后才能发起；存在活动 Task、待提交业务结果、有效租约、未知操作或缺少最终备份时必须拒绝清除。
15. **§14**：清理必须由主系统计算引用关系，禁止服务器目录脚本按时间直接删除。
16. **§15.1**：不得登录生产服务器手工替换容器或目录。
17. **§15.2**：不得手工修改主系统项目版本、路由或数据库迁移状态。
18. **§15.3**：不得强推或重打已经发布的 Commit、Tag 或制品版本。
19. **§15.4**：不得使用 `latest` 等浮动镜像标签发布。
20. **§15.5**：不得把生产 `.env`、数据库密码或服务身份放入仓库。
21. **§15.6**：不得通过直接调用 Agent 或节点脚本绕过主系统发布。
22. **§16**：Windows 执行器制品可被符合能力的 Agent 校验，但未被发布流程擅自启动。
23. **§2**：人工修改不得反向覆盖主系统期望状态。
24. **§8**：一个组件失败不能把其他组件伪装为成功。

## 附录 B：交叉引用（文档提到的其他文件 / 接口 / 概念）

| 引用对象 | 类型 | 引用目的 |
|---|---|---|
| `GET /api/rpa/project-applications/{id}` | API 路径 | 查询应用的 `expectedRelease` / `actualRelease`，确认实际运行版本（§4.1） |
| `rpa:project-application:deploy` | 权限名 | 平台侧发起 `RELEASE` 操作所需权限（§4.1） |
| `main`（受保护分支） | 分支 | Pull Request 合入受保护 `main` 后触发 CI（§4） |
| `REGISTERED` | 候选 Release 状态 | 第 7 步登记完成后 Release 所处状态，不会自动成为运行版本（§4.1） |
| 12 步部署状态机 | 流程 | `RELEASE` 操作需走完 12 步部署状态机（§4.1） |
| 「独立应用列表」 | 主系统 UI 视图 | 查看期望版本与实际版本是否等于目标 commit（§4.1） |
| Expand/Contract | 数据库迁移模式 | 数据库变更必须提前采用以保持旧版本兼容（§10） |
| 统一黑盒测试 | 测试体系 | TEST 流程第 12 步（§6）、生产前置门禁（§7）、发布完成定义（§16） |
| Nginx 文件 | 服务器现场内容 | 明确排除在事实来源之外（§2） |
| Windows 执行器 / Windows Task | 制品与任务类型 | 独立构建部署回滚、升级只登记新制品、环境隔离、生命周期操作（§4/§8/§11/§13） |

> 注：本规范原文未显式引用其他规范文档文件名；以上为文档内出现的接口、权限、状态机、UI 与模式等引用对象。

## 附录 C：关键数字与具体值索引

| 具体值 | 出处 |
|---|---|
| 状态枚举 `PENDING / CLAIMED / RUNNING / SUCCEEDED / FAILED / UNKNOWN / CANCELLED` | §5 |
| 环境枚举 `TEST \| PRODUCTION` | §3 |
| 权限 `rpa:project-application:deploy` | §4.1 |
| API `GET /api/rpa/project-applications/{id}`，字段 `expectedRelease` / `actualRelease` | §4.1 |
| 12 步部署状态机 | §4.1 |
| TEST 发布流程 12 步 | §6 |
| 生产发布顺序 9 步 | §7 |
| Controller 启动恢复流程 6 步 | §9 |
| CI 7 步交付流程 | §4 |
| 制品保留：最近 5 个成功生产版本 | §14 |
| 未发布/失败候选保留：至少 14 天 | §14 |
| 发布输入冻结字段：18 个（appKey … idempotencyKey） | §3 |
| Controller 持久化对象：9 个（ProjectApplication … ReconciliationResult） | §5 |
| 环境隔离清单：8 项 | §11 |
| 资源限制声明：5 项 | §12 |
| 回滚冻结内容：5 项 | §10 |
| 发布完成定义：6 条 | §16 |

---

## 本文件要点速览（关键强制要求）

1. 事实来源只有三处：主系统数据库（期望版本/组件版本/环境/启停状态/配置版本/操作和审计）、制品库（不可变镜像、Windows 执行器制品、Digest、签名、SBOM、CI 证明）、Controller（仅对账，不产生新业务期望）；服务器现场容器、目录、Nginx 文件和人工启动进程都不是事实来源。
2. 人工修改不得反向覆盖主系统期望状态。
3. 一次发布必须冻结完整输入：`appKey`、`environment: TEST | PRODUCTION`、`sourceCommitSha`、`rpaApplicationManifestHash`、组件发布（`componentKey`/`componentType`/`version`/`imageDigest 或 artifactDigest`/`sha256`/`signature`/`sbomDigest`/`ciEvidenceDigest`）、`contract hashes`、`migration checksum`、`secret configuration version`、`expectedVersion`、`idempotencyKey`。
4. 禁止使用分支、Tag、远端 HEAD、浮动镜像标签或服务器现有目录替代不可变 Digest。
5. CI 在 Pull Request 合入受保护 `main` 后必须 7 步：校验根清单和组件清单 → 单元/集成/契约/安全测试 → 构建前端镜像、后端镜像和 Windows 执行器制品 → 生成 SHA256、签名、SBOM、依赖扫描 → 生成 OpenAPI、JSON Schema、权限摘要 → 推不可变制品到公司制品库 → 向主系统登记候选 Release，不直接部署生产。
6. 项目开发机不得成为生产构建来源。
7. CI 全绿不等于已上线：登记后 Release 停在 `REGISTERED` 不会自己变成运行版本，必须平台侧显式发起 `RELEASE`（需权限 `rpa:project-application:deploy`）并走完 12 步部署状态机。
8. 验收/联调前必须确认实际运行版本：查「独立应用列表」期望版本与实际版本，或 `GET /api/rpa/project-applications/{id}` 的 `expectedRelease` / `actualRelease`。
9. 项目侧完成 CI 后应当明确说明版本尚未发布，把发布动作交给平台侧；平台侧发起 `RELEASE` 前先检查有无更早被跳过的候选。
10. Controller 至少依赖 9 个主系统对象：`ProjectApplication`、`ComponentRelease`、`DeploymentOperation`、`DeploymentStep`、`MigrationExecution`、`SecretVersion`、`RouteBinding`、`ControllerLease`、`ReconciliationResult`。
11. 操作状态统一为 `PENDING / CLAIMED / RUNNING / SUCCEEDED / FAILED / UNKNOWN / CANCELLED`。
12. 每个操作和步骤必须有：稳定幂等键、期望版本、租约、输入摘要、开始/结束时间、脱敏结果。
13. TEST 发布 12 步流程必须依序执行，任何步骤失败时停止后续步骤；迁移结果未知进入 `UNKNOWN`，禁止重新执行整次发布。
14. 生产发布前置门禁 5 项：同一不可变候选已通过 TEST；TEST 黑盒门禁和业务 Smoke 通过；无未处置 `UNKNOWN` 发布/迁移/清除；生产容量、Schema、密钥、节点能力满足清单；变更窗口内无冲突操作。
15. 生产发布 9 步顺序：冻结生产目标 Release 和回滚点 → 备份生产 Schema 并保存校验摘要 → 向后兼容迁移 → 部署后端过 readiness → 部署前端和路由 → 登记 Windows 执行器不可变制品和兼容节点要求 → 校验动态入口、SSO、权限和只读接口 → 无副作用任务完成 Smoke → 更新主系统期望 Release。
16. 发布期间旧版本必须保留到新版本健康和 Smoke 完成。
17. 前端、后端、各执行器独立构建、部署和回滚；未变化组件复用已有不可变制品；根清单、共享代码、权限、Task 契约或跨组件接口变化时必须执行整个项目门禁。
18. 一个组件失败不能把其他组件伪装为成功，项目 Release 记录每个组件实际状态。
19. Windows 执行器升级只登记新制品，不要求普通项目升级 Agent。
20. Controller 每次启动后 6 步：领取有效租约 → 恢复未完成 `DeploymentOperation` → 从最后成功 `DeploymentStep` 继续 → 检查容器、路由、版本、健康、密钥和迁移事实 → 只重试明确无副作用或可验证已完成的步骤 → 无法确认结果保持 `UNKNOWN` 等待人工处置。
21. 禁止用"重新执行整次发布"替代步骤级恢复；Controller 暂停不应停止已运行项目或 Windows Task。
22. 回滚必须选择历史成功的完整 Release，并冻结 5 项：目标组件 Digest、目标 Commit 和根清单 Hash、兼容的数据库 Schema 版本范围、权限和任务契约版本、回滚原因和操作人。
23. 代码和容器可以回滚，数据库默认不执行降级脚本；数据库变更必须提前采用 Expand/Contract 保持旧版本兼容。
24. 回滚后必须重新执行 health、readiness、version、SSO 和最小业务 Smoke。
25. 被 Task、重试窗口、审计或回滚引用的制品不得删除。
26. TEST 和 PRODUCTION 必须分别拥有：容器和路由、Schema 和数据库运行账号、服务身份和 HMAC 密钥、项目会话密钥、配置和 `SecretVersion`、Windows Task/节点池/执行器制品登记、回调地址和 Task JWT audience、日志/指标/告警标签。
27. 测试服务身份不能创建生产 Task，测试 Task 不能进入生产节点，两个环境的 JWT 和回调必须互相拒绝。
28. 每个服务端组件必须声明并由容器运行时强制执行：CPU、内存（不额外使用宿主机 Swap）、PID 上限、临时可写盘大小、只读根文件系统。
29. 项目不得写死服务器地址，也不得要求宿主机安装项目专用全局依赖。
30. `DISABLE` 停用：关闭项目入口和新任务，保留服务/Schema/配置/制品/审计，不强制终止已运行 Task，可以恢复。
31. `UNINSTALL` 卸载：停止并删除在线容器、路由和在线运行密钥，保留 Schema/备份/Release/Task/审计；必须先停止新任务并等待活动 Task、Attempt 和租约清零。
32. `PURGE` 清除：独立于普通删除按钮，只有停用和卸载完成后才能发起，输入项目名称二次确认并展示影响摘要，经冷静期后清除，操作不可恢复；存在活动 Task、待提交业务结果、有效租约、未知操作或缺少最终备份时必须拒绝。
33. 制品保留：每个组件保留最近 5 个成功生产版本；未发布或失败候选保留至少 14 天；被引用制品继续保留。
34. 制品清理必须由主系统计算引用关系，禁止服务器目录脚本按时间直接删除。
35. 项目开发者不得：登录生产服务器手工替换容器或目录；手工修改主系统项目版本、路由或数据库迁移状态；强推或重打已发布的 Commit/Tag/制品版本；使用 `latest` 等浮动镜像标签发布；把生产 `.env`、数据库密码或服务身份放入仓库；通过直接调用 Agent 或节点脚本绕过主系统发布。
36. 发布完成必须同时满足 6 条：所有预期组件运行版本与主系统期望一致；迁移/路由/密钥/权限同步均有成功记录；health、readiness、version 和通用黑盒门禁通过；项目动态入口可见且 SSO、权限、部门数据范围正确；Windows 执行器制品可由符合能力的 Agent 校验但未被发布流程擅自启动；回滚点、审计、日志和制品引用完整。
