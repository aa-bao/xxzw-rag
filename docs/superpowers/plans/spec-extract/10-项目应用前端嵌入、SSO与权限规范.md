# 规范契约提取报告：10-项目应用前端嵌入、SSO与权限规范

> 源文件：`E:\dev\project\project-development-specification\项目开发\10-项目应用前端嵌入、SSO与权限规范.md`
> 提取日期：2026-08-12
> 提取方式：Read 全量读取（共 399 行，一次读完），按文档章节逐条提取全部强制要求（必须/禁止/不得/应当/不应/只能/固定等约束条目），具体值逐字保留。
> 覆盖说明：本报告覆盖文档 1~14 全部章节，含所有代码块原文（路径、JS、JSON、YAML、流程图）。

---

## 1. 目的（§1）

- 【应当】独立项目无需复制主系统登录、用户、部门、角色和固定菜单代码，即可安全嵌入主系统。
- 【必须】运营人员只看到被授权项目和本部门业务数据。

---

## 2. 通用入口（§2）

- 【必须】主系统只维护一个通用项目容器组件，项目通过两条路径到达同一个容器（原文）：

```text
/rpa-console/project-app/:appKey                     项目列表卡片跳转
/rpa-console/project-app-<appKey>/workbench?appKey=  自动生成的侧边栏菜单
```

- 【必须】项目发布成功后由 Controller 自动登记 `appKey`、名称、图标、入口、健康地址、权限和显示顺序。
- 【禁止】普通新增或升级项目不得要求重新构建主系统前端或新增固定 Vue 路由。

项目可见规则（同时满足才可见）：
- 【必须】项目已启用且健康。
- 【必须】用户具备 `<appKey>:project:view`。
- 【必须】用户所在租户和项目授权范围匹配。
- 【必须】`PLATFORM_EXECUTOR` 等平台项目只对管理员显示。
- 【必须】入口可见不等于拥有全部业务数据权限（可见性与数据权限分离）。

### 2.1 自动生成的侧边栏入口（§2.1）

- 【必须】发布成功后主系统自动为项目投影可见菜单，形态与手工维护的业务项目一致。

未声明 `navigation` 时（默认形态，原文）：

```text
自动化项目 (rpa-console)
└── <项目显示名>              目录，menu_type=M
    └── 工作台                页面，menu_type=C
```

声明了 `navigation` 时（规范 08 §6.1）：【必须】按声明的树逐层投影；【必须】项目自身侧边栏在嵌入时隐藏，避免双层导航。

- 【必须】`status: PLANNED` 的节点声明了但不投影；项目实现后改为 `AVAILABLE` 重新发布即出现。

投影规则：
- 【必须】目录默认落在 `自动化项目` 下，名称取根清单 `displayName`，图标取 `icon`。
- 【必须】页面复用通用容器组件，项目身份写入 `sys_menu.query_param` 的 `{"appKey":"<appKey>"}`，因此接入新项目不需要重新构建主系统前端。
- 【必须】页面挂 `<appKey>:project:view`，无该权限的用户看不到入口。
- 【必须】权限点（`menu_type=F`）仍投影在隐藏的权限根下，只用于角色勾选，不参与导航。

目录搬移语义：
- 【必须】目录的 `menu_id` 持久化在 `rpa_project_application.sys_menu_id`，后续发布按该 ID 定位并原地更新，不按原父节点位置查找。
- 【必须】管理员把整个目录移入其他目录（例如新建的 BOSS 目录）后，再次发布不会把它搬回，也不会在原位置留下重复副本。

- 【禁止】项目不得自行在主系统创建、修改或删除菜单行；菜单是发布投影的产物。

---

## 3. 路由与域名（§3）

- 【禁止】项目不得写死生产地址；Controller 根据环境生成受控路由；【必须】浏览器访问统一使用 HTTPS。

前端必须：
- 【必须】使用相对静态资源和 API 路径。
- 【必须】在 `rpa-application.yaml` 声明 `basePathMode: RELATIVE`。
- 【必须】支持被同源或受控同站点反向代理路径加载。
- 【禁止】从任务输入、用户输入或数据库字段生成任意 iframe URL。
- 【必须】健康和嵌入地址只能来自 Controller 路由登记；【禁止】主系统请求项目提交的任意 URL，避免 SSRF。

### 3.1 挂载布局（§3.1）

- 【必须】应用根是 `<base>/<appKey>/`。同一个应用有两条路由，落点不同（原文表格）：

