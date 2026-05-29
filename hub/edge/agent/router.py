"""Intent classification for the voice→IoT agent pipeline.

Primary path (when ML model is loaded):
  IntentClassifier (SetFit ONNX, multilingual-e5-small INT8) →
  confidence-gated mapping to IntentClass + prototype label.

Fallback path (no model / CI / dev machines):
  keyword-based heuristic (existing behaviour, preserved for offline use).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fine-grained intent label sets — kept in sync with
# training/intent_classifier/intents.py (INTENT_LABELS).
# ---------------------------------------------------------------------------
_DETERMINISTIC_LABELS: frozenset[str] = frozenset(
    {
        "light_on",
        "light_off",
        "light_toggle",
        "relay_on",
        "relay_off",
        "door_open",
        "door_close",
    }
)
_STRUCTURED_LABELS: frozenset[str] = frozenset(
    {
        "light_brightness_set",
        "light_color_set",
        "thermostat_set",
    }
)
_CREATIVE_LABELS: frozenset[str] = frozenset(
    {
        "query_temperature",
        "query_humidity",
        "query_state",
        "summarize_events",
        "scene_generic",
    }
)


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
        classifier_dir: Path | None = Path("models/intent_classifier"),
        threshold: float = 0.6,
    ) -> None:
        self._prototypes_path = prototypes_path
        self._model_path = model_path
        self._classifier_dir = classifier_dir
        self._threshold = threshold
        self._prototypes: dict[str, list[dict[str, Any]]] = {}
        self._model: Any = None
        self._proto_embeddings: dict[str, list[float]] | None = None
        self._ml_classifier: Any | None = None  # IntentClassifier when loaded

    def load(self) -> None:
        with open(self._prototypes_path) as fh:
            data: dict[str, Any] = yaml.safe_load(fh)
        self._prototypes = data.get("prototypes", {})

        # ML classifier (SetFit ONNX) — preferred over embedding similarity
        if self._classifier_dir is not None:
            try:
                from hub.edge.agent.intent_classifier import IntentClassifier  # noqa: PLC0415

                clf = IntentClassifier(self._classifier_dir, threshold=self._threshold)
                clf.load()
                if clf.is_loaded:
                    self._ml_classifier = clf
                    logger.info(
                        "IntentRouter: ML classifier ready (threshold=%.2f)", self._threshold
                    )
                else:
                    logger.debug(
                        "IntentRouter: ML classifier not available at %s", self._classifier_dir
                    )
            except Exception as exc:
                logger.warning("IntentRouter: ML classifier load failed: %s", exc)

        # EmbeddingGemma (legacy fallback, used only when ML classifier absent)
        if self._ml_classifier is None and self._model_path is not None:
            try:
                from optimum.onnxruntime import ORTModelForFeatureExtraction  # type: ignore

                self._model = ORTModelForFeatureExtraction.from_pretrained(str(self._model_path))
            except Exception:
                self._model = None
        self._proto_embeddings = None

    def classify_intent(self, text: str) -> Intent:
        if not text.strip():
            return Intent(class_=IntentClass.UNKNOWN, score=0.0, prototype=None)

        # Primary: ML classifier
        if self._ml_classifier is not None:
            label, confidence = self._ml_classifier.classify(text)
            if confidence >= self._threshold:
                return self._ml_label_to_intent(label, confidence)
            # Low confidence → unknown (don't guess with keyword fallback)
            return Intent(
                class_=IntentClass.UNKNOWN, score=confidence, prototype="ask_clarification"
            )

        # Legacy: prototype embedding similarity (EmbeddingGemma)
        if self._model is not None:
            return self._embed_classify(text)

        # Offline keyword heuristic
        return self._keyword_fallback(text)

    # ------------------------------------------------------------------
    # ML classifier routing
    # ------------------------------------------------------------------

    @staticmethod
    def _ml_label_to_intent(label: str, confidence: float) -> Intent:
        if label in _DETERMINISTIC_LABELS:
            return Intent(class_=IntentClass.DETERMINISTIC, score=confidence, prototype=label)
        if label in _STRUCTURED_LABELS:
            return Intent(class_=IntentClass.STRUCTURED, score=confidence, prototype=label)
        if label in _CREATIVE_LABELS:
            return Intent(class_=IntentClass.CREATIVE, score=confidence, prototype=label)
        return Intent(class_=IntentClass.UNKNOWN, score=confidence, prototype=None)

    # ------------------------------------------------------------------
    # Legacy embedding similarity (EmbeddingGemma)
    # ------------------------------------------------------------------

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

    @staticmethod
    def _cosine_sim(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(x * x for x in b))
        if mag_a == 0.0 or mag_b == 0.0:
            return 0.0
        return dot / (mag_a * mag_b)

    # ------------------------------------------------------------------
    # Offline keyword heuristic (no model)
    # ------------------------------------------------------------------

    def _keyword_fallback(self, text: str) -> Intent:
        lower = text.lower()

        # Structured (numeric / adjustment commands) — check first
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
                proto: str | None = None
                if "температур" in lower or "градус" in lower:
                    proto = "thermostat_set"
                elif "яскравість" in lower or "відсоток" in lower or "відсотків" in lower:
                    proto = "light_brightness_set"
                elif "гучність" in lower:
                    proto = "volume_set"
                elif "таймер" in lower or "нагадай" in lower:
                    proto = "timer_set"
                return Intent(class_=IntentClass.STRUCTURED, score=0.8, prototype=proto)

        # Timer keywords (deterministic)
        timer_kw = ["таймер", "нагадай", "timer", "remind", "reminder"]
        for kw in timer_kw:
            if kw in lower:
                return Intent(class_=IntentClass.DETERMINISTIC, score=0.85, prototype="timer_set")

        # Deterministic device commands
        action_on = any(kw in lower for kw in ("увімкн", "turn on", "вмикай"))
        action_off = any(kw in lower for kw in ("вимкн", "turn off", "вимикай"))
        action_toggle = any(kw in lower for kw in ("перемкн", "toggle", "switch"))
        action_open = any(kw in lower for kw in ("відкрий", "відкр", "open"))
        action_close = any(kw in lower for kw in ("закрий", "закр", "close"))

        kind_light = any(kw in lower for kw in ("світло", "лампа", "ліхтар", "освітлення"))
        kind_door = any(kw in lower for kw in ("двер", "ворот", "door", "gate", "garage"))
        kind_relay = any(kw in lower for kw in ("реле", "розетк", "вентилятор", "relay"))

        deterministic_hit = action_on or action_off or action_toggle or action_open or action_close

        if deterministic_hit:
            if action_open:
                det_proto: str = "door_open" if kind_door else "blinds_open"
            elif action_close:
                det_proto = "door_close" if kind_door else "blinds_close"
            elif action_toggle:
                det_proto = "light_toggle" if kind_light else "relay_toggle"
            elif action_on:
                det_proto = (
                    "light_on" if kind_light else ("relay_on" if kind_relay else "device_on")
                )
            else:
                det_proto = (
                    "light_off" if kind_light else ("relay_off" if kind_relay else "device_off")
                )

            if any(kw in lower for kw in ("все", "всі", "all")):
                det_proto = f"{'on' if action_on else 'off'}_all"

            return Intent(class_=IntentClass.DETERMINISTIC, score=0.9, prototype=det_proto)

        # Creative / query keywords
        creative_kw = [
            "що",
            "розкажи",
            "summarize",
            "summary",
            "підсумок",
            "звіт",
            "чому",
            "what happened",
            "яка",
            "який",
            "яке",
            "скільки",
            "температур",
            "вологіст",
            "стан",
            "покаж",
            "перевір",
            "дані",
            "зараз",
            "статус",
            "events",
            "події",
            "кімнат",
            "спальн",
            "вітальн",
            "кухн",
        ]
        for kw in creative_kw:
            if kw in lower:
                return Intent(class_=IntentClass.CREATIVE, score=0.75, prototype=None)

        return Intent(class_=IntentClass.UNKNOWN, score=0.0, prototype=None)
