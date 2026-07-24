"""Modal deploy entry for docling.

Deploy:
  modal deploy deploy.py
"""

from __future__ import annotations

import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Optional, cast

import modal
from tongflow import deploy




# Slots this plugin is the default implementation of: the node picker lists
# it first and a newly added node preselects it. Read statically by the
# scanner (never executed), so any SDK version imports this file fine.
TONGFLOW_DEFAULT_SLOTS = ["parse-document"]

_cfg: dict[str, Any] = {}
_ = _cfg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

image = (
    modal.Image.from_registry("python:3.11-slim")
    .apt_install(
        "libgl1",
        "libglib2.0-0",
        "libsm6",
        "libxext6",
        "libxrender1",
        "libgomp1",
        "libglu1-mesa",
    )
    .pip_install(
        "tongflow==0.2.16", "fastapi[standard]",
        "docling",
        "pdf2image",
        "pillow",
        "boto3",
    )
    .run_commands("docling-tools models download")
)

app = modal.App(Path(__file__).resolve().parent.name, image=image)
secrets = modal.Secret.from_dict({})

with image.imports():
    from docling.document_converter import DocumentConverter
    import boto3
    from botocore.config import Config

from tongflow.models.parse_document import ParseDocumentInput, ParseDocumentOutput
from tongflow.node_slots import NodeSlots
from tongflow.protocol import asset_as_path
from tongflow.slots import node_slot


class R2Client:
    def __init__(self):
        self.region = os.getenv("R2_REGION", "auto")
        self.bucket = os.getenv("R2_BUCKET")
        self.access_key_id = os.getenv("R2_ACCESS_KEY_ID")
        self.secret_access_key = os.getenv("R2_SECRET_ACCESS_KEY")
        self.endpoint = os.getenv("R2_ENDPOINT")

        if not self.bucket:
            raise RuntimeError("R2_BUCKET is not set")

        config = Config(region_name=self.region)
        self.client = boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            config=config,
        )
        logger.info(f"R2 client initialized for bucket={self.bucket}")

    def upload_file(self, local_path: str, dest_key: Optional[str] = None) -> str:
        try:
            if dest_key is None:
                dest_key = f"{uuid.uuid4().hex}_{os.path.basename(local_path)}"
            self.client.upload_file(local_path, self.bucket, dest_key)
            logger.info(f"Uploaded {local_path} to {dest_key}")
            return dest_key
        except Exception as e:
            logger.error(f"Failed to upload {local_path} to bucket {self.bucket}: {e}")
            raise

    def download_file(self, r2_key: str, local_path: str) -> str:
        try:
            self.client.download_file(self.bucket, r2_key, local_path)
            logger.info(f"Downloaded {r2_key} to {local_path}")
            return local_path
        except Exception as e:
            logger.error(f"Failed to download {r2_key} from bucket {self.bucket}: {e}")
            raise


def _parse_document_core(task: dict) -> dict:
    r2_client = None

    task_id = task.get("taskId")
    prompt = task.get("prompt", {})
    source = prompt.get("source")
    content_str = ""
    r2_key = ""
    doc = None

    try:
        if not source:
            raise ValueError("source parameter is required")

        logger.info(f"[{task_id}] start parsing document: {source}")

        r2_client = R2Client()

        with tempfile.TemporaryDirectory() as tmpdir:
            filename = os.path.basename(str(source))
            local_file_path = os.path.join(tmpdir, filename)
            r2_client.download_file(str(source), local_file_path)
            logger.info(f"[{task_id}] downloaded from R2 to: {local_file_path}")

            converter = DocumentConverter()

            result = converter.convert(local_file_path)

            if not result:
                error_msg = "document parsing failed: empty result"
                logger.error(f"[{task_id}] {error_msg}")
                return {"success": False, "error": error_msg}

            doc = result.document

            content = doc.export_to_markdown()
            content_str = str(content)

            logger.info(f"[{task_id}] parsed: {source} - content length: {len(content_str)}")

            md_filename = f"{uuid.uuid4().hex}.md"
            local_md_path = Path(tmpdir) / md_filename

            with open(local_md_path, "w", encoding="utf-8") as f:
                f.write(content_str)

            logger.info(f"[{task_id}] saved markdown to temp file: {local_md_path}")

            r2_key = f"tasks/{task_id}/{uuid.uuid4().hex}.md"
            r2_client.upload_file(str(local_md_path), r2_key)
            logger.info(f"[{task_id}] uploaded to R2: {r2_key}")

        return {
            "success": True,
            "source": str(source),
            "r2_key": r2_key,
            "content_length": len(content_str),
            "page_count": len(doc.pages) if doc and hasattr(doc, "pages") else 0,
        }

    except Exception as e:
        error_msg = f"document parse error: {str(e)}"
        logger.error(f"[{task_id}] {error_msg}", exc_info=True)
        return {"success": False, "error": error_msg}


@app.function(cpu=2.0, memory=4096, timeout=600, secrets=[secrets], scaledown_window=5)
def parse_document(task: dict) -> dict:
    return _parse_document_core(task)


@deploy
@app.cls(cpu=2.0, memory=4096, timeout=600, secrets=[secrets], scaledown_window=5)
class Inference:
    @modal.method()
    @node_slot(NodeSlots.PARSE_DOCUMENT)
    def parse_document_slot(
        self,
        input: ParseDocumentInput,
    ) -> ParseDocumentOutput:
        if input.document is None:
            return ParseDocumentOutput(
                success=False, error="Missing `document` Asset"
            )
        try:
            with asset_as_path(input.document, suffix=".bin") as doc_path:
                converter = DocumentConverter()
                result = converter.convert(str(doc_path))
                if not result:
                    return ParseDocumentOutput(
                        success=False, error="parse result is empty"
                    )
                return ParseDocumentOutput(
                    success=True,
                    text=str(result.document.export_to_markdown()),
                )
        except Exception as e:
            logger.error(f"document parse error: {e}", exc_info=True)
            return ParseDocumentOutput(success=False, error=f"parse error: {e}")

    @modal.fastapi_endpoint(method="GET", label=f"{Path(__file__).resolve().parent.name}-serve")
    def serve(self, taskId: str = "", token: str = "", origin: str = ""):
        from fastapi.responses import StreamingResponse
        from tongflow import serve_stream_from_spec

        return StreamingResponse(
            serve_stream_from_spec(
                origin, taskId, token, __file__,
                invoke=lambda m, inp: getattr(self, m).local(inp),
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Access-Control-Allow-Origin": "*"},
        )

