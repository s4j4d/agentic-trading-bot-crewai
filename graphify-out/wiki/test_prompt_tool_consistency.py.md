# test_prompt_tool_consistency.py

> 23 nodes

## Key Concepts

- **test_prompt_tool_consistency.py** (13 connections) — `tests/test_prompt_tool_consistency.py`
- **_registered_per_crew()** (6 connections) — `tests/test_prompt_tool_consistency.py`
- **test_retired_tools_appear_in_no_prompt_or_doc()** (5 connections) — `tests/test_prompt_tool_consistency.py`
- **_tool_classes()** (5 connections) — `tests/test_prompt_tool_consistency.py`
- **_doc_surfaces()** (4 connections) — `tests/test_prompt_tool_consistency.py`
- **test_docs_only_reference_tools_a_crew_actually_has()** (4 connections) — `tests/test_prompt_tool_consistency.py`
- **test_every_registered_tool_is_reachable_from_some_crew()** (4 connections) — `tests/test_prompt_tool_consistency.py`
- **test_scout_docs_match_the_scout_toolset()** (4 connections) — `tests/test_prompt_tool_consistency.py`
- **all_tool_names()** (3 connections) — `tests/test_prompt_tool_consistency.py`
- **_retired_variants()** (3 connections) — `tests/test_prompt_tool_consistency.py`
- **_tool_descriptions()** (3 connections) — `tests/test_prompt_tool_consistency.py`
- **test_scout_scoring_weights_are_consistent_and_total_100()** (2 connections) — `tests/test_prompt_tool_consistency.py`
- **fixture** (1 connections)
- **Guard against prompts documenting tools the crews do not actually have. The…** (1 connections) — `tests/test_prompt_tool_consistency.py`
- **(needle, human label) for each retired tool, in every spelling seen in docs.…** (1 connections) — `tests/test_prompt_tool_consistency.py`
- **The exact regression: a retired tool name must never reach the LLM.** (1 connections) — `tests/test_prompt_tool_consistency.py`
- **Every tool-shaped token in prose must resolve to some registered crew.** (1 connections) — `tests/test_prompt_tool_consistency.py`
- **Scout prompt + skill must name only tools _scout_tools() returns.** (1 connections) — `tests/test_prompt_tool_consistency.py`
- **Orphan-tool guard: a tool wired to no crew is dead code.** (1 connections) — `tests/test_prompt_tool_consistency.py`
- **Table, `### Factor: N%` subheadings and task yaml must all agree. Regression:…** (1 connections) — `tests/test_prompt_tool_consistency.py`
- **Map class name -> registered tool `name`, for every BaseTool subclass.** (1 connections) — `tests/test_prompt_tool_consistency.py`
- **Map registered tool name -> its description string. The closing paren is found…** (1 connections) — `tests/test_prompt_tool_consistency.py`
- **Tool names each crew hands to its agents, keyed by factory function.** (1 connections) — `tests/test_prompt_tool_consistency.py`

## Relationships

- [CouncilAnalysisCrew](CouncilAnalysisCrew.md) (1 shared connections)

## Source Files

- `tests/test_prompt_tool_consistency.py`

## Audit Trail

- EXTRACTED: 34 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*