"""
Local Flow Store
Saves flow_data as JSON files on the server filesystem.
This bypasses Supabase's column size limits for large flows.

Storage: data/flows/<chatbot_id>.json
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Flows are stored in data/flows/ relative to the project root
FLOWS_DIR = Path(__file__).parent.parent / "data" / "flows"


def _ensure_dir():
    FLOWS_DIR.mkdir(parents=True, exist_ok=True)


def _flow_path(chatbot_id: int) -> Path:
    return FLOWS_DIR / f"{chatbot_id}.json"


def save_flow_local(chatbot_id: int, flow_data: dict) -> bool:
    """Save flow_data to local filesystem. Returns True on success."""
    try:
        _ensure_dir()
        path = _flow_path(chatbot_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(flow_data, f, ensure_ascii=False, indent=2)
        size_kb = path.stat().st_size // 1024
        logger.info(f"[LocalFlowStore] Saved flow for chatbot {chatbot_id} ({size_kb}KB) → {path}")
        return True
    except Exception as e:
        logger.error(f"[LocalFlowStore] Failed to save flow for chatbot {chatbot_id}: {e}", exc_info=True)
        return False


def load_flow_local(chatbot_id: int) -> Optional[dict]:
    """Load flow_data from local filesystem. Returns None if not found."""
    try:
        path = _flow_path(chatbot_id)
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"[LocalFlowStore] Failed to load flow for chatbot {chatbot_id}: {e}", exc_info=True)
        return None


def delete_flow_local(chatbot_id: int) -> bool:
    """Delete local flow file."""
    try:
        path = _flow_path(chatbot_id)
        if path.exists():
            path.unlink()
        return True
    except Exception as e:
        logger.error(f"[LocalFlowStore] Failed to delete flow for chatbot {chatbot_id}: {e}")
        return False


def flow_exists_local(chatbot_id: int) -> bool:
    return _flow_path(chatbot_id).exists()
