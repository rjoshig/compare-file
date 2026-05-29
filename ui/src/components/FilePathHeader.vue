<script setup>
import { ref } from 'vue'
import IconField from 'primevue/iconfield'
import InputIcon from 'primevue/inputicon'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import FileBrowserDialog from './FileBrowserDialog.vue'

const props = defineProps({
  label: { type: String, required: true },
  color: { type: String, default: 'a' },
  filePath: { type: String, default: '' },
  placeholder: { type: String, default: '' },
})
const emit = defineEmits(['update:filePath'])

const dialogOpen = ref(false)

function openBrowser() {
  dialogOpen.value = true
}
function onPick(path) {
  emit('update:filePath', path)
}
</script>

<template>
  <div class="file-header" :class="`tone-${color}`">
    <div class="pill">
      <span class="material-symbols-outlined">description</span>
      {{ label }}
    </div>
    <IconField class="path-field">
      <InputIcon class="pi pi-folder-open" />
      <InputText
        :model-value="filePath"
        :placeholder="placeholder"
        @update:model-value="$emit('update:filePath', $event)"
        size="small"
        fluid
      />
    </IconField>
    <Button
      icon="pi pi-folder"
      label="Browse"
      severity="secondary"
      variant="outlined"
      size="small"
      @click="openBrowser"
    />
    <FileBrowserDialog
      v-model:visible="dialogOpen"
      :initial-path="filePath"
      :title="`Pick ${label}`"
      @pick="onPick"
    />
  </div>
</template>

<style scoped>
.file-header {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.5rem 0.65rem;
  background: var(--surface-1);
  border: 1px solid var(--surface-border);
  border-radius: var(--radius-md);
  box-shadow: var(--elev-1);
}
.file-header.tone-a { border-left: 3px solid var(--tone-a); }
.file-header.tone-b { border-left: 3px solid var(--tone-b); }
.pill {
  display: flex; align-items: center; gap: 0.3rem;
  padding: 0.25rem 0.6rem;
  border-radius: 999px;
  font-weight: 600;
  font-size: 0.78rem;
  letter-spacing: 0.02em;
}
.tone-a .pill { background: var(--tone-a-soft); color: var(--tone-a); }
.tone-b .pill { background: var(--tone-b-soft); color: var(--tone-b); }
.pill .material-symbols-outlined { font-size: 16px; }
.path-field { flex: 1; min-width: 0; }
</style>
