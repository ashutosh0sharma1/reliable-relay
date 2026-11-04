# Contributing to Reliable Relay

Start with a small change that somebody can reproduce from a clean checkout. This is an experimental public source release; the README and VALIDATION.md describe the supported scope.

## Find the code

`relay.py` owns the SQLite queue and delivery state machine; `app.py` exposes the demonstration API and dashboard.

## Useful contributions

- Add a restart scenario at a delivery state transition and verify exactly which effects survive.
- Introduce a documented webhook-signature adapter with deterministic invalid-signature tests.
- Improve dead-letter diagnostics while keeping replay explicit and idempotent.

## Verify a change

Install the README dependencies, then run the relevant checks:

```sh
python -m unittest test_relay -v
```

Run only checks appropriate to your change. Do not turn a unit test into a live provider call, model download, deployment or mutation of a real account. Keep optional integration acceptance explicit and report skipped prerequisites.

## Submit a pull request

Explain the observed problem, the resulting behavior, and the evidence used to verify it. Include a small synthetic reproduction for a bug. Keep unrelated formatting out of the change. Preserve upstream copyright/license notices and identify any new dependency and its license. Contributions are made under the applicable repository/source-file license.

Never include credentials, signed URLs, personal recordings, private transcripts, customer data, production exports or unlicensed media. Security reports belong in GitHub's private vulnerability reporting channel when available; do not post secrets in a public issue.
