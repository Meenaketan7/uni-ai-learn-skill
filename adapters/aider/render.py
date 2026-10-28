from uni_learn.adapters import render_spec


def render(context, read_resource):
    import json

    spec = json.loads(read_resource("adapters/aider/adapter.json"))
    return render_spec(context, read_resource, spec)
