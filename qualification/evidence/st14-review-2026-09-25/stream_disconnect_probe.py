"""Review probe (2026-09-25): is the streaming lock still wedged by a client disconnect?

Mirrors the 2026-09-24 review's F4 harness: a real uvicorn server running the real FastAPI app
from components/chat, with an engine double whose stream() holds one lock across its yields the
way ChatEngine.stream does. A client starts a stream, reads one chunk, then goes away. If the
endpoint closes the generator, the lock is free moments later; if it does not, the lock stays
held and every later complete() would block.
"""
import threading
import time

import uvicorn
from fastapi.testclient import TestClient  # noqa: F401  (imported for parity with the app's tests)

from components.chat.app.backend import main as chat_main


class LockEngine:
    model_id = "mlx-community/Qwen3.5-9B-4bit"
    revision = "8b2b98c00a6b4d291155e4890773ca8f769aee53"
    label = "Qwen3.5-9B-4bit"
    family = "qwen3_5"
    context_window_tokens = 262144
    max_output_tokens = 4096
    minimum_unified_memory_gb = 16.0

    def __init__(self):
        self.lock = threading.Lock()
        self.started = 0
        self.finished = 0

    def preflight(self): return {"state": "files_verified"}
    def loaded(self): return False
    def status(self):
        return {"busy": self.lock.locked(), "loaded": False, "active_memory_bytes": None,
                "peak_memory_bytes": None, "model_id": self.model_id, "revision": self.revision}
    def model_limits(self):
        return {"context_window_tokens": {"value": 262144, "source": "probe"},
                "max_output_tokens": {"value": 4096, "source": "probe"}}
    def count_tokens(self, text): return 1
    def prompt_length(self, messages): return 1
    def complete(self, messages, **kw):
        with self.lock:
            return {"text": "ok", "elapsed_seconds": 0.01, "max_tokens": 8,
                    "prompt_tokens": 1, "json_valid": None, "repairs": 0}
    def release(self, **kw): return {"released": False}
    def last_activity_at(self): return None

    def stream(self, messages, **kw):
        # Exactly ChatEngine.stream's shape: the lock is held for the whole generator body.
        with self.lock:
            self.started += 1
            try:
                for index in range(60):
                    yield "chunk%02d " % index
                    time.sleep(0.1)
            finally:
                self.finished += 1


class Policy:
    def status(self): return {"mode": "performance"}
    def save(self, mode): return {"mode": mode}
    def release(self, reason="manual"): return {"resident": False}


engine = LockEngine()
token = "p" * 32
app = chat_main.create_app(engine, Policy(), token, "a" * 64)
config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
server = uvicorn.Server(config)
thread = threading.Thread(target=server.run, daemon=True)
thread.start()
for _ in range(100):
    if server.started:
        break
    time.sleep(0.05)
port = server.servers[0].sockets[0].getsockname()[1]
print("probe server on 127.0.0.1:%d" % port)

import httpx  # noqa: E402

# A client starts a stream and disappears after the first chunk — the same event as quitting the
# app or dropping the connection mid-answer.
try:
    with httpx.stream("POST", "http://127.0.0.1:%d/v1/chat/completions" % port,
                      headers={"Authorization": "Bearer " + token},
                      json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
                      timeout=5.0) as response:
        for line in response.iter_lines():
            if line:
                print("first chunk:", line[:60])
                break
except Exception as exc:
    print("client ended:", type(exc).__name__, exc)

for wait in (5, 10, 20):
    time.sleep(5 if wait == 5 else wait - 5)
    print("after %2ds: lock.locked()=%s streams started=%d finished=%d"
          % (wait, engine.lock.locked(), engine.started, engine.finished))
print()
print("WEDGED" if engine.lock.locked() else "RELEASED")
server.should_exit = True
thread.join(timeout=10)
