from edraft.config import IdentityConfig, LLMConfig
from edraft.draft_generator import DraftGenerator


class DummyClient:
    pass


class CapturingResponses:
    def __init__(self) -> None:
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return type("Response", (), {"output_text": "Hi Andy,\n\nSounds good."})()


class CapturingClient:
    def __init__(self) -> None:
        self.responses = CapturingResponses()


def test_finalize_body_strips_subject_and_formats_lists() -> None:
    generator = DraftGenerator(
        LLMConfig(signature_block="Thanks,\nJude"),
        IdentityConfig(name="Jude Bayham", email="jbayham@colostate.edu"),
        client=DummyClient(),
    )

    output = generator._finalize_body(
        "Subject: Re: Test\n\nHi Andy,\nHere are my suggestions: 1. Add ENRE 292 2. Consider ESS"
    )

    assert not output.startswith("Subject:")
    assert "Here are my suggestions:\n1. Add ENRE 292\n2. Consider ESS" in output
    assert output.endswith("Thanks,\nJude")


def test_generate_passes_reasoning_effort() -> None:
    client = CapturingClient()
    generator = DraftGenerator(
        LLMConfig(model="gpt-5.4", reasoning_effort="medium", signature_block="Thanks,\nJude"),
        IdentityConfig(name="Jude Bayham", email="jbayham@colostate.edu"),
        client=client,
    )

    message = type(
        "Message",
        (),
        {
            "body_content": "<p>Hello</p>",
            "body_content_type": "html",
            "body_preview": "Hello",
            "subject": "Test",
            "sender_name": "Sender",
            "sender_address": "sender@example.com",
            "to_recipients": [],
            "cc_recipients": [],
        },
    )()
    thread_context = type("ThreadContext", (), {"related_messages": []})()

    generator.generate(message, thread_context)

    assert client.responses.kwargs["model"] == "gpt-5.4"
    assert client.responses.kwargs["reasoning"] == {"effort": "medium"}
