import asyncio
import importlib
from functools import lru_cache
from pathlib import Path
from typing import Any


class OcrUnavailableError(RuntimeError):
    """Raised when the optional local OCR runtime is unavailable."""


class LocalOcrService:
    """Process images locally with RapidOCR; no image data leaves the host."""

    def __init__(self) -> None:
        self._engine: Any | None = None
        self._load_lock = asyncio.Lock()
        self._run_lock = asyncio.Lock()

    async def _get_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        async with self._load_lock:
            if self._engine is not None:
                return self._engine
            try:
                module = importlib.import_module("rapidocr")
            except ImportError as exc:
                raise OcrUnavailableError(
                    "Install the 'ocr' optional dependency to enable local image OCR"
                ) from exc
            self._engine = await asyncio.to_thread(module.RapidOCR)
            return self._engine

    async def extract_text(self, image_path: Path) -> tuple[str, float | None]:
        engine = await self._get_engine()
        try:
            async with self._run_lock:
                result = await asyncio.to_thread(engine, str(image_path))
        except (OSError, RuntimeError, ValueError) as exc:
            raise OcrUnavailableError("Local OCR inference failed") from exc
        texts = tuple(getattr(result, "txts", None) or ())
        scores = tuple(float(value) for value in (getattr(result, "scores", None) or ()))
        accepted = [
            str(text).strip()
            for index, text in enumerate(texts)
            if str(text).strip() and (index >= len(scores) or scores[index] >= 0.35)
        ]
        confidence = sum(scores) / len(scores) if scores else None
        return "\n".join(accepted), confidence


@lru_cache(maxsize=1)
def get_ocr_service() -> LocalOcrService:
    return LocalOcrService()
