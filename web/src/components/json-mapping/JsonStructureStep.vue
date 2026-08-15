<template>
  <div class="json-structure" role="region" aria-label="结构检测">
    <p class="json-structure__meta">
      检测到 {{ profile.candidates.length }} 个候选记录路径
      <span class="kpi-num">· 抽样 {{ profile.sampled_records }} 条</span>
      <span class="kpi-num">· 预计记录 {{ profile.total_records_estimate }}</span>
    </p>

    <ul class="json-structure__list">
      <li
        v-for="candidate in profile.candidates"
        :key="candidate.record_path"
        class="structure-card"
        :class="{ 'structure-card--selected': candidate.record_path === selectedPath }"
        :aria-selected="candidate.record_path === selectedPath"
        role="option"
        tabindex="0"
        @click="selectCandidate(candidate.record_path)"
        @keydown.enter="selectCandidate(candidate.record_path)"
      >
        <div class="structure-card__head">
          <span class="structure-card__path" :title="recordPathLabel(candidate.record_path)">
            <span v-if="candidate.record_path === '$'" class="structure-card__path-name">根对象</span>
            <code>{{ candidate.record_path }}</code>
          </span>
          <span class="structure-card__conf" :class="confClass(candidate.confidence)">
            {{ Math.round(candidate.confidence * 100) }}% 置信
          </span>
        </div>
        <div class="structure-card__stats">
          <span class="structure-card__stat">约 <span class="kpi-num">{{ candidate.record_count_estimate }}</span> 条记录</span>
          <span class="structure-card__stat"><span class="kpi-num">{{ candidate.field_count }}</span> 个字段</span>
          <span class="structure-card__stat">
            <span class="kpi-num">{{ candidate.nested_array_count }}</span> 个嵌套数组
          </span>
        </div>
        <ul class="structure-card__samples">
          <li v-for="(sample, i) in candidate.samples.slice(0, 5)" :key="i" class="structure-card__sample">
            {{ sample }}
          </li>
        </ul>
      </li>
    </ul>

    <p v-if="error" class="json-structure__error" role="alert">{{ error }}</p>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import type { SourceProfile } from '../../types/structured'
import type { BusyOperation } from './useJsonMappingWizard'

const props = defineProps<{
  profile: SourceProfile
  busy: BusyOperation | null
  error: string
}>()

const emit = defineEmits<{
  (e: 'select', payload: { profile: SourceProfile; candidatePath: string }): void
}>()

/** 已选择的候选路径（主操作「下一步」使用） */
const selectedPath = ref<string | null>(null)

function selectCandidate(candidatePath: string) {
  if (props.busy !== null) return
  selectedPath.value = candidatePath
}

/** `$` 是标准 JSONPath 根节点；补充人类可读名称，避免被误认为渲染残缺。 */
function recordPathLabel(candidatePath: string): string {
  return candidatePath === '$' ? '$（根对象）' : candidatePath
}

function confClass(confidence: number): string {
  if (confidence >= 0.8) return 'structure-card__conf--high'
  if (confidence >= 0.5) return 'structure-card__conf--mid'
  return 'structure-card__conf--low'
}

defineExpose({ selectedPath, confirmSelection: () => selectedPath.value })
</script>

<style scoped>
.json-structure__meta {
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 12px;
}

.json-structure__list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.structure-card {
  padding: 12px 14px;
  border-radius: var(--radius-xl);
  background: var(--bg-subtle);
  border: 1.5px solid transparent;
  cursor: pointer;
}

.structure-card:hover,
.structure-card:focus-visible {
  border-color: var(--accent-blue);
  outline: none;
}

.structure-card--selected {
  border-color: var(--accent-blue);
  background: color-mix(in srgb, var(--accent-blue) 5%, transparent);
}

.structure-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.structure-card__path {
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 600;
  color: var(--accent-indigo);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.structure-card__path-name {
  margin-right: 8px;
  font-family: var(--font-sans);
  color: var(--text-primary);
}

.structure-card__path code {
  font: inherit;
  color: inherit;
}

.structure-card__conf {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: var(--radius-full);
  flex-shrink: 0;
}

.structure-card__conf--high {
  background: color-mix(in srgb, var(--accent-green) 12%, transparent);
  color: var(--accent-green);
}

.structure-card__conf--mid {
  background: color-mix(in srgb, var(--accent-orange) 12%, transparent);
  color: var(--accent-orange);
}

.structure-card__conf--low {
  background: color-mix(in srgb, var(--accent-red) 12%, transparent);
  color: var(--accent-red);
}

.structure-card__stats {
  display: flex;
  gap: 14px;
  margin-top: 8px;
}

.structure-card__stat {
  font-size: 12px;
  color: var(--text-secondary);
}

.structure-card__samples {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 10px;
}

.structure-card__sample {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.json-structure__error {
  margin-top: 10px;
  font-size: 12px;
  color: var(--accent-red);
}
</style>
