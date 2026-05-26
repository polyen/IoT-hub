"""Intent classification via semantic similarity to prototype embeddings.

Uses EmbeddingGemma 300M ONNX (via optimum) when available.
Falls back to keyword-based classification if model not loaded.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml


class IntentClass(StrEnum):
    DETERMINISTIC = "deterministic"
    STRUCTURED = "structured"
    CREATIVE = "creative"
    UNKNOWN = "unknown"


@dataclass
class Intent:
    class_: IntentClass
    score: float
    prototype: str | None


class IntentRouter:
    def __init__(
        self,
        prototypes_path: Path = Path("hub/edge/agent/prototypes.yaml"),
        model_path: Path | None = None,
    ) -> None:
        self._prototypes_path = prototypes_path
        self._model_path = model_path
        self._prototypes: dict[str, list[dict[str, Any]]] = {}
        self._model: Any = None
        self._proto_embeddings: dict[str, list[float]] | None = None

    def load(self) -> None:
        with open(self._prototypes_path) as fh:
            data: dict[str, Any] = yaml.safe_load(fh)
        self._prototypes = data.get("prototypes", {})
        if self._model_path is not None:
            try:
                from optimum.onnxruntime import ORTModelForFeatureExtraction  # type: ignore

                self._model = ORTModelForFeatureExtraction.from_pretrained(str(self._model_path))
            except Exception:
                self._model = None
        self._proto_embeddings = None

    def classify_intent(self, text: str) -> Intent:
        if not text.strip():
            return Intent(class_=IntentClass.UNKNOWN, score=0.0, prototype=None)
        if self._model is None:
            return self._keyword_fallback(text)
        return self._embed_classify(text)

    def _embed_classify(self, text: str) -> Intent:
        if self._proto_embeddings is None:
            self._build_proto_embeddings()
        emb = self._embed(text)
        best_class = IntentClass.UNKNOWN
        best_score = -1.0
        best_label: str | None = None
        for cls_name, items in self._prototypes.items():
            for item in items:
                key = f"{cls_name}:{item['label']}"
                proto_emb = (self._proto_embeddings or {}).get(key)
                if proto_emb is None:
                    continue
                score = self._cosine_sim(emb, proto_emb)
                if score > best_score:
                    best_score = score
                    best_class = IntentClass(cls_name)
                    best_label = item["label"]
        return Intent(class_=best_class, score=max(best_score, 0.0), prototype=best_label)

    def _build_proto_embeddings(self) -> None:
        self._proto_embeddings = {}
        for cls_name, items in self._prototypes.items():
            for item in items:
                key = f"{cls_name}:{item['label']}"
                self._proto_embeddings[key] = self._embed(item["text"])

    def _embed(self, text: str) -> list[float]:
        if self._model is None:
            return []
        result: list[float] = self._model.encode(text)
        return result

    def _cosine_sim(self, a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(x * x for x in b))
        if mag_a == 0.0 or mag_b == 0.0:
            return 0.0
        return dot / (mag_a * mag_b)

    def _keyword_fallback(self, text: str) -> Intent:
        lower = text.lower()

        # Use word stems to match all Ukrainian verb forms
        # e.g. "увімкн" matches увімкни/увімкне/увімкнути/увімкнеш
        deterministic_kw = [
            "увімкн",  # увімкни / увімкне / увімкнути
            "вимкн",  # вимкни / вимкне / вимкнути
            "відкрий",
            "відкр",
            "закрий",
            "закр",
            "перемкн",  # перемкни / перемкне
            "turn on",
            "turn off",
            "open",
            "close",
            "toggle",
            "switch",
            "світло",  # "світло" alone → likely light command
        ]
        timer_kw = ["таймер", "нагадай", "timer", "remind", "reminder"]
        creative_kw = [
            "що",
            "розкажи",
            "summarize",
            "summary",
            "підсумок",
            "звіт",
            "чому",
            "what happened",
        ]
        structured_kw = [
            "встанови",
            "set",
            "збільш",
            "зменш",
            "increase",
            "decrease",
            "adjust",
            "відсотків",
            "градус",
            "гучність",
        ]

        for kw in structured_kw:
            if kw in lower:
                return Intent(class_=IntentClass.STRUCTURED, score=0.8, prototype=None)
        for kw in deterministic_kw:
            if kw in lower:
                return Intent(class_=IntentClass.DETERMINISTIC, score=0.9, prototype=None)
        for kw in timer_kw:
            if kw in lower:
                return Intent(class_=IntentClass.DETERMINISTIC, score=0.85, prototype=None)
        for kw in creative_kw:
            if kw in lower:
                return Intent(class_=IntentClass.CREATIVE, score=0.75, prototype=None)
        return Intent(class_=IntentClass.UNKNOWN, score=0.0, prototype=None)
