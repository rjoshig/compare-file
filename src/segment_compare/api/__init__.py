"""FastAPI backend (Phase 3).

Three responsibilities:

1. Serve template layouts (``config/layout_file_A.json`` /
   ``layout_file_B.json``) to the Vue UI so the operator sees a
   read-only baseline of the segments + fields the engine already
   knows about.
2. Accept user configs (template overrides + appended fields +
   compare-key + sort settings + input file paths) and persist them
   to ``user_configs/<name>/`` so the engine's existing
   ``load_config(config_dir)`` contract keeps working unchanged.
3. Invoke ``pipeline.run`` against a saved user config and return
   the resulting per-run subdir so the UI can link to
   ``compare_reports.html``.

Run locally during dev::

    uvicorn segment_compare.api.main:app --reload --port 8000

The Vue dev server (Vite on :5173) proxies ``/api/*`` to this
backend; CORS is permissive in dev (locked down per-host in prod).
"""

from segment_compare.api.main import app

__all__ = ["app"]
