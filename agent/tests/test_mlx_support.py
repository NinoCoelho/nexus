"""Tests for the pure MLX helpers — no mlx import, runs on any OS.

The span splitter is the piece most likely to be subtly wrong, because its
whole job is handling sentinels that arrive split across token boundaries. Most
of these tests feed text one character at a time to force that case.
"""

from __future__ import annotations

import pytest

from nexus.agent.llm.mlx_support import (
    SpanSplitter,
    estimate_resident_gb,
    fits_in_unified_memory,
    parse_tool_calls_fallback,
    parse_xml_function_tool_calls,
    sanitize_chat_template,
    select_tool_parser_name,
    sniff_tool_sentinels,
    template_opens_thinking,
)


def drain(splitter: SpanSplitter, text: str, *, chunk: int = 0) -> list[tuple[str, str]]:
    """Feed ``text`` through ``splitter`` and flush, returning all events.

    ``chunk=0`` feeds one character at a time (the adversarial case);
    ``chunk=n`` feeds fixed-size slices; ``chunk=-1`` feeds it all at once.
    """
    events: list[tuple[str, str]] = []
    if chunk == -1:
        events.extend(splitter.feed(text))
    else:
        size = chunk or 1
        for i in range(0, len(text), size):
            events.extend(splitter.feed(text[i : i + size]))
    events.extend(splitter.flush())
    return events


def collapse(events: list[tuple[str, str]]) -> dict[str, str]:
    """Concatenate per channel, so tests assert content not chunk boundaries."""
    out: dict[str, str] = {}
    for channel, text in events:
        out[channel] = out.get(channel, "") + text
    return out


# ---------------------------------------------------------------------------
# SpanSplitter — thinking
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("chunk", [0, 3, -1])
def test_think_span_routed_to_thinking_channel(chunk):
    s = SpanSplitter()
    got = collapse(drain(s, "before<think>secret reasoning</think>after", chunk=chunk))
    assert got.get("prose") == "beforeafter"
    assert got.get("thinking") == "secret reasoning"


def test_sentinel_split_across_chunks_never_leaks_as_prose():
    """The regression this whole tail-hold mechanism exists for."""
    s = SpanSplitter()
    events = []
    for part in ["hello <", "th", "ink>", "why", "</th", "ink>", " done"]:
        events.extend(s.feed(part))
    events.extend(s.flush())
    got = collapse(events)
    assert got.get("prose") == "hello  done"
    assert got.get("thinking") == "why"
    # No fragment of a sentinel may ever appear on the prose channel.
    for channel, text in events:
        if channel == "prose":
            assert "<" not in text and ">" not in text


def test_closing_tag_without_opener_when_template_pre_opens():
    """Reasoning templates that emit `<think>` themselves only stream `</think>`."""
    s = SpanSplitter(starts_in_thinking=True)
    got = collapse(drain(s, "reasoning here</think>the answer"))
    assert got.get("thinking") == "reasoning here"
    assert got.get("prose") == "the answer"


def test_unterminated_think_span_flushes_as_thinking():
    s = SpanSplitter()
    got = collapse(drain(s, "a<think>never closed"))
    assert got.get("prose") == "a"
    assert got.get("thinking") == "never closed"


def test_longer_sentinel_wins_on_tie():
    """`<thinking>` must not be read as `<think>` followed by `ing>`."""
    s = SpanSplitter()
    got = collapse(drain(s, "x<thinking>y</thinking>z"))
    assert got.get("prose") == "xz"
    assert got.get("thinking") == "y"


def test_plain_text_passes_through_untouched():
    s = SpanSplitter()
    got = collapse(drain(s, "no markup at all"))
    assert got == {"prose": "no markup at all"}


def test_multiple_think_spans():
    s = SpanSplitter()
    got = collapse(drain(s, "<think>one</think>mid<think>two</think>end"))
    assert got.get("thinking") == "onetwo"
    assert got.get("prose") == "midend"


