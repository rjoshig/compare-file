<script setup>
// Operator-declared segment aliases (ADR-034 / ADR-039). Lets the operator
// place a logical segment (e.g. EMAD) that mirrors a wire segment's layout
// (e.g. AD01) and is applied to every wire instance appearing after a trigger
// segment (e.g. EM01). Each declared alias renders as a read-only mirror card
// via SegmentEditor; the backend clones the wire segment's fields + emits the
// segment_aliases rule on save.
import { ref, computed } from 'vue'
import Select from 'primevue/select'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import SegmentEditor from './SegmentEditor.vue'

const props = defineProps({
  template: { type: Object, required: true },
  side: { type: Object, required: true },
})

const adding = ref(false)
const draft = ref({ logical_name: '', wire_name: '', after_segment: '' })

// Wires already aliased (template-baked or operator-added): one rule per wire.
const aliasedWires = computed(() => {
  const baked = props.template.segments.filter((s) => s.alias_of).map((s) => s.alias_of)
  const added = props.side.alias_segments.map((a) => a.wire_name)
  return new Set([...baked, ...added])
})
const segNames = computed(() => props.template.segments.map((s) => s.name))

const wireOptions = computed(() =>
  props.template.segments
    .filter((s) => !s.role && !s.alias_of && !aliasedWires.value.has(s.name))
    .map((s) => ({ label: s.name, value: s.name }))
)
const afterOptions = computed(() =>
  props.template.segments
    .filter((s) => s.role !== 'end' && !s.alias_of)
    .map((s) => ({ label: s.name, value: s.name }))
)

// Pseudo-segments for each operator-added alias: a read-only mirror of the
// wire segment's fields so SegmentEditor renders it like any other card.
const aliasCards = computed(() =>
  props.side.alias_segments.map((a, idx) => {
    const wire = props.template.segments.find((s) => s.name === a.wire_name)
    return {
      idx,
      segment: {
        name: a.logical_name,
        role: null,
        alias_of: a.wire_name,
        alias_after: a.after_segment,
        fields: wire ? wire.fields : [],
      },
    }
  })
)

const draftError = computed(() => {
  const d = draft.value
  if (!d.wire_name || !d.after_segment || !d.logical_name.trim()) return null // incomplete, not an error yet
  const name = d.logical_name.trim()
  if (segNames.value.includes(name)) return `Segment "${name}" already exists.`
  if (props.side.alias_segments.some((a) => a.logical_name === name))
    return `Alias "${name}" already declared.`
  return null
})
const canAdd = computed(
  () =>
    !!draft.value.wire_name &&
    !!draft.value.after_segment &&
    !!draft.value.logical_name.trim() &&
    !draftError.value
)

function startAdd() {
  draft.value = { logical_name: '', wire_name: '', after_segment: '' }
  adding.value = true
}
function confirmAdd() {
  if (!canAdd.value) return
  props.side.alias_segments.push({
    logical_name: draft.value.logical_name.trim(),
    wire_name: draft.value.wire_name,
    after_segment: draft.value.after_segment,
  })
  adding.value = false
}
function removeAlias(idx) {
  props.side.alias_segments.splice(idx, 1)
}
</script>

<template>
  <div class="alias-editor">
    <div class="alias-cards">
      <SegmentEditor
        v-for="card in aliasCards"
        :key="`alias-${card.idx}`"
        :segment="card.segment"
        :side="side"
        removable
        @remove="removeAlias(card.idx)"
      />
    </div>

    <div v-if="adding" class="add-form">
      <div class="form-row">
        <label class="row-label">Logical name</label>
        <InputText v-model="draft.logical_name" placeholder="e.g. EMAD" size="small" fluid />
      </div>
      <div class="form-row">
        <label class="row-label">Mirrors segment</label>
        <Select
          v-model="draft.wire_name"
          :options="wireOptions"
          optionLabel="label"
          optionValue="value"
          placeholder="wire segment (e.g. AD01)"
          size="small"
          fluid
        />
      </div>
      <div class="form-row">
        <label class="row-label">Applied after</label>
        <Select
          v-model="draft.after_segment"
          :options="afterOptions"
          optionLabel="label"
          optionValue="value"
          placeholder="trigger segment (e.g. EM01)"
          size="small"
          fluid
        />
      </div>
      <p v-if="draftError" class="form-err">{{ draftError }}</p>
      <div class="form-actions">
        <Button label="Cancel" severity="secondary" variant="text" size="small"
                @click="adding = false" />
        <Button label="Add alias" icon="pi pi-check" size="small"
                :disabled="!canAdd" @click="confirmAdd" />
      </div>
    </div>

    <div v-else class="add-bar">
      <Button
        label="Add alias segment"
        icon="pi pi-plus"
        severity="secondary"
        variant="outlined"
        size="small"
        :disabled="wireOptions.length === 0"
        @click="startAdd"
      />
      <span v-if="wireOptions.length === 0" class="t-small none-left">
        No un-aliased segments available.
      </span>
    </div>
  </div>
</template>

<style scoped>
.alias-editor { display: flex; flex-direction: column; gap: 0.45rem; }
.alias-cards { display: flex; flex-direction: column; gap: 0.45rem; }
.add-form {
  background: var(--surface-1);
  border: 1px dashed var(--surface-border);
  border-radius: var(--radius-md);
  padding: 0.6rem 0.75rem;
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
}
.form-row { display: flex; flex-direction: column; gap: 0.25rem; }
.row-label {
  font-size: 0.7rem; font-weight: 600;
  letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--text-muted);
}
.form-err { margin: 0; font-size: 0.74rem; color: var(--p-red-500, #e5484d); }
.form-actions { display: flex; justify-content: flex-end; gap: 0.4rem; }
.add-bar { display: flex; align-items: center; gap: 0.5rem; }
.none-left { color: var(--text-muted); }
</style>
