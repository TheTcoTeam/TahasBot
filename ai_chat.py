"""AI chatbot with tool-calling into the bot's own systems.

Two free providers, tried in priority order so a shared bot gets more total
daily headroom than either alone: Gemini 2.6 Flash first (sharper reasoning,
250 req/day free), falling through to Groq's gpt-oss-120b (roughly 1000+
req/day free, still solid at tool-calling) once Gemini's quota is hit for the
day. The switch is silent -- the user never sees which model answered.

Tool schema is provider-agnostic (name, description, JSON-schema parameters);
each provider's request/response shape is converted at the call site.
"""
import os
import json
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Persistent HTTP session with connection pooling
_SESSION = requests.Session()
_retries = Retry(total=2, backoff_factor=0.3, status_forcelist=[502, 503, 504])
_adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=_retries)
_SESSION.mount("https://", _adapter)
_SESSION.mount("http://", _adapter)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GEMINI_MODEL = "gemini-3.1-flash-lite"  # faster + more generous free quota than 3.6-flash
GROQ_MODEL = "openai/gpt-oss-120b"

GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def _gemini_tools(tools):
    return [{"functionDeclarations": [
        {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}
        for t in tools
    ]}]


def _groq_tools(tools):
    return [{"type": "function", "function": {
        "name": t["name"], "description": t["description"], "parameters": t["parameters"]
    }} for t in tools]


def _to_gemini_contents(messages):
    """Our messages: [{'role': 'user'|'assistant'|'tool', 'content':..., ...}]"""
    contents = []
    for m in messages:
        if m["role"] == "user":
            contents.append({"role": "user", "parts": [{"text": m["content"]}]})
        elif m["role"] == "assistant":
            if m.get("tool_call"):
                fc_part = {"functionCall": {
                    "name": m["tool_call"]["name"], "args": m["tool_call"]["args"]
                }}
                # Gemini requires its own prior thought_signature echoed back on
                # any functionCall turn fed back into history, or it 400s.
                sig = m["tool_call"].get("thought_signature")
                if sig:
                    fc_part["thoughtSignature"] = sig
                contents.append({"role": "model", "parts": [fc_part]})
            else:
                contents.append({"role": "model", "parts": [{"text": m["content"]}]})
        elif m["role"] == "tool":
            contents.append({"role": "function", "parts": [{"functionResponse": {
                "name": m["name"], "response": {"result": m["content"]}
            }}]})
    return contents


def call_gemini(system_prompt, messages, tools):
    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": _to_gemini_contents(messages),
        "tools": _gemini_tools(tools),
        # Default 'thinking' mode on this model added 7-20s of latency even to
        # trivial replies (measured) -- low thinking keeps responses fast
        # while still being enough for tool-call reasoning.
        "generationConfig": {"thinkingConfig": {"thinkingLevel": "low"}},
    }
    r = _SESSION.post(GEMINI_URL, json=body, timeout=12)
    if r.status_code == 429:
        raise RateLimitError(f"gemini 429: {r.text[:200]}")
    r.raise_for_status()
    data = r.json()
    parts = data["candidates"][0]["content"]["parts"]
    for p in parts:
        if "functionCall" in p:
            return {"type": "tool_call", "name": p["functionCall"]["name"],
                    "args": p["functionCall"].get("args") or {},
                    "thought_signature": p.get("thoughtSignature")}
    text = "".join(p.get("text", "") for p in parts)
    return {"type": "text", "content": text}


def _to_groq_messages(system_prompt, messages):
    out = [{"role": "system", "content": system_prompt}]
    for i, m in enumerate(messages):
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            if m.get("tool_call"):
                # Gemini-originated tool_call entries have no "id" (Gemini
                # doesn't use one); synthesize one so history stays valid when
                # a conversation crosses providers mid-thread.
                tc_id = m["tool_call"].get("id") or f"call_{i}"
                out.append({"role": "assistant", "content": None, "tool_calls": [{
                    "id": tc_id, "type": "function",
                    "function": {"name": m["tool_call"]["name"], "arguments": json.dumps(m["tool_call"]["args"])}
                }]})
            else:
                out.append({"role": "assistant", "content": m["content"]})
        elif m["role"] == "tool":
            tc_id = m.get("tool_call_id") or f"call_{i-1}"
            out.append({"role": "tool", "tool_call_id": tc_id, "content": json.dumps(m["content"])})
    return out


def call_groq(system_prompt, messages, tools):
    body = {
        "model": GROQ_MODEL,
        "messages": _to_groq_messages(system_prompt, messages),
        "tools": _groq_tools(tools),
    }
    r = _SESSION.post(GROQ_URL, headers={"Authorization": f"Bearer {GROQ_API_KEY}"}, json=body, timeout=25)
    if r.status_code == 429:
        raise RateLimitError(f"groq 429: {r.text[:200]}")
    r.raise_for_status()
    data = r.json()
    msg = data["choices"][0]["message"]
    tcs = msg.get("tool_calls") or []
    if tcs:
        tc = tcs[0]
        return {"type": "tool_call", "id": tc["id"], "name": tc["function"]["name"],
                "args": json.loads(tc["function"]["arguments"] or "{}")}
    return {"type": "text", "content": msg.get("content") or ""}


class RateLimitError(Exception):
    pass


def ask_ai(system_prompt, messages, tools):
    """Gemini first, Groq on any rate-limit/quota failure. Returns
    (result_dict, provider_name)."""
    try:
        return call_gemini(system_prompt, messages, tools), "gemini"
    except RateLimitError as e:
        print(f"[ai_chat] Gemini quota hit, falling back to Groq: {e}", flush=True)
    except Exception as e:
        print(f"[ai_chat] Gemini error, falling back to Groq: {e}", flush=True)
    return call_groq(system_prompt, messages, tools), "groq"
