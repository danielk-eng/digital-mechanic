from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ml_service_url: str = "http://127.0.0.1:8101"
    toir_service_url: str = "http://127.0.0.1:8102"
    vehicles_path: str = "data/official/vehicles.json"
    manuals_dir: str = "data/official/manuals"
    engine_docs_path: str = "data/engine_docs.yaml"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    ocr_enabled: bool = False
