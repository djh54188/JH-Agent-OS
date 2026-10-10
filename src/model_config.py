"""Load a configured model role; local configuration overrides the example."""
import json
from pathlib import Path

from src.adapters import AdapterError, GeminiAdapter, OpenAICompatibleAdapter
from src.responses_adapter import OpenAIResponsesAdapter

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_model_adapter(role="reviewer", config_path=None):
    if config_path is None:
        local_path = PROJECT_ROOT / "configs" / "adapters.local.json"
        config_path = local_path if local_path.exists() else PROJECT_ROOT / "configs" / "adapters.example.json"
    with Path(config_path).open(encoding="utf-8") as handle:
        settings = json.load(handle)
    name = settings["active_profiles"][role]
    config = settings["adapters"][name]
    if config.get("role") != role:
        raise AdapterError("Configured adapter role does not match requested role")
    protocol = config.get("api_protocol", "chat_completions")
    if protocol == "responses":
        return OpenAIResponsesAdapter(config)
    if protocol != "chat_completions":
        raise AdapterError("Unsupported model API protocol")
    if config["provider"] == "google":
        return GeminiAdapter(config)
    if config["provider"] == "openai_compatible":
        return OpenAICompatibleAdapter(config)
    raise AdapterError("Unsupported model provider; configure an explicit API protocol")
