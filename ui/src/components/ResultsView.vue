<script setup>
// Results view: shows the most recent run's metrics + per-segment breakdown
// and links out to the full HTML report (which carries the sample records:
// matches, mismatches side-by-side, dups, orphans — ADR-040). Sample tables
// live in the report, not here, to keep one source of truth.
import { ref, computed, watch, onMounted } from 'vue'
import Button from 'primevue/button'
import RunResultPanel from './RunResultPanel.vue'
import { lastRun } from '../composables/run.js'

const emit = defineEmits(['go-config'])

const summary = ref(null)
const loadingSummary = ref(false)
const summaryError = ref('')

const reportHref = computed(() =>
  lastRun.value ? lastRun.value.report_href || lastRun.value.report_url : null
)

async function loadSummary() {
  summary.value = null
  summaryError.value = ''
  if (!lastRun.value?.report_url) return
  // report_url is "/api/runs/<token>/report"; summary.json sits beside it.
  const url = lastRun.value.report_url.replace(/\/report$/, '/summary.json')
  loadingSummary.value = true
  try {
    const r = await fetch(url)
    if (!r.ok) throw new Error(`${r.status}`)
    summary.value = await r.json()
  } catch (e) {
    summaryError.value = `Could not load per-segment detail (${e.message}).`
  } finally {
    loadingSummary.value = false
  }
}

onMounted(loadSummary)
watch(lastRun, loadSummary)
</script>

<template>
  <div v-if="!lastRun" class="empty">
    <span class="material-symbols-outlined">analytics</span>
    <p class="t-headline">No results yet</p>
    <p class="t-small">Run a comparison from Field Configuration to see results here.</p>
    <Button label="Go to Field Config" icon="pi pi-arrow-right" size="small"
            @click="emit('go-config')" />
  </div>

  <div v-else class="results">
    <RunResultPanel :result="lastRun" />

    <section class="card seg-card">
      <header class="seg-head">
        <h3>Per-segment breakdown</h3>
        <a v-if="reportHref" :href="reportHref" target="_blank" class="report-link">
          <span class="material-symbols-outlined">open_in_new</span>
          Open full report
        </a>
      </header>

      <p v-if="loadingSummary" class="t-small">Loading…</p>
      <p v-else-if="summaryError" class="t-small err">{{ summaryError }}</p>

      <table v-else-if="summary" class="seg-table">
        <thead>
          <tr>
            <th>Segment</th>
            <th class="num">Match</th>
            <th class="num">Mismatch</th>
            <th class="num">Total in A</th>
            <th class="num">Total in B</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in summary.per_segment" :key="s.segment_name">
            <td class="code">{{ s.segment_name }}</td>
            <td class="num">{{ s.match_count }}</td>
            <td class="num" :class="{ bad: s.mismatch_count > 0 }">{{ s.mismatch_count }}</td>
            <td class="num">{{ s.total_in_a }}</td>
            <td class="num">{{ s.total_in_b }}</td>
          </tr>
        </tbody>
      </table>

      <p class="t-small note">
        Sample records (matched, mismatched A↔B, duplicates, orphans) are in the
        <a v-if="reportHref" :href="reportHref" target="_blank">full report</a>.
      </p>
    </section>
  </div>
</template>

<style scoped>
.empty {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 0.5rem; text-align: center; height: 100%; color: var(--text-muted);
}
.empty .material-symbols-outlined { font-size: 56px; opacity: 0.5; }

.results { display: flex; flex-direction: column; gap: 0.85rem; }
.card {
  background: var(--surface-1);
  border: 1px solid var(--surface-border);
  border-radius: var(--radius-md, 12px);
  padding: 0.85rem 1rem;
  box-shadow: var(--elev-1);
}
.seg-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.5rem; }
.seg-head h3 { margin: 0; font-size: 0.95rem; color: var(--text-strong); }
.report-link {
  display: inline-flex; align-items: center; gap: 0.3rem;
  font-size: 0.82rem; font-weight: 600; color: var(--tone-a); text-decoration: none;
}
.report-link .material-symbols-outlined { font-size: 16px; }
.seg-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
.seg-table th, .seg-table td {
  padding: 0.35rem 0.5rem; border-bottom: 1px solid var(--surface-divider); text-align: left;
}
.seg-table th {
  font-size: 0.66rem; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--text-muted);
}
.seg-table .num { text-align: right; }
.seg-table td.bad { color: var(--p-red-500, #e5484d); font-weight: 600; }
.note { margin: 0.6rem 0 0; color: var(--text-muted); }
.err { color: var(--p-red-500, #e5484d); }
</style>