# ---------------------------------------------------------------------------
# SpanSplitter — tool channel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("chunk", [0, 5, -1])
def test_tool_span_buffered_not_emitted_as_prose(chunk):
    s = SpanSplitter(tool_open=("<tool_call>",), tool_close=("</tool_call>",))
    got = collapse(drain(s, 'say hi<tool_call>{"name":"x"}</tool_call>', chunk=chunk))
    # Tool markup and payload must never reach the prose channel; the provider
    # reads the payload off `tool_text` and parses it once at the end.
    assert got.get("prose") == "say hi"
    assert got.get("tool") == '{"name":"x"}'
    assert s.tool_text == '{"name":"x"}'


def test_tool_text_accumulates_across_spans():
    s = SpanSplitter(tool_open=("<tool_call>",), tool_close=("</tool_call>",))
    drain(s, "<tool_call>a</tool_call> and <tool_call>b</tool_call>")
    assert s.tool_text == "ab"


def test_thinking_and_tool_channels_coexist():
    s = SpanSplitter(tool_open=("<tool_call>",), tool_close=("</tool_call>",))
    got = collapse(
        drain(s, "<think>plan</think>ok<tool_call>{\"name\":\"f\"}</tool_call>done")
    )
    assert got.get("thinking") == "plan"
    assert got.get("prose") == "okdone"
    assert s.tool_text == '{"name":"f"}'


def test_no_tool_sentinels_means_tool_channel_never_entered():
    s = SpanSplitter()
    got = collapse(drain(s, "<tool_call>not special here</tool_call>"))
    assert got.get("prose") == "<tool_call>not special here</tool_call>"
    assert s.tool_text == ""


# ---------------------------------------------------------------------------
# Parser selection and template sniffing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "archs,model_type,expected",
    [
        (["Qwen3ForCausalLM"], "qwen3", "qwen3_coder"),
        (["Qwen3MoeForCausalLM"], "qwen3_moe", "qwen3_coder"),
        (["Qwen3_5MoeForConditionalGeneration"], "qwen3_5_moe", "qwen3_coder"),
        (["MistralForCausalLM"], "mistral", "mistral"),
        (["LlamaForCausalLM"], "llama", "pythonic"),
        (["Gemma3ForCausalLM"], "gemma3", "gemma4"),
        (["Gemma4ForConditionalGeneration"], "gemma4", "gemma4"),
        (["Glm4MoeLiteForCausalLM"], "glm4_moe_lite", "glm47"),
        (["NemotronHForCausalLM"], "nemotron_h", "xml_functions"),
        ([], "", None),
    ],
)
def test_parser_selected_from_architecture(archs, model_type, expected):
    assert select_tool_parser_name(architectures=archs, model_type=model_type) == expected


def test_parser_falls_back_to_template_sniffing():
    assert (
        select_tool_parser_name(
            architectures=["SomethingNovelForCausalLM"],
            model_type="novel",
            chat_template="emit [TOOL_CALLS] here",
        )
        == "mistral"
    )


def test_architecture_table_beats_template_sniffing():
    """A known architecture must win; the template is the weaker signal."""
    assert (
        select_tool_parser_name(
            architectures=["Qwen3ForCausalLM"],
            model_type="qwen3",
            chat_template="[TOOL_CALLS]",
        )
        == "qwen3_coder"
    )


def test_unknown_model_returns_none():
    assert select_tool_parser_name(architectures=["Zzz"], model_type="zzz") is None


def test_sniff_tool_sentinels_pairs_open_and_close():
    opens, closes = sniff_tool_sentinels("... <tool_call> ... </tool_call> ...")
    assert "<tool_call>" in opens
    assert "</tool_call>" in closes


def test_sniff_tool_sentinels_empty_for_plain_template():
    assert sniff_tool_sentinels("{{ messages }}") == ((), ())


def test_template_opens_thinking_detects_unclosed_opener():
    assert template_opens_thinking("<|assistant|>\n<think>\n") is True
    assert template_opens_thinking("<|assistant|>\n") is False
    assert template_opens_thinking("<think>x</think>\n<|assistant|>\n") is False


# ---------------------------------------------------------------------------
# Chat-template sanitizing (doubled-brace Qwen tool example)
# ---------------------------------------------------------------------------

