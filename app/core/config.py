from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    assemblyai_api_key: str = ""
    assemblyai_end_utterance_silence_ms: int = 700

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""

    tts_api_key: str = ""
    tts_voice_id: str = ""
    elevenlabs_model_id: str = "eleven_turbo_v2_5"

    max_call_duration_seconds: int = 480
    worker_concurrency_limit: int = 5

    database_url: str = "sqlite+aiosqlite:///data/negotiations.db"
    chroma_persist_dir: str = "data/chroma"

    base_url: str = "http://localhost:8000"

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
