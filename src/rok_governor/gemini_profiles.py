"""AI provider profiles and safe request fallback handling."""
from __future__ import annotations

import os
import re
import base64
import json
from io import BytesIO
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable

import httpx
from dotenv import load_dotenv

DEFAULT_FALLBACK_MODELS = ("gemini-2.5-flash", "gemini-3.5-flash-lite")
_KEY_NAME = re.compile(r"^GEMINI_API_KEY_(\d+)$")
_RETRYABLE_ERROR_MARKERS = (
    "429", "resource_exhausted", "rate limit", "quota", "too many requests",
    "401", "unauthenticated", "api_key_invalid", "invalid api key",
    "403", "permission_denied", "404", "not_found", "model not found",
    "402", "payment required", "insufficient credits",
    "413", "payload too large", "request entity too large",
    "500", "internal", "502", "503", "unavailable", "504", "deadline exceeded",
    "model is not available", "model_decommissioned", "unsupported model",
)


@dataclass(frozen=True)
class GeminiProfile:
    name: str
    model: str
    api_key: str = field(repr=False)
    provider: str = "Google Gemini"


class GeminiProfilesExhausted(RuntimeError):
    """All configured provider key/model profiles failed with retryable errors."""


def _usable_key(value: str | None) -> str | None:
    key = (value or "").strip()
    if not key or key.casefold().startswith(("your_", "replace_me", "<")):
        return None
    return key


def load_gemini_profiles(
    *, model_override: str | None = None, default_model: str,
) -> list[GeminiProfile]:
    """Read numbered GEMINI_API_KEY_n/GEMINI_MODEL_n pairs and legacy config.

    Numbered profiles are tried in numeric order. A distinct legacy key is
    appended as a backward-compatible final profile. A CLI model override
    changes only the first profile; other configured pairs retain their model.
    """
    load_dotenv()
    configured_default = os.getenv("GEMINI_MODEL", default_model).strip() or default_model
    numbered: list[tuple[int, str]] = []
    for name, value in os.environ.items():
        match = _KEY_NAME.fullmatch(name)
        key = _usable_key(value)
        if match and key:
            numbered.append((int(match.group(1)), key))

    profiles: list[GeminiProfile] = []
    seen_keys: set[str] = set()
    for index, key in sorted(numbered, key=lambda entry: entry[0]):
        if key in seen_keys:
            continue
        seen_keys.add(key)
        selected_model = os.getenv(f"GEMINI_MODEL_{index}", "").strip() or configured_default
        profiles.append(GeminiProfile(f"GEMINI_API_KEY_{index}", selected_model, key))

    legacy_key = _usable_key(os.getenv("GEMINI_API_KEY"))
    if legacy_key and legacy_key not in seen_keys:
        profiles.append(GeminiProfile("GEMINI_API_KEY", configured_default, legacy_key))

    if not profiles:
        raise RuntimeError(
        "No AI API key is configured. Set GEMINI_API_KEY, GROQ_API_KEY, or "
            "OPENROUTER_API_KEY in .env."
        )
    if model_override:
        profiles[0] = replace(profiles[0], model=model_override.strip())
    return profiles


def load_candidate_profiles(
    *, model_override: str | None = None, default_model: str,
) -> list[GeminiProfile]:
    """Include existing same-key model fallbacks after configured key/model pairs."""
    try:
        profiles = load_gemini_profiles(model_override=model_override, default_model=default_model)
    except RuntimeError:
        profiles = []
    configured_fallbacks = os.getenv(
        "GEMINI_FALLBACK_MODELS", ",".join(DEFAULT_FALLBACK_MODELS)
    )
    if profiles:
        known = {(profile.api_key, profile.model) for profile in profiles}
        primary = profiles[0]
        for raw_model in configured_fallbacks.split(","):
            fallback_model = raw_model.strip()
            pair = (primary.api_key, fallback_model)
            if fallback_model and pair not in known:
                profiles.append(GeminiProfile(f"{primary.name} model fallback", fallback_model, primary.api_key))
                known.add(pair)
    profiles.extend(_load_openai_compatible_profiles("GROQ", "Groq", "qwen/qwen3.8-27b"))
    profiles.extend(_load_openai_compatible_profiles("OPENROUTER", "OpenRouter", "openrouter/free"))
    if not profiles:
        raise RuntimeError("No AI API key is configured. Set GEMINI_API_KEY, GROQ_API_KEY, or OPENROUTER_API_KEY in .env.")
    return profiles


