from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any
from uuid import uuid4

from edraft.config import StyleCorpusConfig
from edraft.draft_generator import DraftGenerationError, DraftGenerator, DraftPrompt
from edraft.models import MailboxMessage, Recipient, ThreadContext
from edraft.style_corpus import StyleCorpusStore, StyleExampleRetriever


@dataclass(slots=True)
class StyleEvalGrade:
    tone_match: int
    brevity_match: int
    commitment_safety: int
    clarity: int
    overall: int
    notes: str


class StyleEvaluator:
    def __init__(
        self,
        *,
        config: StyleCorpusConfig,
        generator: DraftGenerator,
        store: StyleCorpusStore,
        retriever: StyleExampleRetriever,
    ) -> None:
        self.config = config
        self.generator = generator
        self.store = store
        self.retriever = retriever

    def evaluate(
        self,
        *,
        limit: int | None = None,
        include_prompts: bool = False,
    ) -> dict[str, Any]:
        case_limit = limit or self.config.eval_max_cases
        holdout_count = self.store.refresh_eval_holdout(
            holdout_days=self.config.eval_holdout_days,
            limit=case_limit,
        )
        cases = self.store.load_eval_cases(limit=case_limit)
        if not cases:
            return {
                "holdout_cases": holdout_count,
                "evaluated_cases": 0,
                "averages": {},
                "cases": [],
            }

        cutoff = datetime.now(timezone.utc) - timedelta(days=self.config.eval_holdout_days)
        run_id = f"style-eval-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        results = []
        for case in cases:
            inbound_message = MailboxMessage(
                id=f"eval-{case.reply_message_id}",
                conversation_id=None,
                subject=case.subject,
                from_recipient=Recipient(name=case.correspondent_email, address=case.correspondent_email),
                to_recipients=[],
                cc_recipients=[],
                received_at=None,
                body_content=case.inbound_text,
                body_content_type="text",
                body_preview=case.inbound_text[:200],
            )
            thread_context = ThreadContext(conversation_id=None, related_messages=[])
            style_examples = self.retriever.retrieve(
                inbound_message,
                thread_context,
                exclude_reply_ids=(case.reply_message_id,),
                reply_received_before=cutoff,
            )
            generated_reply, generation_prompt = self.generator.generate_with_prompt(
                inbound_message,
                thread_context,
                style_examples,
            )
            grade, grading_prompt = self._grade_case(
                inbound_text=case.inbound_text,
                generated_reply=generated_reply,
                actual_reply=case.actual_reply_text,
            )
            style_example_ids = [example.reply_message_id for example in style_examples]
            self.store.record_eval_result(
                run_id=run_id,
                reply_message_id=case.reply_message_id,
                correspondent_email=case.correspondent_email,
                subject=case.subject,
                generated_reply=generated_reply,
                actual_reply=case.actual_reply_text,
                style_example_ids=style_example_ids,
                generation_system_prompt=generation_prompt.system,
                generation_user_prompt=generation_prompt.user,
                grading_system_prompt=grading_prompt.system,
                grading_user_prompt=grading_prompt.user,
                tone_match=grade.tone_match,
                brevity_match=grade.brevity_match,
                commitment_safety=grade.commitment_safety,
                clarity=grade.clarity,
                overall=grade.overall,
                notes=grade.notes,
                model=self.generator.config.model,
                reasoning_effort=self.generator.config.reasoning_effort,
            )
            results.append(
                {
                    "reply_message_id": case.reply_message_id,
                    "correspondent_email": case.correspondent_email,
                    "subject": case.subject,
                    "generated_reply": generated_reply,
                    "actual_reply": case.actual_reply_text,
                    "style_example_ids": style_example_ids,
                    "grade": asdict(grade),
                }
            )
            if include_prompts:
                results[-1]["generation_prompt"] = asdict(generation_prompt)
                results[-1]["grading_prompt"] = asdict(grading_prompt)

        averages = {
            metric: round(mean(item["grade"][metric] for item in results), 2)
            for metric in ["tone_match", "brevity_match", "commitment_safety", "clarity", "overall"]
        }
        return {
            "run_id": run_id,
            "holdout_cases": holdout_count,
            "evaluated_cases": len(results),
            "averages": averages,
            "cases": results,
        }

    def _grade_case(
        self,
        *,
        inbound_text: str,
        generated_reply: str,
        actual_reply: str,
    ) -> tuple[StyleEvalGrade, DraftPrompt]:
        prompt = DraftPrompt(
            system="You are a strict email style grader. Return JSON only.",
            user="\n".join(
            [
                "You grade whether a generated email reply matches the author's real reply style.",
                "Score each dimension from 1 to 5.",
                "Return strict JSON only with keys tone_match, brevity_match, commitment_safety, clarity, overall, notes.",
                "High commitment_safety means the generated reply avoids unsupported promises or invented facts.",
                "",
                "Inbound message:",
                inbound_text,
                "",
                "Generated reply:",
                generated_reply,
                "",
                "Actual reply:",
                actual_reply,
            ]
            ),
        )
        request_kwargs = {
            "model": self.generator.config.model,
            "input": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
            "store": False,
        }
        if self.generator.config.reasoning_effort:
            request_kwargs["reasoning"] = {"effort": self.generator.config.reasoning_effort}
        try:
            response = self.generator.client.responses.create(**request_kwargs)
        except Exception as exc:
            raise DraftGenerationError(f"Style eval grading failed: {exc}") from exc
        payload = _parse_json_output((getattr(response, "output_text", "") or "").strip())
        return StyleEvalGrade(
            tone_match=int(payload["tone_match"]),
            brevity_match=int(payload["brevity_match"]),
            commitment_safety=int(payload["commitment_safety"]),
            clarity=int(payload["clarity"]),
            overall=int(payload["overall"]),
            notes=str(payload.get("notes", "")).strip(),
        ), prompt


def _parse_json_output(raw: str) -> dict[str, Any]:
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.DOTALL).strip()
    return json.loads(stripped)
