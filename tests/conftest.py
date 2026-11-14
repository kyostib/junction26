"""Test process configuration, loaded before application modules."""

import os


# Tests must not require a developer's local PostgreSQL instance.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_bs_meter.db")
os.environ.setdefault("CONTEXT_DEV_API_KEY", "")
os.environ.setdefault("LLM_PROVIDER", "mock")