| 外部地址 | 转发到 | 说明 |
|---|---|---|
| `<应用根>/` | 前端容器 `/` | 前缀被剥掉；前端另需自代理 `/api/` 与 `/platform/` |
| `<应用根>/api/` | 后端容器 `/api/` | **`/api` 段必须保留，不能剥掉** |

- 【必须】后端在自己的根上同时提供两类路径：
  - `/platform/*` —— 平台契约端点（`health`、`readiness`、`version`、`session`、`sso/bootstrap`）
  - `/api/*` —— 项目业务接口
- 【必须】`/platform/*` 不在后端路由的挂载范围内（后端路由只覆盖 `<应用根>/api/`），因此它**只能经前端路由到达**：`<应用根>/platform/health` → 前端 → 后端 `/platform/health`；这也是前端必须自代理 `/platform/` 的原因。

项目侧写法（两者都相对应用根，原文，见 08 §7）：

```js
fetch('./api/boss/shop-data', { credentials: 'same-origin' })   // 业务，走后端路由
fetch('./platform/session',   { credentials: 'same-origin' })   // 平台契约，走前端路由
```

平台侧不变量：
- 【必须】后端路由的 `proxy_pass` 必须以 `/api/` 结尾。写成 `/` 会把整个匹配前缀替换掉，于是 `<应用根>/api/boss/shop-data` 变成后端的 `/boss/shop-data`，所有业务接口 404。
- 【必须】健康探针必须打在前端路由上（`<应用根>` + `healthPath`），不能打在后端路由上；打成 `<应用根>/api/platform/health` 会请求后端并不存在的 `/api/platform/health`。

- 【必须】必测场景要求经路由验证业务接口（黑盒门禁直连容器、绕过路由验不出此类问题），见 §13。

---

## 4. 嵌入模式（§4）

- 【必须】项目入口支持 `embedded=true`。

嵌入时项目应隐藏：
- 【必须】自己的全局侧边栏。
- 【必须】重复的公司 Logo 和顶层导航。
- 【必须】独立登录、退出和角色管理入口。
- 【允许】项目仍可保留项目内部标签页、业务导航、筛选器和详情页。

- 【必须】主系统 iframe 使用最小 `sandbox` 和 `allow` 权限。
- 【禁止】项目不得访问顶层 DOM、主系统 Cookie、LocalStorage 或其他项目 iframe。

---

## 5. SSO 流程（§5）

- 【禁止】项目不能读取或保存主系统 JWT。

登录流程固定为（原文，步骤逐字保留）：

```text
用户点击项目
  -> 主系统生成 60 秒一次性授权码、state 和 PKCE challenge
  -> 通用容器以受控 POST 或短期 Fragment 打开项目
  -> 项目后端携带服务身份、code verifier 和授权码兑换身份
  -> 主系统返回最小用户、租户、部门、数据范围和权限声明
  -> 项目建立独立 HttpOnly 会话
  -> 前端只使用项目会话访问项目后端
```

授权码必须绑定（字段逐字保留）：

```text
appKey
userId
tenantId
redirectUri
state
codeChallenge
expiresAt
singleUse
```

- 【必须】授权码兑换后立即失效，失败重试不能重新激活。
- 参数清单：授权码一次性（singleUse）、有效期 60 秒（expiresAt）、`state`、PKCE `codeChallenge`/`code verifier`、绑定 `appKey`、`userId`、`tenantId`、`redirectUri`。
- 说明：本文档未给出具体授权码兑换 API 路径（平台契约端点仅列出 `/platform/*` 下的 `health`、`readiness`、`version`、`session`、`sso/bootstrap`，其中 `sso/bootstrap` 为 SSO 相关契约端点）。

---

## 6. 项目会话（§6）

- 【必须】Cookie 使用 `HttpOnly + Secure + SameSite`，并经过目标浏览器 iframe 策略验证。
- 【禁止】Cookie 名称和 Domain 不得与主系统或其他项目相同。
- 【必须】项目会话最长 30 分钟。
- 【必须】项目至少每 5 分钟刷新一次主系统用户、角色和权限状态。
- 【必须】用户被停用、部门变化或权限撤销后，项目会话必须在刷新周期内失效或降权。
- 【必须】退出项目只清理项目会话，不删除主系统会话。

---

## 7. 权限声明（§7）

权限在根清单中声明（YAML 原文逐字保留）：

