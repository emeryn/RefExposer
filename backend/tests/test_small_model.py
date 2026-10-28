"""Agent mode adapted to a small model (1B, 3B).

A 1B model does not fail at agent mode for one reason, so each failure mode gets its own check: too many
tools to choose from, several calls at once, a result that does not fit the context, a `limit` larger than it
can summarise, and the tool call written as text instead of asked for properly.
"""
import json

import pytest

from conftest import refresh
from test_assistant import ask, configure, final, llm, tool_call  # noqa: F401 (fixtures)


def small(env, **changes) -> dict:
    """A saved profile that says the model is a small one, and make it the one in use."""
    body = {"name": "Tiny", "provider": "ollama", "base_url": "http://tiny.internal:11434",
            "model": "llama3.2:1b", "small_model": True, **changes}
    r = env.post("/api/admin/llm-profiles", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def settings(env):
    from app.appsettings import load
    from app.db import session_factory

    with session_factory()() as db:
        return load(db, "assistant")


# --------------------------------------------------------------------------- fewer tools

def test_a_small_model_gets_a_short_tool_list(env, llm):
    from app.llm.agent import Small

    configure(env, agent_enabled=True, allow_writes=True)
    small(env)
    provider = llm("done")
    ask(env, "What is there?")
    offered = set(provider.tool_names)
    assert offered <= set(Small.TOOLS) | set(Small.SQL_TOOLS)
    assert "list_referentials" in offered and "query_rows" in offered
    # No write tool, and no artifact tool: a judgement call that goes wrong at this size
    assert not offered & {"create_referential", "grant_access", "propose_sql", "offer_download"}


def test_a_normal_model_keeps_every_tool(env, llm):
    configure(env, agent_enabled=True, allow_writes=True)
    small(env, small_model=False)
    provider = llm("done")
    ask(env, "What is there?")
    offered = set(provider.tool_names)
    assert {"propose_sql", "offer_download", "create_referential", "lookup_many"} <= offered


# --------------------------------------------------------------------------- one call at a time

def test_only_the_first_tool_call_of_a_burst_is_honoured(env, llm):
    from app.llm.provider import Reply, ToolCall

    refresh(env, "countries")
    configure(env, agent_enabled=True)
    small(env)
    burst = Reply(tool_calls=[
        ToolCall(name="list_referentials", arguments={}, id="a"),
        ToolCall(name="query_rows", arguments={"id": "countries"}, id="b"),
        ToolCall(name="search", arguments={"q": "FR"}, id="c"),
    ])
    llm(burst, "France is there.")
    _, events = ask(env, "Tell me about France")
    steps = final(events)["steps"]
    assert [s["tool"] for s in steps] == ["list_referentials"]


# --------------------------------------------------------------------------- results that fit

def test_a_result_stays_within_what_the_context_holds(env, llm):
    """The row limit does most of the work; the cap on the result is the backstop behind it."""
    from app.llm.agent import MAX_TOOL_RESULT_CHARS, Small
    from test_assistant import tool_results

    refresh(env, "pdns")  # 3002 rows
    configure(env, agent_enabled=True)
    small(env)
    provider = llm(tool_call("query_rows", id="pdns", limit=500), "done")
    ask(env, "Show me the passive DNS rows")
    handed = tool_results(provider, "query_rows")
    assert handed, "the model received no result"
    assert len(handed[0]) <= Small.MAX_RESULT_CHARS + 100
    assert Small.MAX_RESULT_CHARS < MAX_TOOL_RESULT_CHARS


def test_a_result_no_row_limit_can_bound_is_truncated(env, llm):
    """A query is not bounded by the row limit of a tool: the cap is what keeps it in the context."""
    from app.llm.agent import Small
    from test_assistant import tool_results

    refresh(env, "pdns")
    configure(env, agent_enabled=True)
    small(env)
    provider = llm(tool_call("run_sql", sql="SELECT * FROM pdns"), "done")
    ask(env, "Everything from pdns")
    handed = tool_results(provider, "run_sql")
    assert handed, "the model received no result"
    assert "truncated" in handed[0]
    assert len(handed[0]) <= Small.MAX_RESULT_CHARS + 100


def test_a_limit_larger_than_the_model_can_hold_is_brought_down(env, llm):
    from app.llm.agent import Small

    refresh(env, "pdns")
    configure(env, agent_enabled=True)
    small(env)
    llm(tool_call("query_rows", id="pdns", limit=500), "done")
    _, events = ask(env, "Show me the rows")
    step = final(events)["steps"][0]
    assert step["arguments"]["limit"] == Small.MAX_LIMIT
    assert step["ok"] is True


def test_a_tool_call_without_a_limit_gets_one(env, llm):
    from app.llm.agent import Small

    refresh(env, "countries")
    configure(env, agent_enabled=True)
    small(env)
    llm(tool_call("query_rows", id="countries"), "done")
    _, events = ask(env, "Show me the rows")
    assert final(events)["steps"][0]["arguments"]["limit"] == Small.MAX_LIMIT


# --------------------------------------------------------------------------- a call written as text

def test_a_tool_call_written_in_the_answer_is_still_run(env, llm):
    """Small models often print the call instead of using the tool-call field of the API."""
    refresh(env, "countries")
    configure(env, agent_enabled=True)
    small(env)
    written = '```json\n{"name": "query_rows", "arguments": {"id": "countries", "limit": 3}}\n```'
    llm(written, "There are 5 countries.")
    _, events = ask(env, "How many countries?")
    done = final(events)
    assert [s["tool"] for s in done["steps"]] == ["query_rows"]
    assert done["content"] == "There are 5 countries."


def test_a_normal_answer_is_not_mistaken_for_a_tool_call(env, llm):
    configure(env, agent_enabled=True)
    small(env)
    llm("There are 5 countries, from the referential countries.")
    _, events = ask(env, "How many countries?")
    done = final(events)
    assert done["steps"] == [] and "5 countries" in done["content"]


def test_a_written_call_to_a_tool_it_was_not_offered_is_not_run(env, llm):
    """The text parser is not a way around the tool list: only an offered name is accepted."""
    configure(env, agent_enabled=True, allow_writes=True)
    small(env)
    llm('```json\n{"name": "delete_referential", "arguments": {"id": "countries"}}\n```', "done")
    _, events = ask(env, "Delete countries")
    done = final(events)
    assert done["steps"] == []  # treated as text, which is what it is
    assert "countries" in env.get("/api/referentials").text  # still there


# --------------------------------------------------------------------------- prompt and steps

def test_the_prompt_is_the_short_one(env, llm):
    configure(env, agent_enabled=True)
    small(env)
    provider = llm("done")
    ask(env, "hello")
    system = provider.calls[0][0]
    assert system["role"] == "system"
    assert "Call ONE tool" in system["content"]
    # Much shorter than the full one, which is the point
    from app.llm.agent import default_prompt

    assert len(system["content"]) < len(default_prompt())


def test_a_custom_prompt_still_wins(env, llm):
    configure(env, agent_enabled=True, system_prompt="You are the house style.")
    small(env)
    provider = llm("done")
    ask(env, "hello")
    assert "You are the house style." in provider.calls[0][0]["content"]


def test_the_number_of_steps_is_capped(env, llm):
    from app.llm.agent import Small

    refresh(env, "countries")
    configure(env, agent_enabled=True, max_steps=20)
    small(env)
    # More calls than it is allowed: it must be stopped and made to conclude
    llm(*([tool_call("list_referentials")] * 10), "done")
    _, events = ask(env, "Keep looking")
    done = final(events)
    assert len(done["steps"]) <= Small.MAX_STEPS
    assert done["error"] and "stopped after" in done["error"]


def test_the_configured_maximum_still_wins_when_it_is_lower(env, llm):
    refresh(env, "countries")
    configure(env, agent_enabled=True, max_steps=2)
    small(env)
    llm(*([tool_call("list_referentials")] * 10), "done")
    _, events = ask(env, "Keep looking")
    assert len(final(events)["steps"]) <= 2
