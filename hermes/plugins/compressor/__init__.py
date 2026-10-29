"""
Hermes Compressor Plugin — Heuristic pre-pass for context compression.

Runs a fast heuristic classifier before Hermes's built-in compressor,
reducing what the LLM compressor needs to process. Zero dependencies,
zero memory overhead, runs in microseconds.

Usage:
    Install this plugin in ~/.hermes/plugins/compressor/
    Enable in hermes/config.yaml under plugins.enabled

Configuration (hermes/config.yaml):
    compressor:
      enabled: true
      max_tool_output_tokens: 500
      max_summary_lines: 10
"""

__version__ = "0.1.0"

import logging
from typing import Any, Dict, List, Optional

from .classifier import (
    ChunkScore,
    Classification,
    score_chunk,
    score_chunks,
    summarize_chunk,
)
from .compressor import (
    compress_chunk,
    compress_chunks,
    get_compression_stats,
    format_compression_report,
)

logger = logging.getLogger(__name__)

# ── Plugin Configuration ─────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "enabled": True,
    "max_tool_output_tokens": 500,
    "max_summary_lines": 10,
    "dedup_window": 10,
    "stats_logging": True,
}


def _extract_text_from_message(message: Any) -> str:
    """Extract text content from a message (handles various formats)."""
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # Handle multipart content
            texts = []
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    texts.append(part["text"])
            return "\n".join(texts)
    return ""


def _pre_llm_call_hook(ctx: Any, config: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """
    Pre-LLM call hook — applies heuristic compression to conversation history.
    
    This hook fires once per turn before the LLM loop begins.
    It compresses the conversation history using heuristic rules,
    then injects the compressed context.
    
    Returns:
        {"context": "..."} to inject compressed context, or None to skip
    """
    if not config.get("enabled", True):
        return None
    
    try:
        # Get conversation history from context
        conversation_history = getattr(ctx, "conversation_history", None)
        if not conversation_history or len(conversation_history) < 5:
            return None  # Not enough history to compress
        
        # Extract text from messages
        chunks = []
        for msg in conversation_history:
            text = _extract_text_from_message(msg)
            if text:
                chunks.append(text)
        
        if not chunks:
            return None
        
        # Get compression stats before
        stats_before = get_compression_stats(chunks)
        
        # Apply heuristic compression
        compressed = compress_chunks(chunks)
        
        # Get compression stats after
        stats_after = get_compression_stats(compressed)
        
        # Log stats if enabled
        if config.get("stats_logging", True):
            logger.info(
                f"Compressor: {stats_before['total_chunks']} → {len(compressed)} chunks "
                f"({stats_before['estimated_reduction']} reduction)"
            )
        
        # Build compressed context
        if not compressed:
            return None
        
        compressed_context = "\n\n---\n\n".join(compressed)
        
        # Inject as context (Hermes will append this to the user message)
        return {"context": f"[Compressed History]\n{compressed_context}"}
    
    except Exception as e:
        logger.warning(f"Compressor hook failed: {e}")
        return None  # Graceful degradation — let Hermes handle compression


def register(ctx: Any) -> None:
    """
    Register the compressor plugin with Hermes.
    
    This function is called by Hermes when the plugin is loaded.
    It registers the pre_llm_call hook for heuristic compression.
    """
    # Get plugin config from Hermes config
    plugin_config = getattr(ctx, "config", {})
    compressor_config = plugin_config.get("compressor", DEFAULT_CONFIG)
    
    # Merge with defaults
    config = {**DEFAULT_CONFIG, **compressor_config}
    
    if not config.get("enabled", True):
        logger.info("Compressor plugin disabled by config")
        return
    
    # Register the pre_llm_call hook
    ctx.register_hook(
        "pre_llm_call",
        lambda ctx: _pre_llm_call_hook(ctx, config)
    )
    
    logger.info("Compressor plugin registered (heuristic pre-pass enabled)")


# ── Public API (for testing) ─────────────────────────────────────────────────

__all__ = [
    "ChunkScore",
    "Classification",
    "score_chunk",
    "score_chunks",
    "summarize_chunk",
    "compress_chunk",
    "compress_chunks",
    "get_compression_stats",
    "format_compression_report",
    "register",
]
