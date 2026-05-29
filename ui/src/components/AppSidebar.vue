<script setup>
// Sakai-style collapsible sidebar. Two modes:
//
//   expanded  (240px) — icon + label, section headings visible
//   collapsed (64px)  — icon-only rail, label shown as tooltip
//
// On mobile (<992px) the sidebar becomes an overlay; the wrapper class
// `mobile-open` slides it in.

import Tooltip from 'primevue/tooltip'
import { useLayout } from '../composables/layout.js'

defineProps({ active: { type: String, default: 'field-config' } })
defineEmits(['navigate'])

const { sidebarCollapsed } = useLayout()

// Nav visibility is controlled from ui/.env (VITE_SHOW_*). Shown by default;
// set the var to "false" to hide that item. Field Config is always shown.
const showRunHistory = import.meta.env.VITE_SHOW_RUN_HISTORY !== 'false'
const showResults = import.meta.env.VITE_SHOW_RESULTS !== 'false'

const primary = [
  { key: 'field-config', label: 'Field Config', icon: 'tune', enabled: true },
  ...(showRunHistory
    ? [{ key: 'runs', label: 'Run History', icon: 'history', enabled: true }]
    : []),
  ...(showResults
    ? [{ key: 'results', label: 'Results', icon: 'analytics', enabled: true }]
    : []),
]

const vTooltip = Tooltip
</script>

<template>
  <aside class="sidebar">
    <div class="brand">
      <div class="brand-mark">
        <span class="material-symbols-outlined">compare_arrows</span>
      </div>
      <div class="brand-text">
        <div class="brand-name">Segment Compare</div>
      </div>
    </div>

    <nav class="nav-section">
      <p class="nav-heading">Tools</p>
      <button
        v-for="item in primary"
        :key="item.key"
        class="nav-item"
        :class="{ active: active === item.key, disabled: !item.enabled }"
        :disabled="!item.enabled"
        v-tooltip.right="sidebarCollapsed ? item.label : null"
        @click="item.enabled && $emit('navigate', item.key)"
      >
        <span class="material-symbols-outlined">{{ item.icon }}</span>
        <span class="nav-label">{{ item.label }}</span>
        <span v-if="!item.enabled" class="soon">soon</span>
      </button>
    </nav>
  </aside>
</template>

<style scoped>
.sidebar {
  position: fixed;
  top: 0; left: 0;
  width: var(--sidebar-w, 240px);
  height: 100vh;
  background: var(--surface-1);
  border-right: 1px solid var(--surface-border);
  display: flex;
  flex-direction: column;
  padding: 0.7rem 0.55rem;
  z-index: 50;
  overflow: hidden;
  transition: width 0.2s ease, transform 0.2s ease;
}
.layout-wrapper.collapsed .sidebar { width: var(--sidebar-w-collapsed, 64px); }

.brand {
  display: flex; gap: 0.65rem; align-items: center;
  padding: 0.3rem 0.5rem 0.9rem;
  border-bottom: 1px solid var(--surface-divider);
  margin-bottom: 0.5rem;
}
.brand-mark {
  width: 36px; height: 36px; flex-shrink: 0;
  display: grid; place-items: center;
  background: linear-gradient(135deg, var(--tone-a) 0%, var(--tone-b) 100%);
  border-radius: 9px;
  color: white;
  box-shadow: var(--elev-1);
}
.brand-mark .material-symbols-outlined { font-size: 20px; color: white; }
.brand-name {
  font-size: 0.92rem; font-weight: 600;
  color: var(--text-strong);
  white-space: nowrap;
}
.layout-wrapper.collapsed .brand-text { display: none; }

.nav-section { display: flex; flex-direction: column; gap: 0.12rem; }
.nav-section.foot { margin-top: auto; padding-top: 0.5rem; }
.nav-heading {
  font-size: 0.66rem; font-weight: 700; letter-spacing: 0.1em;
  text-transform: uppercase; color: var(--text-muted);
  margin: 0.6rem 0.7rem 0.2rem;
}
.layout-wrapper.collapsed .nav-heading {
  visibility: hidden; height: 0.6rem; margin: 0;
}

.nav-item {
  display: flex; align-items: center; gap: 0.7rem;
  padding: 0.5rem 0.7rem;
  border: none; background: transparent;
  border-radius: 8px;
  font: inherit; font-size: 0.88rem; font-weight: 500;
  color: var(--text-body); text-align: left;
  cursor: pointer;
  transition: background 0.14s ease, color 0.14s ease;
  width: 100%;
}
.nav-item:hover:not(.disabled) { background: var(--surface-2); }
.nav-item.active {
  background: var(--tone-a-soft);
  color: var(--tone-a);
}
.nav-item.active .material-symbols-outlined { color: var(--tone-a); }
.nav-item.disabled { color: var(--text-disabled); cursor: not-allowed; }
.nav-item .material-symbols-outlined { font-size: 20px; flex-shrink: 0; }
.nav-label {
  flex: 1; min-width: 0; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis;
}
.layout-wrapper.collapsed .nav-label,
.layout-wrapper.collapsed .soon { display: none; }
.layout-wrapper.collapsed .nav-item { justify-content: center; padding: 0.55rem 0; }

.soon {
  font-size: 0.62rem; font-weight: 600;
  background: var(--surface-2); color: var(--text-muted);
  padding: 0.08rem 0.45rem; border-radius: 999px;
  letter-spacing: 0.05em;
}

/* Mobile drawer: slide in from the left when wrapper has mobile-open. */
@media (max-width: 992px) {
  .sidebar {
    transform: translateX(-100%);
    width: var(--sidebar-w, 240px) !important;
  }
  .layout-wrapper.mobile-open .sidebar { transform: translateX(0); }
}
</style>
