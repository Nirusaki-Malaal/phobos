"""
Tests for the heuristic compressor plugin.
"""

import pytest

from hermes.plugins.compressor.classifier import (
    ChunkScore,
    Classification,
    score_chunk,
    score_chunks,
    summarize_chunk,
)
from hermes.plugins.compressor.compressor import (
    compress_chunk,
    compress_chunks,
    get_compression_stats,
    estimate_tokens,
    format_compression_report,
)


class TestClassifier:
    """Tests for heuristic scoring."""
    
    def test_empty_content(self):
        result = score_chunk("")
        assert result.classification == Classification.NOISE
        assert result.score == 0.0
    
    def test_code_block(self):
        result = score_chunk("```python\ndef hello():\n    pass\n```")
        assert result.classification == Classification.CRITICAL
        assert result.score > 0.7
    
    def test_error_traceback(self):
        result = score_chunk("Traceback (most recent call last):\n  ValueError: bad")
        assert result.classification == Classification.CRITICAL
        assert result.score > 0.7
    
    def test_decision_language(self):
        result = score_chunk("I decided to use PostgreSQL for this project.")
        assert result.classification == Classification.CRITICAL
        assert result.score > 0.7
    
    def test_metadata_only(self):
        result = score_chunk("tool_call_id: abc123\nmessage_id: def456")
        assert result.classification == Classification.NOISE
        assert result.score < 0.4
    
    def test_empty_result(self):
        result = score_chunk("[]")
        assert result.classification == Classification.NOISE
        assert result.score < 0.4
    
    def test_duplicate_detection(self):
        chunk = "This is a test message"
        seen = ["This is a test message", "Another message"]
        result = score_chunk(chunk, seen_chunks=seen)
        assert result.classification == Classification.NOISE
        assert "exact duplicate" in result.reasons
    
    def test_user_instruction(self):
        result = score_chunk("Please make sure to add error handling.")
        assert result.classification == Classification.CRITICAL
        assert result.score > 0.7


class TestCompressor:
    """Tests for compression strategies."""
    
    def test_compress_critical(self):
        chunk = "```python\ndef hello():\n    pass\n```"
        result = compress_chunk(chunk, Classification.CRITICAL, ["code block"])
        assert result == chunk
    
    def test_compress_noise(self):
        result = compress_chunk("[]", Classification.NOISE, ["empty"])
        assert result is None
    
    def test_compress_summarize(self):
        long_text = "Step 1: do something\n" * 20
        result = compress_chunk(long_text, Classification.SUMMARIZE, ["verbose"])
        assert result is not None
        assert len(result) < len(long_text)
    
    def test_compress_chunks(self):
        chunks = ["```code```", "[]", "I decided something"]
        result = compress_chunks(chunks)
        assert len(result) == 2  # noise discarded
    
    def test_estimate_tokens(self):
        text = "hello world test"
        tokens = estimate_tokens(text)
        assert tokens > 0
        assert tokens < 100


class TestStats:
    """Tests for compression statistics."""
    
    def test_get_stats(self):
        chunks = ["```code```", "[]", "decision"]
        stats = get_compression_stats(chunks)
        assert "total_chunks" in stats
        assert "estimated_reduction" in stats
    
    def test_format_report(self):
        stats = {
            "total_chunks": 10,
            "total_tokens": 1000,
            "critical_chunks": 3,
            "summarize_chunks": 4,
            "noise_chunks": 3,
            "estimated_kept_tokens": 400,
            "estimated_reduction": "60.0%",
        }
        report = format_compression_report(stats)
        assert "Total chunks: 10" in report
        assert "60.0%" in report


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
