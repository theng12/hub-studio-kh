"""Embeddings Studio model-memory policy: Chat's, unchanged.

Both Studios hold one MLX model between requests and release it the same way, so this is the same
module rather than a copy (`components.memory_modes` is the one mode table either way). The engine
interface it reads is `EmbeddingService` in `main.py`.
"""
from components.chat.app.backend.memory_policy import *  # noqa: F401,F403
from components.chat.app.backend.memory_policy import MemoryPolicy  # noqa: F401
