"""senex.cross_cutting - cross-cutting themes pass over aggregated findings.

Implements spec section 5.4.1 (`crosscut_response`) + ARCH-12 (input cap) +
section 8.3 (cross-cutting failure does NOT fail the run; on any LMS failure or
schema mismatch after retry, return None and let the combined report
surface a "[cross-cutting themes unavailable]" gap).

The crosscut LLM call uses NO tools (matches compaction's call shape):
the model sees a compressed `(file, category, priority, title)` tuple list
and emits the strict crosscut schema.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
from pathlib import Path

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema import ValidationError as JsonSchemaValidationError
from pydantic import ValidationError

from senex.config import CrosscutCfg
from senex.events import CrosscutComplete, CrosscutStart, EventBus
from senex.llm_client import LLMClient
from senex.inference_client import ChatMessage
from senex.render_models import FindingRecord, Theme

log = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "cross_cutting.md"
_SCHEMA_PATH = (
    Path(__file__).resolve().parent / "schema" / "crosscut_response.schema.json"
)


def compute_theme_id(title: str, run_id: str) -> str:
    """Stable theme id per spec section 5.4.1: `"t-" + sha256(title|run_id)[:12]`."""
    payload = f"{title}|{run_id}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"t-{digest}"


def _top_n_per_priority(
    findings: list[FindingRecord],
    *,
    top_h: int,
    top_m: int,
    top_l: int,
) -> list[FindingRecord]:
    """Cap per-priority counts (ARCH-12: 50 high + 100 medium + 100 low default)."""
    high = [f for f in findings if f.priority == "high"][:top_h]
    medium = [f for f in findings if f.priority == "medium"][:top_m]
    low = [f for f in findings if f.priority == "low"][:top_l]
    return high + medium + low


def _compress_to_tuples(
    findings: list[FindingRecord],
) -> list[tuple[str, str, str, str]]:
    """Compress to `(file, category, priority, title)` tuples.

    Drops `issue` / `why` / `fix` bodies; the model sees only the
    cross-pattern-relevant signals. This is also the cheapest payload that
    still carries thematic structure.
    """
    return [(f.file, f.category, f.priority, f.title) for f in findings]


def _chunk_for_crosscut(
    findings: list[FindingRecord],
    *,
    cluster_map: dict[str, list[FindingRecord]] | None,
) -> list[list[FindingRecord]]:
    """Hierarchical chunking signature.

    v1 ships the degenerate single-level: when `cluster_map=None` returns one
    chunk = the full list. When `cluster_map` is provided, returns one chunk
    per cluster (insertion order). v2 will introduce true hierarchical
    summarization across very large repos; the signature is shaped to absorb
    that change without a caller break.
    """
    if cluster_map is None:
        return [findings]
    return list(cluster_map.values())


class CrossCutter:
    """LMS-backed cross-cutting themes pass (tolerant: returns None on failure)."""

    def __init__(self, bus: EventBus, run_id: str) -> None:
        self._bus = bus
        self._run_id = run_id
        self._schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        self._validator = Draft202012Validator(self._schema)
        self._prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    async def run(
        self,
        *,
        client: LLMClient,
        findings: list[FindingRecord],
        cfg: CrosscutCfg,
    ) -> list[Theme] | None:
        """Run the crosscut pass; return list[Theme] or None on any failure.

        Args:
            client: LMS-compatible client (LLMClient protocol).
            findings: aggregated findings (unsorted; we cap inside).
            cfg: crosscut config (per-priority caps).

        Returns:
            list[Theme] on success; None on any failure (HTTP, timeout,
            schema mismatch after one retry).
        """
        await self._bus.publish(
            CrosscutStart(type="CrosscutStart", run_id=self._run_id, ts=_now())
        )

        capped = _top_n_per_priority(
            findings,
            top_h=cfg.top_n_high,
            top_m=cfg.top_n_medium,
            top_l=cfg.top_n_low,
        )
        tuples = _compress_to_tuples(capped)

        # Build messages.
        user_payload = json.dumps(tuples, indent=2)
        messages = [
            ChatMessage(role="system", content=self._prompt),
            ChatMessage(role="user", content=user_payload),
        ]

        themes = await self._call_with_retry(client, messages)
        await self._bus.publish(
            CrosscutComplete(
                type="CrosscutComplete",
                run_id=self._run_id,
                ts=_now(),
                theme_count=0 if themes is None else len(themes),
            )
        )
        return themes

    async def _call_with_retry(
        self,
        client: LLMClient,
        messages: list[ChatMessage],
    ) -> list[Theme] | None:
        for attempt in (1, 2):
            try:
                response = await client.chat(
                    task="cross_cutting",
                    messages=messages,
                    schema=self._schema,
                    tools=None,  # ARCH: crosscut uses no tools.
                )
            except Exception as exc:  # noqa: BLE001 - tolerant per section 8.3
                log.warning("crosscut LMS call failed (attempt %d): %s", attempt, exc)
                if attempt == 2:
                    return None
                continue

            try:
                payload = self._extract_payload(response)
                # Substitute placeholder ids so the schema's id pattern check
                # passes; we recompute the canonical id in _build_themes per
                # spec section 5.4.1 (model-emitted id is informational only).
                self._validator.validate(_with_placeholder_ids(payload))
                return self._build_themes(payload)
            except (JsonSchemaValidationError, ValueError, ValidationError) as exc:
                log.warning(
                    "crosscut response failed validation (attempt %d): %s",
                    attempt,
                    exc,
                )
                if attempt == 2:
                    return None
        return None

    @staticmethod
    def _extract_payload(response: object) -> dict[str, object]:
        # Prefer `content_dict` when populated by InferenceClient; fall back to
        # parsing `content` as JSON.
        content_dict = getattr(response, "content_dict", None)
        if isinstance(content_dict, dict):
            return dict(content_dict)
        content = getattr(response, "content", None)
        if not isinstance(content, str):
            raise ValueError("crosscut response missing content")
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("crosscut response is not a JSON object")
        return dict(parsed)

    def _build_themes(self, payload: dict[str, object]) -> list[Theme]:
        themes_raw = payload.get("themes", [])
        if not isinstance(themes_raw, list):
            raise ValueError("crosscut response 'themes' is not a list")
        themes: list[Theme] = []
        for raw in themes_raw:
            if not isinstance(raw, dict):
                continue
            # Recompute the id per spec section 5.4.1 (don't trust the model's id).
            title = str(raw.get("title", ""))
            tid = compute_theme_id(title, self._run_id)
            themes.append(
                Theme(
                    id=tid,
                    title=title,
                    description=str(raw.get("description", "")),
                    affected_files=[str(f) for f in raw.get("affected_files", [])],
                    priority=raw.get("priority", "low"),
                    confidence=raw.get("confidence", "low"),
                    recommended_action=str(raw.get("recommended_action", "")),
                )
            )
        return themes


def _now() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.UTC)


def _with_placeholder_ids(payload: dict[str, object]) -> dict[str, object]:
    """Return a shallow copy of `payload` with theme ids substituted to a
    schema-compliant placeholder. We recompute the canonical id in
    `_build_themes` per spec section 5.4.1 — the model's id is informational.
    """
    out = dict(payload)
    themes = out.get("themes")
    if isinstance(themes, list):
        new_themes = []
        for raw in themes:
            if isinstance(raw, dict):
                copy = dict(raw)
                copy["id"] = "t-000000000000"
                new_themes.append(copy)
            else:
                new_themes.append(raw)
        out["themes"] = new_themes
    return out


__all__ = [
    "CrossCutter",
    "compute_theme_id",
    "_top_n_per_priority",
    "_compress_to_tuples",
    "_chunk_for_crosscut",
]
