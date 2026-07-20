"""Deployment entrypoint: `uvicorn twitchguard.main:app`."""
from __future__ import annotations

from .app import create_app

app = create_app()
