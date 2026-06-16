"""
Video generation schemas.
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


VideoSessionStatus = Literal["queued", "processing", "completed", "failed"]


class VideoSessionResponse(BaseModel):
    """Video generation session response."""

    id: str
    user_id: str
    prompt: str
    status: VideoSessionStatus
    image_path: str
    video_path: Optional[str] = None
    video_url: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class VideoPlaybackResponse(BaseModel):
    """Signed playback URL response."""

    session_id: str
    video_url: str
    expires_in_seconds: int