# The exact pathology shipped in several mlx-community Qwen2/2.5 conversions:
# the tool-call example sits inside a Jinja string literal, so its doubled
# braces are literal text and the model learns to emit invalid JSON.
_BROKEN_QWEN = (
    '{%- for tool in tools %}\n'
    '    {{- "\\n" }}\n'
    '    {{- tool | tojson }}\n'
    '{%- endfor %}\n'
    '{{- "For each function call, return a json object with function name and'
    ' arguments within <tool_call></tool_call> XML tags:\\n<tool_call>\\n'
    '{{\\"name\\": <function-name>, \\"arguments\\": <args-json-object>}}\\n'
    '</tool_call>" }}\n'
)


def test_sanitize_repairs_doubled_brace_example():
    fixed = sanitize_chat_template(_BROKEN_QWEN)
    assert '{{\\"name\\"' not in fixed
    assert '{\\"name\\": <function-name>, \\"arguments\\": <args-json-object>}' in fixed
    # Real Jinja expressions and control blocks must keep their braces.
    assert '{{- "\\n" }}' in fixed
    assert '{{- tool | tojson }}' in fixed
    assert '{%- for tool in tools %}' in fixed
    # Idempotent.
    assert sanitize_chat_template(fixed) == fixed


def test_sanitize_repairs_plain_spelling():
    plain = (
        '<tool_call>\n{{"name": <function-name>, "arguments": <args-json-object>}}\n</tool_call>'
    )
    fixed = sanitize_chat_template(plain)
    assert fixed == (
        '<tool_call>\n{"name": <function-name>, "arguments": <args-json-object>}\n</tool_call>'
    )


def test_sanitize_leaves_clean_template_untouched():
    modern = (
        '{%- for tool in tools %}\n    {{- tool | tojson }}\n{%- endfor %}\n'
        '<tool_call>\n{"name": <function-name>, "arguments": <args-json-object>}\n</tool_call>'
    )
    assert sanitize_chat_template(modern) == modern
    assert sanitize_chat_template("") == ""
    assert sanitize_chat_template("{{ messages }}") == "{{ messages }}"


# ---------------------------------------------------------------------------
# Nexus-provided tool parsers (formats mlx_lm doesn't ship)
# ---------------------------------------------------------------------------


_NEMOTRON_RAW = (
    "Sure, checking that now.\n</think>\n"
    "<tool_call>\n<function=get_weather>\n<parameter=city>\nTokyo\n</parameter>\n"
    "<parameter=unit>\ncelsius\n</parameter>\n</function>\n</tool_call>"
)


def test_xml_functions_parses_nemotron_shape():
    calls = parse_xml_function_tool_calls(_NEMOTRON_RAW)
    assert calls == [
        {"name": "get_weather", "arguments": {"city": "Tokyo", "unit": "celsius"}}
    ]


def test_xml_functions_no_params_and_no_matches():
    assert parse_xml_function_tool_calls("<function=ping>\n</function>") == [
        {"name": "ping", "arguments": {}}
    ]
    assert parse_xml_function_tool_calls("no calls here") == []
    assert parse_xml_function_tool_calls("") == []


# ---------------------------------------------------------------------------
# Gemma 4 channel markup (sentinel tables)
# ---------------------------------------------------------------------------


def test_gemma4_tool_sentinels_sniffed():
    tpl = "... <|tool_call>call:fn{...} <turn|> ..."
    opens, closes = sniff_tool_sentinels(tpl)
    assert "<|tool_call>" in opens
    assert "<turn|>" in closes


def test_gemma4_channel_think_detected():
    assert template_opens_thinking("<|turn>model\n<|channel>thought\n") is True
    # The generation prompt emits an *empty* thought channel — balanced.
    assert template_opens_thinking("<|turn>model\n<|channel>thought\n<channel|>") is False


# ---------------------------------------------------------------------------
# Provider render fixup: tool-call arguments as objects
# ---------------------------------------------------------------------------


def test_tool_args_as_dicts_parses_wire_strings():
    from nexus.agent.llm.mlx_provider import _tool_args_as_dicts

    msgs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "arguments": '{"city": "Tokyo"}',
                    },
                }
            ],
        },
        {"role": "tool", "content": "18C", "tool_call_id": "c1"},
    ]
    out = _tool_args_as_dicts(msgs)
    assert out[0]["tool_calls"][0]["function"]["arguments"] == {"city": "Tokyo"}


