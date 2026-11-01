"""``repo resolve`` outcomes from recorded runner results (no network, gh, or git)."""

from typing import Any

import pytest

from conftest import (
    NETWORK_STDERR,
    NO_AUTH_STDERR,
    NOT_FOUND_STDERR,
    PUBLIC_VIEW,
    VERSION_OK,
    FakeClock,
    InvokeFn,
    resolve_steps,
    result,
    view,
)
from devops_tooling_agent.constants import (
    GH_CALL_TIMEOUT_SECONDS,
    GH_MAX_OUTPUT_BYTES,
    RESOLVE_DEADLINE_SECONDS,
)
from devops_tooling_agent.errors import Interrupted
from devops_tooling_agent.resolve import valid_branch_name

ARGS = ["repo", "resolve", "octocat/Hello-World"]


def error_of(envelope: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    assert envelope["status"] == "error"
    assert envelope["result"] is None
    return envelope["error"]["code"], envelope["error"]["details"]


def test_public_control(invoke: InvokeFn) -> None:
    run = invoke(ARGS, resolve_steps(view()))
    assert run.exit_code == 0
    envelope = run.envelope
    assert envelope["status"] == "ok"
    assert envelope["untrusted"] == []
    assert envelope["result"] == {
        "input": "octocat/Hello-World",
        "name_with_owner": "octocat/Hello-World",
        "node_id": "MDEwOlJlcG9zaXRvcnkxMjk2MjY5",
        "url": "https://github.com/octocat/Hello-World",
        "visibility": "public",
        "default_branch": "master",
        "viewer_permission": "read",
        "archived": False,
        "empty": False,
        "disk_usage_kib": 1,
        "input_matches_canonical": True,
    }
    assert list(envelope["result"]) == list(
        [
            "input",
            "name_with_owner",
            "node_id",
            "url",
            "visibility",
            "default_branch",
            "viewer_permission",
            "archived",
            "empty",
            "disk_usage_kib",
            "input_matches_canonical",
        ]
    )


@pytest.mark.parametrize(
    "identifier",
    [
        "octocat/hello-world",
        "Octocat/Hello-World",
        "octocat/Hello-World.git",
    ],
)
def test_input_matches_canonical_is_exact(invoke: InvokeFn, identifier: str) -> None:
    run = invoke(["repo", "resolve", identifier], resolve_steps(view()))
    assert run.envelope["result"]["input"] == identifier
    assert run.envelope["result"]["input_matches_canonical"] is False


def test_url_form_matches_canonical(invoke: InvokeFn) -> None:
    run = invoke(
        ["repo", "resolve", "git@github.com:octocat/Hello-World.git"], resolve_steps(view())
    )
    assert run.envelope["result"]["input_matches_canonical"] is True


# Exact argument vectors -----------------------------------------------------


def test_argument_vectors_are_fixed(invoke: InvokeFn, gh_dir: Any) -> None:
    run = invoke(
        ["repo", "resolve", "https://github.com/octocat/Hello-World.git"], resolve_steps(view())
    )
    gh = str(next(gh_dir.iterdir()))
    assert [call.argv for call in run.runner.calls] == [
        [gh, "--version"],
        [gh, "auth", "status", "--hostname", "github.com"],
        [
            gh,
            "repo",
            "view",
            "github.com/octocat/Hello-World",
            "--json",
            "defaultBranchRef,diskUsage,id,isArchived,isEmpty,nameWithOwner,url,"
            "viewerPermission,visibility",
        ],
    ]
    for call in run.runner.calls:
        assert call.cwd == gh_dir
        assert call.max_output_bytes == GH_MAX_OUTPUT_BYTES
        joined = " ".join(call.argv)
        assert "token" not in joined
        assert "--show-token" not in joined and "-t" not in call.argv


def test_rejected_identifier_runs_nothing(invoke: InvokeFn) -> None:
    run = invoke(["repo", "resolve", "https://NOT-A-REAL-CREDENTIAL@github.com/o/r"])
    assert error_of(run.envelope) == ("INVALID_IDENTIFIER", {"reason": "userinfo"})
    assert run.runner.calls == []
    assert b"NOT-A-REAL-CREDENTIAL" not in run.raw


# Environment and version ------------------------------------------------------


def test_gh_not_found(invoke: InvokeFn) -> None:
    run = invoke(ARGS, env={"PATH": ""})
    assert error_of(run.envelope) == (
        "ENVIRONMENT_ERROR",
        {"reason": "not_found", "tool": "gh", "minimum_version": "2.40.0"},
    )
    assert run.runner.calls == []


@pytest.mark.parametrize(
    ("step", "reason"),
    [
        (result(0, b"gh version 2.39.9 (2023-01-01)\n"), "version_unsupported"),
        (result(0, b"gh version 1.99.0\n"), "version_unsupported"),
        (result(0, b"something else\n"), "version_unknown"),
        (result(0, b""), "version_unknown"),
        (result(0, b"gh version 2.92\n"), "version_unknown"),
        (result(1, VERSION_OK), "version_unknown"),
    ],
)
def test_version_check(invoke: InvokeFn, step: Any, reason: str) -> None:
    run = invoke(ARGS, [step])
    assert error_of(run.envelope) == (
        "ENVIRONMENT_ERROR",
        {"reason": reason, "tool": "gh", "minimum_version": "2.40.0"},
    )
    assert len(run.runner.calls) == 1


@pytest.mark.parametrize(
    "stdout",
    [
        b"gh version 2.40.0 (2023-12-07)\n",
        b"gh version 2.40.0-rc.1 (2023-12-01)\n",
        b"gh version 3.0.0\n",
        VERSION_OK.replace(b"\n", b"\r\n"),
    ],
)
def test_supported_versions(invoke: InvokeFn, stdout: bytes) -> None:
    run = invoke(ARGS, [result(0, stdout), result(0), view()])
    assert run.exit_code == 0


# Verified gh outcomes ---------------------------------------------------------


def test_auth_status_failure_is_auth_required(invoke: InvokeFn) -> None:
    # Missing authentication and a network failure both exit with 1 (8.1.9).
    run = invoke(ARGS, [result(0, VERSION_OK), result(1, b"", b"ignored output")])
    assert error_of(run.envelope) == ("AUTH_REQUIRED", {})
    assert len(run.runner.calls) == 2
    assert b"ignored output" not in run.raw


def test_repo_view_not_found(invoke: InvokeFn) -> None:
    run = invoke(ARGS, resolve_steps(result(1, b"", NOT_FOUND_STDERR)))
    assert error_of(run.envelope) == ("NOT_FOUND_OR_INACCESSIBLE", {})
    assert b"missing-repo" not in run.raw


def test_repo_view_auth_exit_code(invoke: InvokeFn) -> None:
    run = invoke(ARGS, resolve_steps(result(4, b"", NO_AUTH_STDERR)))
    assert error_of(run.envelope) == ("AUTH_REQUIRED", {})


def test_repo_view_network_failure(invoke: InvokeFn) -> None:
    run = invoke(ARGS, resolve_steps(result(1, b"", NETWORK_STDERR)))
    assert error_of(run.envelope) == (
        "PROVIDER_ERROR",
        {"stage": "gh_repo_view", "exit_code": 1},
    )
    assert b"proxyconnect" not in run.raw


def test_not_found_fragment_on_stdout_is_ignored(invoke: InvokeFn) -> None:
    run = invoke(ARGS, resolve_steps(result(1, NOT_FOUND_STDERR, b"")))
    assert error_of(run.envelope)[0] == "PROVIDER_ERROR"


def test_unclassified_exit_code(invoke: InvokeFn) -> None:
    run = invoke(ARGS, resolve_steps(result(2, b"", b"")))
    assert error_of(run.envelope) == ("PROVIDER_ERROR", {"stage": "gh_repo_view", "exit_code": 2})


# Provider value validation ------------------------------------------------------


def provider_error() -> tuple[str, dict[str, Any]]:
    return ("PROVIDER_ERROR", {"stage": "gh_repo_view", "exit_code": 0})


@pytest.mark.parametrize(
    "changes",
    [
        {"nameWithOwner": "octocat/Hello World"},
        {"nameWithOwner": "octocat/a/b", "url": "https://github.com/octocat/a/b"},
        {"nameWithOwner": 7},
        {"url": "https://github.com/octocat/other"},
        {"url": "http://github.com/octocat/Hello-World"},
        {"url": "https://github.com/octocat/Hello-World/"},
        {"id": "not a node id"},
        {"id": "R_"},
        {"id": "X_kgDOabc"},
        {"id": "R_" + "a" * 97},
        {"id": None},
        {"visibility": "public"},
        {"visibility": "SECRET"},
        {"viewerPermission": "OWNER"},
        {"viewerPermission": "read"},
        {"isArchived": "false"},
        {"isEmpty": 0},
        {"diskUsage": -1},
        {"diskUsage": True},
        {"diskUsage": 1.5},
        {"diskUsage": 2**63},
        {"diskUsage": "1"},
        {"defaultBranchRef": "master"},
        {"defaultBranchRef": {"name": "master", "target": "x"}},
        {"defaultBranchRef": {"name": 1}},
    ],
)
def test_invalid_provider_values(invoke: InvokeFn, changes: dict[str, Any]) -> None:
    run = invoke(ARGS, resolve_steps(view(**changes)))
    assert error_of(run.envelope) == provider_error()


@pytest.mark.parametrize(
    "stdout",
    [
        b"",
        b"not json",
        b"[]",
        b"\xff\xfe",
        b"[" * 100000,
        b'{"nameWithOwner": "octocat/Hello-World"}',
    ],
)
def test_malformed_provider_output(invoke: InvokeFn, stdout: bytes) -> None:
    run = invoke(ARGS, resolve_steps(result(0, stdout)))
    assert error_of(run.envelope) == provider_error()


def test_extra_provider_field(invoke: InvokeFn) -> None:
    run = invoke(ARGS, resolve_steps(view(extra="x")))
    assert error_of(run.envelope) == provider_error()


def test_truncated_provider_output(invoke: InvokeFn) -> None:
    complete = view().stdout
    run = invoke(ARGS, resolve_steps(result(0, complete, stdout_truncated=True)))
    assert error_of(run.envelope) == provider_error()


def test_vocabularies(invoke: InvokeFn) -> None:
    for raw, mapped in [("PRIVATE", "private"), ("INTERNAL", "internal")]:
        run = invoke(ARGS, resolve_steps(view(visibility=raw)))
        assert run.envelope["result"]["visibility"] == mapped
    for raw, mapped_permission in [
        ("ADMIN", "admin"),
        ("MAINTAIN", "maintain"),
        ("WRITE", "write"),
        ("TRIAGE", "triage"),
        ("", None),
        (None, None),
    ]:
        run = invoke(ARGS, resolve_steps(view(viewerPermission=raw)))
        assert run.envelope["result"]["viewer_permission"] == mapped_permission


def test_new_format_node_id(invoke: InvokeFn) -> None:
    run = invoke(ARGS, resolve_steps(view(id="R_kgDOAbCd-_12")))
    assert run.envelope["result"]["node_id"] == "R_kgDOAbCd-_12"


def test_large_disk_usage(invoke: InvokeFn) -> None:
    run = invoke(ARGS, resolve_steps(view(diskUsage=2**63 - 1, isArchived=True)))
    assert run.envelope["result"]["disk_usage_kib"] == 2**63 - 1
    assert run.envelope["result"]["archived"] is True


# Default branch matrix (docs/python-package.md, Section 2.1) ------------------


@pytest.mark.parametrize(
    ("is_empty", "branch", "outcome"),
    [
        (False, {"name": "main"}, "main"),
        (False, {"name": "release/1.x"}, "release/1.x"),
        (False, {"name": ""}, "PROVIDER_ERROR"),
        (False, None, "PROVIDER_ERROR"),
        (False, {"name": "bad..name"}, "UNSUPPORTED_REPOSITORY"),
        (False, {"name": "-option"}, "UNSUPPORTED_REPOSITORY"),
        (True, {"name": ""}, None),
        (True, None, None),
        (True, {"name": "main"}, "PROVIDER_ERROR"),
        (True, {"name": "bad..name"}, "PROVIDER_ERROR"),
    ],
)
def test_default_branch_matrix(
    invoke: InvokeFn, is_empty: bool, branch: Any, outcome: str | None
) -> None:
    run = invoke(ARGS, resolve_steps(view(isEmpty=is_empty, defaultBranchRef=branch)))
    envelope = run.envelope
    if outcome == "PROVIDER_ERROR":
        assert error_of(envelope) == provider_error()
    elif outcome == "UNSUPPORTED_REPOSITORY":
        assert error_of(envelope) == ("UNSUPPORTED_REPOSITORY", {"reason": "default_branch_name"})
        assert b"option" not in run.raw and b"bad.." not in run.raw
    else:
        assert envelope["result"]["default_branch"] == outcome
        assert envelope["result"]["empty"] is is_empty


@pytest.mark.parametrize(
    "name",
    ["main", "master", "dev_1", "feature/x-y", "v1.2", "a/b/c", "x" * 255],
)
def test_valid_branch_names(name: str) -> None:
    assert valid_branch_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "",
        "HEAD",
        "-main",
        ".hidden",
        "a/.b",
        "a..b",
        "a.",
        "a.lock",
        "a/b.lock",
        "a//b",
        "/a",
        "a/",
        "a b",
        "a@{1}",
        "a~1",
        "a^",
        "a:b",
        "a?",
        "a*",
        "a[b",
        "a\\b",
        "résumé",
        "x" * 256,
    ],
)
def test_invalid_branch_names(name: str) -> None:
    assert not valid_branch_name(name)


