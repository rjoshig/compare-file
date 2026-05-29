<script setup>
import { useTheme } from '../composables/theme.js'
import { useLayout } from '../composables/layout.js'

defineProps({ breadcrumb: { type: String, default: '' } })

const { theme, toggle } = useTheme()
const { toggleSidebar, sidebarCollapsed } = useLayout()
</script>

<template>
  <header class="app-bar">
    <div class="bar-left">
      <button
        class="icon-btn menu-btn"
        :title="sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'"
        @click="toggleSidebar"
      >
        <span class="material-symbols-outlined">menu</span>
      </button>
      <nav class="crumbs" aria-label="Breadcrumb">
        <span class="crumb-root">Dashboard</span>
        <span class="crumb-sep">/</span>
        <span class="crumb-current">{{ breadcrumb }}</span>
      </nav>
    </div>
    <div class="bar-right">
      <button
        class="icon-btn"
        :title="theme === 'dark' ? 'Switch to light' : 'Switch to dark'"
        @click="toggle"
      >
        <span class="material-symbols-outlined">
          {{ theme === 'dark' ? 'light_mode' : 'dark_mode' }}
        </span>
      </button>
    </div>
  </header>
</template>

<style scoped>
.app-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: var(--topbar-h, 56px);
  padding: 0 1rem;
  background: var(--surface-sticky);
  border-bottom: 1px solid var(--surface-border);
  position: sticky;
  top: 0;
  z-index: 30;
  backdrop-filter: blur(10px);
}
.bar-left { display: flex; align-items: center; gap: 0.6rem; min-width: 0; }
.bar-right { display: flex; gap: 0.4rem; align-items: center; }

.crumbs {
  display: flex; gap: 0.4rem; align-items: center;
  font-size: 0.88rem;
  min-width: 0;
}
.crumb-root { color: var(--text-muted); }
.crumb-sep { color: var(--text-disabled); }
.crumb-current {
  color: var(--text-strong);
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.icon-btn {
  display: grid; place-items: center;
  width: 36px; height: 36px;
  border-radius: 8px;
  background: transparent;
  border: 1px solid transparent;
  color: var(--text-body);
  cursor: pointer;
  transition: background 0.14s ease, transform 0.18s ease;
}
.icon-btn:hover { background: var(--surface-2); }
.icon-btn:active { transform: scale(0.96); }
.icon-btn .material-symbols-outlined { font-size: 20px; }
.menu-btn { border-color: var(--surface-border); }
</style>
