"""
Authenticated video generation routes.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status

from app.middleware.auth_middleware import get_current_user
from app.models.user_model import CurrentUser
from app.models.video_model import VideoPlaybackResponse, VideoSessionResponse
from app.services.video_service import VideoService


router = APIRouter(prefix="/videos", tags=["videos"])


@router.post("/sessions", response_model=VideoSessionResponse, status_code=202)
async def create_video_session(
    background_tasks: BackgroundTasks,
    prompt: str = Form(..., min_length=1, max_length=2000),
    image: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user),
) -> VideoSessionResponse:
    """
    Create a user-owned video generation session from a prompt and 1 reference image.
    """
    if image.content_type not in {"image/png", "image/jpeg", "image/jpg", "image/webp"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PNG, JPEG, and WebP images are supported",
        )

    image_payload = (
        image.filename or "reference-image",
        await image.read(),
        image.content_type,
    )

    try:
        video_service = VideoService()
        session = video_service.create_session(current_user, prompt, image_payload)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e

    background_tasks.add_task(video_service.generate_video_for_session, session["id"])
    return _build_session_response(session, video_service)


@router.get("/sessions/{session_id}", response_model=VideoSessionResponse)
async def get_video_session(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> VideoSessionResponse:
    """
    Get session status for polling from the frontend.
    """
    video_service = VideoService()
    session = video_service.get_user_session(session_id, current_user)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video session not found",
        )

    return _build_session_response(session, video_service)


@router.get("/sessions/{session_id}/playback", response_model=VideoPlaybackResponse)
async def get_video_playback_url(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> VideoPlaybackResponse:
    """
    Get a short-lived signed URL for the generated video.
    """
    video_service = VideoService()
    session = video_service.get_user_session(session_id, current_user)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video session not found",
        )

    if session.get("status") != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Video is not ready yet",
        )

    expires_in_seconds = 3600
    try:
        video_url = video_service.create_signed_video_url(session, expires_in_seconds)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e

    return VideoPlaybackResponse(
        session_id=session_id,
        video_url=video_url,
        expires_in_seconds=expires_in_seconds,
    )


def _build_session_response(
    session: dict,
    video_service: VideoService,
) -> VideoSessionResponse:
    """
    Include a signed playback URL on completed sessions.
    """
    if session.get("status") == "completed" and session.get("video_path"):
        try:
            session["video_url"] = video_service.create_signed_video_url(
                session,
                expires_in_seconds=3600,
            )
        except ValueError:
            session["video_url"] = None

    return VideoSessionResponse(**session)
