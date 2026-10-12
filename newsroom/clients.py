"""Chat clients: Microsoft Foundry when configured, a deterministic scripted client otherwise (offline development, tests, cached replay).

    FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>
    FOUNDRY_MODEL=gpt-4.1-mini            # the model deployment name in the Foundry project

The scripted client answers from the FACTS block in the prompt with templates, so the whole pipeline (agents, workflow, fact-check,
feed) runs and is testable with no credentials. It is a stand-in for the model, not a second implementation of the agents.
"""
import json
import os
import re

from agent_framework import BaseChatClient, ChatResponse, Message

from newsroom.moments import template_line


def make_client():
    if os.environ.get("FOUNDRY_PROJECT_ENDPOINT"):
        from agent_framework_foundry import FoundryChatClient
        from azure.identity import DefaultAzureCredential
        return FoundryChatClient(project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"], model=os.environ.get("FOUNDRY_MODEL", "gpt-4.1-mini"), credential=DefaultAzureCredential())
    return ScriptedClient()


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