# Deadline, timeouts, and interruption -------------------------------------------


def test_call_timeout_is_bounded_by_call_limit(invoke: InvokeFn) -> None:
    run = invoke(ARGS, resolve_steps(view()))
    assert [call.timeout for call in run.runner.calls] == [GH_CALL_TIMEOUT_SECONDS] * 3


def test_call_timeout_is_bounded_by_remaining_deadline(invoke: InvokeFn) -> None:
    clock = FakeClock()

    def slow_version() -> Any:
        clock.now += RESOLVE_DEADLINE_SECONDS - 10
        return result(0, VERSION_OK)

    run = invoke(ARGS, [slow_version, result(0), view()], clock=clock)
    assert run.exit_code == 0
    assert run.runner.calls[1].timeout == pytest.approx(10)


def test_deadline_expired_before_a_call(invoke: InvokeFn) -> None:
    clock = FakeClock()

    def slow_auth() -> Any:
        clock.now += RESOLVE_DEADLINE_SECONDS
        return result(0)

    run = invoke(ARGS, [result(0, VERSION_OK), slow_auth], clock=clock)
    assert error_of(run.envelope) == ("TIMEOUT", {"stage": "gh_repo_view"})
    assert len(run.runner.calls) == 2


@pytest.mark.parametrize(
    ("steps", "stage"),
    [
        ([result(None, timed_out=True)], "gh_version"),
        ([result(0, VERSION_OK), result(None, timed_out=True)], "gh_auth_status"),
        (resolve_steps(result(None, timed_out=True)), "gh_repo_view"),
    ],
)
def test_runner_timeout(invoke: InvokeFn, steps: list[Any], stage: str) -> None:
    run = invoke(ARGS, steps)
    assert run.exit_code == 1
    assert error_of(run.envelope) == ("TIMEOUT", {"stage": stage})


@pytest.mark.parametrize(
    ("steps", "stage"),
    [
        ([Interrupted()], "gh_version"),
        ([result(0, VERSION_OK), KeyboardInterrupt()], "gh_auth_status"),
        (resolve_steps(Interrupted()), "gh_repo_view"),
    ],
)
def test_interrupted(invoke: InvokeFn, steps: list[Any], stage: str) -> None:
    run = invoke(ARGS, steps)
    assert run.exit_code == 130
    assert error_of(run.envelope) == ("INTERRUPTED", {"stage": stage})


def test_unexpected_exception_is_internal_error(invoke: InvokeFn) -> None:
    run = invoke(ARGS, [RuntimeError("SENSITIVE-EXCEPTION-TEXT")])
    assert run.exit_code == 1
    assert error_of(run.envelope) == ("INTERNAL_ERROR", {})
    assert b"SENSITIVE" not in run.raw
    assert b"Traceback" not in run.raw


def test_public_view_fixture_is_consistent() -> None:
    assert PUBLIC_VIEW["url"] == "https://github.com/" + str(PUBLIC_VIEW["nameWithOwner"])
