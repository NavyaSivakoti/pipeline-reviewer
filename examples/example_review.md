# Example: a review the agent posted automatically

This is a real comment the **AI Pipeline Reviewer** posted on a failing commit in
the [demo-app](https://github.com/NavyaSivakoti/demo-app), with no human involved.

- Failed workflow run: <https://github.com/NavyaSivakoti/demo-app/actions/runs/28689439589>
- The posted comment: <https://github.com/NavyaSivakoti/demo-app/commit/bc5816ff0d5d88efac0ccbe1904f2d1ee87bb2b8#commitcomment-191213335>

---

## 🤖 AI Pipeline Reviewer

**Failure Type:** test_failure

**Key Evidence:**
- `AssertionError: assert 'USD' == 'EUR'`
- `tests/test_payments.py:11`
- Failed test: `test_charge_eur`

**Root Cause:** The `create_charge` function returns 'USD' as the currency, even
when 'EUR' is passed as an argument.

**Responsible Team:** team-billing

**Suggested Fix:** Use the `currency` argument instead of defaulting to 'USD'.

```diff
 def create_charge(amount, currency):
-    return {"amount": amount, "currency": "USD"}
+    return {"amount": amount, "currency": currency}
```

**Security Flag:** none

**Confidence & Verify:** High — `pytest tests/test_payments.py::test_charge_eur`
