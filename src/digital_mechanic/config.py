"""Runtime settings. Secrets come from environment variables only."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ml_service_url: str = "http://127.0.0.1:8101"
    toir_service_url: str = "http://127.0.0.1:8102"
    vehicles_path: str = "data/mock/vehicles.json"
    manuals_dir: str = "data/mock/manuals"
    engine_docs_path: str = "data/engine_docs.yaml"
    cache_dir: str = ".cache"
    trace_dir: str = "runs/traces"
    llm_base_url: str = "https://api.tokenrouter.com/v1"
    llm_api_key: str = ""  # never logged, never committed
    llm_model: str = "deepseek/deepseek-v4.1-flash-1017-free"
    llm_timeout_s: float = 90.0
    ocr_enabled: bool = False


def get_settings() -> Settings:
    return Settings()
