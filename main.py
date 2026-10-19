from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from core.config import settings
from routers import auth, expense, health, storage
from services.storage.errors import StorageWriteError

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)

app.include_router(health.router)
app.include_router(expense.router)
app.include_router(storage.router)
app.include_router(auth.router)


@app.exception_handler(StorageWriteError)
async def storage_write_error_handler(request: Request, exc: StorageWriteError):
    # Nothing is kept server-side: the frontend keeps the draft and offers Retry.
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"code": "storage_write_failed", "message": str(exc)},
    )

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