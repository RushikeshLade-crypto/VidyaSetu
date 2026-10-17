"""Print the chat and embedding model ids available in your watsonx.ai region.
Usage:  python scripts/list_models.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vidyasetu import config, watsonx  # noqa: E402

print(f"Region: {config.WATSONX_URL}\n")
print("Chat models (use for GEN_MODEL / TRANSLATE_MODEL):")
for m in watsonx.list_chat_models():
    print("  ", m)
print("\nEmbedding models (use for EMBED_MODEL):")
for m in watsonx.list_embedding_models():
    print("  ", m)
