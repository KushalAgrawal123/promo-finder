"""Provider-agnostic LLM layer.

complete_json(step, key, system, user, schema, model_ref) -> (pydantic object, usage dict)

model_ref is "provider/model", e.g.
  gemini/gemini-3.5-flash, groq/openai/gpt-oss-120b, ollama/qwen2.5:14b,
  custom/<model> (any OpenAI-compatible URL in CUSTOM_LLM_BASE_URL),
  anthropic/claude-opus-5-5 (native Anthropic SDK).

Features: JSON-schema output + pydantic validation (1 repair retry), disk cache
(re-runs are free and resumable), per-provider rate limiting, retries with backoff,
unedited raw request/response logging, token usage + list-price cost tracking.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError


class LLMError(RuntimeError):
    pass


@dataclass
class Usage:
    calls: int = 0
    cached_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    by_model: dict = field(default_factory=dict)
    failures: list = field(default_factory=list)

    def add(self, model_ref: str, inp: int, out: int, secs: float, cached: bool):
        self.calls += 1
        self.cached_calls += int(cached)
        self.input_tokens += inp
        self.output_tokens += out
        self.seconds += secs
        m = self.by_model.setdefault(model_ref, {"calls": 0, "cached_calls": 0, "input_tokens": 0, "output_tokens": 0})
        m["calls"] += 1
        m["cached_calls"] += int(cached)
        m["input_tokens"] += inp
        m["output_tokens"] += out


class _RateLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._next: dict[str, float] = {}

    def wait(self, provider: str, rpm: float):
        if not rpm:
            return
        gap = 60.0 / rpm
        with self._lock:
            now = time.time()
            t = max(now, self._next.get(provider, 0.0))
            self._next[provider] = t + gap
        if t > now:
            time.sleep(t - now)


def _strictify(schema: dict) -> dict:
    """OpenAI-style strict schemas need every property required + additionalProperties false."""
    schema = json.loads(json.dumps(schema))

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"].keys())
                node["additionalProperties"] = False
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(schema)
    return schema


def _inline_refs(schema: dict) -> dict:
    """Inline $defs/$ref — some OpenAI-compatible providers reject $ref."""
    defs = schema.get("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].split("/")[-1]
                return resolve(json.loads(json.dumps(defs[name])))
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node
    return resolve(schema)


def _clean_schema(model: type[BaseModel]) -> dict:
    s = _inline_refs(model.model_json_schema())

    def strip(node):
        if isinstance(node, dict):
            if isinstance(node.get("title"), str):   # schema annotation, NOT a property named "title"
                node.pop("title")
            for v in node.values():
                strip(v)
        elif isinstance(node, list):
            for v in node:
                strip(v)
    strip(s)
    return _strictify(s)


def _extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        a, b = text.find("{"), text.rfind("}")
        if a >= 0 and b > a:
            return json.loads(text[a:b + 1])
        raise


class LLM:
    def __init__(self, config: dict, cache_dir: str | Path = ".cache", raw_dir: str | Path | None = None,
                 use_cache: bool = True, api_keys: dict | None = None, log=print):
        self.cfg = config
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir = Path(raw_dir) if raw_dir else None
        if self.raw_dir:
            self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.use_cache = use_cache
        self.api_keys = api_keys or {}
        self.usage = Usage()
        self.log = log
        self._limiter = _RateLimiter()
        self._clients: dict[str, Any] = {}
        self._lock = threading.Lock()

    # ---------- provider plumbing ----------
    def _provider(self, model_ref: str) -> tuple[str, str, dict]:
        if "/" not in model_ref:
            raise LLMError(f"model must be 'provider/model', got {model_ref!r}")
        provider, model = model_ref.split("/", 1)
        pcfg = self.cfg["providers"].get(provider)
        if pcfg is None:
            raise LLMError(f"unknown provider {provider!r}; add it under providers: in config.yaml")
        return provider, model, pcfg

    def _key(self, pcfg: dict) -> str | None:
        env = pcfg.get("api_key_env")
        if not env:
            return "not-needed"
        return self.api_keys.get(env) or os.environ.get(env)

    def _client(self, provider: str, pcfg: dict):
        with self._lock:
            if provider in self._clients:
                return self._clients[provider]
            key = self._key(pcfg)
            if not key:
                raise LLMError(f"missing API key: set {pcfg.get('api_key_env')} in .env (local) or in the app's Streamlit Secrets")
            if pcfg["type"] == "anthropic":
                import anthropic
                client = anthropic.Anthropic(api_key=key, max_retries=0, timeout=300)
            else:
                from openai import OpenAI
                base_url = pcfg.get("base_url") or os.environ.get(pcfg.get("base_url_env", ""), "")
                if not base_url:
                    raise LLMError(f"provider {provider} has no base_url (set {pcfg.get('base_url_env')})")
                client = OpenAI(base_url=base_url, api_key=key, max_retries=0, timeout=180)
            self._clients[provider] = client
            return client

    def _call_openai(self, client, model, system, user, schema, temperature, json_mode):
        kwargs: dict[str, Any] = dict(model=model, messages=[{"role": "system", "content": system},
                                                             {"role": "user", "content": user}])
        if temperature is not None:
            kwargs["temperature"] = temperature
        if json_mode == "schema":
            kwargs["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": "output", "schema": schema, "strict": True}}
        else:
            kwargs["response_format"] = {"type": "json_object"}
        r = client.chat.completions.create(**kwargs)
        text = r.choices[0].message.content or ""
        u = r.usage
        return text, (u.prompt_tokens if u else 0), (u.completion_tokens if u else 0), r.model_dump()

    def _call_anthropic(self, client, model, system, user, schema, pcfg):
        # Native Anthropic SDK. No temperature (rejected by current models); structured output via
        # output_config.format; read the text block (thinking blocks may come first).
        r = client.messages.create(
            model=model, max_tokens=16000, system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema},
                           "effort": pcfg.get("effort", "medium")},
        )
        if r.stop_reason == "refusal":
            raise LLMError(f"model refused (stop_details={getattr(r, 'stop_details', None)})")
        text = next((b.text for b in r.content if b.type == "text"), "")
        return text, r.usage.input_tokens, r.usage.output_tokens, r.model_dump()

    # ---------- public ----------
    def complete_json(self, step: str, key: str, system: str, user: str, schema_model: type[BaseModel],
                      model_ref: str, temperature: float | None = None, max_attempts: int = 5,
                      fallback: str | None = None) -> BaseModel:
        """Try model_ref; if it fails after all retries and a fallback is given, try the fallback once."""
        try:
            return self._complete_json(step, key, system, user, schema_model, model_ref, temperature, max_attempts)
        except LLMError as e:
            if not fallback or fallback == model_ref:
                raise
            self.log(f"  [{model_ref}] {step}/{key} failed ({str(e)[:120]}); using fallback {fallback}")
            self.usage.failures.append({"step": step, "key": key, "model": model_ref, "fell_back_to": fallback})
            return self._complete_json(step, key, system, user, schema_model, fallback, temperature, max_attempts)

    def _complete_json(self, step, key, system, user, schema_model, model_ref, temperature, max_attempts):
        provider, model, pcfg = self._provider(model_ref)
        schema = _clean_schema(schema_model)
        digest = hashlib.sha256(json.dumps([model_ref, system, user, schema, temperature]).encode()).hexdigest()[:24]
        cache_file = self.cache_dir / f"{digest}.json"
        safe_key = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)

        if self.use_cache and cache_file.exists():
            data = json.loads(cache_file.read_text())
            obj = schema_model.model_validate(data["parsed"])
            self.usage.add(model_ref, data.get("input_tokens", 0), data.get("output_tokens", 0), 0, cached=True)
            self._raw(step, safe_key, {"model": model_ref, "cached": True, "system": system, "user": user,
                                       "response_text": data.get("text"), "parsed": data["parsed"]})
            return obj

        client = self._client(provider, pcfg)
        json_mode = "schema"
        messages_user = user
        last_err = None
        invalid_count = 0
        for attempt in range(1, max_attempts + 1):
            self._limiter.wait(model_ref, pcfg.get("rpm", 0))   # free-tier quotas are per model
            t0 = time.time()
            try:
                if pcfg["type"] == "anthropic":
                    text, tin, tout, raw = self._call_anthropic(client, model, system, messages_user, schema, pcfg)
                else:
                    text, tin, tout, raw = self._call_openai(client, model, system, messages_user, schema,
                                                             temperature, json_mode)
            except LLMError:
                raise
            except Exception as e:  # network / rate limit / provider errors
                msg = str(e)
                body = getattr(e, "body", None)
                if body:
                    msg = f"{msg} | {json.dumps(body)[:400]}"
                status = getattr(e, "status_code", None)
                last_err = msg
                if status in (400, 422) and json_mode == "schema" and "schema" in msg.lower():
                    self.log(f"  [{model_ref}] schema mode rejected; falling back to json_object")
                    json_mode = "object"
                    messages_user = user + "\n\nReturn ONLY JSON matching this JSON Schema:\n" + json.dumps(schema)
                    continue
                if status in (401, 403, 404):
                    raise LLMError(f"{model_ref}: {msg[:300]}") from e
                wait = min(90, (2 ** attempt) + random.random() * 2)
                if status == 429:
                    wait = max(wait, 30 * attempt)
                self.log(f"  [{model_ref}] {step}/{key} attempt {attempt} failed ({status}): {msg[:300]} — retry in {wait:.0f}s")
                time.sleep(wait)
                continue
            secs = time.time() - t0
            try:
                parsed = schema_model.model_validate(_extract_json(text))
            except (ValidationError, json.JSONDecodeError, ValueError) as e:
                last_err = f"invalid JSON: {str(e)[:500]}"
                invalid_count += 1
                self.usage.add(model_ref, tin, tout, secs, cached=False)
                self._raw(step, f"{safe_key}__invalid{attempt}", {"model": model_ref, "system": system,
                                                                   "user": messages_user, "response_text": text,
                                                                   "error": last_err})
                self.log(f"  [{model_ref}] {step}/{key} returned invalid JSON (attempt {attempt}); retrying with error")
                messages_user = (user + f"\n\nYour previous answer failed validation: {last_err}\n"
                                 "Return corrected JSON only.")
                if invalid_count >= 2:
                    break
                continue
            self.usage.add(model_ref, tin, tout, secs, cached=False)
            payload = {"parsed": parsed.model_dump(), "text": text, "input_tokens": tin, "output_tokens": tout}
            cache_file.write_text(json.dumps(payload))
            self._raw(step, safe_key, {"model": model_ref, "cached": False, "seconds": round(secs, 2),
                                       "input_tokens": tin, "output_tokens": tout, "system": system,
                                       "user": messages_user, "response_text": text, "parsed": payload["parsed"]})
            return parsed
        self.usage.failures.append({"step": step, "key": key, "model": model_ref, "error": last_err})
        raise LLMError(f"{model_ref} {step}/{key} failed after {max_attempts} attempts: {last_err}")

    def _raw(self, step: str, key: str, data: dict):
        if not self.raw_dir:
            return
        d = self.raw_dir / step
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{key}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def cost_summary(self) -> dict:
        """List-price equivalent of this run's tokens for each priced model."""
        p = self.cfg.get("pricing_per_million", {})
        inp, out = self.usage.input_tokens, self.usage.output_tokens
        return {m: round(inp / 1e6 * pr[0] + out / 1e6 * pr[1], 4) for m, pr in p.items()}
