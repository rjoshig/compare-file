<script setup>
import Panel from 'primevue/panel'
import PrefixConfig from './PrefixConfig.vue'
import SegmentEditor from './SegmentEditor.vue'
// Segment-aliases authoring panel is hidden for now (see template below).
// import AliasSegmentEditor from './AliasSegmentEditor.vue'
import CompareKeyPanel from './CompareKeyPanel.vue'

defineProps({
  label: { type: String, required: true },
  template: { type: Object, required: true },
  side: { type: Object, required: true },
  color: { type: String, default: 'a' },
})
</script>

<template>
  <div class="file-body" :class="`tone-${color}`">
    <Panel toggleable>
      <template #header>
        <div class="panel-head">
          <span class="material-symbols-outlined">key</span>
          <span class="t-title">Compare key &amp; sort</span>
        </div>
      </template>
      <CompareKeyPanel :template="template" :side="side" />
    </Panel>

    <Panel toggleable>
      <template #header>
        <div class="panel-head">
          <span class="material-symbols-outlined">data_object</span>
          <span class="t-title">Per-record prefixes</span>
        </div>
      </template>
      <PrefixConfig :side="side" />
    </Panel>

    <Panel toggleable>
      <template #header>
        <div class="panel-head">
          <span class="material-symbols-outlined">view_list</span>
          <span class="t-title">Segments</span>
        </div>
      </template>
      <p class="t-small">
        Template fields are read-only on <code class="t-mono">name</code> and
        <code class="t-mono">length</code>. <code class="t-mono">exclude</code> is overridable
        per config. Use the <strong>+ Add field</strong> button on TU4R to append user-defined
        fields.
      </p>
      <div class="seg-stack">
        <SegmentEditor
          v-for="seg in template.segments"
          :key="seg.name"
          :segment="seg"
          :side="side"
        />
      </div>
    </Panel>

    <!--
      Segment-aliases authoring panel — hidden for now. Re-enable by
      uncommenting this block and the AliasSegmentEditor import above.
      Template-baked aliases (e.g. EMAD) still render as read-only cards
      in the Segments panel above via SegmentEditor's alias note.

    <Panel toggleable>
      <template #header>
        <div class="panel-head">
          <span class="material-symbols-outlined">alt_route</span>
          <span class="t-title">Segment aliases</span>
        </div>
      </template>
      <p class="t-small">
        Place a logical segment (e.g. <code class="t-mono">EMAD</code>) that mirrors another
        segment's layout (e.g. <code class="t-mono">AD01</code>) and is applied to every instance
        appearing <strong>after</strong> a trigger segment (e.g. <code class="t-mono">EM01</code>).
        Displayed as <em>EMAD (AD01 segment)</em>; the engine treats AD01-after-EM01 as EMAD.
      </p>
      <AliasSegmentEditor :template="template" :side="side" />
    </Panel>
    -->
  </div>
</template>

<style scoped>
.file-body { display: flex; flex-direction: column; gap: 0.65rem; min-width: 0; }
.file-body :deep(.p-panel) { box-shadow: var(--elev-1); }
.file-body :deep(.p-panel-content) { padding: 0.7rem 0.85rem !important; }
.file-body :deep(.p-panel-header) { padding: 0.55rem 0.8rem !important; }
.tone-a :deep(.p-panel-header) { border-top: 3px solid var(--tone-a); }
.tone-b :deep(.p-panel-header) { border-top: 3px solid var(--tone-b); }
.panel-head { display: flex; align-items: center; gap: 0.45rem; }
.panel-head .material-symbols-outlined { font-size: 18px; color: var(--text-muted); }
.seg-stack { display: flex; flex-direction: column; gap: 0.45rem; margin-top: 0.4rem; }
.t-small { margin: 0 0 0.4rem; font-size: 0.78rem; }
</style>
