"""Chat clients: Microsoft Foundry when configured, a deterministic scripted client otherwise (offline development, tests, cached replay).

    FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>
    FOUNDRY_MODEL=gpt-4.1-mini            # the model deployment name in the Foundry project

The scripted client answers from the FACTS block in the prompt with templates, so the whole pipeline (agents, workflow, fact-check,
feed) runs and is testable with no credentials. It is a stand-in for the model, not a second implementation of the agents.
"""
import functools
import json
import logging
import os
import re

from agent_framework import BaseChatClient, ChatResponse, Message

from newsroom.moments import template_line


def make_client(model=None):
    if os.environ.get("FOUNDRY_PROJECT_ENDPOINT"):
        from agent_framework_foundry import FoundryChatClient
        from azure.identity import DefaultAzureCredential
        cred = DefaultAzureCredential()
        _tracing(cred)
        client = FoundryChatClient(project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"], model=model or os.environ.get("FOUNDRY_MODEL", "gpt-4.1-mini"), credential=cred)
        client.client = client.client.with_options(timeout=45., max_retries=3)  # one stalled call (seen: 103 s) is retried, not waited out
        return client
    return ScriptedClient()


def strong(client):
    """The client for the agents whose judgement matters most (the writer and the fact-checkers): FOUNDRY_STRONG_MODEL when it is set
    (a larger deployment, e.g. gpt-4.1), else the same client. One per process."""
    model = os.environ.get("FOUNDRY_STRONG_MODEL")
    if not model or isinstance(client, ScriptedClient):
        return client
    global _strong
    if _strong is None:
        _strong = make_client(model)
    return _strong


_strong = None


@functools.cache
def _tracing(cred):
    """Send Agent Framework's traces (each agent run, model call and tool call) to the Application Insights connected to the Foundry
    project, where the portal's Tracing page shows them. Once per process; prompts and replies are not recorded. Off with TRACING=0."""
    if os.environ.get("TRACING") == "0":
        return
    try:
        from azure.ai.projects import AIProjectClient
        from azure.monitor.opentelemetry import configure_azure_monitor
        from agent_framework.observability import create_metric_views, create_resource, enable_instrumentation
        cs = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING") or AIProjectClient(endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"], credential=cred).telemetry.get_application_insights_connection_string()
        configure_azure_monitor(connection_string=cs, views=create_metric_views(), resource=create_resource(service_name="football-kaleidoscope"))
        enable_instrumentation(enable_sensitive_data=False)
    except Exception as ex:  # no Application Insights on the project, or the package is missing: run without traces
        logging.getLogger(__name__).warning("tracing off: %s", ex)


def traced(name):
    """One span around a newsroom job (a question, a moment), so its agent runs and tool calls sit under one trace."""
    from opentelemetry import trace
    tracer = trace.get_tracer("football-kaleidoscope")

    def deco(fn):
        @functools.wraps(fn)
        async def wrap(*a, **kw):
            with tracer.start_as_current_span(name):
                return await fn(*a, **kw)
        return wrap
    return deco


def _facts(messages):
    """The last FACTS: {...} JSON object anywhere in the conversation."""
    text = "\n".join(m.text or "" for m in messages)
    hits = re.findall(r"FACTS:\s*(\{.*?\})\s*(?:\n|$)", text, flags=re.S)
    return json.loads(hits[-1]) if hits else {}


def _role(messages, options):
    text = (options.get("instructions") or "") + "\n".join(m.text or "" for m in messages)  # the agent's instructions arrive in the options, not as a message
    r = re.findall(r"\[\[ROLE:(\w+)\]\]", text)
    return r[-1] if r else "commentator"


class ScriptedClient(BaseChatClient):
    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        f, role = _facts(messages), _role(messages, options)
        out = SCRIPT[role](f)

        async def go():
            return ChatResponse(messages=[Message("assistant", [out])])
        return go()


def _stats(f):
    s = f["stats"]
    return json.dumps([{"metric": k, "value": v} for k, v in list(s.items())[:3]])


def _tactics(f):
    return f["why"] or "Nothing in the build-up stands out tactically."


def _checker(f):
    return json.dumps({"approved": True, "issues": []})


SCRIPT = {"stats": _stats, "tactics": _tactics, "commentator": template_line, "fact_checker": _checker}
