import { createApp } from 'vue'
import App from './App.vue'

import PrimeVue from 'primevue/config'
import Material from '@primevue/themes/material'
import ToastService from 'primevue/toastservice'
import 'primeicons/primeicons.css'

import './style.css'

const app = createApp(App)

app.use(PrimeVue, {
  theme: {
    preset: Material,
    options: {
      // The composable in src/composables/theme.js flips this class on <html>.
      darkModeSelector: '.dark',
      cssLayer: false,
    },
  },
  ripple: true, // Material's hallmark click ripple
})
app.use(ToastService)
app.mount('#app')
