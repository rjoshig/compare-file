<script setup>
import { computed } from 'vue'
import Tag from 'primevue/tag'
import Checkbox from 'primevue/checkbox'
import InputText from 'primevue/inputtext'
import InputNumber from 'primevue/inputnumber'
import Button from 'primevue/button'

const props = defineProps({
  segment: { type: Object, required: true },
  side: { type: Object, required: true },
  // When this is an operator-added alias segment, show a remove control.
  removable: { type: Boolean, default: false },
})
const emit = defineEmits(['remove'])

const isKeySegment = computed(() => props.segment.role === 'key')
// Alias-target segment (e.g. EMAD mirroring AD01 after EM01, ADR-034). Its
// field table is a read-only mirror of the wire segment; excludes follow the
// wire segment, so they aren't editable here.
const isAlias = computed(() => !!props.segment.alias_of)
const addedFields = computed(() => props.side.added_fields[props.segment.name] || [])

const hasUnsavedAdded = computed(() => addedFields.value.some((f) => !f._saved))

const totalSize = computed(() => {
  const HEADER = 7
  const tpl = props.segment.fields.reduce((acc, f) => acc + f.length, 0)
  const added = addedFields.value.reduce((acc, f) => acc + (Number(f.length) || 0), 0)
  return HEADER + tpl + added
})

function getOverride(name) {
  const k = `${props.segment.name}.${name}`
  if (props.side.exclude_overrides[k] === undefined) {
    const fld = props.segment.fields.find((f) => f.name === name)
    return fld ? fld.exclude : false
  }
  return props.side.exclude_overrides[k]
}
function setOverride(name, value) {
  props.side.exclude_overrides[`${props.segment.name}.${name}`] = value
}
function addField() {
  if (!props.side.added_fields[props.segment.name]) {
    props.side.added_fields[props.segment.name] = []
  }
  props.side.added_fields[props.segment.name].push({
    name: '',
    length: 1,
    exclude: false,
    key: false,
    _saved: false,
  })
}
function saveFields() {
  // Lock the unsaved added rows: blank-named rows are dropped, the
  // rest flip to a read-only display.
  const list = props.side.added_fields[props.segment.name] || []
  props.side.added_fields[props.segment.name] = list
    .filter((f) => (f.name || '').trim().length > 0 && (Number(f.length) || 0) > 0)
    .map((f) => ({ ...f, _saved: true }))
}
function unlockField(idx) {
  // "Edit" — flip the row back to an editable input.
  const list = props.side.added_fields[props.segment.name]
  if (list[idx]) list[idx]._saved = false
}
function removeAdded(idx) {
  props.side.added_fields[props.segment.name].splice(idx, 1)
}
</script>

