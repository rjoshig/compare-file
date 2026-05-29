<script setup>
// Per-side compare-key + sort controls. Lives inside FileBody so each
// file's key/sort choice sits next to its prefix/segment settings.
import { computed } from 'vue'
import Select from 'primevue/select'
import Checkbox from 'primevue/checkbox'
import SelectButton from 'primevue/selectbutton'

const props = defineProps({
  template: { type: Object, required: true },
  side: { type: Object, required: true },
})

const keyOptions = computed(() => {
  const keySeg = props.template.segments.find((s) => s.role === 'key')
  if (!keySeg) return []
  const tpl = keySeg.fields.map((f) => f.name)
  const added = (props.side.added_fields[keySeg.name] || []).map((f) => f.name)
  return [...tpl, ...added].map((name) => ({ label: name, value: name }))
})

const orderOpts = [
  { label: 'Asc', value: 'ascending' },
  { label: 'Desc', value: 'descending' },
]
const keyTypeOpts = [
  { label: 'Alpha', value: 'alphanumeric' },
  { label: 'Num',   value: 'number' },
]
</script>

<template>
  <div class="compare-key">
    <div class="row">
      <label class="row-label">Key field</label>
      <Select
        v-model="side.key_field_name"
        :options="keyOptions"
        optionLabel="label"
        optionValue="value"
        placeholder="—"
        size="small"
        fluid
      />
    </div>

    <div class="row inline">
      <label class="check-label">
        <Checkbox v-model="side.sort.input_sorted" binary />
        <span>Input is sorted</span>
      </label>
    </div>

    <div class="row inline">
      <label class="row-label small">Order</label>
      <SelectButton
        v-model="side.sort.order"
        :options="orderOpts"
        optionLabel="label"
        optionValue="value"
        :allowEmpty="false"
        class="seg"
      />
      <label class="row-label small">Type</label>
      <SelectButton
        v-model="side.sort.key_type"
        :options="keyTypeOpts"
        optionLabel="label"
        optionValue="value"
        :allowEmpty="false"
        class="seg"
      />
    </div>
  </div>
</template>

<style scoped>
.compare-key { display: flex; flex-direction: column; gap: 0.5rem; }
.row { display: flex; flex-direction: column; gap: 0.25rem; min-width: 0; }
.row.inline { flex-direction: row; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
.row-label {
  font-size: 0.72rem; font-weight: 600;
  letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--text-muted);
}
.row-label.small { font-size: 0.68rem; }
.check-label {
  display: flex; align-items: center; gap: 0.45rem;
  font-size: 0.83rem; color: var(--text-body);
  cursor: pointer;
}
.seg :deep(.p-togglebutton),
.seg :deep(.p-selectbutton .p-button) {
  padding: 0.2rem 0.55rem !important;
  font-size: 0.7rem !important;
  font-weight: 600;
  letter-spacing: 0.04em;
  min-width: 2.5rem;
}
</style>
