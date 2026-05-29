// Layout-state composable (Sakai-Vue pattern).
//
// Persists `sidebarCollapsed` across reloads so the operator's preference
// sticks. `mobileMenuActive` is transient — drawer-style overlay used
// only on small viewports.

import { reactive, computed, readonly, watchEffect } from 'vue'

const STORAGE_KEY = 'segcmp-sidebar-collapsed'

const initial = (() => {
  try {
    return localStorage.getItem(STORAGE_KEY) === '1'
  } catch {
    return false
  }
})()

const state = reactive({
  sidebarCollapsed: initial,
  mobileMenuActive: false,
})

watchEffect(() => {
  try {
    localStorage.setItem(STORAGE_KEY, state.sidebarCollapsed ? '1' : '0')
  } catch {
    /* localStorage unavailable — ignore */
  }
})

export function useLayout() {
  function toggleSidebar() {
    if (window.innerWidth < 992) {
      state.mobileMenuActive = !state.mobileMenuActive
    } else {
      state.sidebarCollapsed = !state.sidebarCollapsed
    }
  }
  function closeMobileMenu() {
    state.mobileMenuActive = false
  }

  return {
    state: readonly(state),
    sidebarCollapsed: computed(() => state.sidebarCollapsed),
    mobileMenuActive: computed(() => state.mobileMenuActive),
    toggleSidebar,
    closeMobileMenu,
  }
}
