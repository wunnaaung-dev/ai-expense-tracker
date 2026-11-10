from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from starlette.middleware.sessions import SessionMiddleware

from core.config import settings
from core.errors import register_exception_handlers
from routers import auth, dashboard, entries, export, expense, health, insights, storage
from routers import settings as settings_router

app = FastAPI()

_cors_kwargs: dict = {
    "allow_origins": settings.cors_origins,
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
    "allow_origin_regex": settings.allowed_origin_regex or None,
}

app.add_middleware(CORSMiddleware, **_cors_kwargs)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    https_only=settings.secure_cookies,
)

app.include_router(health.router)
app.include_router(expense.router)
app.include_router(entries.router)
app.include_router(export.router)
app.include_router(insights.router)
app.include_router(dashboard.router)
app.include_router(storage.router)
app.include_router(settings_router.router)
app.include_router(auth.router)
app.include_router(auth.v1_router)

register_exception_handlers(app)

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