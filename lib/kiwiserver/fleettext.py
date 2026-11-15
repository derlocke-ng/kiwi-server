"""One value of the fleet file changed in its text, so that the comments around it stay.

A command that changes one thing (who may reach what, a hidden service) should not write the
whole file anew: a fleet file kept by hand is full of comments. `put` replaces or inserts the
lines of one key under a path and proves the result by reading it back: when the text is
laid out in a way this does not follow, it returns None and the caller writes the file from
its data (`form.Doc.dump`, comments lost).
"""
import copy

import yaml

from .access import _block


def _flow(value):
    # a plain word comes with YAML's end-of-document mark on a line of its own
    return yaml.safe_dump(value, default_flow_style=True, width=1000, sort_keys=False).replace("\n...", "").strip()


def _lines(key, value, pad):
    """A key and its value: a mapping one level deep as a block, the rest on one line."""
    if isinstance(value, dict) and value:
        return [pad + "%s:" % key] + ["%s  %s: %s" % (pad, k, _flow(v)) for k, v in value.items()]
    return ["%s%s: %s" % (pad, key, _flow(value))]


def put(text, path, value):
    """`text` with the key at `path` (a list of keys) set to `value`, or None when this
    cannot be done in the text. The parent of the key must be a block that is there."""
    try:
        want = copy.deepcopy(yaml.safe_load(text))
        d = want
        for k in path[:-1]:
            d = d[k]
        if not isinstance(d, dict):
            return None
        d[path[-1]] = value
    except (yaml.YAMLError, KeyError, TypeError):
        return None
    lines = text.split("\n")
    at = _block(lines, list(path))
    if at:
        start, end, indent = at
        new = _lines(path[-1], value, " " * indent)
        head = lines[start]
        if "#" in head and len(new) == 1 and end == start + 1:      # a comment on its line stays there
            new[0] += "   " + head[head.index("#"):]
        out = lines[:start] + new + lines[end:]
    else:
        parent = _block(lines, list(path[:-1]))
        if not parent or lines[parent[0]].split("#")[0].rstrip().split(":", 1)[1].strip():
            return None             # no such block, or the parent is written on one line
        start, end, indent = parent
        child = next((len(x) - len(x.lstrip()) for x in lines[start + 1:end] if x.strip() and not x.lstrip().startswith("#")),
                     indent + 2)
        out = lines[:end] + _lines(path[-1], value, " " * child) + lines[end:]
    out = "\n".join(out)
    try:
        return out if yaml.safe_load(out) == want else None
    except yaml.YAMLError:
        return None