```yaml
permissions:
  - code: auto-parts-listing:project:view
    name: 进入汽配上品项目
  - code: auto-parts-listing:queue:create
    name: 创建汽配上品排队
  - code: auto-parts-listing:operation-log:view
    name: 查看运营日志
  - code: auto-parts-listing:technical-log:view
    name: 查看技术日志
  - code: auto-parts-listing:config:edit
    name: 修改项目配置
```

权限枚举格式规则：
- 【必须】权限必须以 `<appKey>:` 开头（例如 `auto-parts-listing:project:view`、`auto-parts-listing:queue:create`、`auto-parts-listing:operation-log:view`、`auto-parts-listing:technical-log:view`、`auto-parts-listing:config:edit`）。
- 【必须】新权限默认不授予普通角色。
- 【禁止】已发布权限不得改名或复用为不同语义；语义变化时新增权限。
- 【必须】删除权限先标记 `DEPRECATED`，保证旧版本可回滚。
- 【必须】Controller 发布时同步权限到主系统；【禁止】项目不得维护自己的角色配置页。

---

## 8. 角色和日志边界（§8）

- 【必须】管理员即开发人员，可查看项目配置、发布、技术日志和全部运营日志。
- 【必须】普通运营人员只能查看被授权项目、本部门业务数据和运营日志。
- 【禁止】技术堆栈、stderr、请求头、内部路径、密钥配置和发布日志不向普通运营人员展示。
- 【禁止】前端隐藏按钮不构成权限控制；【必须】每个项目后端接口必须校验对应权限字符。

---

## 9. 部门和数据范围（§9）

- 【必须】主系统是租户、部门、角色和数据范围的唯一来源。

### 9.1 身份载荷字段契约（§9.1）

SSO 交换成功后项目后端拿到的身份对象，字段固定如下（JSON 原文逐字保留）：

```json
{
  "tenantId": "000000",
  "userId": "1",
  "username": "admin",
  "displayName": "admin",
  "departmentId": "2068630213954715649",
  "departmentName": "服装",
  "departmentCategory": "",
  "roles": ["superadmin"],
  "permissions": ["boss-shop-data:shop-data:view"],
  "dataScope": { "mode": "ALL", "departmentIds": [], "userIds": [] },
  "issuedAt": "2026-08-11 14:40:04"
}
```

字段清单：`tenantId`、`userId`、`username`、`displayName`、`departmentId`、`departmentName`、`departmentCategory`、`roles`（数组）、`permissions`（数组）、`dataScope`（对象）、`issuedAt`。

- 【必须】**所有标识符恒为 JSON 字符串**，包括 `userId`、`departmentId` 以及 `dataScope` 里的两个数组。
- 【必须】主系统 ID 是 19 位雪花值，超出 JavaScript `Number.MAX_SAFE_INTEGER`（9007199254740991），项目侧必须原样当字符串用（正确/错误写法原文逐字保留）：

```js
// 错误：本地开发用小 ID 能跑，生产环境雪花 ID 精度丢失，判断必然失败
const departmentId = Number(identity.departmentId);
if (!Number.isSafeInteger(departmentId)) throw new Error('IDENTITY_SCOPE_INVALID');

// 正确：当作不透明字符串，只做非空校验
const departmentId = String(identity.departmentId ?? '');
if (!departmentId) throw new Error('IDENTITY_SCOPE_INVALID');
```

- 【必须】错误码 `IDENTITY_SCOPE_INVALID`（身份 ID 非法时抛出）。
- 【必须】本地开发桩里的身份也必须用字符串 ID，否则桩和生产的类型不一致，问题只会在上线后暴露。

### 9.2 `dataScope` 的结构与语义（§9.2）

- 【必须】`dataScope` 是**已经解析完毕**的可见范围，项目直接按它过滤即可（字段表原文）：

| 字段 | 含义 |
|---|---|
| `mode` | `ALL` 或 `RESTRICTED` |
| `departmentIds` | 可见部门 ID（字符串数组） |
| `userIds` | 可见归属人 ID（字符串数组） |

判定规则：
- 【必须】`mode = "ALL"`：不加范围过滤。
- 【必须】`mode = "RESTRICTED"`：一行可见，当且仅当它的所属部门在 `departmentIds` 中，**或**它的归属人在 `userIds` 中。
- 【禁止】`RESTRICTED` 且两个数组都为空：**什么都不可见**。没有授予就不是授予，禁止回退成"看全部"或"看本部门"。

参考实现（JS 原文逐字保留，`1 = 0` 失败关闭）：

