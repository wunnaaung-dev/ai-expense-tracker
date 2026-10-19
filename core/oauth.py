from authlib.integrations.starlette_client import OAuth

from core.config import settings

# drive.file (non-sensitive) covers every Sheets/Drive call on files this app creates,
# so the sensitive spreadsheets scope is not requested.
GOOGLE_SCOPES = "openid email profile https://www.googleapis.com/auth/drive.file"

oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": GOOGLE_SCOPES},
    # offline: issue a refresh token so storage calls work after the 1h access token expires.
    authorize_params={"access_type": "offline", "include_granted_scopes": "true"},
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
