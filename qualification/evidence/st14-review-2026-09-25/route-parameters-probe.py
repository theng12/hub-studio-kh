"""What /v1/chat/completions accepts today, behind the real FastAPI app.

Run with the app runtime from the repo root:

    PYTHONPATH=. ./.build/voice-runtime/bin/python3 \
        qualification/evidence/st14-review-2026-09-25/route-parameters-probe.py

The engine is a recording double that returns a fixed completion, so nothing is loaded and no
weights are read. Every row below is what the route did with the request, which is what a caller
(the Hub, gateway-kh, Story Studio) can rely on.
"""
import json

from fastapi.testclient import TestClient

from components.chat.app.backend import main as chat_main

TOKEN = "t" * 32
HEADERS = {"Authorization": "Bearer " + TOKEN}


class RecordingEngine:
    model_id = "mlx-community/Qwen3.5-9B-4bit"
    revision = "8b2b98c00a6b4d291155e4890773ca8f769aee53"
    label = "Qwen3.5-9B-4bit"
    family = "qwen3_5"
    context_window_tokens = 262144
    max_output_tokens = 4096
    minimum_unified_memory_gb = 16.0

    def __init__(self):
        self.seen = []

    def preflight(self): return {"state": "files_verified"}
    def loaded(self): return False
    def status(self):
        return {"busy": False, "loaded": False, "active_memory_bytes": None,
                "peak_memory_bytes": None, "model_id": self.model_id, "revision": self.revision}
    def model_limits(self):
        return {"context_window_tokens": {"value": 262144, "source": "probe"},
                "max_output_tokens": {"value": 4096, "source": "probe"}}
    def count_tokens(self, text): return 1
    def prompt_length(self, messages): return 1
    def complete(self, messages, **kwargs):
        self.seen.append({**kwargs, "message_count": len(messages)})
        return {"text": "ok", "elapsed_seconds": 0.01, "max_tokens": 8,
                "prompt_tokens": 1, "json_valid": None, "repairs": 0}
    def release(self, **kwargs): return {"released": False}
    def last_activity_at(self): return None


class Policy:
    def status(self): return {"mode": "immediate"}
    def save(self, mode): return {"mode": mode}
    def release(self, reason="manual"): return {"resident": False}


def main() -> None:
    engine = RecordingEngine()
    client = TestClient(chat_main.create_app(engine, Policy(), TOKEN, "a" * 64))
    base = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 8}
    cases = [
        ("thinking string 'false'", {"thinking": "false"}),
        ("thinking string 'no'", {"thinking": "no"}),
        ("thinking number 0", {"thinking": 0}),
        ("max_tokens -5", {"max_tokens": -5}),
        ("max_tokens 0", {"max_tokens": 0}),
        ("max_tokens 1e11", {"max_tokens": 100000000000}),
        ("temperature -3.5", {"temperature": -3.5}),
        ("top_p 0", {"top_p": 0}),
        ("top_p 1.5", {"top_p": 1.5}),
        ("response_format 42", {"response_format": 42}),
        ("seed 'not-a-number'", {"seed": "not-a-number"}),
        ("prompt 200 000 chars", {"messages": [{"role": "user", "content": "x" * 200000}]}),
        ("500 messages", {"messages": [{"role": "user", "content": "hi"}] * 500}),
        ("tools / n / logit_bias sent", {"tools": [1], "n": 5, "logit_bias": {"a": 1}}),
    ]
    results = []
    for name, extra in cases:
        body = dict(base)
        body.update(extra)
        engine.seen = []
        response = client.post("/v1/chat/completions", json=body, headers=HEADERS)
        record = {"case": name, "http": response.status_code}
        if engine.seen:
            received = engine.seen[-1]
            record["engine_received"] = {key: received.get(key) for key in
                                         ("max_tokens", "temperature", "top_p", "thinking", "seed")}
            record["message_count"] = received.get("message_count")
        else:
            record["detail"] = response.json().get("detail")
        results.append(record)

    # `NaN` has to be sent as a raw body: httpx refuses to serialise it, while Python's json
    # parser accepts it — which is exactly the door a hand-written client can walk through.
    engine.seen = []
    raw = '{"messages":[{"role":"user","content":"hi"}],"max_tokens":8,"temperature":NaN}'
    response = client.post("/v1/chat/completions", content=raw,
                           headers={**HEADERS, "Content-Type": "application/json"})
    record = {"case": "temperature NaN", "http": response.status_code}
    if engine.seen:
        record["engine_received"] = {"temperature": repr(engine.seen[-1].get("temperature"))}
    results.append(record)

    print(json.dumps({
        "probe": "chat route parameter surface, 2026-09-25",
        "app": "components/chat/app/backend/main.py (shipped 0.6.62)",
        "results": results,
    }, indent=1))


if __name__ == "__main__":
    main()
