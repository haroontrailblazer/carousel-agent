"""Central configuration — everything comes from environment variables.

Copy .env.example to .env and fill it in; python-dotenv loads it on import.
No secret may ever be hard-coded anywhere else in the codebase.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _csv(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    # --- app ---
    app_name: str = os.getenv("APP_NAME", "carousel_factory")
    review_api_base_url: str = os.getenv("REVIEW_API_BASE_URL", "http://localhost:8080")
    max_rework_rounds: int = int(os.getenv("MAX_REWORK_ROUNDS", "5"))
    max_carousel_slides: int = int(os.getenv("MAX_CAROUSEL_SLIDES", "10"))  # IG limit

    # --- models (LLM) ---
    # "openai/" ids go through LiteLLM (see app/llm.py); bare ids (gemini-*)
    # use ADK's native Google path and need GOOGLE_API_KEY. Change these in
    # .env, not here. All-OpenAI by default per the user's subscription.
    planner_model: str = os.getenv("PLANNER_MODEL", "openai/gpt-5.6-sol")
    utility_model: str = os.getenv("UTILITY_MODEL", "openai/gpt-5.4-mini")
    phrasing_model: str = os.getenv("PHRASING_MODEL", "openai/gpt-5.6-sol")
    image_model: str = os.getenv("IMAGE_MODEL", "gpt-image-2")

    # --- storage / db (Supabase) ---
    database_url: str = os.getenv("DATABASE_URL", "")  # postgresql+asyncpg://...
    supabase_url: str = os.getenv("SUPABASE_URL", "")
    supabase_service_key: str = os.getenv("SUPABASE_SERVICE_KEY", "")
    media_bucket: str = os.getenv("MEDIA_BUCKET", "carousel-media")
    # S3-compatible credentials for the artifact service adapter
    s3_endpoint: str = os.getenv("SUPABASE_S3_ENDPOINT", "")
    s3_region: str = os.getenv("SUPABASE_S3_REGION", "us-east-1")
    s3_access_key: str = os.getenv("SUPABASE_S3_ACCESS_KEY", "")
    s3_secret_key: str = os.getenv("SUPABASE_S3_SECRET_KEY", "")

    # --- mail ---
    gmail_sender: str = os.getenv("GMAIL_SENDER", "")
    gmail_credentials_path: str = os.getenv(
        "GMAIL_CREDENTIALS_PATH", str(PROJECT_ROOT / "secrets" / "gmail-credentials.json")
    )
    gmail_token_path: str = os.getenv(
        "GMAIL_TOKEN_PATH", str(PROJECT_ROOT / "secrets" / "gmail-token.json")
    )
    reviewer_emails: list[str] = field(default_factory=lambda: _csv("REVIEWER_EMAILS"))

    # --- instagram ---
    ig_user_id: str = os.getenv("IG_USER_ID", "")
    ig_access_token: str = os.getenv("IG_ACCESS_TOKEN", "")
    ig_api_version: str = os.getenv("IG_API_VERSION", "v23.0")

    # --- CTA destinations ---
    substack_url: str = os.getenv("SUBSTACK_URL", "")
    youtube_url: str = os.getenv("YOUTUBE_URL", "")
    ig_handle: str = os.getenv("IG_HANDLE", "")

    # --- fetcher sources ---
    rss_feeds: list[str] = field(default_factory=lambda: _csv("RSS_FEEDS"))
    youtube_channels: list[str] = field(default_factory=lambda: _csv("YOUTUBE_CHANNELS"))
    newsletter_query: str = os.getenv(
        "NEWSLETTER_QUERY", "label:newsletters newer_than:2d"
    )

    # --- observability (Langfuse; empty keys = tracing disabled) ---
    langfuse_public_key: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    langfuse_secret_key: str = os.getenv("LANGFUSE_SECRET_KEY", "")
    langfuse_base_url: str = os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

    # --- media / design assets ---
    ffmpeg_bin: str = os.getenv("FFMPEG_BIN", "ffmpeg")
    skills_dir: Path = PROJECT_ROOT / "skills"
    workdir: Path = Path(os.getenv("WORKDIR", str(PROJECT_ROOT / ".work")))
    cover_overlay_template: Path = Path(
        os.getenv("COVER_OVERLAY_TEMPLATE", str(PROJECT_ROOT / "STRANGE-COVER (1).png"))
    )
    cover_reference_images: tuple[str, ...] = (
        str(PROJECT_ROOT / "CONFIG-INSTA-1.png"),
        str(PROJECT_ROOT / "STRANGE-COVER (1).png"),
        str(PROJECT_ROOT / "WhatsApp Image 2026-08-11 at 4.25.57 PM.jpeg"),
    )
    slide_width: int = 1080
    slide_height: int = 1350  # 4:5 — first item's aspect ratio governs the carousel


settings = Settings()


def agent_instructions(name: str) -> str:
    """Load an agent's instruction file from skills/agents/<name>.md.

    The Learner agent updates these files from feedback ("harness updates"),
    so instructions must always be read from disk at agent-build time.
    """
    path = settings.skills_dir / "agents" / f"{name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def load_skill(filename: str) -> str:
    """Load a shared skill file from skills/ (e.g. 'cover-style.md')."""
    path = settings.skills_dir / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""
