"""
Authentication middleware for protecting routes
"""

import logging
import base64
import json
import os

from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.models.user_model import CurrentUser
from app.services.firebase import FirebaseService
from app.services.user_service import UserService


logger = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)


def _normalize_token(token: str) -> str:
    token = token.strip().strip('"').strip("'")
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def _decode_unverified_payload(token: str) -> dict:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}

        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload + padding))
    except Exception:
        return {}


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> CurrentUser:
    """
    Dependency for getting current authenticated user

    Args:
        request: The incoming request, used to access cookies
        credentials: HTTP Bearer credentials (optional)

    Returns:
        CurrentUser with user_id, email, role, and name
    """
    try:
        firebase = FirebaseService()
        user_service = UserService()
        #Debugging for accesing cookie token
        print("=" * 80)
        print("REQUEST HEADERS")
        print(dict(request.headers))
        print("=" * 80)

        print("REQUEST COOKIES")
        print(request.cookies)
        print("=" * 80)

        print("COOKIE HEADER")
        print(request.headers.get("cookie"))
        print("=" * 80)

        # Try to get token from cookie first
        token = request.cookies.get("id_token")

        # If not in cookie, try Authorization header
        if not token and credentials:
            token = _normalize_token(credentials.credentials)

        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication credentials were not provided.",
            )

        if token.count(".") != 2:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid ID token: expected a Firebase ID token JWT",
            )

        expected_project_id = os.getenv("FIREBASE_PROJECT_ID")
        token_payload = _decode_unverified_payload(token)
        token_audience = token_payload.get("aud")

        if (
            expected_project_id
            and token_audience
            and token_audience != expected_project_id
        ):
            logger.warning(
                "Firebase token project mismatch. token aud=%s expected=%s",
                token_audience,
                expected_project_id,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    "Invalid ID token: token belongs to a different Firebase project"
                ),
            )

        # Log token verification attempt (first 50 chars for security)
        logger.debug(f"🔍 Verifying token: {token[:50]}...")

        try:
            decoded_token = firebase.verify_id_token(token)
        except ValueError as token_error:
            logger.warning(f"❌ Invalid ID token: {str(token_error)}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid ID token: {str(token_error)}",
            )

        user_id = decoded_token.get("uid") or decoded_token.get("user_id")

        if not user_id:
            logger.warning("❌ Token missing user ID (uid claim)")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing user ID",
            )

        logger.debug(f"✅ Token verified for user: {user_id}")

        # Get user from Firestore
        user_data = user_service.get_user(user_id)

        if not user_data:
            logger.warning(f"❌ User {user_id} not found in Firestore")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found in database",
            )

        logger.debug(
            f"✅ User found: {user_data.get('email')} (role: {user_data.get('role')})"
        )

        return CurrentUser(
            user_id=user_id,
            email=user_data.get("email", ""),
            role=user_data.get("role", "user"),
            name=user_data.get("name", ""),
        )

    except HTTPException:
        raise
    except ValueError as e:
        logger.warning(f"❌ Authentication validation error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication failed: {str(e)}",
        )
    except Exception as e:
        logger.error(f"❌ Unexpected authentication error: {str(e)}")
        import traceback

        logger.error(f"Traceback:\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication error",
        )


def get_admin_user(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """
    Dependency for admin-only routes

    Args:
        current_user: Current authenticated user

    Returns:
        CurrentUser if user is admin

    Raises:
        HTTPException: If user is not admin
    """
    if current_user.role != "admin":
        logger.warning(f"Unauthorized admin access attempt: {current_user.user_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user