def test_tool_args_as_dicts_handles_garbage_and_missing():
    from nexus.agent.llm.mlx_provider import _tool_args_as_dicts

    msgs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "function": {"name": "f1", "arguments": "not json"}},
                {"id": "c2", "function": {"name": "f2", "arguments": None}},
                {"id": "c3", "function": {"name": "f3", "arguments": {"a": 1}}},
                {"id": "c4", "function": {"name": "f4"}},
            ],
        }
    ]
    out = _tool_args_as_dicts(msgs)
    fns = [tc["function"] for tc in out[0]["tool_calls"]]
    assert fns[0]["arguments"] == {}
    assert fns[1]["arguments"] == {}
    assert fns[2]["arguments"] == {"a": 1}
    assert fns[3]["arguments"] == {}


# ---------------------------------------------------------------------------
# Provider render fixup: multipart content lists → text
# ---------------------------------------------------------------------------


def test_flatten_parts_joins_text_and_placeholders():
    from nexus.agent.llm.mlx_provider import _flatten_content_parts

    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "analisa essa foto"},
                {"type": "image_url", "image_url": {"url": "data:..."}},
            ],
        },
        {"role": "user", "content": "plain string stays"},
    ]
    out = _flatten_content_parts(msgs)
    assert out[0]["content"] == "analisa essa foto\n\n[attachment]"
    assert out[1]["content"] == "plain string stays"


def test_flatten_parts_image_only_becomes_placeholder():
    from nexus.agent.llm.mlx_provider import _flatten_content_parts

    msgs = [
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:..."}}]},
        {"role": "user", "content": []},
    ]
    out = _flatten_content_parts(msgs)
    assert out[0]["content"] == "[attachment]"
    assert out[1]["content"] == ""


# ---------------------------------------------------------------------------
# Generation stall timeout (silent wedge in the worker thread)
# ---------------------------------------------------------------------------


def test_generation_stall_times_out(monkeypatch):
    """A worker that goes silent mid-generation must not hang the consumer.

    Real failure this pins: Nemotron on the daemon wedged inside
    stream_generate (no token, no end, no exception) and the turn showed
    "processing" forever with the generation lock held. The bridge now
    times out, unloads the model and raises a 504.
    """
    import asyncio
    import time as _time

    mlx_lm = pytest.importorskip("mlx_lm")
    from nexus.agent.llm.mlx_runtime import LoadedModel, MlxRuntime, reset_runtime

    class _Resp:
        def __init__(self, text, finish_reason=None):
            self.text = text
            self.finish_reason = finish_reason

    def wedged_generate(model, tokenizer, prompt, **kwargs):
        # 30 chars > _COALESCE_CHARS so the token actually flushes out
        yield _Resp("x" * 30)
        _time.sleep(1.0)  # silence far beyond the test's stall budget
        yield _Resp("", finish_reason="stop")

    monkeypatch.setattr(mlx_lm, "stream_generate", wedged_generate)

    async def scenario():
        runtime = MlxRuntime.get()
        loaded = LoadedModel(model_path="/fake/model", model=object(), tokenizer=object())
        chunks = []
        with pytest.raises(Exception) as excinfo:
            async for kind, payload in runtime._run_locked(
                loaded=loaded,
                prompt_tokens=[1, 2, 3],
                max_tokens=16,
                cache_key=None,
                sampler=None,
                stall_timeout=0.2,
            ):
                chunks.append((kind, payload))
        # the one token made it out before the stall tripped
        assert any(k == "text" for k, _ in chunks)
        assert "stalled" in str(excinfo.value)
        assert getattr(excinfo.value, "status_code", None) == 504
        # the lock must be free again
        assert not runtime._gen_lock.locked()

    asyncio.run(scenario())
    reset_runtime()


# ---------------------------------------------------------------------------
# Fallback tool-call parsing
# ---------------------------------------------------------------------------


