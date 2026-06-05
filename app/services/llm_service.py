# app/services/llm_service.py
"""LLM service abstraction layer — supports OpenAI, Groq (dev) and Bedrock (prod)."""

from abc import ABC, abstractmethod
from app.core.config import settings


class BaseLLMService(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    def classify_message(self, message: str, system_prompt: str, max_tokens: int = 500, temperature: float = 0.0) -> str:
        """
        Send a message to the LLM with a system prompt.
        Returns the model's text response as a plain string.
        """
        pass


class GroqLLMService(BaseLLMService):
    """Used when LLM_PROVIDER=groq (local development)."""

    def __init__(self):
        from groq import Groq
        self.client = Groq(api_key=settings.GROQ_API_KEY)
        self.model = "meta-llama/llama-4-scout-17b-16e-instruct"

    def classify_message(self, message: str, system_prompt: str, max_tokens: int = 500, temperature: float = 0.0) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
        )
        return response.choices[0].message.content.strip()


class BedrockLLMService(BaseLLMService):
    """
    Used when LLM_PROVIDER=bedrock (production on AWS).
    Requires AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY in .env.
    No code changes needed anywhere else when switching from Groq to Bedrock.
    """

    def __init__(self):
        import boto3
        import json
        self._json = json
        self.client = boto3.client(
            "bedrock-runtime",
            region_name=settings.AWS_REGION,
        )
        self.model_id = "anthropic.claude-3-haiku-20240307-v1:0"

    def classify_message(self, message: str, system_prompt: str, max_tokens: int = 500, temperature: float = 0.0) -> str:
        import json
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": message}],
        })
        response = self.client.invoke_model(modelId=self.model_id, body=body)
        return json.loads(response["body"].read())["content"][0]["text"].strip()


class OpenAILLMService(BaseLLMService):
    """
    Used when LLM_PROVIDER=openai. Calls the OpenAI Chat Completions API
    directly over httpx (no SDK dependency).
    Reference: https://developers.openai.com/api/docs/api-reference/chat
    """

    _URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self):
        import httpx
        self._httpx = httpx
        self._headers = {
            "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }
        self.model = settings.OPENAI_CHAT_MODEL

    def classify_message(self, message: str, system_prompt: str, max_tokens: int = 500, temperature: float = 0.0) -> str:
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
        }
        # One retry on timeout — transient network slowness to OpenAI must not
        # bubble up and kill a chat socket.
        last_exc = None
        for attempt in (1, 2):
            try:
                resp = self._httpx.post(
                    self._URL, headers=self._headers, json=payload, timeout=60.0,
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"].strip()
            except self._httpx.TimeoutException as exc:
                last_exc = exc
        raise last_exc


def get_llm_service() -> BaseLLMService:
    """
    Factory function. Reads LLM_PROVIDER from settings.
    Returns the correct implementation.
    To migrate from Groq to Bedrock: set LLM_PROVIDER=bedrock in .env.
    Zero code changes required anywhere else.
    """
    if settings.LLM_PROVIDER == "bedrock":
        return BedrockLLMService()
    if settings.LLM_PROVIDER == "openai":
        return OpenAILLMService()
    return GroqLLMService()


# Module-level singleton — import this in all other modules
llm_service = get_llm_service()
