"""
JH-Agent OS Model Adapter Implementations v1.1.0
Provides unified abstractions for Codex CLI, OpenAI-compatible (DeepSeek), Google Gemini, and Mock adapters.
Supports --json event stream processing and --output-schema structured outputs.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional
from jsonschema import validate, ValidationError

@dataclass
class AdapterResponse:
    content: str
    parsed_json: Optional[Dict[str, Any]]
    model_id: str
    tokens_prompt: Optional[int]
    tokens_completion: Optional[int]
    total_tokens: Optional[int]
    latency_ms: int
    is_mock: bool
    raw_response: Optional[Dict[str, Any]] = None

class AdapterError(Exception):
    """Base exception for model adapter errors."""
    pass

class CredentialMissingError(AdapterError):
    """Raised when required API credential is not found in environment."""
    pass

class SchemaValidationError(AdapterError):
    """Raised when model output fails JSON Schema validation."""
    pass


def invocation_timeout(configured, requested=None):
    value = min(float(configured), float(configured if requested is None else requested))
    if not 0 < value < float("inf"):
        raise AdapterError("Invocation timeout budget must be positive and finite")
    return value


class ModelAdapter(ABC):
    """Abstract Base Class for all model adapters."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.role = config.get("role", "worker")
        self.model_id = config.get("model_id", "unknown")
        self.timeout_seconds = config.get("timeout_seconds", 180)

    @abstractmethod
    def invoke(self, messages: List[Dict[str, str]], **kwargs) -> AdapterResponse:
        pass

    @abstractmethod
    def invoke_structured(
        self,
        messages: List[Dict[str, str]],
        schema: Dict[str, Any],
        **kwargs
    ) -> AdapterResponse:
        pass

    @abstractmethod
    def check_health(self) -> Dict[str, Any]:
        pass


