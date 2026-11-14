from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.db.session import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="AM — Allied Mastercomputer", version="0.1.0", lifespan=lifespan)
app.include_router(health_router, prefix="/api")
