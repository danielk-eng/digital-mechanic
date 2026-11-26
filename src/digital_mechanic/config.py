"""Runtime settings. Secrets come from environment variables only (never logged)."""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # External services (TZ F6): swap mocks for production by editing .env only
    ml_service_url: str = Field("http://127.0.0.1:8001", validation_alias=AliasChoices("ML_SERVICE_URL", "MOCK_ML_URL"))
    toir_service_url: str = Field("http://127.0.0.1:8002", validation_alias=AliasChoices("TOIR_SERVICE_URL", "MOCK_TOIR_URL"))
    http_timeout_s: float = 20.0
    # Serverless backup (Vercel): run the official ML/TOiR mocks in-process instead of separate services
    embed_mocks: bool = False
    mocks_dir: str = "vendor/neurodvs_mock"

    # Data
    vehicles_path: str = "data/official/vehicles.json"
    fault_types_path: str = "data/official/fault_types.json"
    manuals_dir: str = "data/manuals"
    engine_docs_path: str = "data/engine_docs.yaml"
    index_path: str = "data/index/pages.jsonl.gz"
    extra_index_path: str = "data/index/extra_pages.jsonl.gz"
    extra_images_dir: str = "data/extra"
    extra_kb_enabled: bool = True
    demo_history_path: str = "data/demo_history.json"  # synthetic past tickets/repairs/parts for the timeline demo; "" to disable
    db_path: str = "var/app.db"
    uploads_dir: str = "var/uploads"
    page_cache_dir: str = "var/page_png"
    log_path: str = "var/app.log.jsonl"
    audit_retention_days: int = 90

    # LLM: any OpenAI-compatible endpoint
    llm_base_url: str = "https://api.tokenrouter.com/v1"
    llm_api_key: str = ""
    llm_api_key_file: str = ""
    llm_model: str = "deepseek/deepseek-v4.1-flash-1017-free"
    llm_vision_model: str = ""  # defaults to llm_model
    llm_timeout_s: float = 90.0
    llm_max_tool_rounds: int = 5
    llm_hedge_s: float = 12.0  # resend the same request if no answer after N s, take the first; 0 = off
    # "off" sends thinking={"type":"disabled"} (DeepSeek-style); ~4x faster answers. "on" keeps model default.
    llm_thinking: str = "off"

    # Voice
    deepgram_api_key: str = ""
    deepgram_model: str = "nova-3"
    stt_order: str = "local-first"  # "cloud-first": Deepgram before local whisper (ru/hi/vi), e.g. for a slow CPU host
    speech_timeout_s: float = 30.0
    speech_url: str = ""  # local speech service (services/speech): whisper STT + Silero TTS
    whisper_model: str = "small"  # local fallback (faster-whisper), optional
    silero_enabled: bool = False  # local TTS (torch), optional; browser TTS otherwise

    # Tracing (optional)
    comet_api_key: str = ""
    opik_project: str = "digital-mechanic"

    # 152-FZ
    pii_masking: bool = True
    require_consent: bool = True

    def api_key(self) -> str:
        if self.llm_api_key:
            return self.llm_api_key
        if self.llm_api_key_file:
            try:
                return open(self.llm_api_key_file, encoding="utf-8").read().strip()
            except OSError:
                return ""
        return ""


def get_settings() -> Settings:
    return Settings()
