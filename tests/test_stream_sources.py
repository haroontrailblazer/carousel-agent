from app.runs.stream import build_trace


def _row(seq: int, timestamp: str, parts: list[dict]) -> dict:
    return {
        "seq": seq,
        "created_at": timestamp,
        "event_data": {
            "author": "research",
            "invocation_id": "invocation-1",
            "content": {"parts": parts},
        },
    }


def test_build_trace_pairs_tools_and_exposes_recorded_sources() -> None:
    rows = [
        _row(
            1,
            "2026-08-25T12:00:00+00:00",
            [
                {
                    "function_call": {
                        "id": "call-1",
                        "name": "search_web",
                        "args": {"query": "client tracing"},
                    }
                }
            ],
        ),
        _row(
            2,
            "2026-08-25T12:00:02+00:00",
            [
                {
                    "function_response": {
                        "id": "call-1",
                        "name": "search_web",
                        "response": {
                            "answer": "See https://supabase.com/docs/tracing for details.",
                            "sources": [
                                "https://supabase.com/docs/tracing",
                                "https://opentelemetry.io/docs/concepts/context-propagation/",
                            ],
                        },
                    }
                }
            ],
        ),
    ]

    frames, summary = build_trace(rows)

    assert frames[0]["tools"][0]["status"] == "ok"
    assert frames[0]["tools"][0]["ms"] == 2000
    assert frames[1]["data"]["sources"] == [
        "https://supabase.com/docs/tracing",
        "https://opentelemetry.io/docs/concepts/context-propagation/",
    ]
    assert summary["tool_calls"] == 1
