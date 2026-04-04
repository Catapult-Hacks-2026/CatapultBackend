from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # AI
    anthropic_api_key: str = ""
    negotiation_model: str = "claude-sonnet-4-20250514"

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""

    # Deepgram
    deepgram_api_key: str = ""

    # TTS
    tts_provider: str = "elevenlabs"  # elevenlabs | cartesia
    tts_api_key: str = ""
    tts_voice_id: str = ""

    # Database
    database_url: str = "sqlite+aiosqlite:///data/negotiations.db"
    chroma_persist_dir: str = "data/chroma"

    # App
    base_url: str = "http://localhost:8000"

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
