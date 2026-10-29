"""
compression strategies for context chunks.

Applies different compression strategies based on classification:
- CRITICAL: keep verbatim
- SUMMARIZE: extract key points
- NOISE: discard entirely
"""

import re
from typing import List, Optional

from .classifier import (
    ChunkScore,
    Classification,
    score_chunk,
    score_chunks,
    summarize_chunk,
)


# ── Configuration ────────────────────────────────────────────────────────────

# Maximum tokens per tool output before compression
MAX_TOOL_OUTPUT_TOKENS = 500

# Maximum lines for summarized content
MAX_SUMMARY_LINES = 10

# Number of recent chunks to keep for deduplication
DEDUP_WINDOW = 10

# ── Tool output patterns ─────────────────────────────────────────────────────

TOOL_OUTPUT_START = re.compile(r'^\[(terminal|file|search|docker|notebook)\]', re.IGNORECASE)
TOOL_OUTPUT_ERROR = re.compile(r'(error|failed|exception|traceback)', re.IGNORECASE)


def estimate_tokens(text: str) -> int:
    """Rough token estimate (words * 1.3)."""
    return int(len(text.split()) * 1.3)


def compress_chunk(chunk: str, classification: Classification, reasons: List[str]) -> Optional[str]:
    """
    Compress a single chunk based on its classification.
    
    Args:
        chunk: The text content
        classification: CRITICAL, SUMMARIZE, or NOISE
        reasons: Why this classification was chosen
    
    Returns:
        Compressed text, or None if discarded
    """
    if classification == Classification.NOISE:
        return None  # discard entirely
    
    if classification == Classification.CRITICAL:
        return chunk  # keep verbatim
    
    # SUMMARIZE: extract key points
    return summarize_chunk(chunk, max_lines=MAX_SUMMARY_LINES)


def compress_chunks(chunks: List[str]) -> List[str]:
    """
    Compress a list of message chunks.
    
    This is the main entry point for the heuristic pre-pass.
    It runs BEFORE Hermes's built-in compressor to reduce what
    the LLM compressor needs to process.
    
    Args:
        chunks: List of text chunks (messages, tool outputs, etc.)
    
    Returns:
        List of compressed chunks (None entries removed)
    """
    scored = score_chunks(chunks)
    
    compressed = []
    for chunk, score_result in zip(chunks, scored):
        result = compress_chunk(chunk, score_result.classification, score_result.reasons)
        if result is not None:
            compressed.append(result)
    
    return compressed


def get_compression_stats(chunks: List[str]) -> dict:
    """
    Get statistics about compression without actually compressing.
    
    Args:
        chunks: List of text chunks
    
    Returns:
        Dictionary with compression statistics
    """
    scored = score_chunks(chunks)
    
    total_tokens = sum(estimate_tokens(c) for c in chunks)
    
    critical_count = 0
    summarize_count = 0
    noise_count = 0
    kept_tokens = 0
    
    for chunk, score_result in zip(chunks, scored):
        if score_result.classification == Classification.CRITICAL:
            critical_count += 1
            kept_tokens += estimate_tokens(chunk)
        elif score_result.classification == Classification.SUMMARIZE:
            summarize_count += 1
            # Estimate ~30% of tokens kept after summarization
            kept_tokens += int(estimate_tokens(chunk) * 0.3)
        else:
            noise_count += 1
    
    return {
        "total_chunks": len(chunks),
        "total_tokens": total_tokens,
        "critical_chunks": critical_count,
        "summarize_chunks": summarize_count,
        "noise_chunks": noise_count,
        "estimated_kept_tokens": kept_tokens,
        "estimated_reduction": f"{(1 - kept_tokens / max(total_tokens, 1)) * 100:.1f}%",
    }


def format_compression_report(stats: dict) -> str:
    """Format compression stats as a readable report."""
    return (
        f"Compression Analysis:\n"
        f"  Total chunks: {stats['total_chunks']}\n"
        f"  Total tokens: ~{stats['total_tokens']}\n"
        f"  Critical (keep): {stats['critical_chunks']}\n"
        f"  Summarize: {stats['summarize_chunks']}\n"
        f"  Noise (discard): {stats['noise_chunks']}\n"
        f"  Estimated kept: ~{stats['estimated_kept_tokens']} tokens\n"
        f"  Estimated reduction: {stats['estimated_reduction']}"
    )
