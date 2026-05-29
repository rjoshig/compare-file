<script setup>
// Run History (ADR-041): directory-driven. Point it at an output directory and
// it shows the newest 5 `report-*` runs found there (read from each run's
// summary.json). No server-side state — what you see is what's on disk.
import { ref, onMounted } from 'vue'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import { api } from '../services/api.js'
import { useTheme } from '../composables/theme.js'
import { setLastRun } from '../composables/run.js'
import FileBrowserDialog from './FileBrowserDialog.vue'

const emit = defineEmits(['go-results', 'go-config'])
const { theme } = useTheme()

const outputDir = ref('/tmp/segment_compare/runs')
const runs = ref([])
const loading = ref(true)
const error = ref('')
const dirDialogOpen = ref(false)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.listRuns(outputDir.value)
    runs.value = res.runs || []
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function onPickDir(p) {
  outputDir.value = p
  load()
}

function fmtTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return isNaN(d) ? iso : d.toLocaleString()
}

function reportHref(run) {
  return `${run.report_url}?theme=${theme.value}`
}

function openInResults(run) {
  setLastRun({ ...run, report_href: reportHref(run) })
  emit('go-results')
}

onMounted(load)
</script>

<template>
  <div class="runs">
    <header class="runs-head">
      <div class="head-text">
        <h2 class="t-title">Run History</h2>
        <p class="t-small">Newest 5 runs in the selected output directory.</p>
      </div>
    </header>

    <div class="dir-bar">
      <label class="t-label">Output directory</label>
      <div class="dir-row">
        <InputText
          v-model="outputDir"
          placeholder="/path/to/output"
          size="small"
          fluid
          @keyup.enter="load"
        />
        <Button
          icon="pi pi-folder-open"
          severity="secondary"
          variant="outlined"
          size="small"
          aria-label="Browse output directory"
          @click="dirDialogOpen = true"
        />
        <Button
          label="Refresh"
          icon="pi pi-refresh"
          severity="secondary"
          size="small"
          :loading="loading"
          @click="load"
        />
      </div>
    </div>

    <p v-if="loading" class="t-small">Loading…</p>
    <p v-else-if="error" class="t-small err">{{ error }}</p>

    <div v-else-if="!runs.length" class="empty">
      <span class="material-symbols-outlined">history</span>
      <p class="t-headline">No runs found</p>
      <p class="t-small">
        No <code class="t-mono">report-*</code> folders in this directory. Run a comparison, or
        pick a different output directory.
      </p>
      <Button label="Go to Field Config" icon="pi pi-arrow-right" size="small"
              @click="emit('go-config')" />
    </div>

    <div v-else class="card table-wrap">
      <table class="runs-table">
        <thead>
          <tr>
            <th>When</th>
            <th>Files (A ↔ B)</th>
            <th class="num">Matched</th>
            <th class="num">Mismatched</th>
            <th class="num">Only A</th>
            <th class="num">Only B</th>
            <th class="num">Dups A</th>
            <th class="num">Dups B</th>
            <th class="act"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="run in runs" :key="run.run_dir_path">
            <td class="when">{{ fmtTime(run.created_at) }}</td>
            <td class="code files">{{ run.file_a || '—' }} ↔ {{ run.file_b || '—' }}</td>
            <td class="num">{{ run.records_matched }}</td>
            <td class="num" :class="{ bad: run.records_mismatched > 0 }">
              {{ run.records_mismatched }}
            </td>
            <td class="num">{{ run.keys_in_a_only }}</td>
            <td class="num">{{ run.keys_in_b_only }}</td>
            <td class="num">{{ run.dups_in_a }}</td>
            <td class="num">{{ run.dups_in_b }}</td>
            <td class="act">
              <Button
                label="Results"
                size="small"
                severity="secondary"
                variant="text"
                @click="openInResults(run)"
              />
              <a :href="reportHref(run)" target="_blank" class="report-link">
                <span class="material-symbols-outlined">open_in_new</span>
                Report
              </a>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <FileBrowserDialog
      v-model:visible="dirDialogOpen"
      :initial-path="outputDir"
      title="Pick output directory"
      pick-mode="dir"
      @pick="onPickDir"
    />
  </div>
</template>

<style scoped>
.runs { display: flex; flex-direction: column; gap: 0.75rem; }
.runs-head h2 { margin: 0; }
.runs-head .t-small { margin: 0.1rem 0 0; }

.dir-bar { display: flex; flex-direction: column; gap: 0.3rem; max-width: 640px; }
.dir-row { display: flex; gap: 0.4rem; align-items: stretch; }
.dir-row :deep(.p-inputtext) { flex: 1; min-width: 0; }

.empty {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 0.5rem; text-align: center; padding: 3rem 1rem; color: var(--text-muted);
}
.empty .material-symbols-outlined { font-size: 56px; opacity: 0.5; }
.empty .t-small { max-width: 30rem; }

.card {
  background: var(--surface-1);
  border: 1px solid var(--surface-border);
  border-radius: var(--radius-md, 12px);
  box-shadow: var(--elev-1);
}
.table-wrap { overflow-x: auto; }
.runs-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; min-width: 720px; }
.runs-table th, .runs-table td {
  padding: 0.5rem 0.7rem; border-bottom: 1px solid var(--surface-divider); text-align: left;
  white-space: nowrap;
}
.runs-table thead th {
  font-size: 0.66rem; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--text-muted);
}
.runs-table tbody tr:last-child td { border-bottom: none; }
.runs-table tbody tr:hover { background: var(--surface-2); }
.runs-table .num { text-align: right; }
.runs-table td.bad { color: var(--p-red-500, #e5484d); font-weight: 600; }
.files { max-width: 22rem; overflow: hidden; text-overflow: ellipsis; }
.when { color: var(--text-body); }
.act {
  text-align: right; display: flex; gap: 0.5rem; align-items: center; justify-content: flex-end;
}
.report-link {
  display: inline-flex; align-items: center; gap: 0.25rem;
  font-size: 0.8rem; font-weight: 600; color: var(--tone-a); text-decoration: none;
}
.report-link .material-symbols-outlined { font-size: 15px; }
.err { color: var(--p-red-500, #e5484d); }
</style>
