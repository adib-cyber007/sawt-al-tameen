import json

import pytest

from preauth.application.assemblyai_post_call import normalize_timeline
from preauth.infrastructure.assemblyai_signature import (
    AssemblyAIWebhookSignatureError,
    sign,
    verify,
)


def test_assemblyai_webhook_signature_accepts_an_authentic_delivery():
    body = b'{"type":"session.completed"}'
    header = sign(body, "secret", 1_700_000_000)
    verify(body, header, "secret", now=1_700_000_100)


@pytest.mark.parametrize(
    "header",
    [None, "", "v1=abc", "t=not-a-number,v1=abc", "t=1700000000", "unstructured"],
)
def test_assemblyai_webhook_signature_rejects_missing_or_malformed_headers(header):
    with pytest.raises(AssemblyAIWebhookSignatureError):
        verify(b"{}", header, "secret", now=1_700_000_000)


def test_assemblyai_webhook_signature_rejects_tampering_and_replay():
    body = b"{}"
    header = sign(body, "secret", 1_700_000_000)
    with pytest.raises(AssemblyAIWebhookSignatureError, match="does not match"):
        verify(body + b" ", header, "secret", now=1_700_000_000)
    with pytest.raises(AssemblyAIWebhookSignatureError, match="tolerance"):
        verify(body, header, "secret", now=1_700_000_301)
    with pytest.raises(AssemblyAIWebhookSignatureError, match="tolerance"):
        verify(body, header, "secret", now=1_699_999_699)


def test_timeline_normalization_preserves_dialogue_tools_and_quality_metrics():
    timeline = {
        "turns": [
            {
                "trigger": "user_speech",
                "status": "complete",
                "user_transcript": "I need a knee arthroscopy.",
                "user_confidence": 0.9,
                "agent_text": "I will check the rule.",
                "time_to_first_audio_ms": 220,
                "tool_calls": [{"name": "check_coverage_rule", "arguments": {"procedure_code": "SP-20040"}}],
            },
            {
                "status": "interrupted",
                "user_transcript": "Please call me back.",
                "user_confidence": 0.7,
                "time_to_first_audio_ms": 300,
                "tool_calls": [
                    {
                        "tool_name": "log_transcript",
                        "args": json.dumps({"summary": "Arthroscopy request sent for human review."}),
                        "timed_out": True,
                    }
                ],
            },
        ]
    }

    transcript, metrics, summary = normalize_timeline(timeline)

    assert [entry["role"] for entry in transcript] == ["caller", "tool", "agent", "caller", "tool"]
    assert transcript[1]["arguments"] == {"procedure_code": "SP-20040"}
    assert transcript[-1]["is_error"] is True
    assert metrics == {
        "turns": 2,
        "caller_turns": 2,
        "interrupted_turns": 1,
        "tool_calls": 2,
        "tool_errors": 1,
        "average_user_confidence": pytest.approx(0.8),
        "average_time_to_first_audio_ms": 260,
    }
    assert summary == "Arthroscopy request sent for human review."

