from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    negotiation_model: str = "claude-sonnet-4-20250514"
    research_model: str = "claude-opus-4-20250514"
    reasoning_model: str = "gpt-4o"
    realtime_model: str = "gpt-4o-realtime-preview"

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""

    deepgram_api_key: str = ""

    tts_provider: str = "elevenlabs"
    tts_api_key: str = ""
    tts_voice_id: str = ""
    elevenlabs_model_id: str = "eleven_turbo_v2_5"
    cartesia_voice_mode: str = "id"

    database_url: str = "sqlite+aiosqlite:///data/negotiations.db"
    redis_url: str = "redis://localhost:6379/0"
    chroma_persist_dir: str = "data/chroma"

    base_url: str = "http://localhost:8000"
    max_concurrent_campaigns: int = 3
    worker_heartbeat_interval: int = 10
    session_lock_ttl: int = 900
    quote_cache_ttl: int = 300
    working_memory_ttl: int = 1800
    memory_validation_threshold: int = 3
    memory_confidence_threshold: float = 0.7

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
