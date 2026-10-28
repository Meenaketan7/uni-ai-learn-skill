from uni_learn.adapters import render_spec


def render(context, read_resource):
    import json

    spec = json.loads(read_resource("adapters/claude-code/adapter.json"))
    return render_spec(context, read_resource, spec)
