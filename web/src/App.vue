<template>
  <router-view />
</template>

<script setup lang="ts">
</script>

<style>
:root {
  /* Bright cream/warm white palette */
  --paper: #FFFBF5;
  --ink: #2C2416;
  --olive: #7D9B6E;
  --olive-hover: #5F7A52;
  --accent: #D4A96A;
  --surface: #FFF8ED;
  --border: #E8DDCD;
  --dust: #9B8E7A;
  --white: #FFFFFF;

  --font-display: 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif;
  --font-mono: 'SF Mono', 'Cascadia Code', monospace;
}

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: var(--font-display);
  background: var(--paper);
  color: var(--ink);
  -webkit-font-smoothing: antialiased;
}

/* Element Plus overrides — warm cream, no blue/purple */
:root {
  --el-color-primary: var(--olive);
  --el-color-primary-hover: var(--olive-hover);
  --el-color-primary-light-3: rgba(125, 155, 110, 0.3);
  --el-color-primary-light-5: rgba(125, 155, 110, 0.15);
  --el-color-primary-light-9: rgba(125, 155, 110, 0.05);
  --el-color-success: #7D9B6E;
  --el-color-warning: #D4A96A;
  --el-color-danger: #D48A6A;
  --el-border-color-base: var(--border);
  --el-bg-color: var(--paper);
  --el-input-bg-color: var(--white);
}

.el-button--primary {
  --el-button-bg-color: var(--olive);
  --el-button-border-color: var(--olive);
  --el-button-hover-bg-color: var(--olive-hover);
  --el-button-hover-border-color: var(--olive-hover);
}

.el-input__wrapper {
  border-color: var(--border);
}

.el-input__wrapper.is-focus {
  box-shadow: 0 0 0 1px var(--olive) inset !important;
}
</style>