def test_fallback_parses_single_object():
    out = parse_tool_calls_fallback('{"name": "vault_read", "arguments": {"path": "a.md"}}')
    assert out == [{"name": "vault_read", "arguments": {"path": "a.md"}}]


def test_fallback_parses_array():
    out = parse_tool_calls_fallback('[{"name":"a","arguments":{}},{"name":"b","arguments":{}}]')
    assert [c["name"] for c in out] == ["a", "b"]


def test_fallback_parses_openai_wrapper():
    out = parse_tool_calls_fallback('{"function": {"name": "f", "arguments": "{\\"x\\": 1}"}}')
    assert out == [{"name": "f", "arguments": {"x": 1}}]


def test_fallback_accepts_parameters_and_args_aliases():
    out = parse_tool_calls_fallback('{"name":"f","parameters":{"a":1}}')
    assert out[0]["arguments"] == {"a": 1}
    assert parse_tool_calls_fallback('{"name":"f","args":{"b":2}}')[0]["arguments"] == {"b": 2}


def test_fallback_rejects_unknown_names_when_tools_given():
    """A model merely *talking* about JSON must not produce a phantom call."""
    tools = [{"function": {"name": "real_tool"}}]
    assert parse_tool_calls_fallback('{"name": "not_a_tool", "arguments": {}}', tools) == []
    assert parse_tool_calls_fallback('{"name": "real_tool", "arguments": {}}', tools) != []


def test_fallback_returns_empty_on_garbage_never_raises():
    assert parse_tool_calls_fallback("this is not json at all") == []
    assert parse_tool_calls_fallback("") == []
    assert parse_tool_calls_fallback("{broken") == []


def test_fallback_missing_arguments_defaults_to_empty_dict():
    assert parse_tool_calls_fallback('{"name": "f"}') == [{"name": "f", "arguments": {}}]


# ---------------------------------------------------------------------------
# Memory sizing
# ---------------------------------------------------------------------------


def _write_snapshot(tmp_path, *, weight_bytes: int, config: dict) -> None:
    import json

    (tmp_path / "model.safetensors").write_bytes(b"\0" * weight_bytes)
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")


def test_estimate_zero_when_no_weights(tmp_path):
    assert estimate_resident_gb(tmp_path) == 0.0


def test_estimate_counts_weights_and_kv(tmp_path):
    _write_snapshot(
        tmp_path,
        weight_bytes=2_000_000_000,
        config={
            "num_hidden_layers": 32,
            "num_attention_heads": 32,
            "num_key_value_heads": 8,
            "hidden_size": 4096,
        },
    )
    small = estimate_resident_gb(tmp_path, context_tokens=1024)
    large = estimate_resident_gb(tmp_path, context_tokens=131072)
    assert small > 2.0  # weights + slack
    assert large > small  # KV scales with the window


def test_estimate_handles_missing_config(tmp_path):
    (tmp_path / "model.safetensors").write_bytes(b"\0" * 1_000_000_000)
    assert estimate_resident_gb(tmp_path) > 1.0  # weights + slack, KV unknown


def test_fits_rejects_oversized_model():
    ok, reason = fits_in_unified_memory(40.0, {"is_apple_silicon": True, "vram_gb": 36.0})
    assert ok is False
    assert "unified memory" in reason


def test_fits_accepts_model_within_budget():
    ok, reason = fits_in_unified_memory(8.0, {"is_apple_silicon": True, "vram_gb": 36.0})
    assert ok is True
    assert "8.0" in reason


def test_fits_leaves_headroom_for_the_os():
    """75% of unified memory is the budget, not 100%."""
    ok, _ = fits_in_unified_memory(30.0, {"is_apple_silicon": True, "vram_gb": 36.0})
    assert ok is False


def test_fits_is_permissive_off_apple_silicon():
    ok, reason = fits_in_unified_memory(8.0, {"is_apple_silicon": False, "ram_gb": 16.0})
    assert ok is True
    assert "not Apple Silicon" in reason


def test_fits_rejects_empty_snapshot():
    ok, reason = fits_in_unified_memory(0.0, {"is_apple_silicon": True, "vram_gb": 36.0})
    assert ok is False
    assert "no model weights" in reason
