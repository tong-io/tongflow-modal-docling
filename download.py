"""Modal download entry for docling.

Run:
  modal run download.py::download
"""

from __future__ import annotations

import modal

app = modal.App("docling-download")


@app.local_entrypoint()
def download() -> None:
    print(
        "No separate download for docling; "
        "models are baked into the image via docling-tools."
    )
