// Theme toggle composable. Persists the choice in localStorage and
// toggles the `.dark` class on <html> (which PrimeVue's Aura theme
// reads via its darkModeSelector).
import { ref, watchEffect } from 'vue'

const STORAGE_KEY = 'segment-compare:theme'

function readInitial() {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (stored === 'dark' || stored === 'light') return stored
  // Fall back to the OS preference.
  if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
    return 'dark'
  }
  return 'light'
}

const theme = ref(readInitial())

watchEffect(() => {
  const html = document.documentElement
  if (theme.value === 'dark') {
    html.classList.add('dark')
  } else {
    html.classList.remove('dark')
  }
  localStorage.setItem(STORAGE_KEY, theme.value)
})

export function useTheme() {
  return {
    theme,
    toggle: () => {
      theme.value = theme.value === 'dark' ? 'light' : 'dark'
    },
  }
}
