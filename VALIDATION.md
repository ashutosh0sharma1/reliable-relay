# Validation

Release preparation: 2026-11-04. These are results from the clean source export on Windows; the workflow badge reports subsequent fresh Linux CI results.

## Verified

9 tests passed for durable intake, concurrent duplicates, retry timing, dead-letter transitions and replay.

## Limits

The sink is a simulated CRM. External webhook providers, signature verification and production scheduling are outside this demo.

Provider credentials were excluded from test environments. Test success does not establish production readiness or compatibility with every machine. Reproduce the documented checks after installing dependencies; report failures with synthetic fixtures and environment versions.