```js
function scopeCondition(dataScope) {
  if (dataScope?.mode === 'ALL') return { sql: '1 = 1', params: [] };
  const departmentIds = dataScope?.departmentIds ?? [];
  const userIds = dataScope?.userIds ?? [];
  const parts = [];
  const params = [];
  if (departmentIds.length) {
    parts.push(`department_id IN (${departmentIds.map(() => '?').join(',')})`);
    params.push(...departmentIds);
  }
  if (userIds.length) {
    parts.push(`owner_user_id IN (${userIds.map(() => '?').join(',')})`);
    params.push(...userIds);
  }
  if (!parts.length) return { sql: '1 = 0', params: [] };   // 失败关闭
  return { sql: `(${parts.join(' OR ')})`, params };
}
```

- 【必须】平台负责把主系统的数据权限展开成上面这两个集合，包括需要查 `sys_role_dept` 和部门树的那几种。
- 【禁止】项目不需要、也无法自行解析主系统的数据权限编码：项目运行账号只对自己的 schema 有 SELECT 权限，读不到 `sys_dept` / `sys_role_dept`。
- 【禁止】若在项目里看到形如 `dataScope: "1"` / `"3"` 的编码判断，属于实现错误。
- 【必须】用户持有多个角色时，平台已按并集合并，项目侧无需再合并。

### 9.3 后台任务没有身份（§9.3）

- 【必须】身份只在**交互式请求**里存在。定时任务、补数脚本、消费队列的后台逻辑都没有登录用户，项目必须为它们显式声明一个固定归属（租户 + 部门），并且：
  - 【必须】**后台归属必须和交互式写入落到同一分区**（若日数据表按 `(tenant_id, department_id)` 组织，定时任务用部门 A、用户手动触发用部门 B，两边就各自维护一份数据和各自的增量锚点）。
  - 【必须】**该归属必须来自清单声明的环境变量，不能是代码里的隐式默认值**（隐式默认值不会出现在任何配置里，排查时无从发现，而它决定了数据落在哪）。
  - 【必须】若数据本身不属于任何部门（例如面向管理层的全租户视图），用一个**明确约定并写进项目 README 的哨兵值**，【禁止】不要借用某个真实部门 ID；此时只有 `dataScope.mode = ALL` 的身份看得到该数据，部门受限用户看不到。
- 【必须】由平台下发的任务不用自己猜归属：Task JWT 里已经带了 `department_id` 和 `owner_user_id`，无登录用户创建的任务取哨兵值 `-1`，见 [03-Agent任务执行与报告上报设计](03-Agent任务执行与报告上报设计.md) §11.1；【禁止】不要再造第三套约定。

### 9.4 项目后端必须遵守（§9.4）

- 【必须】按 `tenantId` 隔离所有查询和修改。
- 【必须】按 §9.2 的规则应用数据范围，【禁止】不得自行放宽。
- 【必须】创建排队记录时由服务端重新计算租户、部门和操作人。
- 【禁止】拒绝前端自行提交或覆盖 `tenantId`、`departmentId`、`operatorUserId`。
- 【必须】任务类型本身不必绑定部门；是否允许用户选择某任务类型由权限决定，队列数据可见范围由部门决定。

---

## 10. 运营表单（§10）

- 【必须】运营页面只展示业务人员能理解和决定的字段；系统字段由项目配置或主系统冻结。

运营人员可以填写或选择（允许清单）：
- 已授权店铺。
- 业务数量。
- 任务类型。
- 必要的业务选项。

运营人员不应看到或填写（禁止清单）：
- 【禁止】`appKey`、`executorKey`、`contextRef`。
- 【禁止】运行模式、节点、并发、Commit、配置版本。
- 【禁止】HMAC、Task JWT、Schema 版本。
- 【禁止】JSON 配置、数据库键、店铺内部稳定 ID。
- 【必须】下拉选项必须来自项目后端受控目录接口，不能要求运营人员手工输入内部编码。

---

## 11. 全局业务队列入口（§11）

项目工作台可以提供"新增排队"，但它只是主系统全局业务队列的项目筛选入口：
- 【禁止】不创建项目专属运营队列表。
- 【禁止】不复制主系统队列状态机。
- 【禁止】不允许项目自行选择 Agent。
- 【必须】项目工作台自动预选本项目允许的任务类型。
- 【必须】全局排队中心可以由运营人员选择其有权限的任务类型。
- 【禁止】定时任务不出现在运营任务类型选择器中。

---

## 12. 浏览器安全（§12）

