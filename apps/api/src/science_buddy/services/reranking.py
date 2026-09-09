import asyncio
import importlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from uuid import UUID


class RerankerUnavailableError(RuntimeError):
    """Raised when the optional local reranker cannot be loaded or executed."""


@dataclass(frozen=True, slots=True)
class RerankDocument:
    chunk_id: UUID
    text: str


class CrossEncoderReranker:
    """Lazy local multilingual CrossEncoder reranker with serialized inference."""

    def __init__(
        self,
        *,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        batch_size: int = 8,
        encoder_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self._encoder_factory = encoder_factory
        self._encoder: Any | None = None
        self._load_error: RerankerUnavailableError | None = None
        self._load_task: asyncio.Task[Any] | None = None
        self._load_lock = asyncio.Lock()
        self._predict_lock = asyncio.Lock()

    def _load_encoder_sync(self) -> Any:
        factory = self._encoder_factory
        if factory is None:
            module = importlib.import_module("sentence_transformers")
            factory = module.CrossEncoder
        return factory(self.model_name)

    async def _load_encoder(self) -> Any:
        try:
            self._encoder = await asyncio.to_thread(self._load_encoder_sync)
        except ImportError as exc:
            error = RerankerUnavailableError(
                "Install the 'rerank' optional dependency to use local reranking"
            )
            self._load_error = error
            raise error from exc
        except (OSError, RuntimeError, ValueError) as exc:
            error = RerankerUnavailableError(
                f"Local reranker model '{self.model_name}' could not be loaded"
            )
            self._load_error = error
            raise error from exc
        return self._encoder

    async def _get_encoder(self) -> Any:
        if self._encoder is not None:
            return self._encoder
        if self._load_error is not None:
            raise self._load_error
        async with self._load_lock:
            if self._encoder is not None:
                return self._encoder
            if self._load_error is not None:
                raise self._load_error
            if self._load_task is None:
                self._load_task = asyncio.create_task(self._load_encoder())
            load_task = self._load_task
        return await asyncio.shield(load_task)

    async def score(
        self,
        query: str,
        documents: Sequence[RerankDocument],
    ) -> dict[UUID, float]:
        if not documents:
            return {}
        encoder = await self._get_encoder()
        pairs = [(query, document.text) for document in documents]
        async with self._predict_lock:
            try:
                values: Any = await asyncio.to_thread(
                    encoder.predict,
                    pairs,
                    batch_size=self.batch_size,
                    show_progress_bar=False,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                raise RerankerUnavailableError(
                    f"Local reranker model '{self.model_name}' failed during inference"
                ) from exc
        result = values.tolist() if hasattr(values, "tolist") else values
        scores = [float(value[0] if isinstance(value, list) else value) for value in result]
        if len(scores) != len(documents):
            raise RerankerUnavailableError("Local reranker returned an unexpected score count")
        return {
            document.chunk_id: score
            for document, score in zip(documents, scores, strict=True)
        }


@lru_cache(maxsize=2)
def get_reranker_service(model_name: str) -> CrossEncoderReranker:
    return CrossEncoderReranker(model_name=model_name)
