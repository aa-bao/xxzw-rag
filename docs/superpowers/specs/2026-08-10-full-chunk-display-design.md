# 知识块正文完整展示设计

## 目标

管理端所有已确认的知识块正文入口默认展示完整 `chunk.content`，不再因固定行数出现省略号。知识块长度已由后端切片规则约束，因此允许卡片随正文自然增高。

## 修改范围

仅修改以下三个 Vue 页面中的正文样式：

- `web/src/views/KbChunksView.vue` 的 `.chunk-card__content`
- `web/src/views/KbDocsView.vue` 的 `.docs-drawer__chunk-content`
- `web/src/views/KbTestingView.vue` 的 `.result-card__snippet`

从三个选择器中移除 `display: -webkit-box`、`-webkit-line-clamp`、`-webkit-box-orient` 和 `overflow: hidden`。保留字体、行高、颜色、`white-space: pre-wrap` 与 `word-break: break-word`，确保原始换行和长文本折行正常。

## 非目标

- 不修改后端、API 或切片长度约束。
- 不增加“展开/收起”交互或新的组件状态。
- 不改变标题、知识库名、文件名、来源标签等辅助信息的单行省略。
- 不修改聊天引用抽屉；其内容仍作为摘要展示。

## 行为与布局

正文卡片按完整内容自然撑高。知识块列表继续使用现有分页；文档预览抽屉继续使用现有内部滚动；召回测试结果继续使用现有卡片列表布局。此次不设置新的最大高度或滚动容器。

## 验证

- 运行 `npx vue-tsc --noEmit`。
- 运行 `npm run build`。
- 浏览器检查三个入口中超过六行且包含换行的知识块，确认正文完整、换行保留、卡片自然增高，分页和抽屉滚动正常。

