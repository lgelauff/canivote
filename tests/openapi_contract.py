"""Validate a response body against canivote's OpenAPI document.

The document is the contract; this is the one place that reads it as one, so
`test_openapi_contract.py` and the rate-limit test can both hold their responses
to it without a second copy of the schema.

Not named `test_*`, so pytest does not try to collect it.
"""

import json
from pathlib import Path

from jsonschema import Draft202012Validator

import app as app_module

OPENAPI = json.loads(Path(app_module.__file__).with_name("openapi.json").read_text())


def validate(schema_name, instance):
    """Raise `jsonschema.ValidationError` if `instance` is not `schema_name`.

    The `$ref` is resolved against this wrapper, whose `components` sibling is
    the document's — so every nested reference resolves too.
    """
    schema = {
        "$ref": f"#/components/schemas/{schema_name}",
        "components": OPENAPI["components"],
    }
    Draft202012Validator(schema).validate(instance)