def _load_openai_compatible_profiles(prefix: str, provider: str, default_model: str) -> list[GeminiProfile]:
    """Read numbered or legacy credentials for OpenAI-compatible providers."""
    key_re = re.compile(rf"^{prefix}_API_KEY_(\d+)$")
    found: list[tuple[int, str]] = []
    for name, value in os.environ.items():
        match, key = key_re.fullmatch(name), _usable_key(value)
        if match and key:
            found.append((int(match.group(1)), key))
    result: list[GeminiProfile] = []
    seen: set[str] = set()
    for index, key in sorted(found):
        if key in seen:
            continue
        seen.add(key)
        model = os.getenv(f"{prefix}_MODEL_{index}", "").strip() or os.getenv(f"{prefix}_MODEL", "").strip() or default_model
        result.append(GeminiProfile(f"{prefix}_API_KEY_{index}", model, key, provider))
    legacy = _usable_key(os.getenv(f"{prefix}_API_KEY"))
    if legacy and legacy not in seen:
        model = os.getenv(f"{prefix}_MODEL", "").strip() or default_model
        result.append(GeminiProfile(f"{prefix}_API_KEY", model, legacy, provider))
    return result


def request_profile(profile: GeminiProfile, prompt: str, image_bytes: bytes, *, schema: dict, temperature: float, timeout_ms: int) -> Any:
    """Send one screenshot+prompt request to Gemini, Groq, or OpenRouter."""
    if profile.provider == "Google Gemini":
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=profile.api_key, http_options=types.HttpOptions(
            timeout=timeout_ms, retry_options=types.HttpRetryOptions(attempts=1)))
        return client.models.generate_content(model=profile.model, contents=[
            prompt, types.Part.from_bytes(data=image_bytes, mime_type="image/png")],
            config=types.GenerateContentConfig(response_mime_type="application/json",
                response_schema=schema, temperature=temperature))
    endpoint = "https://api.groq.com/openai/v1/chat/completions" if profile.provider == "Groq" else "https://openrouter.ai/api/v1/chat/completions"
    provider_image = _prepare_provider_image(image_bytes)
    image_data = base64.b64encode(provider_image).decode("ascii")
    headers = {"Authorization": f"Bearer {profile.api_key}", "Content-Type": "application/json"}
    if profile.provider == "OpenRouter":
        headers.update({"HTTP-Referer": "https://github.com/rok-ai-governor", "X-Title": "RoK AI Governor"})
    response = httpx.post(endpoint, headers=headers, json={
        "model": profile.model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt + "\nReturn a single JSON object matching this schema:\n" + json.dumps(schema)},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}},
        ]}],
        "response_format": {"type": "json_object"}, "temperature": temperature,
    }, timeout=timeout_ms / 1000)
    response.raise_for_status()
    payload = response.json()
    return type("ProviderResponse", (), {"text": payload["choices"][0]["message"]["content"]})()


def _prepare_provider_image(image_bytes: bytes, *, max_bytes: int = 8 * 1024 * 1024) -> bytes:
    """Convert screenshots to a compact JPEG below provider request limits."""
    from PIL import Image

    with Image.open(BytesIO(image_bytes)) as source:
        image = source.convert("RGB")
    image.thumbnail((2560, 2560), Image.Resampling.LANCZOS)
    for quality in (88, 82, 76, 70, 64):
        output = BytesIO()
        image.save(output, format="JPEG", quality=quality, optimize=True)
        result = output.getvalue()
        if len(result) <= max_bytes:
            return result
    while image.width > 960 or image.height > 960:
        image.thumbnail((int(image.width * 0.8), int(image.height * 0.8)), Image.Resampling.LANCZOS)
        output = BytesIO()
        image.save(output, format="JPEG", quality=64, optimize=True)
        result = output.getvalue()
        if len(result) <= max_bytes:
            return result
    if len(result) > max_bytes:
        raise ValueError("Screenshot could not be reduced below the provider image size limit.")
    return result


def is_retryable_profile_error(error: Exception) -> bool:
    if isinstance(error, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    message = str(error).casefold()
    return any(marker in message for marker in _RETRYABLE_ERROR_MARKERS)


def _safe_error(error: Exception, api_key: str) -> str:
    message = str(error).replace(api_key, "[redacted]").replace("\n", " ")
    return f"{type(error).__name__}: {message[:240]}"


def generate_with_profiles(
    profiles: Iterable[GeminiProfile],
    generate: Callable[[GeminiProfile], Any],
) -> tuple[GeminiProfile, Any]:
    """Try configured key/model pairs on quota, credential, and model errors."""
    failures: list[str] = []
    candidates = list(profiles)
    for profile in candidates:
        try:
            return profile, generate(profile)
        except Exception as error:
            if not is_retryable_profile_error(error):
                raise
            failures.append(
                f"{profile.provider}/{profile.name}/{profile.model}: {_safe_error(error, profile.api_key)}"
            )
            print(
                f"[WARN] {profile.provider} profile {profile.name} with model {profile.model} "
                "was unavailable; trying the next configured profile.",
                flush=True,
            )
    if not candidates:
        raise GeminiProfilesExhausted("No provider key/model profiles are configured.")
    summary = "; ".join(failures)
    raise GeminiProfilesExhausted(
        f"All {len(candidates)} AI provider key/model profiles failed: {summary}"
    )
