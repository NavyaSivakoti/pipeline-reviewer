"""
notices.py — the "no review this run" notice marker. Standard library only:
post_review.py imports it in CI steps that don't install the reviewer's
requirements (e.g. marking a comment resolved).
"""

# Every "no review this run" notice starts with this, so the poster can tell a
# notice from a review and keep the previous review in the sticky comment.
NO_REVIEW_PREFIX = "_The AI Pipeline Reviewer could not produce"


def is_no_review(text: str) -> bool:
    return (text or "").lstrip().startswith(NO_REVIEW_PREFIX)
