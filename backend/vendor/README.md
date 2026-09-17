# Vendored: emergentintegrations stub wheel

`emergentintegrations==0.1.0` is pinned in `backend/requirements.txt` but is **not
published on PyPI** — the real package ships only inside the Emergent base image.
Without it, `pip install -r requirements.txt` fails and `backend/agents.py`
cannot import (`from emergentintegrations.llm.chat import LlmChat, UserMessage`).

The wheel here is a 34-line local stub that preserves exactly that import
surface. `LlmChat.send_message` raises a clear `RuntimeError` instead of silently
no-oping — live agent pipelines error visibly until real credentials exist.

**Removal plan:** the LLM-adapter task replaces `emergentintegrations` with the
async Anthropic SDK; when that lands, delete this `vendor/` directory and drop
the pin from `requirements.txt`. Provenance: stub authored in this project
(sandbox `venv-claimos`), packaged as a standard `py3-none-any` wheel by the
CI-substrate task.
