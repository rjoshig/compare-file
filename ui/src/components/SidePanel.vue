<script setup>
import { ref } from 'vue'
import Panel from 'primevue/panel'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import ProgressBar from 'primevue/progressbar'
import FileBrowserDialog from './FileBrowserDialog.vue'

defineProps({
  configName: { type: String, default: '' },
  outputDir: { type: String, default: '' },
  busy: { type: Boolean, default: false },
  canRun: { type: Boolean, default: false },
})
const emit = defineEmits(['update:configName', 'update:outputDir', 'run'])

const dirDialogOpen = ref(false)
</script>

<template>
  <aside class="side-panel">
    <Panel toggleable>
      <template #header>
        <div class="panel-head">
          <span class="material-symbols-outlined">play_circle</span>
          <span class="t-title">Run</span>
        </div>
      </template>
      <div class="stack-sm">
        <div class="field">
          <label class="t-label">Config name <span class="muted">(optional)</span></label>
          <InputText
            :model-value="configName"
            @update:model-value="emit('update:configName', $event)"
            placeholder="e.g. prod-feed-monthly"
            size="small"
            fluid
          />
        </div>
        <div class="field">
          <label class="t-label">Output directory</label>
          <div class="dir-row">
            <InputText
              :model-value="outputDir"
              @update:model-value="emit('update:outputDir', $event)"
              placeholder="/tmp/segment_compare/runs"
              size="small"
              fluid
            />
            <Button
              icon="pi pi-folder-open"
              severity="secondary"
              variant="outlined"
              size="small"
              aria-label="Browse output directory"
              @click="dirDialogOpen = true"
            />
          </div>
        </div>

        <div class="run-stack" :class="{ busy }">
          <Button
            class="run-btn"
            :label="busy ? 'Comparing files…' : 'Save & Run'"
            severity="primary"
            :disabled="!canRun || busy"
            :loading="busy"
            @click="emit('run')"
            fluid
          >
            <template #icon>
              <span v-if="busy" class="material-symbols-outlined spin-icon">progress_activity</span>
              <span v-else class="material-symbols-outlined">play_arrow</span>
            </template>
          </Button>
          <ProgressBar
            v-if="busy"
            mode="indeterminate"
            class="run-progress"
            :pt="{ root: { style: 'height: 4px' } }"
          />
        </div>
      </div>
    </Panel>

    <FileBrowserDialog
      v-model:visible="dirDialogOpen"
      :initial-path="outputDir"
      title="Pick output directory"
      pick-mode="dir"
      @pick="(p) => emit('update:outputDir', p)"
    />
  </aside>
</template>

<style scoped>
.side-panel {
  position: sticky;
  top: 68px;
  align-self: start;
  display: flex;
  flex-direction: column;
  gap: 0.55rem;
  max-height: calc(100vh - 88px);
  overflow: auto;
  padding-right: 2px;
}
.side-panel :deep(.p-panel) { box-shadow: var(--elev-1); }
.side-panel :deep(.p-panel-content) { padding: 0.7rem 0.85rem; }
.panel-head { display: flex; align-items: center; gap: 0.5rem; }
.panel-head .material-symbols-outlined { font-size: 18px; color: var(--text-muted); }

.field { display: flex; flex-direction: column; gap: 0.25rem; }
.dir-row { display: flex; gap: 0.35rem; align-items: stretch; }
.dir-row :deep(.p-inputtext) { flex: 1; min-width: 0; }

.run-stack { display: flex; flex-direction: column; gap: 0; position: relative; margin-top: 0.25rem; }
.run-btn { font-weight: 600; }
.run-stack.busy .run-btn {
  cursor: progress !important;
  box-shadow: 0 0 0 4px var(--tone-a-soft);
  animation: pulse 1.4s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { box-shadow: 0 0 0 4px var(--tone-a-soft); }
  50%      { box-shadow: 0 0 0 8px var(--tone-a-soft); }
}
.run-progress {
  margin-top: -2px;
  border-bottom-left-radius: 8px;
  border-bottom-right-radius: 8px;
  overflow: hidden;
}
.spin-icon {
  font-size: 18px;
  margin-right: 0.4rem;
  animation: spin 1s linear infinite;
  display: inline-block;
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}
</style>