class OpenAICompatibleAdapter(ModelAdapter):
    """Adapter for OpenAI-compatible REST endpoints (e.g., DeepSeek, Moonshot, etc.)."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.endpoint_url = config.get("endpoint_url", "https://api.deepseek.com/v1").rstrip("/")
        self.api_key_env = config.get("api_key_env", "DEEPSEEK_API_KEY")
        self.temperature = config.get("temperature", 0.2)
        self.max_tokens = config.get("max_tokens", 4096)

    def _get_api_key(self) -> str:
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            raise CredentialMissingError(
                f"Missing environment variable '{self.api_key_env}' for {self.model_id}"
            )
        return key

    def check_health(self) -> Dict[str, Any]:
        try:
            key = self._get_api_key()
            url = f"{self.endpoint_url}/models"
            req = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {key}"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("id") for m in data.get("data", [])]
                return {"healthy": True, "models": models, "error": None}
        except Exception as e:
            return {"healthy": False, "models": [], "error": str(e)}

    def invoke(self, messages: List[Dict[str, str]], **kwargs) -> AdapterResponse:
        api_key = self._get_api_key()
        timeout = invocation_timeout(self.timeout_seconds, kwargs.get("timeout"))
        url = f"{self.endpoint_url}/chat/completions"
        payload = {
            "model": self.model_id,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": min(self.max_tokens, kwargs.get("max_output_tokens", kwargs.get("max_tokens", self.max_tokens))),
        }
        json_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=json_data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            }
        )

        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                duration_ms = int((time.perf_counter() - start) * 1000)
                raw = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            raise AdapterError(f"HTTP {e.code} error: {err_body}")
        except Exception as e:
            raise AdapterError(f"Request failed: {str(e)}")

        choice = raw.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "")
        usage = raw.get("usage") or {}

        return AdapterResponse(
            content=content,
            parsed_json=None,
            model_id=self.model_id,
            tokens_prompt=usage.get("prompt_tokens"),
            tokens_completion=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            latency_ms=duration_ms,
            is_mock=False,
            raw_response=raw
        )

    def invoke_structured(
        self,
        messages: List[Dict[str, str]],
        schema: Dict[str, Any],
        **kwargs
    ) -> AdapterResponse:
        # Augment system instructions with schema definition
        current_date_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        schema_prompt = (
            "\n\nCRITICAL REQUIREMENT: You MUST respond with ONLY a valid, parseable JSON object "
            f"conforming strictly to this JSON Schema:\n{json.dumps(schema, ensure_ascii=False)}\n"
            f"Note: Use current timestamp '{current_date_iso}' for any created_at/updated_at fields.\n"
            "Do NOT include markdown formatting or backticks around the json. Output raw JSON only."
        )
        augmented_messages = list(messages)
        if augmented_messages and augmented_messages[0]["role"] == "system":
            augmented_messages[0] = {
                "role": "system",
                "content": augmented_messages[0]["content"] + schema_prompt
            }
        else:
            augmented_messages.insert(0, {"role": "system", "content": schema_prompt})

        resp = self.invoke(augmented_messages, **kwargs)
        raw_text = resp.content.strip()
        # Clean potential markdown fences
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        elif raw_text.startswith("```"):
            raw_text = raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
        raw_text = raw_text.strip()

        try:
            parsed = json.loads(raw_text)
        except Exception as e:
            raise SchemaValidationError(f"Failed to parse JSON from response: {e}\nRaw output: {raw_text[:200]}")

        try:
            validate(instance=parsed, schema=schema)
        except ValidationError as e:
            raise SchemaValidationError(f"Schema validation failed: {e.message}")

        resp.parsed_json = parsed
        return resp


class CodexCliAdapter(ModelAdapter):
    """
    Adapter for local OpenAI Codex CLI subprocess harness.
    Communicates via subprocess with --json event streams and --output-schema enforcement.
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.workspace_root = config.get("workspace_root", os.getcwd())
        self.sandbox_mode = config.get("sandbox_mode", "read-only")
        self.codex_bin = shutil.which("codex") or "codex"
        self.timeout_seconds = config.get("timeout_seconds", 180)

    def check_health(self) -> Dict[str, Any]:
        try:
            res = subprocess.run(
                [self.codex_bin, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
                shell=(os.name == "nt")
            )
            return {"healthy": True, "version": res.stdout.strip(), "error": None}
        except Exception as e:
            return {"healthy": False, "version": None, "error": str(e)}

    def _execute_codex(self, prompt: str, schema_file: Optional[str] = None) -> AdapterResponse:
        cmd = [
            self.codex_bin, "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox", self.sandbox_mode,
            "-C", self.workspace_root,
            "--json"
        ]
        if schema_file:
            cmd.extend(["--output-schema", schema_file])
        cmd.append(prompt)

        start = time.perf_counter()
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=(os.name == "nt")
            )
            stdout, stderr = proc.communicate(timeout=self.timeout_seconds)
            duration_ms = int((time.perf_counter() - start) * 1000)
            if proc.returncode != 0:
                raise AdapterError(f"Codex CLI exited with code {proc.returncode}: {stderr}")
        except subprocess.TimeoutExpired:
            proc.kill()
            raise AdapterError(f"Codex CLI invocation timed out after {self.timeout_seconds}s")
        except Exception as e:
            raise AdapterError(f"Codex CLI execution failed: {e}")

        # Parse JSONL events
        collected_texts = []
        tokens_prompt = None
        tokens_completion = None
        total_tokens = None
        last_agent_text = ""

        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                ev_type = ev.get("type")
                if ev_type == "turn.completed":
                    usage = ev.get("usage") or {}
                    tokens_prompt = usage.get("input_tokens")
                    tokens_completion = usage.get("output_tokens")
                    if tokens_prompt is not None and tokens_completion is not None:
                        total_tokens = tokens_prompt + tokens_completion
                    else:
                        total_tokens = usage.get("total_tokens")
                elif ev_type == "item.completed":
                    item = ev.get("item", {})
                    if item.get("type") == "agent_message":
                        txt = item.get("text", "")
                        collected_texts.append(txt)
                        last_agent_text = txt
            except Exception:
                # Non-JSON status lines (e.g. initial connection logs)
                pass

        final_content = last_agent_text if last_agent_text else "\n".join(collected_texts)
        if not final_content and stdout:
            final_content = stdout

        return AdapterResponse(
            content=final_content,
            parsed_json=None,
            model_id=self.model_id,
            tokens_prompt=tokens_prompt,
            tokens_completion=tokens_completion,
            total_tokens=total_tokens,
            latency_ms=duration_ms,
            is_mock=False,
            raw_response={"stdout_lines": len(stdout.splitlines()), "stderr": stderr}
        )

    def invoke(self, messages: List[Dict[str, str]], **kwargs) -> AdapterResponse:
        prompt_parts = [f"[{m['role'].upper()}]: {m['content']}" for m in messages]
        combined_prompt = "\n".join(prompt_parts)
        return self._execute_codex(combined_prompt, schema_file=None)

    def invoke_structured(
        self,
        messages: List[Dict[str, str]],
        schema: Dict[str, Any],
        **kwargs
    ) -> AdapterResponse:
        prompt_parts = [f"[{m['role'].upper()}]: {m['content']}" for m in messages]
        combined_prompt = "\n".join(prompt_parts)

        # Write schema to a temporary file
        tf = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        try:
            json.dump(schema, tf, ensure_ascii=False)
            tf_path = tf.name
            tf.close()

            resp = self._execute_codex(combined_prompt, schema_file=tf_path)
            raw_text = resp.content.strip()

            # Clean markdown code fences if present
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:]
            elif raw_text.startswith("```"):
                raw_text = raw_text[3:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]
            raw_text = raw_text.strip()

            try:
                parsed = json.loads(raw_text)
            except Exception as e:
                raise SchemaValidationError(f"Failed to parse JSON from Codex structured output: {e}\nRaw: {raw_text[:200]}")

            try:
                validate(instance=parsed, schema=schema)
            except ValidationError as e:
                raise SchemaValidationError(f"Schema validation failed: {e.message}")

            resp.parsed_json = parsed
            return resp
        finally:
            if os.path.exists(tf_path):
                try:
                    os.remove(tf_path)
                except OSError:
                    pass


