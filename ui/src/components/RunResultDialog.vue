<script setup>
// Pops up the moment a run completes. The "Open Report" button is the
// primary CTA — opens compare_reports.html in a new tab. Operator can
// dismiss the dialog and still find the same data in the inline
// RunResultPanel below the dashboard.
import { computed } from 'vue'
import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import Tag from 'primevue/tag'

const props = defineProps({
  visible: { type: Boolean, default: false },
  result: { type: Object, default: null },
})
defineEmits(['update:visible'])

const cards = computed(() => {
  if (!props.result) return []
  return [
    { label: 'Matched',    value: props.result.records_matched,    tone: 'match',    icon: 'check_circle' },
    { label: 'Mismatched', value: props.result.records_mismatched, tone: 'mismatch', icon: 'cancel' },
    { label: 'Only in A',  value: props.result.keys_in_a_only,     tone: 'a',        icon: 'shift_lock' },
    { label: 'Only in B',  value: props.result.keys_in_b_only,     tone: 'b',        icon: 'shift_lock' },
    { label: 'Dups in A',  value: props.result.dups_in_a,          tone: 'warn',     icon: 'content_copy' },
    { label: 'Dups in B',  value: props.result.dups_in_b,          tone: 'warn',     icon: 'content_copy' },
  ]
})
</script>

<template>
  <Dialog
    :visible="visible"
    @update:visible="(v) => $emit('update:visible', v)"
    modal
    :closable="true"
    :style="{ width: '640px' }"
  >
    <template #header>
      <div class="head">
        <span class="material-symbols-outlined success-mark">task_alt</span>
        <span class="head-title">Run complete</span>
        <Tag v-if="result" :value="result.run_dir_name" severity="secondary" rounded />
      </div>
    </template>

    <div v-if="result" class="body">
      <div class="grid">
        <div v-for="c in cards" :key="c.label" class="metric" :class="`tone-${c.tone}`">
          <span class="material-symbols-outlined metric-icon">{{ c.icon }}</span>
          <div class="metric-label t-label">{{ c.label }}</div>
          <div class="metric-value">{{ c.value.toLocaleString() }}</div>
        </div>
      </div>
      <p class="path-line t-small">
        Output: <code class="t-mono">{{ result.run_dir_path }}</code>
      </p>
    </div>

    <template #footer>
      <div class="foot">
        <Button
          label="Dismiss"
          severity="secondary"
          variant="text"
          @click="$emit('update:visible', false)"
        />
        <a v-if="result" :href="result.report_href" target="_blank" rel="noopener">
          <Button
            label="Open report"
            icon="pi pi-external-link"
            iconPos="right"
            severity="primary"
          />
        </a>
      </div>
    </template>
  </Dialog>
</template>

<style scoped>
.head { display: flex; gap: 0.55rem; align-items: center; }
.head-title { font-size: 1.05rem; font-weight: 600; }
.success-mark { color: var(--status-match); font-size: 22px; }

.body { display: flex; flex-direction: column; gap: 0.7rem; }
.grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.55rem;
}
.metric {
  position: relative;
  padding: 0.7rem 0.75rem;
  background: var(--surface-2);
  border-radius: var(--radius-md);
  border: 1px solid var(--surface-border);
  overflow: hidden;
}
.metric-icon {
  position: absolute;
  right: 0.35rem; top: 0.35rem;
  font-size: 20px;
  opacity: 0.2;
}
.metric-label { margin-bottom: 0.15rem; font-size: 0.7rem; }
.metric-value {
  font-size: 1.4rem; font-weight: 700;
  letter-spacing: -0.01em; line-height: 1;
  color: var(--text-strong);
  font-variant-numeric: tabular-nums;
}
.tone-match .metric-value, .tone-match .metric-icon { color: var(--status-match); }
.tone-mismatch .metric-value, .tone-mismatch .metric-icon { color: var(--status-mismatch); }
.tone-warn .metric-icon { color: var(--status-warn); opacity: 0.4; }
.tone-a .metric-icon { color: var(--tone-a); }
.tone-b .metric-icon { color: var(--tone-b); }

.path-line { margin: 0.2rem 0 0; word-break: break-all; }

.foot { display: flex; justify-content: flex-end; gap: 0.5rem; }
</style>
