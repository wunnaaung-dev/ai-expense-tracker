import os
from fastapi import FastAPI
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from starlette.middleware.sessions import SessionMiddleware
from core.db import init_db
from routers import expense, health, auth

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "https://ai-expense-tracker-xbae.onrender.com",
        "https://ai-expense-tracker-frontend-8ptxvxpby-wunnas-projects.vercel.app",
        "https://ai-expense-tracker-frontend-bice.vercel.app"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(SessionMiddleware, secret_key=os.getenv("JWT_SECRET"))

app.include_router(health.router)
app.include_router(expense.router)
app.include_router(auth.router)


@app.get("/")
async def root():
    return {"status": "ok"}


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)

    # Workaround for FastAPI 0.129.1+ generating contentMediaType instead of
    # format: binary for UploadFile, which breaks the Swagger UI file picker.
    # See: https://github.com/fastapi/fastapi/discussions/14975
    for component in schema.get("components", {}).get("schemas", {}).values():
        for prop in component.get("properties", {}).values():
            if prop.get("contentMediaType") == "application/octet-stream":
                del prop["contentMediaType"]
                prop["format"] = "binary"
            items = prop.get("items", {})
            if items.get("contentMediaType") == "application/octet-stream":
                del items["contentMediaType"]
                items["format"] = "binary"

    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi