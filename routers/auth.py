from datetime import datetime, timedelta, timezone
from authlib.jose import jwt
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from core.db import get_db
from core.oauth import oauth
from core.config import settings
from core.security import get_current_user
from models.users import User

router = APIRouter(prefix="/auth", tags=["auth"])

@router.get("/google/login")
async def login(request: Request):
   redirect_uri = settings.redirect_uri
   return await oauth.google.authorize_redirect(request, redirect_uri)

@router.get("/google/callback")
async def callback(request: Request, db: AsyncSession = Depends(get_db)):
    token = await oauth.google.authorize_access_token(request)
    user_info = token.get("userinfo")

    result = await db.execute(
        select(User).where(User.google_sub == user_info.get("sub"))
    )
    user = result.scalar_one_or_none()
    if not user:
        user = User(
            google_sub=user_info.get("sub"),
            email=user_info.get("email"),
            name=user_info.get("name"),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        print(f"New user created: {user.id}")

    payload = {
        "user_id": user.id,
        "exp": datetime.now(timezone.utc) + timedelta(days=1),
    }
    jwt_token = jwt.encode({"alg": "HS256"}, payload, settings.jwt_secret)
    if isinstance(jwt_token, bytes):
        jwt_token = jwt_token.decode("utf-8")

    response = JSONResponse({
        "message": "Login successful",
        "user": {"id": user.id, "email": user.email, "name": user.name},
    })
    response.set_cookie(
        key="access_token",
        value=jwt_token,
        httponly=True,
        secure=False,
        max_age=7 * 24 * 3600, # 7 days
        samesite="lax",
    )
    return response

@router.post("/logout")
async def logout():
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(key="access_token")
    return response

@router.get("/me")
async def me(current_user: User = Depends(get_current_user)):
    return current_user