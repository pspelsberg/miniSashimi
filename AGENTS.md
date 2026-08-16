# miniSashimi Engineering Guide

## Validation
- Python: `python -m pytest -q`
- Legacy smoke suite: `.venv/bin/python tests/run_tests.py`
- Dashboard: `cd dashboard && npm test && npm run lint && npm run build`
- Dependency scan: `.venv/bin/pip-audit -r requirements.txt`

## Architecture boundaries
- Keep inbound adapters (`app/adapters/inbound`) independent from outbound adapters.
- Put shared policies and state in `app/core` or `app/middleware`.
- New use cases belong in isolated `app/features/<feature>` slices.
- A feature must not import another feature's internals; duplicate small DTOs instead.
- Add a deterministic architecture test whenever a boundary is introduced.

## Security rules
- All external input is bounded and validated at the boundary.
- Never execute model- or user-controlled code outside the approved bwrap sandbox.
- Outbound URLs require provider allowlisting and SSRF checks.
- Do not log tokens, API keys, prompts, PII, or provider response bodies.
- Pin GitHub Actions to immutable commit SHAs.
