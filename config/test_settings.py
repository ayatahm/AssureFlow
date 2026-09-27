"""Test settings: the 'test' profile with the full EMAIL_CODE_REQUIRED stage by default."""
import os

os.environ.setdefault("ASSUREFLOW_PROFILE", "test")
os.environ.setdefault("AUTH_FLOW_STAGE", "EMAIL_CODE_REQUIRED")

from .settings import *  # noqa: E402,F401,F403
