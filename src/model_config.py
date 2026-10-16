"""Load a configured model role; local configuration overrides the example."""
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from src.adapters import (
    AdapterError,
    GeminiAdapter,
    MockModelAdapter,
    ModelAdapter,
    OpenAICompatibleAdapter,
)
from src.anthropic_adapter import AnthropicMessagesAdapter
from src.responses_adapter import OpenAIResponsesAdapter

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_KNOWN_USER_ENV_VARS = frozenset({
    "MUSE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "DEEPSEEK_API_KEY",
})


def refresh_user_env_var(var_name: Optional[str]) -> bool:
    """Refresh a known credential variable from Windows User environment if not yet in os.environ.
    Only checks existence and populates os.environ; never prints or logs secret values.
    """
    if not var_name:
        return False
    if os.environ.get(var_name, "").strip():
        return True
    if os.name == "nt" and var_name in _KNOWN_USER_ENV_VARS:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                val, _ = winreg.QueryValueEx(key, var_name)
                if isinstance(val, str) and val.strip():
                    os.environ[var_name] = val.strip()
                    return True
        except OSError:
            pass
    return False


def has_credential(var_name: Optional[str]) -> bool:
    """Check whether an environment credential exists (in process or Windows User env) without exposing it."""
    if not var_name:
        return False
    if os.environ.get(var_name, "").strip():
        return True
    if var_name == "GEMINI_API_KEY" and os.environ.get("GOOGLE_API_KEY", "").strip():
        return True
    if refresh_user_env_var(var_name):
        return True
    if var_name == "GEMINI_API_KEY" and refresh_user_env_var("GOOGLE_API_KEY"):
        return True
    return False


def load_adapter_settings(config_path: Optional[Path] = None) -> Dict[str, Any]:
    if config_path is None:
        local_path = PROJECT_ROOT / "configs" / "adapters.local.json"
        config_path = local_path if local_path.exists() else PROJECT_ROOT / "configs" / "adapters.example.json"
    with Path(config_path).open(encoding="utf-8") as handle:
        return json.load(handle)


def build_adapter_from_config(config: Dict[str, Any]) -> ModelAdapter:
    refresh_user_env_var(config.get("api_key_env"))
    protocol = config.get("api_protocol", "chat_completions")
    if protocol == "responses":
        return OpenAIResponsesAdapter(config)
    if protocol == "anthropic_messages":
        return AnthropicMessagesAdapter(config)
    if protocol != "chat_completions":
        raise AdapterError("Unsupported model API protocol")
    if config["provider"] == "google":
        return GeminiAdapter(config)
    if config["provider"] == "openai_compatible":
        return OpenAICompatibleAdapter(config)
    if config["provider"] == "mock":
        return MockModelAdapter(config, canned_payload=config.get("canned_payload"))
    raise AdapterError("Unsupported model provider; configure an explicit API protocol")


def load_model_adapter(role: str = "reviewer", config_path: Optional[Path] = None, profile_override: Optional[str] = None) -> ModelAdapter:
    settings = load_adapter_settings(config_path)
    name = profile_override or settings["active_profiles"][role]
    config = settings["adapters"][name]
    if config.get("role") != role:
        raise AdapterError("Configured adapter role does not match requested role")
    return build_adapter_from_config(config)
