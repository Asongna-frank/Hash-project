# tests/test_openapi_docs.py
"""
Guards the Swagger/OpenAPI documentation quality pass: realistic examples on
schemas and a summary + description on every endpoint. Metadata only — no DB,
no network.

Run:  pytest tests/test_openapi_docs.py -v
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture(scope="module")
def schema():
    return client.get("/openapi.json").json()


def test_openapi_has_real_examples(schema):
    dumped = str(schema)
    # Domain-accurate example values must be present (not "string"/0 placeholders)
    assert "+237679977660" in dumped
    assert "Maria Nkeng" in dumped
    assert "General Hospital Douala" in dumped


def test_patient_create_example_phone(schema):
    pc = schema["components"]["schemas"]["PatientCreate"]
    example = pc["examples"][0] if "examples" in pc else pc.get("example")
    assert example is not None, "PatientCreate must carry a whole-body example"
    assert example["phone"] == "+237679977660"
    assert example["name"] == "Maria Nkeng"


def test_every_endpoint_has_summary_and_description(schema):
    missing = []
    for path, methods in schema["paths"].items():
        for method, op in methods.items():
            if method not in ("get", "post", "patch", "put", "delete"):
                continue
            if not op.get("summary") or not op.get("description"):
                missing.append(f"{method.upper()} {path}")
    assert not missing, f"Endpoints missing summary/description: {missing}"


def test_no_placeholder_string_in_request_examples(schema):
    # whole-body request examples should not be the bare "string"/0 placeholders
    for name in ("PatientCreate", "HospitalCreate", "AppointmentCreate", "LoginRequest"):
        comp = schema["components"]["schemas"][name]
        example = (comp.get("examples") or [None])[0] or comp.get("example")
        assert example, f"{name} missing whole-body example"
        assert "string" not in example.values(), f"{name} still has a 'string' placeholder"
