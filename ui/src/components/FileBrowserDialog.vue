<script setup>
// Modal file/dir browser. Mode is controlled by `pickMode`:
//   'file' (default) — show dirs + .dat/.csv/.txt files; clicking a
//                      file emits `pick` with its absolute path.
//   'dir'            — hide files; toolbar shows a "Pick this folder"
//                      button that emits the current dir's path.
import { ref, watch, computed } from 'vue'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import ProgressSpinner from 'primevue/progressspinner'
import { api } from '../services/api.js'

const props = defineProps({
  visible: { type: Boolean, default: false },
  initialPath: { type: String, default: '' },
  title: { type: String, default: 'Pick a file' },
  pickMode: { type: String, default: 'file' }, // 'file' | 'dir'
})
const emit = defineEmits(['update:visible', 'pick'])

const loading = ref(false)
const errorMsg = ref('')
const listing = ref({ path: '', parent: null, dirs: [], files: [] })
const filter = ref('')

const isDirMode = computed(() => props.pickMode === 'dir')

async function load(path) {
  loading.value = true
  errorMsg.value = ''
  try {
    listing.value = await api.browse(path || '')
  } catch (e) {
    errorMsg.value = e.message
  } finally {
    loading.value = false
  }
}

watch(
  () => props.visible,
  (v) => {
    if (v) load(props.initialPath)
  },
  { immediate: true }
)

function selectFile(file) {
  emit('pick', file.path)
  emit('update:visible', false)
}
function pickCurrentDir() {
  if (!listing.value.path) return
  emit('pick', listing.value.path)
  emit('update:visible', false)
}

function pretty(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function breadcrumbs(path) {
  if (!path) return []
  const parts = path.split('/').filter(Boolean)
  const out = []
  let acc = ''
  for (const p of parts) {
    acc += '/' + p
    out.push({ label: p, path: acc })
  }
  return out
}
</script>

<template>
  <Dialog
    :visible="visible"
    @update:visible="(v) => emit('update:visible', v)"
    :header="title"
    modal
    :style="{ width: '720px' }"
    :pt="{ root: { style: 'max-height: 80vh' } }"
  >
    <div class="browser-toolbar">
      <Button icon="pi pi-home" severity="secondary" variant="text"
              @click="load('')" title="Home" />
      <Button icon="pi pi-arrow-up" severity="secondary" variant="text"
              :disabled="!listing.parent"
              @click="load(listing.parent)" title="Up" />
      <div class="path-bar">
        <span
          v-for="(c, i) in breadcrumbs(listing.path)"
          :key="c.path"
          class="crumb"
        >
          <span class="sep" v-if="i > 0">/</span>
          <a @click.prevent="load(c.path)" href="#">{{ c.label }}</a>
        </span>
      </div>
      <InputText
        v-model="filter"
        placeholder="Filter…"
        size="small"
        style="width: 9rem"
      />
      <Button
        v-if="isDirMode"
        label="Pick this folder"
        icon="pi pi-check"
        size="small"
        severity="primary"
        @click="pickCurrentDir"
      />
    </div>

    <div v-if="loading" class="state">
      <ProgressSpinner style="width: 28px; height: 28px" strokeWidth="6" />
    </div>
    <div v-else-if="errorMsg" class="state error">
      <span class="material-symbols-outlined">error</span> {{ errorMsg }}
    </div>
    <div v-else class="entries">
      <div
        v-for="d in listing.dirs.filter((x) => !filter || x.name.toLowerCase().includes(filter.toLowerCase()))"
        :key="d.path"
        class="entry dir"
        @click="load(d.path)"
      >
        <span class="material-symbols-outlined">folder</span>
        <span class="entry-name">{{ d.name }}</span>
      </div>
      <template v-if="!isDirMode">
        <div
          v-for="f in listing.files.filter((x) => !filter || x.name.toLowerCase().includes(filter.toLowerCase()))"
          :key="f.path"
          class="entry file"
          @click="selectFile(f)"
        >
          <span class="material-symbols-outlined">draft</span>
          <span class="entry-name">{{ f.name }}</span>
          <span class="entry-size">{{ pretty(f.size) }}</span>
        </div>
      </template>
      <div
        v-if="!listing.dirs.length && (isDirMode || !listing.files.length)"
        class="state muted"
      >
        <span class="material-symbols-outlined">inbox</span>
        {{ isDirMode ? 'No subfolders here — click "Pick this folder" to use the current one.' : 'Empty' }}
      </div>
    </div>
  </Dialog>
</template>

<style scoped>
.browser-toolbar {
  display: flex;
  gap: 0.4rem;
  align-items: center;
  padding-bottom: 0.7rem;
  border-bottom: 1px solid var(--surface-border);
  margin-bottom: 0.6rem;
}
.path-bar {
  flex: 1; min-width: 0;
  overflow-x: auto; white-space: nowrap;
  font-size: 0.88rem;
}
.crumb { color: var(--text-muted); }
.crumb a { color: var(--text-strong); text-decoration: none; }
.crumb a:hover { color: var(--tone-a); text-decoration: underline; }
.sep { margin: 0 0.3rem; opacity: 0.5; }

.entries {
  display: flex; flex-direction: column;
  max-height: 50vh; overflow-y: auto;
  border-radius: 6px;
}
.entry {
  display: flex; align-items: center; gap: 0.55rem;
  padding: 0.45rem 0.6rem;
  cursor: pointer;
  border-radius: 6px;
}
.entry:hover { background: var(--surface-2); }
.entry .material-symbols-outlined { font-size: 20px; }
.entry.dir .material-symbols-outlined { color: var(--tone-b); }
.entry.file .material-symbols-outlined { color: var(--tone-a); }
.entry-name { flex: 1; min-width: 0; font-size: 0.9rem; }
.entry-size { font-size: 0.78rem; color: var(--text-muted); font-variant-numeric: tabular-nums; }

.state {
  display: flex; gap: 0.5rem; align-items: center;
  padding: 1.5rem; justify-content: center; color: var(--text-muted);
  text-align: center;
}
.state.error { color: var(--status-mismatch); }
</style>
