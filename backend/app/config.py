from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://warehouse:warehouse@localhost:5432/warehouse"
    cors_origins: list[str] = ["http://localhost:5173"]


settings = Settings()
