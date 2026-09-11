"""
Unit tests for the _load_jsonc() helper in council_crew.py.

All tests operate on in-memory strings (via tmp_path) to stay independent
of the real agents.jsonc file.
"""

import json
import pytest
from pathlib import Path

from crypto_council_flow.crews.council.council_crew import _load_jsonc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "test.jsonc"
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------

class TestLoadJsoncHappyPath:
    def test_plain_json(self, tmp_path):
        """Standard JSON (no comments, no parens) is parsed correctly."""
        data = {"key": "value", "num": 42}
        p = _write(tmp_path, json.dumps(data))
        result = _load_jsonc(p)
        assert result == data

    def test_single_line_comments_stripped(self, tmp_path):
        content = """\
{
  // This is a comment
  "name": "test"  // inline comment
}
"""
        result = _load_jsonc(_write(tmp_path, content))
        assert result == {"name": "test"}

    def test_block_comments_stripped(self, tmp_path):
        content = """\
{
  /* block comment */
  "key": "val"
}
"""
        result = _load_jsonc(_write(tmp_path, content))
        assert result == {"key": "val"}

    def test_trailing_comma_in_object(self, tmp_path):
        content = '{"a": 1, "b": 2,}'
        result = _load_jsonc(_write(tmp_path, content))
        assert result == {"a": 1, "b": 2}

    def test_trailing_comma_in_array(self, tmp_path):
        content = '{"items": [1, 2, 3,]}'
        result = _load_jsonc(_write(tmp_path, content))
        assert result == {"items": [1, 2, 3]}

    def test_parenthesised_multiline_string(self, tmp_path):
        """Multi-line string using parentheses (as in agents.jsonc) is collapsed."""
        content = """\
{
  "role": (
    "Senior "
    "Analyst"
  ),
  "verbose": true
}
"""
        result = _load_jsonc(_write(tmp_path, content))
        assert result["role"] == "Senior  Analyst"
        assert result["verbose"] is True

    def test_boolean_and_null_values(self, tmp_path):
        content = '{"flag": true, "other": false, "nothing": null}'
        result = _load_jsonc(_write(tmp_path, content))
        assert result["flag"] is True
        assert result["other"] is False
        assert result["nothing"] is None

    def test_nested_objects(self, tmp_path):
        content = """\
{
  // outer comment
  "outer": {
    "inner": "hello" // inner comment
  }
}
"""
        result = _load_jsonc(_write(tmp_path, content))
        assert result["outer"]["inner"] == "hello"

    def test_real_agents_file_parseable(self):
        """The actual agents.jsonc in the project can be loaded without error."""
        from pathlib import Path
        agents_path = (
            Path(__file__).parent.parent
            / "src" / "crypto_council_flow"
            / "crews" / "council" / "config" / "agents.jsonc"
        )
        result = _load_jsonc(agents_path)
        # All four expected agents must be present
        for key in ("market_scout", "technical_analyst", "sentiment_analyst", "risk_manager"):
            assert key in result, f"agents.jsonc is missing '{key}'"

    def test_real_agents_file_agent_fields(self):
        """Each agent in agents.jsonc has the required role, goal, backstory keys."""
        from pathlib import Path
        agents_path = (
            Path(__file__).parent.parent
            / "src" / "crypto_council_flow"
            / "crews" / "council" / "config" / "agents.jsonc"
        )
        result = _load_jsonc(agents_path)
        for agent_name, cfg in result.items():
            for field in ("role", "goal", "backstory"):
                assert field in cfg, f"Agent '{agent_name}' is missing field '{field}'"
            assert isinstance(cfg["role"], str) and cfg["role"].strip()
            assert isinstance(cfg["goal"], str) and cfg["goal"].strip()
            assert isinstance(cfg["backstory"], str) and cfg["backstory"].strip()


# ---------------------------------------------------------------------------
# Edge-case / error tests
# ---------------------------------------------------------------------------

class TestLoadJsoncEdgeCases:
    def test_empty_object(self, tmp_path):
        result = _load_jsonc(_write(tmp_path, "{}"))
        assert result == {}

    def test_comment_only_lines_do_not_break_parse(self, tmp_path):
        content = """\
{
  // comment 1
  // comment 2
  "x": 1
  // comment 3
}
"""
        result = _load_jsonc(_write(tmp_path, content))
        assert result["x"] == 1

    def test_invalid_json_raises(self, tmp_path):
        with pytest.raises(json.JSONDecodeError):
            _load_jsonc(_write(tmp_path, "{bad json}"))
