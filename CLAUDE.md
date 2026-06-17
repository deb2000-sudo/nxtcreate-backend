# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies (dev mode)
pip install -e ".[dev]"

# Run the dev server
uvicorn app.main:app --reload

# Run with Docker Compose (dev profile with hot-reload)
docker-compose --profile dev up

# Run tests
pytest -v

# Run a single test file
pytest tests/test_auth.py -v

# Lint
flake8 app/

# Format
black app/

# Type check
mypy app/
```

## Architecture

Layered FastAPI app with Firebase/GCP backend, organized under `app/`:

- **`routes/`** — HTTP handlers (auth, video, admin). Thin layer: validate input, call a service, return response.
- **`services/`** — Core business logic:
  - `firebase.py` — Singleton `FirebaseService` wrapping Firebase Auth + Firestore. All DB/auth access goes through this.
  - `user_service.py` — User CRUD on top of FirebaseService.
  - `video_service.py` — Orchestrates the Veo video generation lifecycle (upload image to GCS → start Veo job → poll → store result).
- **`middleware/auth_middleware.py`** — Reads httpOnly cookie, verifies Firebase JWT, attaches `CurrentUser` to request state. All protected routes go through this via FastAPI's `Depends()`.
- **`models/`** — Pydantic v2 schemas for request/response; no database logic here.
- **`utils/seeder.py`** — `DatabaseSeeder` runs at startup (via lifespan) to create default admin and test users if absent.

## Key Flows

**Authentication**: Login → Firebase REST API → ID token (JWT) → httpOnly cookie (SameSite=None, 10-min expiry). Token is verified per-request by Firebase Admin SDK.

**Video Generation**: `POST /videos/sessions` stores image in GCS and creates a Firestore session doc with `status=queued`. A FastAPI background task calls the Veo model, polls every `VEO_POLL_SECONDS` seconds, then writes the video to GCS and updates the session to `completed` or `failed`. Clients poll `GET /videos/sessions/{id}` for status and call `.../playback` to get a 1-hour signed URL.

**Firestore Collections**:
- `users` — email, name, role (admin | user), timestamps
- `video_sessions` — user_id, prompt, status (queued/processing/completed/failed), image_path, video_path, error, timestamps

## Environment Variables

Copy `.env.example` to `.env`. Key variables:

| Variable | Purpose |
|---|---|
| `FIREBASE_PROJECT_ID`, `FIREBASE_PRIVATE_KEY`, `FIREBASE_CLIENT_EMAIL`, `FIREBASE_DATABASE_URL`, `FIREBASE_WEB_API_KEY` | Firebase credentials |
| `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` | GCP project/region |
| `VIDEO_BUCKET_NAME` | GCS bucket for images and videos |
| `VEO_MODEL` | Vertex AI model name (default: `veo-3.1-generate-001`) |
| `ALLOWED_ORIGINS` | CORS allowlist (comma-separated) |

## Deployment

- **CI**: GitHub Actions (`.github/workflows/ci.yml`) runs flake8, mypy, and pytest on every PR.
- **Cloud Run**: `cloudbuild.yaml` builds a Docker image, pushes to Artifact Registry (`asia-south1`), and deploys to Cloud Run with secrets from Cloud Secret Manager.
- GCS paths follow the convention: `video-sessions/{user_id}/{session_id}/reference.{ext}` and `.../output.mp4`.

## Code Conventions

- Line length: 100 characters (Black config).
- All functions and class attributes must have type hints (mypy is enforced in CI).
- Only async def for route handlers and service methods that do I/O.
- Role-based access: admin routes check `current_user.role == "admin"` inside the handler after the auth middleware runs.
