"""
Platform mode module for Discord RAG.

When ENABLE_PLATFORM=true, this transforms the application into a full
chat platform with user management, invite-based registration, and a
ChatGPT-style interface.
"""
import os

# Check if platform mode is enabled
PLATFORM_ENABLED = os.getenv("ENABLE_PLATFORM", "false").lower() == "true"
