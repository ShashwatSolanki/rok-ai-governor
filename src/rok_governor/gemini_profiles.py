"""Single-model Gemini key rotation with safe request fallback handling."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import httpx
from dotenv import load_dotenv

_KEY_NAME = re.compile(r"^GEMINI_API_KEY_(\d+)$")
_RETRYABLE_ERROR_MARKERS = (
    "429", "resource_exhausted", "rate limit", "quota", "too many requests",
    "401", "unauthenticated", "api_key_invalid", "invalid api key",
    "403", "permission_denied", "404", "not_found", "model not found",
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
    """All configured Gemini keys failed with retryable errors."""


def _usable_key(value: str | None) -> str | None:
    key = (value or "").strip()
    if not key or key.casefold().startswith(("your_", "replace_me", "<")):
        return None
    return key


def load_gemini_profiles(
    *, model_override: str | None = None, default_model: str,
) -> list[GeminiProfile]:
    """Load numbered Gemini keys in order, all using the same model.

    ``GEMINI_API_KEY`` remains supported as a final legacy key. Model selection
    is global: one CLI override or ``GEMINI_MODEL`` applies to every key.
    """
    load_dotenv()
    model = (model_override or os.getenv("GEMINI_MODEL") or default_model).strip()
    numbered: list[tuple[int, str]] = []
    for name, value in os.environ.items():
        match = _KEY_NAME.fullmatch(name)
        key = _usable_key(value)
        if match and key:
            numbered.append((int(match.group(1)), key))

    profiles: list[GeminiProfile] = []
    seen_keys: set[str] = set()
    for index, key in sorted(numbered, key=lambda entry: entry[0]):
        if key not in seen_keys:
            profiles.append(GeminiProfile(f"GEMINI_API_KEY_{index}", model, key))
            seen_keys.add(key)

    legacy_key = _usable_key(os.getenv("GEMINI_API_KEY"))
    if legacy_key and legacy_key not in seen_keys:
        profiles.append(GeminiProfile("GEMINI_API_KEY", model, legacy_key))

    if not profiles:
        raise RuntimeError(
            "No Gemini API key is configured. Set GEMINI_API_KEY or numbered "
            "GEMINI_API_KEY_1 through GEMINI_API_KEY_10 in .env."
        )
    return profiles


def load_candidate_profiles(
    *, model_override: str | None = None, default_model: str,
) -> list[GeminiProfile]:
    """Compatibility name for the configured single-model Gemini key list."""
    return load_gemini_profiles(model_override=model_override, default_model=default_model)


def request_profile(
    profile: GeminiProfile,
    prompt: str,
    image_bytes: bytes,
    *,
    schema: dict,
    temperature: float,
    timeout_ms: int,
) -> Any:
    """Send one screenshot+prompt request to Google Gemini."""
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=profile.api_key,
        http_options=types.HttpOptions(
            api_version="v1beta",
            timeout=timeout_ms,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    return client.models.generate_content(
        model=profile.model,
        contents=[prompt, types.Part.from_bytes(data=image_bytes, mime_type="image/png")],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=temperature,
        ),
    )


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
    """Try each Gemini API key with the same model on retryable errors."""
    failures: list[str] = []
    candidates = list(profiles)
    for profile in candidates:
        try:
            return profile, generate(profile)
        except Exception as error:
            if not is_retryable_profile_error(error):
                raise
            detail = _safe_error(error, profile.api_key)
            failures.append(f"{profile.name}/{profile.model}: {detail}")
            print(
                f"[WARN] Gemini key profile {profile.name} with model {profile.model} "
                f"was unavailable ({detail}); trying the next Gemini key.",
                flush=True,
            )
    if not candidates:
        raise GeminiProfilesExhausted("No Gemini API keys are configured.")
    raise GeminiProfilesExhausted(
        f"All {len(candidates)} Gemini API key(s) failed: " + "; ".join(failures)
    )
