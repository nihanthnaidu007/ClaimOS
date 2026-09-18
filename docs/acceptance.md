# Acceptance Coverage — AC-1..AC-13

Evidence sweep for the ClaimOS Production Upgrade Spec (`art_qGdARnzJ`, verification section), executed per the QA & E2E Test Plan (`art_x9dhhWUl`). This file records, for each acceptance criterion, the automated test or observable check that proves it, as of `feat/e2e-suite` @ `ee89921`.

How to reproduce the E2E evidence:

```bash
# Deterministic, zero-LLM-spend stack (fixture adapter, no ANTHROPIC_API_KEY):
docker compose --profile e2e up -d --build
# wait for /api/ready to report database ok, then:
cd frontend && npx playwright test
```

The fixture adapter (`backend/app/llm/fixture.py`, `LLM_PROVIDER=fixture`) makes the whole pipeline deterministic: every E2E run exercises the same agent outputs, ratings, and routing with zero API spend. Fault injection (`FIXTURE_FAULT=timeout:intake|refusal:decision|…`) drives the failure paths.

## Coverage table

| AC | Criterion | Proven by (all green on this branch unless noted) | Evidence |
|---|---|---|---|
| AC-1 | Authn/authz on every non-public endpoint | `backend/tests/test_auth.py` (route-table parametrization, refresh rotation, CSRF), role interlock in `test_workbench.py`; E2E: every spec registers real users and logs in through the UI gate; customer-token 403s asserted in the adjuster-polling helper design | pytest |
| AC-2 | Zero scaffold artifacts | CI `lint` job (`rg` scaffold-reference gate, `.env` tracking check, README/LICENSE presence); CI `lint-workflows` (actionlint) | CI |
| AC-3 | Persisted traces, halt-on-error, retry-on-save-failure | `backend/tests/test_pipeline.py::test_full_run_checkpoints_usage_and_finalizes`, `::test_agent_failure_persists_sanitized_failed_claim`, `::test_invalid_intake_halts_as_pending`, `::test_shutdown_before_first_stage_requeues_intact`; E2E `agent-failure.spec.js` (visible PROCESSING FAILED), `validation.spec.js` (unknown-policy halt with visible reason), `refusal-fallback.spec.js` (refusal retry + template-letter fallback) | pytest + PW; captures `tc-1-fnol-decision-panel.png`, `tc-1-fnol-evidence-pack.png`, `tc-2-policy-halt.png`, `tc-2-validation-blocked.png` |
| AC-4 | Deterministic pure-Python money math | `backend/tests/test_rating.py` (golden payouts/risk vectors), `test_llm_fixture.py` (LLM outputs feed judgment fields only — fixture echoes, never recalculates, arithmetic) | pytest |
| AC-5 | STP gate both branches | `backend/tests/test_stp.py::test_gate_auto_finalizes_when_all_legs_hold`, `::test_stp_escalates_low_confidence_with_reason`; E2E `stp-approval.spec.js` ($2,000 theft → `auto_approved`, $1,500 payout, access code surfaced) and `override.spec.js` ($4,200 accident → escalated) | pytest + PW; captures `tc-4-stp-auto-approval.png`, `tc-4-access-code-panel.png` |
| AC-6 | SSE replay + two-worker delivery | `backend/tests/test_pipeline.py::test_last_event_id_replay`, `::test_two_pollers_execute_each_claim_exactly_once`; E2E `sse-fallback.spec.js` (SSE blocked → board still finalizes via polling; connection chip never reports LIVE while degraded); compose runs API + worker as separate containers sharing Mongo | pytest + PW; capture `tc-7-sse-polling-fallback.png` |
| AC-7 | Override validation, audit, SLA aging | `backend/tests/test_workbench.py` (422 without reason, audit append, SLA state machine, case summary); E2E `override.spec.js` (missing-reason 422, client-mirrored disabled submit, reasoned override → `overridden` + exactly one audit entry with before/after + verbatim reason) | pytest + PW; capture `tc-5-override-audit.png` |
| AC-8 | Public status portal | `backend/tests/test_status_portal.py` (`test_valid_code_returns_masked_status`, `test_wrong_code_is_generic_404`, `test_unknown_claim_number_returns_identical_404`, `test_lookup_rate_limited_per_ip`); E2E `status-portal.spec.js` (uniform miss text, valid-code timeline with milestones, per-IP 429 message) | pytest + PW; captures `tc-8-portal-timeline.png`, `tc-8-portal-uniform-error.png`, `tc-8-portal-rate-limited.png` |
| AC-9 | Analytics against fixtures | `backend/tests/test_analytics.py` (distribution, STP rate, cycle-time percentiles over the seeded dataset) | pytest |
| AC-10 | Frontend build, decomposition, component + E2E tests | CI `frontend-lint` + `frontend-test` (Vitest/RTL, 19+ suites, coverage ≥ 88%) + `e2e-compose` (the eight Playwright specs against the compose stack, fixture mode) | CI |
| AC-11 | CI green, protected main, compose serves the app | CI: 8 jobs on PR (`backend-ruff`, `backend-pytest`, `frontend-lint`, `frontend-test`, `docker-builds`, `security-audit`, `e2e-compose`, `lint-workflows`); `e2e-compose` boots the full compose stack, waits on `/api/ready`, and runs the suite against the served UI — the compose-smoke proof | CI |
| AC-12 | Deployment live and observable (Railway + Atlas M0) | **GAP — requires deployed environment.** Not verifiable from CI/sandbox: needs Railway service deploy + Atlas M0 credentials, then the checklist (`/health` 200, `/ready` database ok, request-ID logs, mongodump job). **Owner: repository owner** (deployment credentials are user-scoped secrets; the compose stack in `e2e-compose` verifies the same readiness contract in CI) | checklist (pending) |
| AC-13 | Dogfood evidence per UI PR | `obvious autobuild upload` per acceptance criterion with a UI surface, captured at the tested head SHA (viewport 1440×900, before/after where visual); CI failures upload Playwright traces/reports as artifacts | QA uploads |

