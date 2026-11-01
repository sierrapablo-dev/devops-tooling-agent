"""Offline identifier validation (ADR-0006 Section 2, docs/python-package.md 2.1).

Credential-like sentinels are obviously fake and match no real token format.
"""

import pytest

from devops_tooling_agent.identifier import (
    IdentifierRejected,
    Repository,
    parse_identifier,
    parse_name_with_owner,
)

OWNER_39 = "a" * 39
REPO_100 = "r" * 100


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("octocat/Hello-World", "octocat/Hello-World"),
        ("https://github.com/octocat/Hello-World", "octocat/Hello-World"),
        ("https://github.com/octocat/Hello-World.git", "octocat/Hello-World"),
        ("git@github.com:octocat/Hello-World", "octocat/Hello-World"),
        ("git@github.com:octocat/Hello-World.git", "octocat/Hello-World"),
        # The short form keeps a .git suffix as part of the repository name.
        ("octocat/Hello-World.git", "octocat/Hello-World.git"),
        # Only one suffix is removed from the URL forms.
        ("https://github.com/o/r.git.git", "o/r.git"),
        ("a-b/c.d_e-f", "a-b/c.d_e-f"),
        ("A1/.github", "A1/.github"),
        ("o/...", "o/..."),
        ("o/_", "o/_"),
        (f"{OWNER_39}/{REPO_100}", f"{OWNER_39}/{REPO_100}"),
        (f"https://github.com/{OWNER_39}/{REPO_100}.git", f"{OWNER_39}/{REPO_100}"),
    ],
)
def test_accepted_forms(identifier: str, expected: str) -> None:
    assert parse_identifier(identifier).full_name == expected


def rejected(identifier: str) -> str:
    with pytest.raises(IdentifierRejected) as caught:
        parse_identifier(identifier)
    return caught.value.reason


@pytest.mark.parametrize(
    ("identifier", "reason"),
    [
        # format
        ("", "format"),
        ("octocat", "format"),
        ("-octocat/repo", "format"),
        ("octocat-/repo", "format"),
        ("octo--cat/repo", "format"),
        ("octocat/.", "format"),
        ("octocat/..", "format"),
        ("/repo", "format"),
        ("octocat/", "format"),
        ("https://github.com/octocat/.git", "format"),
        ("github.com:octocat/repo", "format"),
        ("git@github.com", "format"),
        # scheme
        ("http://github.com/octocat/repo", "scheme"),
        ("HTTPS://github.com/octocat/repo", "scheme"),
        ("ssh://git@github.com/octocat/repo", "scheme"),
        ("git://github.com/octocat/repo", "scheme"),
        ("file:///octocat/repo", "scheme"),
        # host
        ("https://GitHub.com/octocat/repo", "host"),
        ("https://www.github.com/octocat/repo", "host"),
        ("https://gitlab.com/octocat/repo", "host"),
        ("https://github.com.example/octocat/repo", "host"),
        ("git@gitlab.com:octocat/repo", "host"),
        ("git@GITHUB.COM:octocat/repo", "host"),
        # userinfo
        ("https://user@github.com/octocat/repo", "userinfo"),
        ("https://user:notarealsecret@github.com/octocat/repo", "userinfo"),
        ("https://github.com@example.com/octocat/repo", "userinfo"),
        ("FAKE-CREDENTIAL@github.com:octocat/repo", "userinfo"),
        ("git@x@github.com:octocat/repo", "userinfo"),
        # port
        ("https://github.com:443/octocat/repo", "port"),
        # query and fragment
        ("https://github.com/octocat/repo?tab=readme", "query"),
        ("https://github.com/octocat/repo#readme", "fragment"),
        ("https://github.com/octocat/repo#a?b", "fragment"),
        ("https://github.com/octocat/repo?a#b", "query"),
        ("octocat/repo?x", "query"),
        ("git@github.com:octocat/repo#x", "fragment"),
        # path
        ("https://github.com/octocat/repo/", "path"),
        ("https://github.com/octocat/repo/tree/main", "path"),
        ("https://github.com/octocat", "path"),
        ("https://github.com", "path"),
        ("https://github.com/", "path"),
        ("git@github.com:/octocat/repo", "path"),
        ("git@github.com:22/octocat/repo", "path"),
        ("octocat/repo/", "path"),
        ("octocat/repo/extra", "path"),
        ("github.com/octocat/repo", "path"),
        ("octocat/../repo", "path"),
        ("../octocat/repo", "path"),
        ("octocat/repo/..", "path"),
        # characters
        ("octocat/re po", "characters"),
        ("octocat/repo\n", "characters"),
        ("octocat/repo\x00", "characters"),
        ("octocat%2Frepo", "characters"),
        ("https://github.com/octocat/repo%2e", "characters"),
        ("octocat\\repo", "characters"),
        ("octo_cat/repo", "characters"),
        ("octocat/re+po", "characters"),
        ("octocat/répo", "characters"),
        ("https://gіthub.com/octocat/repo", "characters"),  # Cyrillic i
        ("octocat／repo", "characters"),  # fullwidth solidus
        ("octocat/repo‮", "characters"),  # right-to-left override
        ("octocat/re​po", "characters"),  # zero-width space
        # length
        ("a" * 257, "length"),
        (f"{'a' * 40}/repo", "length"),
        (f"octocat/{'r' * 101}", "length"),
    ],
)
def test_rejection_reasons(identifier: str, reason: str) -> None:
    assert rejected(identifier) == reason


@pytest.mark.parametrize(
    ("identifier", "reason"),
    [
        # length before characters
        ("é" * 300, "length"),
        # characters before any structural reason
        ("http://us er@gitlab.com/o/r", "characters"),
        # scheme before userinfo
        ("http://user:notarealsecret@github.com/o/r", "scheme"),
        # userinfo before port and host
        ("https://user:notarealsecret@example.com:8443/o/r", "userinfo"),
        # port before host
        ("https://example.com:8443/o/r", "port"),
        # host before query and path
        ("https://example.com/o/r/x?y", "host"),
        # query and fragment before path
        ("https://github.com/o/r/x?y", "query"),
        # path before component validation
        ("https://github.com/-bad/r/x", "path"),
        # owner before repository
        ("-bad/r..", "format"),
        ("o_o/" + "r" * 101, "characters"),
        # within a component: characters, then length, then format
        ("-" + "a" * 39 + "_/r", "characters"),
        ("-" + "a" * 39 + "/r", "length"),
    ],
)
def test_reason_precedence(identifier: str, reason: str) -> None:
    assert rejected(identifier) == reason


def test_rejection_carries_only_the_reason() -> None:
    sentinel = "https://NOT-A-REAL-CREDENTIAL-SENTINEL@github.com/o/r"
    with pytest.raises(IdentifierRejected) as caught:
        parse_identifier(sentinel)
    assert str(caught.value) == "userinfo"
    assert "SENTINEL" not in repr(caught.value.args)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("octocat/Hello-World", Repository("octocat", "Hello-World")),
        ("octocat/Hello-World.git", Repository("octocat", "Hello-World.git")),
        ("octocat", None),
        ("octocat/a/b", None),
        ("-octocat/a", None),
        ("octocat/..", None),
        ("octo cat/a", None),
        ("https://github.com/octocat/a", None),
        (f"{'a' * 40}/r", None),
    ],
)
def test_name_with_owner_grammar(text: str, expected: Repository | None) -> None:
    assert parse_name_with_owner(text) == expected
