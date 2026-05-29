// Thin fetch wrapper over the Phase 3 backend (segment_compare.api).
//
// Every endpoint returns JSON (except the report file which is HTML);
// HTTP errors surface as thrown Error instances with the server's
// detail string on .message so the UI can render them inline.

const BASE = '/api'

async function request(method, path, body) {
  const init = { method, headers: {} }
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(body)
  }
  const r = await fetch(`${BASE}${path}`, init)
  const text = await r.text()
  let parsed
  try {
    parsed = text ? JSON.parse(text) : null
  } catch {
    parsed = null
  }
  if (!r.ok) {
    const detail =
      parsed && (parsed.detail || parsed.error) ? parsed.detail || parsed.error : text
    throw new Error(`${r.status}: ${detail}`)
  }
  return parsed
}

export const api = {
  health: () => request('GET', '/health'),
  templateLayouts: () => request('GET', '/template-layouts'),
  saveConfig: (body) => request('POST', '/configs', body),
  listConfigs: () => request('GET', '/configs'),
  runCompare: (body) => request('POST', '/runs', body),
  listRuns: (outputDir) =>
    request('GET', `/runs${outputDir ? `?output_dir=${encodeURIComponent(outputDir)}` : ''}`),
  browse: (path) =>
    request('GET', `/browse${path ? `?path=${encodeURIComponent(path)}` : ''}`),
}