<template>
  <article class="seg-card">
    <header class="seg-head">
      <div class="seg-name-row">
        <span class="seg-name code">{{ segment.name }}</span>
        <Tag v-if="segment.role === 'key'" value="key" severity="info" rounded />
        <Tag v-else-if="segment.role === 'end'" value="end" severity="secondary" rounded />
        <Tag v-if="isAlias" value="alias" severity="warn" rounded />
      </div>
      <div class="seg-actions">
        <span class="size-readout">Total <strong>{{ totalSize }}</strong></span>
        <Button
          v-if="isKeySegment && hasUnsavedAdded"
          label="Save fields"
          icon="pi pi-save"
          severity="primary"
          size="small"
          variant="text"
          @click="saveFields"
        />
        <Button
          v-if="removable"
          icon="pi pi-times"
          severity="danger"
          variant="text"
          rounded
          size="small"
          aria-label="remove alias"
          @click="emit('remove')"
        />
      </div>
    </header>

    <p v-if="isAlias" class="alias-note">
      <span class="code">{{ segment.name }}</span> ({{ segment.alias_of }} segment)
      · applied to <span class="code">{{ segment.alias_of }}</span> after
      <span class="code">{{ segment.alias_after }}</span>
    </p>

    <table class="fields">
      <thead>
        <tr>
          <th>Field</th>
          <th class="num">Length</th>
          <th class="num">Exclude</th>
          <th class="src"></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="fld in segment.fields" :key="fld.name">
          <td class="field-cell">
            <span class="code">{{ fld.name }}</span>
            <Tag v-if="fld.key" value="KEY" severity="info" class="key-badge" rounded />
          </td>
          <td class="num code">{{ fld.length }}</td>
          <td class="num">
            <Checkbox
              :model-value="getOverride(fld.name)"
              binary
              :disabled="isAlias"
              @update:model-value="(v) => setOverride(fld.name, v)"
            />
          </td>
          <td class="src"></td>
        </tr>
        <tr v-for="(fld, i) in addedFields" :key="`added-${i}`" class="user-row">
          <template v-if="fld._saved">
            <td class="field-cell">
              <span class="code">{{ fld.name }}</span>
            </td>
            <td class="num code">{{ fld.length }}</td>
            <td class="num">
              <Checkbox v-model="fld.exclude" binary />
            </td>
            <td class="src actions">
              <Button icon="pi pi-pencil" severity="secondary" variant="text"
                      rounded size="small" aria-label="edit"
                      @click="unlockField(i)" />
              <Button icon="pi pi-times" severity="danger" variant="text"
                      rounded size="small" aria-label="remove"
                      @click="removeAdded(i)" />
            </td>
          </template>
          <template v-else>
            <td>
              <InputText v-model="fld.name" placeholder="e.g. branch_code"
                         size="small" fluid />
            </td>
            <td class="num">
              <InputNumber v-model="fld.length" :min="1" placeholder="bytes"
                           :showButtons="false" size="small" class="len-input" />
            </td>
            <td class="num">
              <Checkbox v-model="fld.exclude" binary />
            </td>
            <td class="src">
              <Button icon="pi pi-times" severity="danger" variant="text"
                      rounded size="small" aria-label="remove"
                      @click="removeAdded(i)" />
            </td>
          </template>
        </tr>
      </tbody>
    </table>

    <div v-if="isKeySegment" class="add-bar">
      <Button label="Add field" icon="pi pi-plus" severity="secondary"
              variant="outlined" size="small" @click="addField" />
    </div>
  </article>
</template>

<style scoped>
.seg-card {
  background: var(--surface-1);
  border: 1px solid var(--surface-border);
  border-radius: var(--radius-md);
  padding: 0.6rem 0.75rem 0.5rem;
}
.seg-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.35rem;
}
.seg-name-row { display: flex; gap: 0.45rem; align-items: center; }
.seg-name { font-weight: 600; font-size: 0.92rem; color: var(--text-strong); }
.alias-note {
  margin: 0 0 0.4rem;
  font-size: 0.72rem;
  font-style: italic;
  color: var(--text-muted);
}
.alias-note .code { font-style: normal; }
.seg-actions { display: flex; gap: 0.5rem; align-items: center; }
.size-readout { font-size: 0.8rem; color: var(--text-muted); }
.size-readout strong { color: var(--text-strong); font-weight: 600; }

table.fields { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 0.85rem; }
table.fields th {
  font-size: 0.65rem;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--text-muted);
  padding: 0.3rem 0.45rem;
  text-align: left;
  border-bottom: 1px solid var(--surface-divider);
}
table.fields td {
  padding: 0.28rem 0.45rem;
  border-bottom: 1px solid var(--surface-divider);
  vertical-align: middle;
}
table.fields tbody tr:last-child td { border-bottom: none; }
table.fields tbody tr:hover { background: var(--surface-2); }
table.fields .num { text-align: right; }
table.fields .src { width: 4.5rem; text-align: right; }
.field-cell { display: flex; align-items: center; gap: 0.35rem; }
.key-badge { font-size: 0.6rem; }
.actions { display: flex; gap: 0.15rem; justify-content: flex-end; }

.user-row :deep(.p-inputnumber input),
.user-row :deep(.p-inputtext) { width: 100%; padding: 0.3rem 0.5rem; font-size: 0.85rem; }
.user-row .len-input :deep(input) { width: 4.5rem !important; text-align: right; }

.add-bar { margin-top: 0.4rem; text-align: right; }
</style>