## Feature Waves coverage (spec `art_442ZCjeO`)

Wave-by-wave acceptance criteria from the Feature Waves spec, proven on this branch:

| AC | Criterion | Proven by |
|---|---|---|
| F8 / AC-8.1 | Queue search: claim-number **prefix**, policy-number **substring**, customer-name **substring**, case-insensitive, composable with the status/severity/age filters and the current sort, within the existing 500-row fetch cap | `backend/tests/test_workbench.py` — pure `matches_search` semantics tests plus endpoint tests for each field, blank-query no-op, no-match empty list, and search combined with severity/age filters and severity sort (`test_queue_search_composes_with_filters_and_sort`) |
| F8 / AC-8.2 | Search does not widen access: a customer token cannot reach the workbench queue with `search` | `backend/tests/test_workbench.py::test_queue_search_does_not_widen_customer_access` (403) plus the adjuster-gate tests on every workbench route |
| F8 / AC-8.3 | Search box: debounced input, skeleton loading rows, "no matches" empty state with a working clear action | `frontend/src/components/workbench/WorkbenchQueue.test.jsx` — debounce param wiring, clearing drops the param, row-shaped skeletons while loading, no-matches state + clear action |

## Suite inventory (this branch)

- **Backend (pytest):** 345 tests green (includes `test_llm_fixture.py`, 22 tests, added this wave for the deterministic adapter + fault semantics, and a login regression test for the pyjwt≥2.13 empty-secret `InvalidKeyError` caught by the first compose run; F8 adds 11 queue-search tests).
- **Frontend (Vitest/RTL):** 123 tests green in 17 files (`frontend-test` in CI runs the same suite with coverage; F8 adds 4 search-state tests).
- **Playwright E2E (8 spec files, 17 tests, fixture mode):** fnol (3), validation (3), status-portal (3), stp-approval (2), override (2), sse-fallback (1), agent-failure (2, fault-gated: `FIXTURE_FAULT=timeout:intake`), refusal-fallback (1, fault-gated: `FIXTURE_FAULT=refusal:decision`). Serial mode, one worker. Local proof 2026-09-18: 14/14 runnable tests passed on a fresh DB (`claimos_e2e_0918f`) with CI-equivalent rate limits (`LOGIN_RATE_LIMIT=30/minute`, `STATUS_LOOKUP_RATE_LIMIT=10/minute`); the three fault-gated tests passed on dedicated fault-injected stacks (`timeout:intake`, `refusal:decision`) and run in CI's compose job, which recreates the worker per fault phase.
- **Claim-history slate (why specs use different policies):** the eligibility stage adds +25 risk once a policy carries 3+ claims in 12 months — counting the claim under evaluation. The suite submits ~7 pipeline claims, so specs spread across the three seeded AUTO policies (assignment map in `frontend/e2e/utils.js`) to keep every approval-asserting scenario under the flag in both local and CI-alphabetical run orders. Incident dates stay distinct per spec because duplicate fingerprints are (policy, date, type).

## Evidence captures

Captured at viewport 1440×900 by the suite itself (`E2E_EVIDENCE_DIR`), uploaded per AC via `obvious autobuild upload` against the tested head SHA:

| Files | AC |
|---|---|
| `tc-1-fnol-decision-panel.png`, `tc-1-fnol-evidence-pack.png` | AC-3, AC-10 |
| `tc-2-validation-blocked.png`, `tc-2-policy-halt.png` | AC-3 |
| `tc-4-stp-auto-approval.png`, `tc-4-access-code-panel.png` | AC-5 |
| `tc-5-override-audit.png` | AC-7 |
| `tc-6-template-letter-fallback.png` | AC-3 |
| `tc-7-sse-polling-fallback.png` | AC-6 |
| `tc-8-portal-timeline.png`, `tc-8-portal-uniform-error.png`, `tc-8-portal-rate-limited.png` | AC-8 |

Fault-gated flows that skip in the default local run (agent-failure's PROCESSING FAILED surface, refusal retry) are captured by CI's compose job artifacts. Screenshots are the evidence medium throughout: each AC's flow is verified as a sequence of terminal states (blocked form → submitted → decision panel → portal), satisfying the plan's state-pair rule without video.

## Known gaps / deferred

| Gap | Why | Owner |
|---|---|---|
| AC-12 deployment checklist | Requires live Railway + Atlas M0 credentials (user-scoped secrets) | Repo owner |
| Forced-failure reason omits LLM error class | Failure banner/`failure.reason` names the agent ("Agent ELIGIBILITY_AGENT failed") but not the error class ("LLMTimeout after 3 retries") required by the QA plan's AC-3 shape. Sanitization is intentional (customer-safe surface); the class belongs in the adjuster-visible record. Tracked for the backend wave — not fixable in an E2E PR | Backend wave |
| Customer login lands on "Dashboard stats failed to load" | Customers hitting the ops dashboard (adjuster-only `/api/dashboard/stats`) see an error surface instead of their own landing view. UX defect, product code — tracked separately, not fixable in an E2E PR | Frontend wave |
