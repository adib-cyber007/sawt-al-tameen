"""The local conversational agent.

The agent orchestrates; it does not decide. Every fact it states about eligibility, cover, documents, limits or
outcome arrives as the result of a tool call made in the same turn, through the *same* ``VoiceToolGateway`` the
hosted AssemblyAI channel uses. There is no local rules engine, no local catalogue lookup and no local case handling.

Three things are enforced here rather than asked for in the prompt:

1. **The tool set.** Whatever the model tries to call is resolved against ``AgentToolbox``; an unknown name comes
   back as a tool error the model has to recover from. There is no tool that decides a case, so asking for one
   cannot produce one.
2. **The actor.** Tool calls are made as a ``VOICE_AGENT`` actor, which the review API rejects outright.
3. **The wording.** A reply that states a final approval or denial is stripped before it reaches the caller and
   replaced with the standard boundary sentence. This never touches case state — it only stops the agent from
   *saying* something the system did not do.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from preauth.agent_tools.toolbox import AgentToolbox
from preauth.agent_tools.voice_gateway import VoiceToolGateway, VoiceToolResponse
from preauth.local.config import LocalSettings
from preauth.local.errors import SpeechNotRecognisedError
from preauth.local.llm import ChatModel
from preauth.local.prompt import DECISION_BOUNDARY, GREETING, system_prompt
from preauth.local.session import REQUIRED_INTAKE_FIELDS, LocalSession, Turn
from preauth.local.tools import llm_tool_definitions

logger = logging.getLogger("preauth.local.agent")

# A tool result is evidence the model must reason over, so it is passed through whole rather than summarised.
# The cap only guards against a pathological payload filling the model's context.
MAX_TOOL_RESULT_CHARS = 8000

# Wording that asserts a decision has been made. Matching text is removed from the reply, never from the record.
_FINAL_DECISION = re.compile(
    r"\b(?:is|are|was|were|has been|have been|been|now)\s+(?:been\s+)?"
    r"(?:approved|denied|authorised|authorized|rejected|declined)\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")

FALLBACK_REPLY = "Sorry, I did not catch that. Could you repeat it, spelling out any reference numbers?"
# Said when the model spent its tool budget without producing an answer. It admits the trouble rather than
# blaming the caller, and promises nothing the system has not actually done.
BUDGET_EXHAUSTED_REPLY = (
    "Sorry, I am having trouble completing that check. Could you give me the last detail again?"
)


@dataclass(frozen=True)
class AgentTurn:
    reply: str
    tool_calls: tuple[dict[str, Any], ...]
    state: dict[str, Any]
    # True when decision wording was removed from the model's reply before the caller saw it.
    decision_language_blocked: bool = False


def _strip_decision_language(text: str) -> tuple[str, bool]:
    sentences = [s for s in _SENTENCE.split(text) if s.strip()]
    kept = [s for s in sentences if not _FINAL_DECISION.search(s)]
    if len(kept) == len(sentences):
        return text, False
    return (" ".join(kept + [DECISION_BOUNDARY])).strip(), True


def _compact(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, default=str, separators=(",", ":"))
    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return text
    logger.warning("tool_result_truncated", extra={"length": len(text)})
    return text[:MAX_TOOL_RESULT_CHARS] + '..."}'


@dataclass
class LocalAgent:
    """Owns one model and one gateway; sessions are passed in, never held."""

    model: ChatModel
    gateway: VoiceToolGateway
    toolbox: AgentToolbox
    settings: LocalSettings
    _tools: list[dict[str, Any]] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self._tools = llm_tool_definitions(self.toolbox.describe())

    # ------------------------------------------------------------------ turns

    def greet(self, session: LocalSession, now: datetime) -> AgentTurn:
        session.record_turn(Turn(role="agent", text=GREETING, at=now))
        session.messages.append({"role": "assistant", "content": GREETING})
        logger.info("conversation_started")
        return AgentTurn(reply=GREETING, tool_calls=(), state=session.state())

    def respond(self, session: LocalSession, caller_text: str, now: datetime) -> AgentTurn:
        text = (caller_text or "").strip()
        if not text:
            raise SpeechNotRecognisedError("Nothing was said. Ask the caller to repeat.")

        session.record_turn(Turn(role="caller", text=text, at=now))
        session.messages.append({"role": "user", "content": text})
        logger.info(
            "llm_started",
            extra={"model": self.model.name, "turns": len(session.turns)},
        )

        performed: list[dict[str, Any]] = []
        reply = ""
        for iteration in range(self.settings.max_tool_iterations + 1):
            last = iteration == self.settings.max_tool_iterations
            # On the final pass the tools are withheld, so the model has no choice but to speak to the caller.
            answer = self.model.chat(self._messages(session), [] if last else self._tools)
            if last or not answer.tool_calls:
                if last and answer.tool_calls:
                    # No tools were offered on this pass, so anything it asked for is discarded, not run.
                    logger.warning(
                        "tool_budget_exhausted", extra={"discarded": [c.name for c in answer.tool_calls]}
                    )
                    reply = answer.text.strip() or BUDGET_EXHAUSTED_REPLY
                else:
                    reply = answer.text
                break
            session.messages.append(
                {
                    "role": "assistant",
                    "content": answer.text,
                    "tool_calls": [
                        {"function": {"name": c.name, "arguments": c.arguments}} for c in answer.tool_calls
                    ],
                }
            )
            for call in answer.tool_calls:
                performed.append(self._run_tool(session, call.name, call.arguments, now))
        else:  # pragma: no cover - the loop always breaks or exhausts through `last`
            reply = ""

        reply = reply.strip() or FALLBACK_REPLY
        reply, blocked = _strip_decision_language(reply)
        if blocked:
            logger.warning("decision_language_blocked")
        session.messages.append({"role": "assistant", "content": reply})
        session.record_turn(Turn(role="agent", text=reply, at=now))
        logger.info(
            "llm_completed",
            extra={"tool_calls": len(performed)},
        )
        return AgentTurn(
            reply=reply,
            tool_calls=tuple(performed),
            state=session.state(),
            decision_language_blocked=blocked,
        )

    # ------------------------------------------------------------------ internals

    def _messages(self, session: LocalSession) -> list[dict[str, Any]]:
        """System prompt, the dialogue, then a reminder of what the backend already holds for this call."""
        return [
            {"role": "system", "content": system_prompt()},
            *session.messages,
            {"role": "system", "content": self._state_reminder(session)},
        ]

    @staticmethod
    def _state_reminder(session: LocalSession) -> str:
        """What the *backend* holds for this call, plus what the next tool call will need.

        Carefully worded. The backend only learns a procedure code when a tool is called with one, so this must
        not say a detail is outstanding merely because no tool has run — the caller may well have just given it,
        and telling the model otherwise sends it round in circles asking again. Facts are stated as facts; the
        rest is stated as a requirement of the tool.
        """
        lines = ["Backend state for this call (authoritative -- never ask again for anything listed as held):"]
        if session.verified and session.verification_id and session.member_verified:
            lines.append(f"- caller and member verified; verification_id={session.verification_id}")
        elif session.verified and session.verification_id:
            lines.append(
                f"- organisation verified (verification_id={session.verification_id}) but NO member verified. "
                "check_coverage_rule will refuse until one is: once the caller gives the policy number and date "
                "of birth, call verify_caller again with member_policy_number and member_date_of_birth."
            )
        else:
            lines.append(
                "- caller NOT verified yet. Call verify_caller once you have the organisation name, the "
                "provider or onboarding reference, and -- for a member request -- the policy number and date "
                "of birth. Discuss nothing policy-specific before it returns authorised: true."
            )
        if session.intake:
            lines.append("- held: " + ", ".join(f"{name}={value}" for name, value in session.intake.items()))
        if session.case_reference:
            lines.append(f"- case_reference={session.case_reference}, status={session.case_status}")
        if session.recommendation_outcome:
            lines.append(
                f"- last check returned {session.recommendation_outcome} -- a recommendation for a human to "
                "confirm, never a decision"
            )
        if session.missing_documents:
            lines.append("- documents outstanding: " + "; ".join(session.missing_documents))

        if session.member_verified and not session.case_reference:
            lines.append(
                "Next: check_coverage_rule needs "
                + ", ".join(REQUIRED_INTAKE_FIELDS)
                + " and the verification_id. If the caller has already given a value, use it -- do not ask "
                "twice. Read the full request back for confirmation, then call the tool."
            )
        return "\n".join(lines)

    def _run_tool(
        self, session: LocalSession, name: str, arguments: dict[str, Any], now: datetime
    ) -> dict[str, Any]:
        logger.info("tool_called", extra={"tool": name})
        response = self.gateway.call(name, arguments, conversation_id=session.conversation_id)
        payload = response.model_dump(mode="json", exclude_none=True)
        session.messages.append({"role": "tool", "name": name, "content": _compact(payload)})
        session.record_turn(
            Turn(
                role="tool",
                text=_compact(payload),
                at=now,
                tool_name=name,
                tool_ok=response.ok,
            )
        )
        self._absorb(session, name, arguments, response)
        record = {
            "tool": name,
            "ok": response.ok,
            "arguments": arguments,
            "error_code": (response.error or {}).get("code") if response.error else None,
        }
        session.tool_calls.append(record)
        logger.info(
            "tool_completed",
            extra={"tool": name, "ok": response.ok, "error_code": record["error_code"]},
        )
        return record

    @staticmethod
    def _absorb(
        session: LocalSession, name: str, arguments: dict[str, Any], response: VoiceToolResponse
    ) -> None:
        """Copy the durable references out of a tool result so the UI and the next turn can use them."""
        result = response.result or {}
        if name == "check_coverage_rule":
            session.note_intake(arguments)
        if not response.ok:
            return
        if name == "verify_caller":
            session.verification_id = result.get("verification_id")
            session.verified = bool(result.get("authorised"))
            session.member_verified = session.verified and bool(result.get("member"))
        elif name == "check_coverage_rule":
            session.case_id = result.get("case_id") or session.case_id
            session.case_reference = result.get("case_reference") or session.case_reference
            session.case_status = result.get("status") or session.case_status
            session.recommendation_outcome = result.get("outcome") or session.recommendation_outcome
            session.escalation_rule_ids = [
                c.get("rule_id") for c in result.get("escalation_citations") or [] if c.get("rule_id")
            ]
            session.missing_documents = [
                m.get("description") or m.get("code") for m in result.get("missing_information") or []
            ]
            if session.recommendation_outcome == "ESCALATE":
                logger.info(
                    "escalation",
                    extra={"escalation_rule_ids": session.escalation_rule_ids},
                )
            else:
                logger.info(
                    "recommendation_created",
                    extra={"outcome": session.recommendation_outcome},
                )
        elif name == "log_transcript":
            session.call_log_reference = result.get("reference") or session.call_log_reference