- 【必须】`Content-Security-Policy: frame-ancestors` 只允许主系统受控域名。
- 【必须】CSP 用 `default-src 'self'` 时，`script-src` 会回落到 `'self'`，而 `'self'` **不含内联脚本**：页面脚本必须放独立 `.js` 文件并以 `<script src>` 引用；【禁止】**禁止**改用 `'unsafe-inline'` 绕过。
- 【必须】内联脚本被拦是静默的（页面照常渲染、字段停在初始值、无报错），且 `curl` 测不出来，必须在浏览器中验收并检查控制台 CSP 告警。
- 【禁止】授权码不能进入访问日志、Referer、浏览器历史或错误上报。
- 【必须】入口加载后立即从地址中清理短期凭据。
- 【必须】校验 `state`、PKCE、目标 `appKey`、租户和一次性消费状态。
- 【禁止】禁止宽泛 `postMessage('*')`；消息必须校验来源、目标和结构。
- 【禁止】禁止项目请求主系统 Cookie、LocalStorage 或内部管理接口。

---

## 13. 必测场景（§13）

以下均为必测项（原文逐条保留）：
- 【必须】正常用户进入已授权项目。
- 【必须】无项目权限用户看不到入口且直接访问返回拒绝。
- 【必须】授权码过期、重复兑换、跨项目、跨租户、错误 state 和错误 PKCE 均被拒绝。
- 【必须】项目会话过期后不能继续访问 API。
- 【必须】权限撤销和用户停用在刷新周期内生效。
- 【必须】普通运营人员不能查看其他部门记录或技术日志。
- 【必须】管理员可按主系统数据范围查看全部。
- 【必须】身份里的 `userId`、`departmentId` 用 19 位雪花值（例如 `"2068630213954715649"`）跑通一次业务查询；本地开发桩常用 `1`、`12` 这类小 ID，只有大值才会暴露把 ID 当数字处理的写法（见 §9.1）。
- 【必须】`dataScope.mode = "RESTRICTED"` 且 `departmentIds`、`userIds` 均为空时查询返回空集，不是全集也不是本部门（见 §9.2）。
- 【必须】**经平台路由**（不是直连容器）验证一个真实业务接口能取到数据，例如 `<应用根>/api/<业务路径>`；黑盒门禁直连容器、绕过路由，这类问题只有经路由才验得出来（见 §3.1）。
- 【必须】经前端路由验证 `<应用根>/platform/health` 返回 200；同一路径挂在后端路由下（`<应用根>/api/platform/health`）应当 404，说明两条路由的落点没有互相串位。（状态码：200 / 404）
- 【必须】iframe CSP、sandbox、相对资源和目标浏览器策略通过。
- 【必须】项目前端在主系统布局内无重叠、无双导航和无横向内容丢失。
- 【必须】发布后侧边栏 `自动化项目` 下出现项目目录和页面，点击可进入容器。
- 【必须】把项目目录移入其他目录后再次发布，目录停留在移动后的位置，原位置不出现重复副本。
- 【必须】浏览器必须处于安全上下文（HTTPS）：`crypto.subtle` 只在安全上下文可用，用 HTTP 访问主系统时 PKCE 无法计算，入口会加载失败。

---

## 14. 接入完成定义（§14）

完成定义（全部必须满足）：
- 【必须】项目入口由根清单自动登记。
- 【必须】用户从主系统项目列表或自动生成的侧边栏菜单进入通用容器后，无需再次登录即可访问项目。
- 【必须】项目后端正确执行租户、部门和权限控制。
- 【必须】项目无需在主系统仓库增加专用页面或固定菜单代码。

---

## 附 A：「禁止」类规则汇总（按章节）

