from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    negotiation_model: str = "claude-3-5-sonnet-latest"
    research_model: str = ""

    assemblyai_api_key: str = ""
    assemblyai_end_utterance_silence_ms: int = 700

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""
    twilio_to_phone_number: str = ""
    twilio_to_phone_numbers: str = ""
    hotel_rep_override_phone_number: str = ""
    galileo_call_phone_number: str = ""

    tts_api_key: str = ""
    tts_voice_id: str = ""
    tts_provider: str = "elevenlabs"
    elevenlabs_model_id: str = "eleven_turbo_v2_5"
    cartesia_voice_mode: str = "id"
    email_api_key: str = ""
    email_from_address: str = ""
    email_reply_domain: str = ""
    email_webhook_secret: str = ""

    max_call_duration_seconds: int = 480
    worker_concurrency_limit: int = 5

    database_url: str = "sqlite+aiosqlite:///data/negotiations.db"
    redis_url: str = "redis://localhost:6379/0"
    chroma_persist_dir: str = "data/chroma"  # legacy; used only for one-time migration
    session_lock_ttl: int = 900

    base_url: str = "http://localhost:8000"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache()
def get_settings() -> Settings:
    return Settings()
