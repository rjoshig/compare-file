<script setup>
import { computed } from 'vue'
import Checkbox from 'primevue/checkbox'
import InputNumber from 'primevue/inputnumber'
import SelectButton from 'primevue/selectbutton'

const props = defineProps({ side: { type: Object, required: true } })

const stripEnabled = computed({
  get: () => props.side.strip_leading_bytes.enabled,
  set: (v) => {
    props.side.strip_leading_bytes.enabled = v
    if (!v) props.side.strip_leading_bytes.size = null
  },
})
const rdwEnabled = computed({
  get: () => props.side.rdw.enabled,
  set: (v) => {
    props.side.rdw.enabled = v
    if (!v) {
      props.side.rdw.rdw1_bytes = null
      props.side.rdw.rdw2_bytes = null
    }
  },
})

const stripEncOpts = [
  { label: 'BIN', value: 'binary' },
  { label: 'ASC', value: 'ascii' },
]
const rdwEncOpts = [
  { label: 'BIN', value: 'binary_le_uint' },
  { label: 'ASC', value: 'ascii_int' },
]
</script>

<template>
  <div class="prefix">
    <div class="row" :class="{ disabled: !stripEnabled }">
      <Checkbox v-model="stripEnabled" inputId="stripT" binary />
      <label for="stripT" class="row-label code">strip_leading_bytes</label>
      <InputNumber
        v-model="side.strip_leading_bytes.size"
        :disabled="!stripEnabled"
        :min="1"
        placeholder="bytes"
        :showButtons="false"
        class="cell-num"
      />
      <SelectButton
        v-model="side.strip_leading_bytes.encoding"
        :options="stripEncOpts"
        optionLabel="label"
        optionValue="value"
        :disabled="!stripEnabled"
        :allowEmpty="false"
        class="cell-seg"
      />
    </div>

    <div class="row" :class="{ disabled: !rdwEnabled }">
      <Checkbox v-model="rdwEnabled" inputId="rdwT" binary />
      <label for="rdwT" class="row-label code">rdw</label>
      <InputNumber
        v-model="side.rdw.rdw1_bytes"
        :disabled="!rdwEnabled"
        :min="1"
        placeholder="r1"
        :showButtons="false"
        class="cell-num"
      />
      <InputNumber
        v-model="side.rdw.rdw2_bytes"
        :disabled="!rdwEnabled"
        :min="1"
        placeholder="r2"
        :showButtons="false"
        class="cell-num"
      />
      <SelectButton
        v-model="side.rdw.encoding"
        :options="rdwEncOpts"
        optionLabel="label"
        optionValue="value"
        :disabled="!rdwEnabled"
        :allowEmpty="false"
        class="cell-seg"
      />
    </div>
  </div>
</template>

<style scoped>
.prefix {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  width: 100%;
}
.row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) 3.4rem 3.4rem auto;
  align-items: center;
  gap: 0.4rem;
  padding: 0.32rem 0.5rem;
  background: var(--surface-2);
  border-radius: var(--radius-sm);
  min-width: 0;
}
/* strip_leading_bytes has one number cell instead of two. */
.prefix .row:first-child {
  grid-template-columns: auto minmax(0, 1fr) 3.4rem 0 auto;
}
.row.disabled { opacity: 0.55; }
.row-label {
  font-size: 0.77rem;
  font-weight: 500;
  color: var(--text-strong);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cell-num { min-width: 0; }
.cell-num :deep(input) {
  width: 100% !important;
  padding: 0.22rem 0.4rem !important;
  font-size: 0.78rem !important;
  text-align: right;
}
/* Segmented BIN | ASC control — never overflows, no popup. */
.cell-seg :deep(.p-togglebutton),
.cell-seg :deep(.p-selectbutton .p-button) {
  padding: 0.22rem 0.5rem !important;
  font-size: 0.7rem !important;
  font-weight: 600;
  letter-spacing: 0.04em;
  min-width: 2.4rem;
}
</style>
