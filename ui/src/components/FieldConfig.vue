<script setup>
// Main dashboard view. Left column: sticky file-path strip + two
// per-file panels (Prefixes / Segments). Right column: a fixed-width
// context panel (Run config + Compare Key A/B + Save & Run).
import { reactive, ref, onMounted, computed } from 'vue'
import { useToast } from 'primevue/usetoast'
import ProgressSpinner from 'primevue/progressspinner'

import FilePathHeader from './FilePathHeader.vue'
import FileBody from './FileBody.vue'
import SidePanel from './SidePanel.vue'
import RunResultPanel from './RunResultPanel.vue'
import RunResultDialog from './RunResultDialog.vue'

import { api } from '../services/api.js'
import { useTheme } from '../composables/theme.js'

const { theme } = useTheme()
const toast = useToast()
const loading = ref(true)
const templates = ref(null)

const sides = reactive({
  A: makeEmptySide(),
  B: makeEmptySide(),
})

const configName = ref('')
const outputDir = ref('/tmp/segment_compare/runs')
const runResult = ref(null)
const resultDialogOpen = ref(false)
const busy = ref(false)

function makeEmptySide() {
  return {
    file_path: '',
    strip_leading_bytes: { enabled: false, size: null, encoding: 'binary' },
    rdw: { enabled: false, rdw1_bytes: null, rdw2_bytes: null, encoding: 'binary_le_uint' },
    sort: { input_sorted: true, order: 'ascending', key_type: 'alphanumeric' },
    exclude_overrides: {},
    added_fields: {},
    key_field_name: '',
    alias_segments: [],
  }
}

onMounted(async () => {
  try {
    templates.value = await api.templateLayouts()
    for (const label of ['A', 'B']) {
      const layout = templates.value[`layout_${label.toLowerCase()}`]
      const keySeg = layout.segments.find((s) => s.role === 'key')
      const keyField = keySeg ? keySeg.fields.find((f) => f.key) : null
      sides[label].key_field_name = keyField ? keyField.name : ''
    }
    toast.add({
      severity: 'info',
      summary: 'Templates loaded',
      detail: `${templates.value.layout_a.segments.length} segments per side.`,
      life: 1500,
    })
  } catch (e) {
    toast.add({ severity: 'error', summary: 'Templates', detail: e.message, life: 6000 })
  } finally {
    loading.value = false
  }
})

const canRun = computed(
  () =>
    !!sides.A.file_path &&
    !!sides.B.file_path &&
    !!sides.A.key_field_name &&
    !!sides.B.key_field_name
)

async function saveAndRun() {
  runResult.value = null
  busy.value = true
  try {
    const saved = await api.saveConfig({
      name: configName.value.trim() || null,
      file_a: sides.A,
      file_b: sides.B,
    })
    toast.add({
      severity: 'info',
      summary: 'Saved',
      detail: `Config "${saved.name}" written.`,
      life: 2200,
    })
    const result = await api.runCompare({
      config_name: saved.name,
      output_dir: outputDir.value,
    })
    // Tag the report URL with the current theme so the new tab opens
    // in the matching color scheme.
    // Backend returns report_url like `/api/runs/{token}/report`; append the
    // current theme so the new tab opens in the matching color scheme.
    result.report_href = `${result.report_url}?theme=${theme.value}`
    runResult.value = result
    resultDialogOpen.value = true
  } catch (e) {
    toast.add({ severity: 'error', summary: 'Run failed', detail: e.message, life: 8000 })
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div v-if="loading" class="loading">
    <ProgressSpinner style="width: 42px; height: 42px" strokeWidth="5" />
    <p class="t-small">Loading templates…</p>
  </div>

  <div v-else class="dashboard">
    <div class="main-col">
      <!-- Sticky file header strip below the top app bar. -->
      <section class="sticky-files">
        <div class="two-col">
          <FilePathHeader
            label="File A"
            color="a"
            v-model:filePath="sides.A.file_path"
            placeholder="/path/to/your/a.dat"
          />
          <FilePathHeader
            label="File B"
            color="b"
            v-model:filePath="sides.B.file_path"
            placeholder="/path/to/your/b.dat"
          />
        </div>
      </section>

      <section class="two-col bodies">
        <FileBody label="File A" :template="templates.layout_a" :side="sides.A" color="a" />
        <FileBody label="File B" :template="templates.layout_b" :side="sides.B" color="b" />
      </section>

      <RunResultPanel v-if="runResult" :result="runResult" />
    </div>

    <RunResultDialog
      v-model:visible="resultDialogOpen"
      :result="runResult"
    />

    <SidePanel
      v-model:configName="configName"
      v-model:outputDir="outputDir"
      :busy="busy"
      :canRun="canRun"
      @run="saveAndRun"
    />
  </div>
</template>

<style scoped>
.loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.6rem;
  margin: 5rem auto;
  color: var(--text-muted);
}
.dashboard {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  gap: 0.85rem;
}
.main-col { min-width: 0; }
.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.75rem;
}
.sticky-files {
  position: sticky;
  top: 56px; /* sits just below the AppBar */
  z-index: 20;
  background: var(--surface-sticky);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--surface-border);
  padding: 0.55rem 1.25rem;
  margin: -0.9rem -1.25rem 0.5rem;
}
.bodies { margin-top: 0.35rem; }

@media (max-width: 1100px) {
  .dashboard { grid-template-columns: 1fr; }
  .two-col { grid-template-columns: 1fr; }
}
</style>
