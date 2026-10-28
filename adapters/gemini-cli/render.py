import json

from uni_learn.adapters import render_spec


def render(context, read_resource):
    spec = json.loads(read_resource("adapters/gemini-cli/adapter.json"))
    result = render_spec(context, read_resource, spec)
    for artifact in result["artifacts"]:
        if artifact["role"] == "command":
            artifact["content"] = (
                "description = " + json.dumps("Explicit Uni Learn command")
                + "\nprompt = " + json.dumps(artifact["content"], ensure_ascii=False)
                + "\n"
            )
    return result
