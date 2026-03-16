"""Regression tests for story output sanitization and anti-thought filtering."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gateway.rest_api import _sanitize_story_text, _extract_json_array
from agents.story_agent import StoryAgent


def test_sanitize_story_text_removes_thought_traces():
    raw = """
Reasoning: I should plan the answer first.
<think>internal chain of thought</think>
Story: Il etait une fois un lion et un lievre.
"""
    cleaned = _sanitize_story_text(raw)
    assert "Reasoning:" not in cleaned
    assert "<think>" not in cleaned
    assert "chain of thought" not in cleaned.lower()


def test_extract_json_array_skips_reasoning_prefix():
    raw = "Analysis: drafting output... [ {\"title\":\"A\",\"description\":\"B\",\"day\":1,\"month\":\"Jan\",\"region\":\"EA\",\"imageUrl\":\"\"} ] trailing"
    arr = _extract_json_array(raw)
    assert arr.startswith("[")
    assert arr.endswith("]")
    assert '"title":"A"' in arr


def test_story_agent_clean_narration_removes_markers():
    raw = """
Thought: Let me think.
[VISUAL: a market scene]
Il etait une fois un village ancien...
"""
    cleaned = StoryAgent._clean_narration(raw)
    assert "Thought:" not in cleaned
    assert "[VISUAL:" not in cleaned
    assert "Il etait une fois" in cleaned
