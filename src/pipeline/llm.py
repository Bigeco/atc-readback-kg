"""LLM backends for M4 extraction and the optional E2 evidence judge.

- "hf": local Hugging Face model (default Qwen/Qwen3-4B-Instruct-2507), batched greedy decoding.
        Required for restricted data (e.g. Korean recordings) because nothing leaves the machine.
- "openai": any OpenAI-compatible /v1/chat/completions endpoint (vLLM, OpenAI, ...), via urllib.
"""
import json
import os
import time
import urllib.request
from typing import List, Optional, Tuple

from json_repair import repair_json


def strip_thinking(text: str) -> str:
    if "</think>" in text:
        return text.split("</think>", 1)[1]
    return text


def extract_json_block(text: str, default: Optional[dict] = None) -> dict:
    start = text.find("{")
    if start == -1:
        return dict(default or {})

    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                chunk = text[start:i + 1]
                try:
                    parsed = json.loads(chunk)
                except json.JSONDecodeError:
                    parsed = repair_json(chunk, return_objects=True)
                if isinstance(parsed, dict):
                    return parsed
                break

    parsed = repair_json(text[start:], return_objects=True)
    return parsed if isinstance(parsed, dict) else dict(default or {})


class HFChatModel:
    def __init__(self, model_name: str = "Qwen/Qwen3-4B-Instruct-2507", device_map: str = "auto"):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.name = model_name
        self.tokenizer = AutoTokenizer.from_pretrained(model_name, padding_side="left")
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.model = AutoModelForCausalLM.from_pretrained(model_name, dtype=torch.bfloat16, device_map=device_map)
        self.model.eval()
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "latency_sec": 0.0}

    def generate(self, system_prompt: str, user_prompts: List[str], max_new_tokens: int = 320,
                 batch_size: int = 16) -> List[str]:
        import torch

        outputs = []
        for i in range(0, len(user_prompts), batch_size):
            batch = user_prompts[i:i + batch_size]
            texts = [
                self.tokenizer.apply_chat_template(
                    [{"role": "system", "content": system_prompt}, {"role": "user", "content": p}],
                    tokenize=False, add_generation_prompt=True, enable_thinking=False,
                )
                for p in batch
            ]
            enc = self.tokenizer(texts, return_tensors="pt", padding=True).to(self.model.device)

            t0 = time.perf_counter()
            with torch.no_grad():
                gen = self.model.generate(**enc, max_new_tokens=max_new_tokens, do_sample=False,
                                          pad_token_id=self.tokenizer.pad_token_id)
            elapsed = time.perf_counter() - t0

            new_tokens = gen[:, enc["input_ids"].shape[1]:]
            decoded = [strip_thinking(d) for d in self.tokenizer.batch_decode(new_tokens, skip_special_tokens=True)]
            outputs.extend(decoded)

            self.usage["calls"] += len(batch)
            self.usage["input_tokens"] += int(enc["attention_mask"].sum())
            self.usage["output_tokens"] += int((new_tokens != self.tokenizer.pad_token_id).sum())
            self.usage["latency_sec"] += elapsed
        return outputs


class OpenAICompatibleModel:
    def __init__(self, model_name: str, base_url: Optional[str] = None, api_key_env: str = "OPENAI_API_KEY"):
        self.name = model_name
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.api_key = os.getenv(api_key_env, "EMPTY")
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "latency_sec": 0.0}

    def _call(self, system_prompt: str, user_prompt: str, max_new_tokens: int) -> Tuple[str, dict]:
        payload = json.dumps({
            "model": self.name,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            "max_tokens": max_new_tokens,
            "temperature": 0,
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read())
        return body["choices"][0]["message"]["content"], body.get("usage", {})

    def generate(self, system_prompt: str, user_prompts: List[str], max_new_tokens: int = 320,
                 batch_size: int = 1) -> List[str]:
        outputs = []
        for prompt in user_prompts:
            t0 = time.perf_counter()
            text, usage = self._call(system_prompt, prompt, max_new_tokens)
            self.usage["calls"] += 1
            self.usage["input_tokens"] += int(usage.get("prompt_tokens", 0))
            self.usage["output_tokens"] += int(usage.get("completion_tokens", 0))
            self.usage["latency_sec"] += time.perf_counter() - t0
            outputs.append(text)
        return outputs


def load_llm(backend: str, model_name: str, base_url: Optional[str] = None):
    if backend == "hf":
        return HFChatModel(model_name)
    if backend == "openai":
        return OpenAICompatibleModel(model_name, base_url)
    raise ValueError(f"Unsupported LLM backend: {backend}")
