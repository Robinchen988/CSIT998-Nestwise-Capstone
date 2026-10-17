import os

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing required setting {name}. "
            f"Copy .env.example to .env and fill in {name}."
        )
    return value


def _optional(name: str) -> str | None:
    return os.getenv(name) or None


DB_DSN: str = _required("DB_DSN")
EMBED_MODEL: str = _required("EMBED_MODEL")

LLM_API_KEY: str | None = _optional("LLM_API_KEY")
LLM_MODEL: str | None = _optional("LLM_MODEL")
LLM_BASE_URL: str | None = _optional("LLM_BASE_URL")


def _rate(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a decimal such as 0.28; got {raw!r}") from exc
    if not 0 <= value < 1:
        raise RuntimeError(f"{name} must be in [0, 1); got {value}")
    return value


def _money(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = int(float(raw))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an amount in AUD such as 2000; got {raw!r}") from exc
    if value < 0:
        raise RuntimeError(f"{name} cannot be negative; got {value}")
    return value


OPEX_RATE: float = _rate("OPEX_RATE", 0.28)

OTHER_ACQUISITION_COSTS: int = _money("OTHER_ACQUISITION_COSTS", 2_000)


def require_llm_config() -> tuple[str, str, str | None]:
    if not LLM_API_KEY:
        raise RuntimeError("Missing required setting LLM_API_KEY: the workflow needs it to call the LLM.")
    if not LLM_MODEL:
        raise RuntimeError("Missing required setting LLM_MODEL, for example gpt-4o-mini or deepseek-chat.")
    return LLM_API_KEY, LLM_MODEL, LLM_BASE_URL
