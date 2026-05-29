<script setup>
import { ref, computed } from 'vue'
import Toast from 'primevue/toast'
import AppBar from './components/AppBar.vue'
import AppSidebar from './components/AppSidebar.vue'
import FieldConfig from './components/FieldConfig.vue'
import { useLayout } from './composables/layout.js'

const { sidebarCollapsed, mobileMenuActive, closeMobileMenu } = useLayout()

const activeView = ref('field-config')

const breadcrumb = computed(() => {
  const map = {
    'field-config': 'Field Configuration',
    runs: 'Run History',
    results: 'Results',
    datasets: 'Datasets',
    settings: 'Settings',
    about: 'About',
  }
  return map[activeView.value] || ''
})

const shellClass = computed(() => ({
  collapsed: sidebarCollapsed.value,
  'mobile-open': mobileMenuActive.value,
}))
</script>

<template>
  <Toast position="top-right" />

  <div class="layout-wrapper" :class="shellClass">
    <AppSidebar
      :active="activeView"
      @navigate="(v) => (activeView = v)"
    />

    <div
      class="layout-mask"
      @click="closeMobileMenu"
      aria-hidden="true"
    />

    <div class="layout-main">
      <AppBar :breadcrumb="breadcrumb" />
      <main class="view">
        <FieldConfig v-if="activeView === 'field-config'" />
        <div v-else class="placeholder">
          <span class="material-symbols-outlined">construction</span>
          <p class="t-headline">Coming soon</p>
          <p class="t-small">This view will be available in a later phase.</p>
        </div>
      </main>
    </div>
  </div>
</template>

<style scoped>
.layout-wrapper {
  --sidebar-w: 240px;
  --sidebar-w-collapsed: 64px;
  --topbar-h: 56px;
  min-height: 100vh;
  display: block;
}

/* Sidebar is fixed-position; main column reserves space with left margin
   so the topbar's left edge aligns with content. */
.layout-main {
  margin-left: var(--sidebar-w);
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  transition: margin-left 0.2s ease;
}
.layout-wrapper.collapsed .layout-main {
  margin-left: var(--sidebar-w-collapsed);
}
.view {
  flex: 1;
  min-height: 0;
  padding: 0.9rem 1.25rem 1.5rem;
}

/* Mask used only on mobile: dims the page when the sidebar overlays it. */
.layout-mask {
  display: none;
  position: fixed; inset: 0;
  background: rgba(0, 0, 0, 0.4);
  z-index: 40;
}
.layout-wrapper.mobile-open .layout-mask { display: block; }

.placeholder {
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  text-align: center; height: 100%;
  color: var(--text-muted);
}
.placeholder .material-symbols-outlined {
  font-size: 56px; margin-bottom: 0.6rem; opacity: 0.5;
}

@media (max-width: 992px) {
  .layout-main { margin-left: 0; }
  .layout-wrapper.collapsed .layout-main { margin-left: 0; }
}
</style>
