import os
from authlib.integrations.starlette_client import OAuth

from core.config import settings

oauth = OAuth()
oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)
# Notion has no OIDC; the token response itself carries the user (owner) and workspace.
oauth.register(
    name="notion",
    client_id=settings.notion_client_id,
    client_secret=settings.notion_client_secret,
    authorize_url="https://api.notion.com/v1/oauth/authorize",
    access_token_url="https://api.notion.com/v1/oauth/token",
    authorize_params={"owner": "user"},
    client_kwargs={"token_endpoint_auth_method": "client_secret_basic"},
)