1. 【§2】普通新增或升级项目不得要求重新构建主系统前端或新增固定 Vue 路由。
2. 【§2.1】项目不得自行在主系统创建、修改或删除菜单行；菜单是发布投影的产物。
3. 【§3】项目不得写死生产地址。
4. 【§3】禁止从任务输入、用户输入或数据库字段生成任意 iframe URL。
5. 【§3】禁止主系统请求项目提交的任意 URL（健康和嵌入地址只能来自 Controller 路由登记），避免 SSRF。
6. 【§3.1】后端路由的 `/api` 段必须保留，不能剥掉；`proxy_pass` 不得写成 `/`（必须以 `/api/` 结尾）。
7. 【§3.1】健康探针不能打在后端路由上（不能打 `<应用根>/api/platform/health`）。
8. 【§3.1】`/platform/*`（含 `/platform/session`、`/platform/health` 等平台契约端点）只能经前端路由到达，后端路由不得挂载覆盖。
9. 【§4】项目不得访问顶层 DOM、主系统 Cookie、LocalStorage 或其他项目 iframe。
10. 【§5】项目不能读取或保存主系统 JWT。
11. 【§5】授权码兑换后立即失效，失败重试不能重新激活（singleUse，不可复用）。
12. 【§6】Cookie 名称和 Domain 不得与主系统或其他项目相同。
13. 【§6】退出项目只清理项目会话，不删除主系统会话。
14. 【§7】已发布权限不得改名或复用为不同语义；语义变化时新增权限。
15. 【§7】项目不得维护自己的角色配置页（权限由 Controller 发布时同步到主系统）。
16. 【§8】技术堆栈、stderr、请求头、内部路径、密钥配置和发布日志不向普通运营人员展示。
17. 【§8】前端隐藏按钮不构成权限控制（必须由后端校验权限字符）。
18. 【§9.2】`RESTRICTED` 且 `departmentIds`、`userIds` 均为空时禁止回退成"看全部"或"看本部门"——什么都不可见。
19. 【§9.2】禁止在项目里出现 `dataScope: "1"` / `"3"` 的编码判断（项目无法自行解析主系统数据权限编码）。
20. 【§9.3】后台归属不能是代码里的隐式默认值（必须来自清单声明的环境变量）。
21. 【§9.3】不要借用某个真实部门 ID 作哨兵值（须用写进项目 README 的明确约定哨兵值）。
22. 【§9.3】不要再造第三套后台任务归属约定（Task JWT 已带 `department_id`、`owner_user_id`，哨兵值 `-1`）。
23. 【§9.4】不得自行放宽数据范围（按 §9.2 规则应用）。
24. 【§9.4】拒绝前端自行提交或覆盖 `tenantId`、`departmentId`、`operatorUserId`。
25. 【§10】运营人员不应看到或填写：`appKey`、`executorKey`、`contextRef`、运行模式、节点、并发、Commit、配置版本、HMAC、Task JWT、Schema 版本、JSON 配置、数据库键、店铺内部稳定 ID。
26. 【§10】下拉选项不能要求运营人员手工输入内部编码（必须来自受控目录接口）。
27. 【§11】不创建项目专属运营队列表、不复制主系统队列状态机、不允许项目自行选择 Agent、定时任务不出现在运营任务类型选择器中。
28. 【§12】禁止改用 `'unsafe-inline'` 绕过 CSP（脚本必须独立 `.js` + `<script src>`）。
29. 【§12】授权码不能进入访问日志、Referer、浏览器历史或错误上报。
30. 【§12】禁止宽泛 `postMessage('*')`（消息必须校验来源、目标和结构）。
31. 【§12】禁止项目请求主系统 Cookie、LocalStorage 或内部管理接口。

---

## 附 B：交叉引用清单

| 引用处（本文件章节） | 被引文件/章节 | 引用目的 |
|---|---|---|
| §2.1（navigation 投影） | 规范 08 §6.1（`08-项目容器与前端工程规范.md` 类文档） | navigation 树声明的投影方式：按声明的树逐层投影 |
| §3.1（项目侧写法） | 规范 08 §7 | fetch 相对应用根路径的写法约定（`./api/...`、`./platform/...`） |
| §9.3（后台任务归属） | `03-Agent任务执行与报告上报设计.md` §11.1 | Task JWT 已带 `department_id`、`owner_user_id`；无登录用户创建的任务取哨兵值 `-1` |
| §13 ↔ §3.1 | 本文档内部 | 必测场景引用挂载布局不变量（经路由验证业务接口） |
| §13 ↔ §9.1、§9.2 | 本文档内部 | 必测场景引用雪花 ID 字符串契约与 dataScope 空集语义 |

## 附 C：本文件未包含内容说明（已核查）

- 授权码兑换 API 的具体 URL 路径：本文档未给出（仅列出平台契约端点名 `health`、`readiness`、`version`、`session`、`sso/bootstrap`）。
- HMAC canonical string 字段顺序、nonce/时间戳要求：本文档未包含（仅在 §10 禁止运营人员看到 HMAC、Task JWT）。
- Cookie 的具体名称：本文档未给出（仅要求名称和 Domain 不得与主系统或其他项目相同，且必须 HttpOnly + Secure + SameSite）。
- 状态码：本文档仅出现 200（`<应用根>/platform/health` 返回 200）与 404（`<应用根>/api/platform/health` 应 404）。

---

## 附 D：本文件要点速览（关键强制要求，含具体参数名与值）

