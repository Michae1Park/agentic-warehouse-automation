from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://warehouse:warehouse@localhost:5432/warehouse"
    cors_origins: list[str] = ["http://localhost:5173"]
    llm_base_url: str = "http://localhost:8001/v1"
    llm_model: str = "qwen3.5-9b"
    llm_thinking: bool = False
    agent_max_iterations: int = 12
    robot_ids: list[str] = ["amr-01"]
    sim_time_scale: float = 1.0
    mock_robot_speed: float = 1.2
    pick_max_attempts: int = 3


settings = Settings()
