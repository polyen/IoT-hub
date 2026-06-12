"""Production ONNX intent classifier for the edge agent.

Loads artifacts exported by training/intent_classifier/convert_to_onnx.py:
  - model.int8.onnx       — sentence-transformer body (multilingual-e5-small INT8)
  - classifier_head.npz   — LogisticRegression coef + intercept
  - metadata.json         — canonical INTENT_LABELS list
  - tokenizer/            — HF fast tokenizer

CPU-only inference (<100 ms on Pi5 ARM64).
No transformers or setfit required at runtime — only onnxruntime + tokenizers.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_MAX_SEQ_LEN = 128


class IntentClassifier:
    """SetFit intent classification via ONNX sentence encoder + sklearn LR head."""

    def __init__(self, model_dir: Path, threshold: float = 0.6) -> None:
        self._model_dir = model_dir
        self._threshold = threshold
        self._session: Any | None = None
        self._tokenizer: tuple[str, Any] | None = None  # ("kind", tok_object)
        self._has_token_type_ids: bool = False
        self._coef: np.ndarray | None = None  # [n_classes, embedding_dim]
        self._intercept: np.ndarray | None = None  # [n_classes]
        self._labels: list[str] = []  # canonical intent label strings

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load all model artifacts; logs warnings and degrades gracefully on any failure."""
        self._load_session()
        if self._session is None:
            return
        self._load_tokenizer()
        self._load_head()

    @property
    def is_loaded(self) -> bool:
        return (
            self._session is not None
            and self._tokenizer is not None
            and self._coef is not None
            and bool(self._labels)
        )

    def classify(self, text: str) -> tuple[str, float]:
        """Return (intent_label, confidence).  Falls back to ("ask_clarification", 0.0)."""
        if not self.is_loaded or not text.strip():
            return "ask_clarification", 0.0
        emb = self._encode(text)
        if emb is None:
            return "ask_clarification", 0.0
        return self._predict(emb)

    # ------------------------------------------------------------------
    # Loading helpers
    # ------------------------------------------------------------------

    def _load_session(self) -> None:
        try:
            import onnxruntime as ort  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("IntentClassifier: onnxruntime not installed")
            return

        for fname in ("model.int8.onnx", "model.onnx"):
            path = self._model_dir / fname
            if not path.exists():
                continue
            try:
                opts = ort.SessionOptions()
                opts.inter_op_num_threads = 2
                opts.intra_op_num_threads = 2
                self._session = ort.InferenceSession(
                    str(path),
                    sess_options=opts,
                    providers=["CPUExecutionProvider"],
                )
                input_names = {inp.name for inp in self._session.get_inputs()}
                self._has_token_type_ids = "token_type_ids" in input_names
                logger.info("IntentClassifier: loaded %s", fname)
                return
            except Exception as exc:
                logger.warning("IntentClassifier: could not load %s: %s", fname, exc)

        logger.warning("IntentClassifier: no ONNX model found in %s", self._model_dir)

    def _load_tokenizer(self) -> None:
        tok_dir = self._model_dir / "tokenizer"
        if not tok_dir.exists():
            tok_dir = self._model_dir
        tok_json = tok_dir / "tokenizer.json"

        # tokenizers (HF Rust tokenizer — very lightweight, no torch)
        if tok_json.exists():
            try:
                from tokenizers import Tokenizer  # type: ignore[import-untyped]

                tok = Tokenizer.from_file(str(tok_json))
                tok.enable_padding(pad_id=1, pad_token="<pad>", length=_MAX_SEQ_LEN)
                tok.enable_truncation(max_length=_MAX_SEQ_LEN)
                self._tokenizer = ("tokenizers", tok)
                self._load_labels()
                return
            except ImportError:
                pass  # fall through to transformers
            except Exception as exc:
                logger.warning("IntentClassifier: tokenizers lib error: %s", exc)

        # fallback: transformers.AutoTokenizer
        try:
            from transformers import AutoTokenizer  # type: ignore[import-not-found]

            tok = AutoTokenizer.from_pretrained(str(tok_dir))  # type: ignore[no-untyped-call]
            self._tokenizer = ("transformers", tok)
            self._load_labels()
        except Exception as exc:
            logger.warning("IntentClassifier: tokenizer unavailable: %s", exc)

    def _load_head(self) -> None:
        path = self._model_dir / "classifier_head.npz"
        if not path.exists():
            logger.warning("IntentClassifier: classifier_head.npz missing in %s", self._model_dir)
            return
        try:
            head = np.load(str(path))
            self._coef = head["coef"].astype(np.float32)
            self._intercept = head["intercept"].astype(np.float32)
        except Exception as exc:
            logger.warning("IntentClassifier: failed to load head weights: %s", exc)

    def _load_labels(self) -> None:
        # metadata.json (written by train.py) has the full canonical INTENT_LABELS list
        meta = self._model_dir / "metadata.json"
        if meta.exists():
            try:
                self._labels = json.loads(meta.read_text()).get("labels", [])
                return
            except Exception:
                pass
        # classifier_head_labels.json has int-strings "0".."N" (sklearn classes_)
        labels_file = self._model_dir / "classifier_head_labels.json"
        if labels_file.exists():
            try:
                raw: list[str] = json.loads(labels_file.read_text())
                try:
                    from training.intent_classifier.intents import INTENT_LABELS  # noqa: PLC0415

                    self._labels = [INTENT_LABELS[int(s)] for s in raw]
                except Exception:
                    self._labels = raw
            except Exception as exc:
                logger.warning("IntentClassifier: could not load label map: %s", exc)

    # ------------------------------------------------------------------
    # Inference helpers
    # ------------------------------------------------------------------

    def _encode(self, text: str) -> np.ndarray | None:
        """Tokenize → ONNX forward → mean-pool → L2-normalize → [embedding_dim]."""
        if self._tokenizer is None or self._session is None:
            return None
        try:
            kind, tok = self._tokenizer
            if kind == "tokenizers":
                enc = tok.encode(text)
                input_ids = np.array([enc.ids], dtype=np.int64)
                attention_mask = np.array([enc.attention_mask], dtype=np.int64)
            else:
                encoded = tok(
                    text,
                    max_length=_MAX_SEQ_LEN,
                    padding="max_length",
                    truncation=True,
                    return_tensors="np",
                )
                input_ids = encoded["input_ids"].astype(np.int64)
                attention_mask = encoded["attention_mask"].astype(np.int64)

            feeds: dict[str, np.ndarray] = {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
            }
            if self._has_token_type_ids:
                feeds["token_type_ids"] = np.zeros_like(input_ids)

            outputs: list[np.ndarray] = self._session.run(None, feeds)
            last_hidden: np.ndarray = outputs[0]  # [1, seq, dim]

            # Mean-pool (ignore padding tokens)
            mask = attention_mask[..., np.newaxis].astype(np.float32)  # [1, seq, 1]
            pooled = (last_hidden * mask).sum(axis=1) / mask.sum(axis=1).clip(min=1e-9)  # [1, dim]

            vec: np.ndarray = pooled[0].astype(np.float32)
            norm = float(np.linalg.norm(vec))
            if norm > 1e-9:
                vec = vec / norm
            return vec
        except Exception as exc:
            logger.warning("IntentClassifier._encode failed: %s", exc)
            return None

    def _predict(self, emb: np.ndarray) -> tuple[str, float]:
        """Apply LR head via dot product and return (label, confidence)."""
        assert self._coef is not None and self._intercept is not None
        logits = emb @ self._coef.T + self._intercept  # [n_classes]
        logits = logits - logits.max()
        exp_l = np.exp(logits)
        probs = exp_l / exp_l.sum()
        idx = int(np.argmax(probs))
        label = self._labels[idx] if idx < len(self._labels) else "ask_clarification"
        return label, float(probs[idx])
