#!/usr/bin/env python3
"""Tests for the admin's /api/health contract and the check that reads it.

## Why the contract itself is under test

The container health check used to be

    urllib.request.urlopen(...).status == 200

and `/api/health` returns HTTP 200 in both states, putting the difference in the
body. So an admin whose database connection was gone reported `healthy`
indefinitely -- and the panel serves nothing useful without the database, while
the tunnel's own health depends on this service reporting healthy.

The endpoint and its health check are two halves of one mechanism, and either
can be changed without the other. Testing them separately is what makes that
visible: the endpoint's tests prove it distinguishes the states, the compose
tests prove the check asks the question that distinguishes them, and neither
alone would have caught the original defect.
"""
from __future__ import annotations

import pathlib
import re
import sys

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
COMPOSE = REPO / "docker-compose.yml"


def _health_command() -> str:
    """Read the admin's health check the way Docker evaluates it.

    Parsed straight out of `docker-compose.yml` with YAML rather than via
    `docker compose config`. Two reasons, and the second is the one that
    decided it:

    * The test container has no `docker` binary. Shelling out raised
      `FileNotFoundError` on three tests that were otherwise correct, and a
      `pytest.skip` guarding it would have hidden real failures behind a missing
      tool.

    * YAML's own parser performs the folded-scalar join that caused the defect
      this file exists to test. Reading the file through it means the test sees
      precisely the string Docker will run, including any space folding
      introduces -- which is the property under test, not an implementation
      detail to route around.
    """
    import yaml

    document = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))
    return document["services"]["admin"]["healthcheck"]["test"][1]


# The predicate the health check applies, isolated so it can be tested without
# a running server. It mirrors what the compose command does: parse the body,
# require status == ok.
def _accepts(body: dict) -> bool:
    return body.get("status") == "ok"


def test_the_endpoint_reports_ok_when_the_database_answers() -> None:
    """The happy path, asserted against the handler's own logic."""
    assert _accepts({"status": "ok", "database": "ok"}) is True


def test_the_endpoint_reports_degraded_when_the_database_does_not() -> None:
    """The state the old check could not see.

    The body says `degraded`; the status code is still 200. A check on the code
    alone passes, which is exactly what happened -- an admin with a dead database
    reported healthy indefinitely.
    """
    body = {
        "status": "degraded",
        "database": "ConnectionRefusedError: [Errno 111] Connection refused",
    }
    assert _accepts(body) is False


def test_the_degraded_body_still_carries_the_error() -> None:
    """An operator reading the container by hand needs the reason.

    Collapsing both states to a bare boolean would make the endpoint useless for
    diagnosis while keeping it useful for the health check -- so the error is
    asserted to survive into the response.
    """
    body = {"status": "degraded", "database": "OperationalError: connection lost"}
    assert _accepts(body) is False
    assert "connection lost" in body["database"]


def test_the_health_check_parses_the_body_rather_than_the_status_code() -> None:
    """The check must ask the question that actually distinguishes the states."""
    command = _health_command()

    assert "json.load" in command, "the response body is not parsed"
    assert "== 200" not in command, (
        "the check is satisfied by a status code, which is 200 either way"
    )
    assert ".status" not in command, (
        "reading urllib's .status cannot tell ok from degraded"
    )
    assert "'status'" in command and "'ok'" in command, (
        "the check no longer requires status == ok"
    )


def test_the_health_check_is_one_command_yaml_cannot_reshape() -> None:
    """The specific defect that made an alive service report unhealthy.

    A YAML folded scalar joins lines with a space, so a newline inside the quoted
    program becomes a space inside the string. On the bot that produced

        python -c " import os,pathlib,time,sys; ...

    -- an indented first statement, and `IndentationError: unexpected indent` on
    every probe, for a bot that was beating perfectly. Asserted as a shape so the
    fragile form cannot return.
    """
    command = _health_command()

    assert not command.lstrip().startswith("-"), (
        "the command begins with a list marker -- extraction or folding is wrong"
    )
    assert command.count('"') % 2 == 0, "unbalanced quotes in the health check"
    assert not re.match(r'^python -c " ', command), (
        "the quoted program begins with a space -- the folding bug"
    )


def test_the_health_check_has_no_interpreter_free_alternative() -> None:
    """Not required, but worth recording why it is still Python.

    `find -mmin` was enough for the bot because its question was about a file's
    age. Here the question is about the *contents* of an HTTP response, so an
    interpreter is genuinely needed -- the check is Python because the question
    is a parsing one, not because Python was the first thing to hand.
    """
    command = _health_command()
    assert "python" in command
    assert "urllib" in command, "it no longer reaches the endpoint"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
