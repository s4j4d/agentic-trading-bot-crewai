"""Guard against prompts documenting tools the crews do not actually have.

The scout called an unknown tool because a removed tool survived in a runtime
tool description, and later because `upcoming_catalysts` stayed documented in
the task yaml and SKILL.md after it was dropped from the crew. Both were caught
by hand; this module makes the check mechanical.

Covers every prose surface that reaches the LLM (task yamls, SKILL.md files, the
README tool table) plus the `name`/`description` of every registered tool.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from crypto_council_flow.crews.council.council_crew import (
    CouncilAnalysisCrew,
    CouncilPortfolioCrew,
    CouncilScoutCrew,
)

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src" / "crypto_council_flow"
_CREWS = _SRC / "crews" / "council"
_TOOLS = _SRC / "tools"

# Tools deliberately retired. Any mention of these in a prompt, a skill, the
# README tool table, or a live tool description is a bug: the LLM will try to
# call a tool it does not have.
_RETIRED_TOOLS = ("exchange_ticker", "upcoming_catalysts")

# Tools intentionally kept in the package but attached to no crew.
_KNOWN_ORPHANS = {"upcoming_catalysts"}


def _tool_classes() -> dict[str, str]:
    """Map class name -> registered tool `name`, for every BaseTool subclass."""
    out: dict[str, str] = {}
    for path in _TOOLS.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"class\s+(\w+Tool)\b", text):
            segment = text[match.start() : match.start() + 2000]
            name = re.search(r'name:\s*str\s*=\s*"([^"]+)"', segment)
            if name:
                out[match.group(1)] = name.group(1)
    return out


def _tool_descriptions() -> dict[str, str]:
    """Map registered tool name -> its description string.

    The closing paren is found by paren-depth counting, not a lazy regex: several
    descriptions contain parentheses inside the string literals ("(concurrently)",
    "(pass a 1-element array ...)"), so a lazy match truncates at the first ")"
    and the rest of the description goes unchecked.
    """
    out: dict[str, str] = {}
    for path in _TOOLS.glob("*.py"):
        lines = path.read_text(encoding="utf-8").split("\n")
        for idx, line in enumerate(lines):
            if not re.match(r"\s*class\s+(\w+Tool)\b", line):
                continue
            end = min(idx + 60, len(lines))
            block = "\n".join(lines[idx:end])
            name = re.search(r'name:\s*str\s*=\s*"([^"]+)"', block)
            if not name:
                continue
            start = next(
                (
                    j
                    for j in range(idx, end)
                    if re.match(r"\s*description:\s*str\s*=\s*\($", lines[j])
                ),
                None,
            )
            if start is None:
                continue
            depth = 0
            for j in range(start, end):
                depth += lines[j].count("(") - lines[j].count(")")
                if depth == 0:
                    out[name.group(1)] = "\n".join(lines[start : j + 1])
                    break
    return out


def _registered_per_crew() -> dict[str, set[str]]:
    """Tool names each crew hands to its agents, keyed by factory function."""
    classes = _tool_classes()
    crew_src = (_CREWS / "council_crew.py").read_text(encoding="utf-8")
    per_crew: dict[str, set[str]] = {}
    for fn, body in re.findall(r"def (_\w+_tools)\(\).*?return \[(.*?)\]", crew_src, re.S):
        per_crew[fn] = {
            classes[cls] for cls in re.findall(r"(\w+Tool)\(\)", body) if cls in classes
        }
    return per_crew


def _doc_surfaces() -> list[tuple[str, str]]:
    files = sorted((_CREWS / "config").glob("*.yaml"))
    files += sorted((_CREWS / "skills").rglob("SKILL.md"))
    files.append(_REPO / "README.md")
    return [
        (f.relative_to(_REPO).as_posix(), f.read_text(encoding="utf-8"))
        for f in files
        if f.exists()
    ]


@pytest.fixture(scope="module")
def all_tool_names() -> set[str]:
    return set(_tool_classes().values())


def _retired_variants() -> list[tuple[str, str]]:
    """(needle, human label) for each retired tool, in every spelling seen in docs.

    The README tool table uses class names (`UpcomingCatalystsTool`), task yamls
    use registered tool names (`upcoming_catalysts`), and a tool can also be
    advertised in prose ("exchange ticker tool"). All of those must fail.

    Deliberately NOT matched: the bare spaced phrase. "Drop candidates with no
    exchange ticker" is legitimate prose about a ticker *result*, and flagging it
    would train the team to ignore the check. Requiring "tool"/"tools" after the
    phrase separates the two, and every real defect also carries a backticked or
    CamelCase form that the other needles catch.
    """
    out: list[tuple[str, str]] = []
    for retired in _RETIRED_TOOLS:
        camel = "".join(part.capitalize() for part in retired.split("_")) + "Tool"
        spaced = retired.replace("_", " ")
        for needle in (
            retired,
            retired.upper(),
            camel,
            camel.replace("Tool", ""),
            spaced + " tool",
            spaced + " tools",
        ):
            out.append((needle, retired))
    return out


def test_retired_tools_appear_in_no_prompt_or_doc() -> None:
    """The exact regression: a retired tool name must never reach the LLM."""
    offenders: list[str] = []
    needles = _retired_variants()

    for label, text in _doc_surfaces():
        lowered = text.lower()
        for needle, retired in needles:
            if needle.lower() in lowered:
                offenders.append(f"{label}: {retired!r} (as {needle!r})")

    for tool_name, description in _tool_descriptions().items():
        if tool_name in _RETIRED_TOOLS:
            continue
        for needle, retired in needles:
            if needle.lower() in description.lower():
                offenders.append(f"{tool_name}.description: {retired!r} (as {needle!r})")

    assert not offenders, "retired tool names still advertised to the LLM:\n" + "\n".join(offenders)


def test_docs_only_reference_tools_a_crew_actually_has(
    all_tool_names: set[str],
) -> None:
    """Every tool-shaped token in prose must resolve to some registered crew."""
    registered_anywhere = set().union(*_registered_per_crew().values())
    offenders: list[str] = []
    for label, text in _doc_surfaces():
        tokens = set(re.findall(r"`([a-z][a-z0-9_]{4,})`", text))
        tokens |= set(re.findall(r"^\s*\d+\.\s*([a-z_]+)\s*\(", text, re.M))
        for token in sorted(tokens):
            # Not a tool name at all (field name, prose word) -> not our problem.
            if token not in all_tool_names:
                continue
            if token not in registered_anywhere:
                offenders.append(f"{label}: `{token}` (class exists, no crew registers it)")
    assert not offenders, "docs reference tools no crew registers:\n" + "\n".join(offenders)


def test_scout_docs_match_the_scout_toolset(all_tool_names: set[str]) -> None:
    """Scout prompt + skill must name only tools _scout_tools() returns."""
    scout = _registered_per_crew()["_scout_tools"]
    surfaces = [
        (label, text)
        for label, text in _doc_surfaces()
        if label.endswith("scout_tasks.yaml") or label.endswith("market-scout/SKILL.md")
    ]
    assert surfaces, "scout surfaces vanished - the sweep would silently pass"
    for label, text in surfaces:
        named = {t for t in re.findall(r"`([a-z][a-z0-9_]{4,})`", text) if t in all_tool_names}
        named |= {
            t for t in re.findall(r"^\s*\d+\.\s*([a-z_]+)\s*\(", text, re.M) if t in all_tool_names
        }
        extra = named - scout
        assert not extra, f"{label} names tools the scout lacks: {sorted(extra)}"


def test_every_registered_tool_is_reachable_from_some_crew() -> None:
    """Orphan-tool guard: a tool wired to no crew is dead code."""
    registered = set().union(*_registered_per_crew().values())
    orphans = set(_tool_classes().values()) - registered
    unexpected = orphans - _KNOWN_ORPHANS
    assert not unexpected, (
        "newly orphaned tools (wire them up or delete them): " f"{sorted(unexpected)}"
    )


def test_scout_scoring_weights_are_consistent_and_total_100() -> None:
    """Table, `### Factor: N%` subheadings and task yaml must all agree.

    Regression: ddd14e7 updated the weights table and the `Weight in scoring:`
    lines but left the subheadings at the old values, so the skill handed the
    agent two different weightings for the same factors.
    """
    skill = (_CREWS / "skills" / "market-scout" / "SKILL.md").read_text(encoding="utf-8")
    expected = {
        "tradable volatility": 42.5,
        "liquidity & volume": 27.5,
        "momentum": 17.5,
        "market attention": 12.5,
    }

    table = {
        m.group(1).strip().lower(): float(m.group(2))
        for m in re.finditer(r"^\|\s*([A-Za-z &]+?)\s*\|\s*([\d.]+)%", skill, re.M)
    }
    assert table, "weights table not found in market-scout SKILL.md"
    for factor, pct in expected.items():
        assert table.get(factor) == pct, f"weights table {factor}={table.get(factor)}, want {pct}"
    assert abs(sum(table.values()) - 100.0) < 1e-6, f"weights sum to {sum(table.values())}, want 100"

    subheads = {
        m.group(1).strip().lower(): float(m.group(2))
        for m in re.finditer(r"^#{2,4}\s*([A-Za-z &]+?):\s*([\d.]+)%", skill, re.M)
    }
    for factor, pct in expected.items():
        assert subheads.get(factor) == pct, (
            f"subheading for {factor} is {subheads.get(factor)}, want {pct} "
            "(subheadings must match the table above them)"
        )

    # The inline "Weight in scoring:" lines hang off the four *source* sections
    # (trending / momentum / volatility / new listings). They deliberately do NOT
    # sum to 100: liquidity is a hard gate with its own section and carried no
    # inline line even before the catalyst factor was removed. Assert the known
    # per-source values instead of a total.
    inline_values = [float(v) for v in re.findall(r"\*\*Weight in scoring:\*\*\s*([\d.]+)%", skill)]
    assert sorted(inline_values, reverse=True) == [42.5, 17.5, 12.5, 10.0], (
        f"inline source weights drifted: {inline_values}"
    )

    yaml = (_CREWS / "config" / "scout_tasks.yaml").read_text(encoding="utf-8")
    yaml_expected = {
        "Tradable volatility": 42.5,
        "Liquidity and volume": 27.5,
        "Momentum": 17.5,
        "Market attention": 12.5,
    }
    for label, pct in yaml_expected.items():
        assert f"{label}: {pct}%" in yaml, f"yaml missing '{label}: {pct}%'"


def test_scout_crews_still_build() -> None:
    """Guard the crew factories the sweep introspects."""
    assert len(CouncilScoutCrew().crew().agents) == 1
    assert len(CouncilAnalysisCrew().crew().agents) == 3
    assert len(CouncilPortfolioCrew().crew().agents) == 1