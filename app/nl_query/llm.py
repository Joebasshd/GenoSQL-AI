"""
Thin LLM adapter. Two backends cover everything we need to benchmark:
- "ollama": local open models (default, zero marginal cost)
- "openai": any OpenAI-compatible /chat/completions endpoint (vLLM,
  llama.cpp server, LM Studio, hosted APIs) for comparison runs
"""
import time
from dataclasses import dataclass

import httpx
from app import config


@dataclass
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int | None
    output_tokens: int | None
    latency_ms: int


def chat(messages: list[dict], model: str | None = None, backend: str | None = None) -> LLMResponse:
    model = model or config.LLM_MODEL
    backend = backend or config.LLM_BACKEND
    started = time.perf_counter()

    if backend == "ollama":
        r = httpx.post(
            f"{config.OLLAMA_HOST}/api/chat",
            json={"model": model, "messages": messages, "stream": False,
                  "options": {"temperature": 0, "seed": 42, "num_ctx": config.LLM_NUM_CTX,
                              "num_predict": config.LLM_MAX_TOKENS}},
            timeout=600,
        )
        r.raise_for_status()
        body = r.json()
        text = body["message"]["content"]
        prompt_tokens, output_tokens = body.get("prompt_eval_count"), body.get("eval_count")

    elif backend == "openai":
        headers = {"Authorization": f"Bearer {config.OPENAI_API_KEY}"} if config.OPENAI_API_KEY else {}
        r = httpx.post(
            f"{config.OPENAI_BASE_URL.rstrip('/')}/chat/completions",
            json={"model": model, "messages": messages, "temperature": 0, "max_tokens": config.LLM_MAX_TOKENS},
            headers=headers, timeout=600,
        )
        r.raise_for_status()
        body = r.json()
        text = body["choices"][0]["message"]["content"]
        usage = body.get("usage") or {}
        prompt_tokens, output_tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")

    else:
        raise ValueError(f"Unknown LLM backend: {backend}")

    return LLMResponse(
        text=text, model=model, prompt_tokens=prompt_tokens, output_tokens=output_tokens,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