class GeminiAdapter(ModelAdapter):
    """Adapter for Google Gemini REST API."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.endpoint_url = config.get(
            "endpoint_url",
            "https://generativelanguage.googleapis.com/v1beta"
        ).rstrip("/")
        self.api_key_env = config.get("api_key_env", "GEMINI_API_KEY")
        self.model_id = config.get("model_id", "gemini-1.5-pro")

    def _get_api_key(self) -> str:
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            key = os.environ.get("GOOGLE_API_KEY", "").strip()
        if not key:
            raise CredentialMissingError(
                f"Missing environment variable '{self.api_key_env}' or 'GOOGLE_API_KEY'"
            )
        return key

    def check_health(self) -> Dict[str, Any]:
        try:
            key = self._get_api_key()
            url = f"{self.endpoint_url}/models?key={key}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", [])]
                return {"healthy": True, "models": models, "error": None}
        except Exception as e:
            return {"healthy": False, "models": [], "error": str(e)}

    def invoke(self, messages: List[Dict[str, str]], **kwargs) -> AdapterResponse:
        key = self._get_api_key()
        timeout = invocation_timeout(self.timeout_seconds, kwargs.get("timeout"))
        url = f"{self.endpoint_url}/models/{self.model_id}:generateContent?key={key}"
        contents = []
        for m in messages:
            role = "user" if m["role"] in ["user", "system"] else "model"
            contents.append({"role": role, "parts": [{"text": m["content"]}]})

        payload = {"contents": contents, "generationConfig": {
            "maxOutputTokens": min(self.config.get("max_output_tokens", 8192), kwargs.get("max_output_tokens", 8192))}}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})

        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                duration_ms = int((time.perf_counter() - start) * 1000)
                raw = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            raise AdapterError(f"HTTP {e.code} error: {err_body}")
        except Exception as e:
            raise AdapterError(f"Gemini call failed: {e}")

        text = ""
        candidates = raw.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts:
                text = parts[0].get("text", "")

        usage_meta = raw.get("usageMetadata") or {}
        return AdapterResponse(
            content=text,
            parsed_json=None,
            model_id=self.model_id,
            tokens_prompt=usage_meta.get("promptTokenCount"),
            tokens_completion=usage_meta.get("candidatesTokenCount"),
            total_tokens=usage_meta.get("totalTokenCount"),
            latency_ms=duration_ms,
            is_mock=False,
            raw_response=raw
        )

    def invoke_structured(
        self,
        messages: List[Dict[str, str]],
        schema: Dict[str, Any],
        **kwargs
    ) -> AdapterResponse:
        schema_prompt = f"Respond in valid JSON adhering to:\n{json.dumps(schema)}"
        augmented = list(messages) + [{"role": "user", "content": schema_prompt}]
        resp = self.invoke(augmented, **kwargs)
        parsed = json.loads(resp.content.strip())
        validate(instance=parsed, schema=schema)
        resp.parsed_json = parsed
        return resp


class MockModelAdapter(ModelAdapter):
    """
    Deterministic Mock Adapter for contract testing and offline verification.
    Guarantees strict JSON Schema conformance without network or API keys.
    """

    def __init__(self, config: Dict[str, Any], canned_payload: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.canned_payload = canned_payload or {}

    def check_health(self) -> Dict[str, Any]:
        return {"healthy": True, "mock": True, "error": None}

    def invoke(self, messages: List[Dict[str, str]], **kwargs) -> AdapterResponse:
        content = json.dumps(self.canned_payload, ensure_ascii=False)
        return AdapterResponse(
            content=content,
            parsed_json=self.canned_payload,
            model_id=self.model_id,
            tokens_prompt=50,
            tokens_completion=50,
            total_tokens=100,
            latency_ms=5,
            is_mock=True,
            raw_response={"mock": True}
        )

    def invoke_structured(
        self,
        messages: List[Dict[str, str]],
        schema: Dict[str, Any],
        **kwargs
    ) -> AdapterResponse:
        validate(instance=self.canned_payload, schema=schema)
        return self.invoke(messages, **kwargs)
