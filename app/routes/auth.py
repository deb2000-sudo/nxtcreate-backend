"""
Authentication routes.
"""

import logging
import os
import base64
import json

import requests
from fastapi import APIRouter, Depends, HTTPException, status, Response

from app.middleware.auth_middleware import get_current_user
from app.models.user_model import (
    CurrentUser,
    LoginRequest,
    LoginResponse,
    UserResponse,
)
from app.services.firebase import FirebaseService
from app.services.user_service import UserService


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])
firebase = FirebaseService()
user_service = UserService()


@router.post("/login", response_model=LoginResponse, status_code=200)
async def login(request: LoginRequest, response: Response) -> LoginResponse:
    """
    Login with email and password using Firebase Authentication.
    """
    try:
        project_id = os.getenv("FIREBASE_PROJECT_ID")
        web_api_key = os.getenv("FIREBASE_WEB_API_KEY")

        if not project_id or not web_api_key:
            logger.error("Firebase login configuration is incomplete")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Firebase configuration error",
            )

        user = firebase.get_user_by_email(request.email)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        user_data = user_service.get_user(user.uid)
        if not user_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found in database",
            )

        id_token = _generate_id_token_via_rest_api(
            request.email,
            request.password,
            web_api_key,
        )
        if not id_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        token_payload = _decode_unverified_payload(id_token)
        token_project_id = token_payload.get("aud")
        token_user_id = token_payload.get("user_id") or token_payload.get("sub")

        if token_project_id != project_id:
            logger.error(
                "Firebase project mismatch. FIREBASE_WEB_API_KEY returned token aud=%s, "
                "but FIREBASE_PROJECT_ID is %s",
                token_project_id,
                project_id,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "Firebase configuration error: FIREBASE_WEB_API_KEY does not "
                    "belong to FIREBASE_PROJECT_ID"
                ),
            )

        if token_user_id != user.uid:
            logger.error(
                "Firebase user mismatch. Auth lookup uid=%s, token uid=%s",
                user.uid,
                token_user_id,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Firebase configuration error: token user does not match Auth user",
            )

        response.set_cookie(
            key="id_token",
            value=id_token,
            httponly=True,
            samesite="lax",  # or "strict"
            secure=True,  # Set to True in production
            max_age=600,  # 10 minutes
        )
        return LoginResponse(
            user_id=user.uid,
            email=user.email,
            name=user_data.get("name", ""),
            role=user_data.get("role", "user"),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )


@router.post("/logout", status_code=204)
async def logout(response: Response):
    """
    Clear the authentication cookie to log the user out.
    """
    response.delete_cookie(key="id_token")
    return {"message": "Logout successful"}



def _generate_id_token_via_rest_api(email: str, password: str, web_api_key: str) -> str:
    """
    Generate a Firebase ID token through the password sign-in REST API.
    """
    try:
        response = requests.post(
            "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword",
            json={
                "email": email,
                "password": password,
                "returnSecureToken": True,
            },
            params={"key": web_api_key},
            timeout=10,
        )

        if response.status_code != 200:
            logger.warning("Firebase password authentication failed: %s", response.status_code)
            return ""

        return response.json().get("idToken", "")

    except requests.exceptions.RequestException as e:
        logger.error(f"Network error during Firebase authentication: {str(e)}")
        return ""


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


@router.get("/me", response_model=UserResponse, status_code=200)
async def get_current_user_profile(
    current_user: CurrentUser = Depends(get_current_user),
) -> UserResponse:
    """
    Get current user profile.
    """
    try:
        user_data = user_service.get_user(current_user.user_id)

        if not user_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        return UserResponse(
            id=current_user.user_id,
            name=user_data.get("name", ""),
            email=user_data.get("email", ""),
            role=user_data.get("role", "user"),
            created_at=user_data.get("created_at"),
            updated_at=user_data.get("updated_at"),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user profile: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error retrieving user profile",
        )
