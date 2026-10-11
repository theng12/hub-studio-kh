"""Vision Studio model-memory policy: Chat's, unchanged.

Like Chat and Embeddings it holds MLX models between requests and releases them the same way, so
this is the same module rather than a copy (`components.memory_modes` is the one mode table either
way). The engine interface it reads is `VisionService` in `main.py`.
"""
from components.chat.app.backend.memory_policy import *  # noqa: F401,F403
from components.chat.app.backend.memory_policy import MemoryPolicy  # noqa: F401
