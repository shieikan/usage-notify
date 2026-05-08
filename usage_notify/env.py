from __future__ import annotations

import os

from dotenv import load_dotenv


DEFAULT_ENV_FILE = ".env"


def load_app_env(path: str | None = None, override: bool = False) -> bool:
    env_path = path or os.environ.get("USAGE_NOTIFY_ENV_FILE", DEFAULT_ENV_FILE)
    return load_dotenv(env_path, override=override)


def env_default(name: str, fallback: str) -> str:
    return os.environ.get(name, fallback)
