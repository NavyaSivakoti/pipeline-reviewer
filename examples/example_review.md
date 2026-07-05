# Example: a review the agent posted automatically

This is a real comment the **AI Pipeline Reviewer** posted on a **pull request** in
the [demo-app](https://github.com/NavyaSivakoti/demo-app), with no human involved.
On a PR it inspects the changed files (`get_pr_changes`), ties the failure to the
specific change, and **@-mentions the author** who opened the PR.

- The pull request: <https://github.com/NavyaSivakoti/demo-app/pull/2>
- The posted comment: <https://github.com/NavyaSivakoti/demo-app/pull/2#issuecomment-4885142136>
- The failed run: <https://github.com/NavyaSivakoti/demo-app/actions/runs/28732392827>

---

## 🤖 AI Pipeline Reviewer

cc @NavyaSivakoti — CI failed on your PR. Here's the review:

**Failure Type:** test_failure

**Key Evidence:**
- Test `test_login_valid_user` in `tests.test_auth.py` failed.
- `AssertionError: assert False is True` in `tests/test_auth.py:6`.
- The `login` function in `app/auth.py` intentionally returns `False`.

**Root Cause:** The `test_login_valid_user` failed because the `login` function in
`app/auth.py` intentionally returns `False` for valid credentials. This failure is
in code that CHANGED, as both `app/auth.py` and `tests/test_auth.py` were introduced
in this PR.

**Suggested Fix:** The `login` function in `app/auth.py` needs to return `True` for
valid credentials.

```diff
--- a/app/auth.py
+++ b/app/auth.py
@@ -6,5 +6,5 @@
     # BUG (on purpose): this always returns False, so even users with the
     # correct password are locked out. It makes test_login_valid_user fail,
     # giving the AI Pipeline Reviewer a fresh, different failure to review.
-    if _VALID_USERS.get(username) == 'correct-horse-battery':
-        return False  # should be True
+    if _VALID_USERS.get(username) == password:
+        return True  # Changed from False to True
     return False
```

**Security Flag:** none

**Recurrence:** first occurrence

**Confidence & Verify:** High / `pytest tests/test_auth.py::test_login_valid_user`
