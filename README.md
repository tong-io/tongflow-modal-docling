# tongflow-modal-docling

Official [TongFlow](https://github.com/tong-io/tongflow) plugin. Document-to-text extraction with **Docling**, running on [Modal](https://modal.com). Parses PDFs and office documents into plain text.

## Capabilities

- **Document → text** (`parse-document`) — extract plain text from a document.

## Credentials

Add in TongFlow **Settings** (gear icon, top-right):

| Key | Required | Notes |
| --- | --- | --- |
| `MODAL_TOKEN_ID` | ✅ | Create at [modal.com/settings/tokens](https://modal.com/settings/tokens). |
| `MODAL_TOKEN_SECRET` | ✅ | Paired with `MODAL_TOKEN_ID`. |

On first use the plugin deploys to your Modal account automatically and caches the build. No Hugging Face token required.
