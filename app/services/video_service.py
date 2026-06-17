"""
Video generation service using Vertex AI Veo and Google Cloud Storage.
"""

import logging
import os
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

from google import genai
from google.cloud import storage
from google.genai import types
from google.oauth2 import service_account

from app.models.user_model import CurrentUser
from app.services.firebase import FirebaseService


logger = logging.getLogger(__name__)


class VideoService:
    """
    Creates and tracks user-owned video generation sessions.
    """

    collection = "video_sessions"

    def __init__(self):
        self.firebase = FirebaseService()
        self.project = "nxt-create-deb"
        self.location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        self.bucket_name = os.getenv("VIDEO_BUCKET_NAME")
        self.model = os.getenv("VEO_MODEL", "veo-3.1-generate-001")
        self.storage_client: storage.Client | None = None

    def create_session(
        self,
        current_user: CurrentUser,
        prompt: str,
        image: tuple[str, bytes, str],
    ) -> dict[str, Any]:
        """
        Create a session and upload the reference image to GCS.
        """
        self._validate_configuration()

        session_id = uuid.uuid4().hex
        now = datetime.utcnow().isoformat()
        filename, image_bytes, content_type = image
        extension = self._extension_for_content_type(content_type)
        object_name = (
            f"video-sessions/{current_user.user_id}/{session_id}/"
            f"reference{extension}"
        )
        image_path = f"gs://{self.bucket_name}/{object_name}"
        self._upload_bytes(object_name, image_bytes, content_type)

        session = {
            "user_id": current_user.user_id,
            "prompt": prompt,
            "status": "queued",
            "image_path": image_path,
            "source_filename": filename,
            "video_path": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
        }

        self.firebase.set_document(self.collection, session_id, session)
        return {"id": session_id, **session}

    def generate_video_for_session(self, session_id: str) -> None:
        """
        Run Veo generation and persist the generated video to GCS.
        """
        try:
            session = self.firebase.get_document(self.collection, session_id)
            if not session:
                logger.error("Video session not found: %s", session_id)
                return

            self._update_session(session_id, {"status": "processing", "error": None})

            image_bytes, content_type = self._download_gcs_uri(session["image_path"])
            reference_images = [
                types.VideoGenerationReferenceImage(
                    image=types.Image(
                        image_bytes=image_bytes,
                        mime_type=content_type,
                    ),
                    reference_type="asset",
                )
            ]

            client = genai.Client(
                vertexai=True,
                project=self.project,
                location=self.location,
            )

            operation = client.models.generate_videos(
                model=self.model,
                prompt=session["prompt"],
                config=types.GenerateVideosConfig(
                    reference_images=reference_images,
                    aspect_ratio=os.getenv("VEO_ASPECT_RATIO", "9:16"),
                ),
            )

            poll_seconds = int(os.getenv("VEO_POLL_SECONDS", "15"))
            while not operation.done:
                time.sleep(poll_seconds)
                operation = client.operations.get(operation)

            video_path = self._store_generated_video(session_id, session["user_id"], operation)
            self._update_session(
                session_id,
                {
                    "status": "completed",
                    "video_path": video_path,
                    "error": None,
                },
            )

        except Exception as e:
            logger.error("Video generation failed for session %s: %s", session_id, str(e))
            self._update_session(
                session_id,
                {
                    "status": "failed",
                    "error": str(e),
                },
            )

    def get_user_session(self, session_id: str, current_user: CurrentUser) -> dict[str, Any] | None:
        """
        Fetch a session if it belongs to the current user or the user is an admin.
        """
        session = self.firebase.get_document(self.collection, session_id)
        if not session:
            return None

        if session.get("user_id") != current_user.user_id and current_user.role != "admin":
            return None

        return {"id": session_id, **session}

    def list_user_sessions(self, user_id: str) -> list[dict[str, Any]]:
        """
        Return all video sessions owned by a user, newest first.
        """
        sessions = self.firebase.query_collection(
            self.collection, "user_id", "==", user_id
        )
        return sorted(sessions, key=lambda s: s.get("created_at", ""), reverse=True)

    def create_signed_video_url(self, session: dict[str, Any], expires_in_seconds: int) -> str:
        """
        Create a signed URL for the completed video.
        """
        video_path = session.get("video_path")
        if not video_path:
            raise ValueError("Video is not ready")

        bucket_name, object_name = self._parse_gcs_uri(video_path)
        blob = self._storage_client().bucket(bucket_name).blob(object_name)
        return blob.generate_signed_url(
            version="v4",
            expiration=timedelta(seconds=expires_in_seconds),
            method="GET",
        )

    def _store_generated_video(self, session_id: str, user_id: str, operation: Any) -> str:
        if not operation.result or not operation.result.generated_videos:
            raise ValueError("Video generation finished without a video result")

        video = operation.result.generated_videos[0].video
        object_name = f"video-sessions/{user_id}/{session_id}/output.mp4"

        if video.video_bytes:
            self._upload_bytes(object_name, video.video_bytes, "video/mp4")
            return f"gs://{self.bucket_name}/{object_name}"

        if video.uri:
            return video.uri

        raise ValueError("Video result did not include bytes or a URI")

    def _upload_bytes(self, object_name: str, payload: bytes, content_type: str) -> None:
        blob = self._storage_client().bucket(self.bucket_name).blob(object_name)
        blob.upload_from_string(payload, content_type=content_type)

    def _download_gcs_uri(self, gcs_uri: str) -> tuple[bytes, str]:
        bucket_name, object_name = self._parse_gcs_uri(gcs_uri)
        blob = self._storage_client().bucket(bucket_name).blob(object_name)
        return blob.download_as_bytes(), blob.content_type or "image/jpeg"

    def _update_session(self, session_id: str, data: dict[str, Any]) -> None:
        data["updated_at"] = datetime.utcnow().isoformat()
        self.firebase.update_document(self.collection, session_id, data)

    def _validate_configuration(self) -> None:
        missing = []
        if not self.project:
            missing.append("GOOGLE_CLOUD_PROJECT or FIREBASE_PROJECT_ID")
        if not self.bucket_name:
            missing.append("VIDEO_BUCKET_NAME")
        if missing:
            raise ValueError(f"Missing video configuration: {', '.join(missing)}")

    def _storage_client(self) -> storage.Client:
        if self.storage_client is None:
            self.storage_client = self._build_storage_client()
        return self.storage_client

    def _build_storage_client(self) -> storage.Client:
        firebase_private_key = os.getenv("FIREBASE_PRIVATE_KEY")
        firebase_client_email = os.getenv("FIREBASE_CLIENT_EMAIL")

        if firebase_private_key and firebase_client_email:
            credentials = service_account.Credentials.from_service_account_info(
                {
                    "type": "service_account",
                    "project_id": self.project,
                    "private_key": firebase_private_key.replace("\\n", "\n"),
                    "client_email": firebase_client_email,
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            )
            return storage.Client(project=self.project, credentials=credentials)

        return storage.Client(project=self.project)

    @staticmethod
    def _parse_gcs_uri(gcs_uri: str) -> tuple[str, str]:
        if not gcs_uri.startswith("gs://"):
            raise ValueError("Expected a gs:// URI")

        path = gcs_uri[5:]
        bucket_name, _, object_name = path.partition("/")
        if not bucket_name or not object_name:
            raise ValueError("Invalid GCS URI")
        return bucket_name, object_name

    @staticmethod
    def _extension_for_content_type(content_type: str) -> str:
        if content_type == "image/png":
            return ".png"
        if content_type in {"image/jpeg", "image/jpg"}:
            return ".jpg"
        if content_type == "image/webp":
            return ".webp"
        raise ValueError("Only PNG, JPEG, and WebP images are supported")
