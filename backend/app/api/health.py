from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthStatus(BaseModel):
    status: str
    service: str
    version: str
    simulated_data_only: bool


@router.get("/health", response_model=HealthStatus)
def health() -> HealthStatus:
    return HealthStatus(
        status="ok",
        service="am-backend",
        version="0.1.0",
        simulated_data_only=True,
    )