1. 两条入口路由到达同一通用容器：`/rpa-console/project-app/:appKey`（列表卡片跳转）与 `/rpa-console/project-app-<appKey>/workbench?appKey=`（侧边栏菜单）。
2. 项目发布后由 Controller 自动登记 `appKey`、名称、图标、入口、健康地址、权限、显示顺序；禁止要求重新构建主系统前端或新增固定 Vue 路由。
3. 项目可见四条件：已启用且健康；具备 `<appKey>:project:view`；租户与授权范围匹配；`PLATFORM_EXECUTOR` 等平台项目仅管理员可见。
4. 默认侧边栏投影形态：`自动化项目 (rpa-console)` → `<项目显示名>`（目录 menu_type=M）→ `工作台`（页面 menu_type=C）。
5. 声明 navigation 后按树逐层投影（规范 08 §6.1），项目自身侧边栏嵌入时隐藏防双层导航；`status: PLANNED` 不投影，改 `AVAILABLE` 重新发布即出现。
6. 页面身份写入 `sys_menu.query_param` 的 `{"appKey":"<appKey>"}`；目录 `menu_id` 持久化在 `rpa_project_application.sys_menu_id`，发布按该 ID 原地更新。
7. 管理员搬移目录后再次发布不回搬、不留重复副本；项目禁止自行增删改主系统菜单行。
8. 前端必须：相对静态资源与 API 路径；`rpa-application.yaml` 声明 `basePathMode: RELATIVE`；支持同源/受控同站点反代加载；禁止从任务/用户输入/数据库字段生成任意 iframe URL。
9. 健康和嵌入地址只能来自 Controller 路由登记，禁止主系统请求项目提交的任意 URL（防 SSRF）。
10. 应用根 `<base>/<appKey>/`：`<应用根>/` → 前端容器 `/`；`<应用根>/api/` → 后端容器 `/api/`（**`/api` 段必须保留，不能剥掉**）。
11. 后端同时提供 `/platform/*`（health、readiness、version、session、sso/bootstrap）与 `/api/*`（业务接口）；`/platform/*` 只能经前端路由到达。
12. 项目侧写法：`fetch('./api/boss/shop-data', { credentials: 'same-origin' })` 与 `fetch('./platform/session', { credentials: 'same-origin' })`。
13. 后端 `proxy_pass` 必须以 `/api/` 结尾（写成 `/` 则业务接口全部 404）；健康探针必须打前端路由（`<应用根>` + `healthPath`），不能打后端路由。
14. 嵌入模式支持 `embedded=true`：隐藏全局侧边栏、重复 Logo/顶层导航、独立登录/退出/角色管理入口；可保留内部标签页、业务导航、筛选器、详情页。
15. 主系统 iframe 必须使用最小 `sandbox` 和 `allow` 权限；项目禁止访问顶层 DOM、主系统 Cookie、LocalStorage 或其他项目 iframe。
16. 项目禁止读取或保存主系统 JWT。
17. SSO 固定流程：点击项目 → 主系统生成 60 秒一次性授权码、state、PKCE challenge → 容器以受控 POST 或短期 Fragment 打开 → 项目后端携带服务身份、code verifier、授权码兑换 → 主系统返回最小用户/租户/部门/数据范围/权限声明 → 项目建立独立 HttpOnly 会话 → 前端只用项目会话访问项目后端。
18. 授权码必须绑定 8 个字段：`appKey`、`userId`、`tenantId`、`redirectUri`、`state`、`codeChallenge`、`expiresAt`、`singleUse`。
19. 授权码兑换后立即失效，失败重试不能重新激活。
20. 项目会话 Cookie：`HttpOnly + Secure + SameSite`，经目标浏览器 iframe 策略验证；名称和 Domain 不得与主系统或其他项目相同。
21. 项目会话最长 30 分钟；至少每 5 分钟刷新一次主系统用户、角色、权限状态；停用/部门变化/权限撤销须在刷新周期内失效或降权。
22. 退出项目只清理项目会话，不删除主系统会话。
23. 权限格式：`<appKey>:` 前缀（如 `auto-parts-listing:project:view`、`auto-parts-listing:queue:create`、`auto-parts-listing:operation-log:view`、`auto-parts-listing:technical-log:view`、`auto-parts-listing:config:edit`）；新权限默认不授予普通角色。
24. 已发布权限禁止改名或复用为不同语义（语义变化新增权限）；删除先标记 `DEPRECATED`；项目禁止维护角色配置页。
25. 普通运营人员只能看被授权项目、本部门业务数据、运营日志；技术堆栈、stderr、请求头、内部路径、密钥配置、发布日志不展示。
26. 前端隐藏按钮不构成权限控制，每个项目后端接口必须校验对应权限字符。
27. 身份载荷字段固定：`tenantId`、`userId`、`username`、`displayName`、`departmentId`、`departmentName`、`departmentCategory`、`roles`、`permissions`、`dataScope`、`issuedAt`（JSON 原文见 §9.1）。
28. 所有标识符恒为 JSON 字符串（含 `userId`、`departmentId`、`dataScope.departmentIds`、`dataScope.userIds`）；主系统 ID 为 19 位雪花值，超出 `Number.MAX_SAFE_INTEGER`（9007199254740991），禁止 `Number()` 转换；ID 非法抛 `IDENTITY_SCOPE_INVALID`。
29. 本地开发桩身份也必须用字符串 ID。
30. `dataScope` 字段：`mode`（`ALL`/`RESTRICTED`）、`departmentIds`（字符串数组）、`userIds`（字符串数组）；`ALL` 不过滤。
31. `RESTRICTED`：行可见当且仅当所属部门在 `departmentIds` **或** 归属人在 `userIds`；两数组皆空则什么都不可见，禁止回退"看全部/看本部门"（参考实现：空数组返回 `sql: '1 = 0'` 失败关闭）。
32. 项目运行账号只对自己的 schema 有 SELECT 权限，读不到 `sys_dept`/`sys_role_dept`，禁止自行解析数据权限编码（禁止 `dataScope: "1"`/`"3"` 判断）；多角色已由平台并集合并，项目侧不再合并。
33. 后台任务（定时/补数/消费队列）无登录用户，必须显式声明固定归属（租户+部门）：归属须与交互式写入同分区；必须来自清单声明的环境变量，不能是代码隐式默认值。
34. 无部门归属的数据用写进项目 README 的哨兵值，禁止借用真实部门 ID；仅 `mode = ALL` 身份可见。
35. 平台下发任务（Task JWT 带 `department_id`、`owner_user_id`，哨兵值 `-1`，见 03 规范 §11.1）禁止再造第三套归属约定。
36. 项目后端必须：按 `tenantId` 隔离所有查询和修改；按 §9.2 应用数据范围不得放宽；创建排队记录由服务端重算租户/部门/操作人；拒绝前端提交或覆盖 `tenantId`、`departmentId`、`operatorUserId`。
37. 运营表单禁止出现：`appKey`、`executorKey`、`contextRef`、运行模式、节点、并发、Commit、配置版本、HMAC、Task JWT、Schema 版本、JSON 配置、数据库键、店铺内部稳定 ID；下拉选项必须来自受控目录接口。
38. 工作台"新增排队"只是全局业务队列的项目筛选入口：不建专属队列表、不复制队列状态机、不允许项目自行选 Agent、自动预选本项目任务类型、定时任务不进选择器。
39. 浏览器安全：CSP `frame-ancestors` 只允许主系统受控域名；`default-src 'self'` 下脚本必须独立 `.js` + `<script src>`，**禁止 `'unsafe-inline'`**；内联脚本被拦是静默的，必须浏览器验收并查控制台 CSP 告警。
40. 授权码禁止进入访问日志、Referer、浏览器历史或错误上报；入口加载后立即从地址清理短期凭据；校验 `state`、PKCE、目标 `appKey`、租户、一次性消费状态。
41. 禁止宽泛 `postMessage('*')`（校验来源、目标、结构）；禁止项目请求主系统 Cookie、LocalStorage 或内部管理接口。
42. 必测：`<应用根>/platform/health` 返回 200；`<应用根>/api/platform/health` 应 404；经平台路由（非直连容器）验证 `<应用根>/api/<业务路径>` 可取数据。
43. 必测：雪花 ID（如 `"2068630213954715649"`）跑通业务查询；`RESTRICTED` 空数组查询返回空集（非全集非本部门）。
44. 必测：授权码过期/重复兑换/跨项目/跨租户/错误 state/错误 PKCE 均拒绝；会话过期不能访问 API；撤销/停用在刷新周期内生效。
45. 浏览器必须处于安全上下文（HTTPS）：`crypto.subtle` 仅安全上下文可用，HTTP 下 PKCE 无法计算，入口加载失败。
46. 接入完成定义：入口由根清单自动登记；进容器无需再次登录；后端正确执行租户/部门/权限控制；主系统仓库无专用页面或固定菜单代码。
