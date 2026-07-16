"""Deliberately failing fixture for a fail-closed Feishu approval-card E2E.

This file exists only on the temporary test/approval-card-blocked-merge branch.
Its deterministic CI failure makes the PR impossible to merge through required
checks while exercising the approval-card callback and origin-session receipt.
Close the PR and delete its branch immediately after the E2E completes.
"""


def test_approval_card_e2e_fixture_intentionally_blocks_merge() -> None:
    assert False, (
        "INTENTIONAL E2E FIXTURE: this temporary PR must remain blocked by CI "
        "while the approval-card origin-session receipt path is tested."
    )
