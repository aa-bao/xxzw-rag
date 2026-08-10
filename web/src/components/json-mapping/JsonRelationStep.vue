<template>
  <div class="json-relations" role="region" aria-label="关系与层级">
    <p class="json-relations__hint">
      为每种记录类型选择切块策略与父子关系；子记录可引用最近前序同级记录的字段（仅下拉选择）。
    </p>

    <ul class="json-relations__tree">
      <li v-for="rt in mapping.record_types" :key="rt.name" class="json-relations__branch">
        <JsonRecordTypeNode
          :record-type="rt"
          :ancestors="[]"
          :mapping="mapping"
          @update:mapping="emit('update:mapping', $event)"
        />
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import JsonRecordTypeNode from './JsonRecordTypeNode.vue'
import type { MappingDefinition } from '../../types/structured'

defineProps<{
  mapping: MappingDefinition
}>()

const emit = defineEmits<{
  (e: 'update:mapping', mapping: MappingDefinition): void
}>()
</script>

<style scoped>
.json-relations {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.json-relations__hint {
  font-size: 12px;
  color: var(--text-secondary);
}

.json-relations__tree {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
</style>
