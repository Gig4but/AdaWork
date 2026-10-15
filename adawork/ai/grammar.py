"""JSON schema -> llama.cpp GBNF grammar that allows no whitespace.

llama-server's own conversion lets the model pretty-print (newlines and indentation), which
costs ~20 % more output tokens, and every output token is a slow decoding step on a laptop.
Only the small schema subset the app uses is supported: an object with fixed keys (in schema
order) whose values are booleans, enums, short strings, integers, arrays of enums or short
strings (with a minimum length, so a small model cannot dodge with []), or any of these or null.
A string with "x-cites" must end with the numbers of its sources: "Dobrý kolektiv [1,3]".
"""

import json

_CHAR = r'[^"\\\x7F\x00-\x1F] | "\\" ["\\/bfnrt]'


def _literal(value) -> str:
    """A JSON value as a GBNF string literal."""
    text = json.dumps(value, ensure_ascii=False)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


class _Builder:
    def __init__(self) -> None:
        self.rules: dict[str, str] = {}

    def add(self, name: str, body: str) -> str:
        self.rules[name] = body
        return name

    def value(self, name: str, spec: dict) -> str:
        if "enum" in spec:
            return self.add(name, " | ".join(_literal(v) for v in spec["enum"]))
        kinds = spec.get("type")
        kinds = kinds if isinstance(kinds, list) else [kinds]
        nullable = "null" in kinds
        kind = next(k for k in kinds if k != "null")
        if kind == "boolean":
            body = '"true" | "false"'
        elif kind == "integer":
            body = '"-"? ("0" | [1-9] [0-9]{0,8})'  # no leading zeros: JSON forbids them
        elif kind == "string":
            length = spec.get("maxLength", 200)
            if spec.get("x-cites"):
                cites = '" [" [1-9] [0-9]? ( "," [1-9] [0-9]? ){0,3} "]"'
                body = f'"\\"" char{{1,{length}}} {cites} "\\""'
            else:
                body = f'"\\"" char{{0,{length}}} "\\""'
            self.rules["char"] = _CHAR
        elif kind == "array":
            item = self.value(f"{name}-item", spec["items"])
            more = spec.get("maxItems", 10) - 1
            if spec.get("minItems", 0) > 0:
                least = spec["minItems"] - 1
                body = f'"[" {item} ( "," {item} ){{{least},{more}}} "]"'
            else:
                body = f'"[" ( {item} ( "," {item} ){{0,{more}}} )? "]"'
        else:
            raise ValueError(f"unsupported schema type {kind!r}")
        return self.add(name, f'{body} | "null"' if nullable else body)


def compact_grammar(schema: dict) -> str:
    builder = _Builder()
    parts = []
    for i, (key, spec) in enumerate(schema["properties"].items()):
        rule = builder.value(f"v{i}", spec)
        parts.append(f'{_literal(key)} ":" {rule}')
    builder.rules = {"root": '"{" ' + ' "," '.join(parts) + ' "}"', **builder.rules}
    return "\n".join(f"{name} ::= {body}" for name, body in builder.rules.items()) + "\n"
