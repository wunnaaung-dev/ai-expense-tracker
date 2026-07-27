from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from routers import expense

app = FastAPI()

app.include_router(expense.router)


@app.get("/")
def hello_world():
    return {"message": "Hello World"}


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