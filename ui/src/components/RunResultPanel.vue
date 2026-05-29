<script setup>
import { computed } from 'vue'
import Card from 'primevue/card'
import Button from 'primevue/button'
import Tag from 'primevue/tag'

const props = defineProps({ result: { type: Object, required: true } })

const cards = computed(() => [
  { label: 'Matched',    value: props.result.records_matched,    tone: 'match',    icon: 'check_circle' },
  { label: 'Mismatched', value: props.result.records_mismatched, tone: 'mismatch', icon: 'cancel' },
  { label: 'Only in A',  value: props.result.keys_in_a_only,     tone: 'a',        icon: 'shift_lock' },
  { label: 'Only in B',  value: props.result.keys_in_b_only,     tone: 'b',        icon: 'shift_lock' },
  { label: 'Dups in A',  value: props.result.dups_in_a,          tone: 'warn',     icon: 'content_copy' },
  { label: 'Dups in B',  value: props.result.dups_in_b,          tone: 'warn',     icon: 'content_copy' },
])
</script>

<template>
  <Card class="result">
    <template #title>
      <div class="title-row">
        <span class="material-symbols-outlined success-mark">task_alt</span>
        <span>Run complete</span>
        <Tag :value="result.run_dir_name" severity="secondary" rounded />
      </div>
    </template>
    <template #content>
      <div class="grid">
        <div v-for="c in cards" :key="c.label" class="metric" :class="`tone-${c.tone}`">
          <span class="material-symbols-outlined metric-icon">{{ c.icon }}</span>
          <div class="metric-label t-label">{{ c.label }}</div>
          <div class="metric-value">{{ c.value.toLocaleString() }}</div>
        </div>
      </div>
      <p class="t-small">
        Output: <code class="t-mono">{{ result.run_dir_path }}</code>
      </p>
      <a :href="result.report_href" target="_blank" rel="noopener">
        <Button label="Show Result" icon="pi pi-external-link" iconPos="right" />
      </a>
    </template>
  </Card>
</template>

<style scoped>
.result { margin-top: 1.2rem; box-shadow: var(--elev-2) !important; }
.title-row { display: flex; gap: 0.6rem; align-items: center; font-size: 1.05rem; font-weight: 600; }
.success-mark { color: var(--status-match); font-size: 22px; }

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 0.6rem;
  margin-bottom: 0.85rem;
}
.metric {
  position: relative;
  padding: 0.85rem;
  background: var(--surface-2);
  border-radius: var(--radius-md);
  border: 1px solid var(--surface-border);
  text-align: left;
  overflow: hidden;
}
.metric-icon {
  position: absolute;
  right: 0.4rem; top: 0.4rem;
  font-size: 22px;
  opacity: 0.18;
}
.metric-label { margin-bottom: 0.2rem; }
.metric-value { font-size: 1.7rem; font-weight: 700; letter-spacing: -0.01em; line-height: 1; color: var(--text-strong); }

.tone-match .metric-value, .tone-match .metric-icon { color: var(--status-match); }
.tone-mismatch .metric-value, .tone-mismatch .metric-icon { color: var(--status-mismatch); }
.tone-warn .metric-icon { color: var(--status-warn); opacity: 0.35; }
.tone-a .metric-icon { color: var(--tone-a); }
.tone-b .metric-icon { color: var(--tone-b); }
</style>
