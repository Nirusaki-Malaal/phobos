"""
heuristic classifier for context compression.

Scores message chunks 0.0 (noise) to 1.0 (critical) using pattern matching
and structural analysis. No ML models, no API calls, runs in microseconds.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class Classification(Enum):
    CRITICAL = "critical"    # keep verbatim (score > 0.7)
    SUMMARIZE = "summarize"  # extract key points (0.4 - 0.7)
    NOISE = "noise"          # discard entirely (< 0.4)


@dataclass
class ChunkScore:
    """Result of scoring a single chunk."""
    score: float
    classification: Classification
    reasons: List[str]


# ── Pattern definitions ──────────────────────────────────────────────────────

# CRITICAL patterns (boost score)
CODE_BLOCK = re.compile(r'```[\s\S]+?```', re.MULTILINE)
INLINE_CODE = re.compile(r'`[^`]+`')
ERROR_TRACEBACK = re.compile(
    r'(Traceback \(most recent call last\)|'
    r'Exception:|Error:|FAILED|CRITICAL|FATAL|'
    r'raise \w+Error|raise \w+Exception)',
    re.IGNORECASE
)
DECISION_LANGUAGE = re.compile(
    r'(I decided|The solution is|We should|Conclusion:|'
    r'Decision:|Recommendation:|The answer is|'
    r'After analysis|Key finding|Important:)',
    re.IGNORECASE
)
FILE_PATH = re.compile(r'(/[\w/.~-]+|\w+://\S+|[\w-]+\.(py|js|ts|yaml|json|md|sh|go|rs))')
API_RESPONSE = re.compile(r'("status"\s*:\s*\d|"error"\s*:|"data"\s*:|"result"\s*:)')
URL = re.compile(r'https?://\S+')
USER_INSTRUCTION = re.compile(
    r'(please|make sure|do not|never|always|must|'
    r'important|requirement|constraint|rule:)',
    re.IGNORECASE
)

# NOISE patterns (reduce score)
METADATA_ONLY = re.compile(
    r'^(tool_call_id|message_id|role:|content_type:|'
    r'token_count:|model:|created_at:)',
    re.IGNORECASE
)
EMPTY_RESULT = re.compile(
    r'^\s*(\[\]|\{\}|"result"\s*:\s*null|"data"\s*:\s*\[\]|'
    r'No results found|No matches|Empty response)\s*$',
    re.IGNORECASE
)
TOOL_SUMMARY_LINE = re.compile(
    r'^\[(terminal|file|search|docker|notebook)\]',
    re.IGNORECASE
)
INTERMEDIATE_STEP = re.compile(
    r'(Step \d+:|Phase \d+:|Iteration \d+:|'
    r'Attempt \d+:|Trying|Searching|Looking for|'
    r'Reading file|Checking|Scanning)',
    re.IGNORECASE
)


def score_chunk(chunk: str, seen_chunks: Optional[List[str]] = None) -> ChunkScore:
    """
    Score a message chunk from 0.0 (noise) to 1.0 (critical).
    
    Args:
        chunk: The text content to score
        seen_chunks: Recent chunks for deduplication (optional)
    
    Returns:
        ChunkScore with score, classification, and reasons
    """
    if not chunk or not chunk.strip():
        return ChunkScore(0.0, Classification.NOISE, ["empty content"])
    
    score = 0.5  # neutral baseline
    reasons = []
    text = chunk.strip()
    text_lower = text.lower()
    
    # ── Boost: Critical content ──────────────────────────────────────────
    
    # Code blocks (highest signal)
    if CODE_BLOCK.search(text):
        score += 0.30
        reasons.append("contains code block")
    
    # Error tracebacks
    if ERROR_TRACEBACK.search(text):
        score += 0.25
        reasons.append("contains error/traceback")
    
    # Decision language
    if DECISION_LANGUAGE.search(text):
        score += 0.20
        reasons.append("contains decision language")
    
    # User instructions
    if USER_INSTRUCTION.search(text):
        score += 0.15
        reasons.append("contains user instruction")
    
    # File paths and URLs (indicates concrete references)
    if FILE_PATH.search(text):
        score += 0.10
        reasons.append("contains file path/URL")
    
    # API responses with structured data
    if API_RESPONSE.search(text):
        score += 0.10
        reasons.append("contains API response")
    
    # Inline code references
    if INLINE_CODE.search(text) and len(text) < 500:
        score += 0.05
        reasons.append("contains inline code")
    
    # Short, direct answers (high information density)
    if len(text.split()) < 20 and '?' not in text:
        score += 0.05
        reasons.append("short/direct content")
    
    # ── Reduce: Noise ────────────────────────────────────────────────────
    
    # Exact duplicate detection
    if seen_chunks and text in seen_chunks:
        score -= 0.35
        reasons.append("exact duplicate")
    
    # Empty results
    if EMPTY_RESULT.search(text):
        score -= 0.30
        reasons.append("empty result")
    
    # Metadata-only content
    if METADATA_ONLY.search(text) and len(text.split()) < 10:
        score -= 0.25
        reasons.append("metadata only")
    
    # Tool summary lines (compressed output from previous compression)
    if TOOL_SUMMARY_LINE.match(text) and len(text.split()) < 15:
        score -= 0.15
        reasons.append("tool summary line")
    
    # Intermediate steps
    if INTERMEDIATE_STEP.search(text) and not DECISION_LANGUAGE.search(text):
        score -= 0.10
        reasons.append("intermediate step")
    
    # Very long verbose output (>2000 chars without code/structure)
    if len(text) > 2000 and not CODE_BLOCK.search(text) and not API_RESPONSE.search(text):
        score -= 0.10
        reasons.append("long verbose output")
    
    # Repetitive content (many repeated words)
    words = text_lower.split()
    if len(words) > 20:
        unique_ratio = len(set(words)) / len(words)
        if unique_ratio < 0.3:
            score -= 0.15
            reasons.append("repetitive content")
    
    # ── Clamp and classify ───────────────────────────────────────────────
    
    score = max(0.0, min(1.0, score))
    
    if score > 0.7:
        classification = Classification.CRITICAL
    elif score > 0.4:
        classification = Classification.SUMMARIZE
    else:
        classification = Classification.NOISE
    
    if not reasons:
        reasons.append("baseline score")
    
    return ChunkScore(score, classification, reasons)


def score_chunks(chunks: List[str]) -> List[ChunkScore]:
    """
    Score a list of chunks with deduplication awareness.
    
    Args:
        chunks: List of text chunks to score
    
    Returns:
        List of ChunkScore results
    """
    seen = []
    results = []
    
    for chunk in chunks:
        result = score_chunk(chunk, seen_chunks=seen[-10:])  # check last 10
        results.append(result)
        seen.append(chunk.strip())
    
    return results


def summarize_chunk(text: str, max_lines: int = 10) -> str:
    """
    Extract key lines from a chunk for summarization.
    
    Args:
        text: The text to summarize
        max_lines: Maximum lines to keep
    
    Returns:
        Summarized text with key lines extracted
    """
    if not text or not text.strip():
        return text
    
    lines = text.split('\n')
    
    # Score each line
    scored_lines = []
    for i, line in enumerate(lines):
        line_score = 0.0
        
        # Boost important lines
        if ERROR_TRACEBACK.search(line):
            line_score += 0.5
        if DECISION_LANGUAGE.search(line):
            line_score += 0.4
        if CODE_BLOCK.search(line) or INLINE_CODE.search(line):
            line_score += 0.3
        if FILE_PATH.search(line):
            line_score += 0.2
        if line.strip().startswith(('#', '##', '###')):
            line_score += 0.2  # headings
        
        # Reduce noise lines
        if METADATA_ONLY.match(line):
            line_score -= 0.3
        if not line.strip():
            line_score -= 0.2
        
        scored_lines.append((line_score, i, line))
    
    # Sort by score, keep top N in original order
    scored_lines.sort(key=lambda x: x[0], reverse=True)
    kept = sorted(scored_lines[:max_lines], key=lambda x: x[1])  # restore order
    
    return '\n'.join(line for _, _, line in kept)
