import { ref } from 'vue'

// Shared, module-singleton state for the most recent run. FieldConfig writes
// it when a comparison completes; the Results view reads it. Kept here (not in
// a component) so navigating between views doesn't lose the last result.
export const lastRun = ref(null)

export function setLastRun(result) {
  lastRun.value = result
}
