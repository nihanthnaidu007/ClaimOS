# Acceptance Coverage — AC-1..AC-14

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
| AC-8 | Public status portal | `backend/tests/test_status_portal.py` (`test_valid_code_returns_masked_status`, `test_wrong_code_is_generic_404`, `test_unknown_claim_number_returns_identical_404`, `test_lookup_rate_limited_per_ip`); F6 decision transparency: `backend/tests/test_portal_projection.py` (deny-by-default projection — `test_deny_by_default_new_internal_trace_field_is_never_projected` injects unknown internal fields and asserts they never reach the projection; also guards F11 notes) + endpoint-level transparency tests in `test_status_portal.py`; E2E `status-portal.spec.js` (uniform miss text, valid-code timeline with milestones, transparency section renders stage copy + decision citation + no internal markers, per-IP 429 message) | pytest + PW; captures `tc-8-portal-timeline.png`, `tc-8-portal-uniform-error.png`, `tc-8-portal-rate-limited.png`, `tc-8-portal-transparency.png` |
| AC-9 | Analytics against fixtures | `backend/tests/test_analytics.py` (distribution, STP rate, cycle-time percentiles over the seeded dataset) | pytest |
| AC-10 | Frontend build, decomposition, component + E2E tests | CI `frontend-lint` + `frontend-test` (Vitest/RTL, 19+ suites, coverage ≥ 88%) + `e2e-compose` (the eight Playwright specs against the compose stack, fixture mode) | CI |
| AC-11 | CI green, protected main, compose serves the app | CI: 8 jobs on PR (`backend-ruff`, `backend-pytest`, `frontend-lint`, `frontend-test`, `docker-builds`, `security-audit`, `e2e-compose`, `lint-workflows`); `e2e-compose` boots the full compose stack, waits on `/api/ready`, and runs the suite against the served UI — the compose-smoke proof | CI |
| AC-12 | Deployment live and observable (Railway + Atlas M0) | **GAP — requires deployed environment.** Not verifiable from CI/sandbox: needs Railway service deploy + Atlas M0 credentials, then the checklist (`/health` 200, `/ready` database ok, request-ID logs, mongodump job). **Owner: repository owner** (deployment credentials are user-scoped secrets; the compose stack in `e2e-compose` verifies the same readiness contract in CI) | checklist (pending) |
| AC-13 | Dogfood evidence per UI PR | `obvious autobuild upload` per acceptance criterion with a UI surface, captured at the tested head SHA (viewport 1440×900, before/after where visual); CI failures upload Playwright traces/reports as artifacts | QA uploads |
| AC-14 | Customer–adjuster claim messaging (feature-wave F5, spec AC-5.1–5.3) | `backend/tests/test_claim_messages.py` (23 tests): thread order both directions, provider-boundary email notifications incl. contact→policy-holder fallback, adjuster-bell refresh, driver-failure resilience, sanitization, length cap, portal rate limits, cross-claim denial (generic 404), read receipts; hostile strings render inert (RTL asserts no `script`/`img`/`iframe` nodes in `MessageThreadPanel.test.jsx` / `PortalMessageThread.test.jsx`) | pytest + RTL; captures `tc-9-workbench-thread.png`, `tc-9-portal-thread.png`, `tc-9-message-send.webm` |
| AC-15 | Audited reopen of decided claims (feature-wave F14, spec AC-14.1–14.3) | `backend/tests/test_reopen.py` (23 tests: decided-only gate with 409s, blank-reason 422, adjuster gating, audit + `claim_reopened` event, no pipeline re-run, queue re-eligibility, second decision replacement, portal projection with reopened chip/milestone and masked internals); Vitest `ReopenModal.test.jsx` / `CaseView.test.jsx` / `StatusTimeline.test.jsx`; E2E `override.spec.js` reopen step (reopen → review state → explicit second decision → portal "Decision updated" with the reopened milestone) | pytest + RTL + PW; captures `tc-14-reopen-workbench.png`, `tc-14-reopen-portal.png` |

## Feature Waves coverage (spec `art_442ZCjeO`)

Wave-by-wave acceptance criteria from the Feature Waves spec, proven on this branch:

| AC | Criterion | Proven by |
|---|---|---|
| F8 / AC-8.1 | Queue search: claim-number **prefix**, policy-number **substring**, customer-name **substring**, case-insensitive, composable with the status/severity/age filters and the current sort, within the existing 500-row fetch cap | `backend/tests/test_workbench.py` — pure `matches_search` semantics tests plus endpoint tests for each field, blank-query no-op, no-match empty list, and search combined with severity/age filters and severity sort (`test_queue_search_composes_with_filters_and_sort`) |
| F8 / AC-8.2 | Search does not widen access: a customer token cannot reach the workbench queue with `search` | `backend/tests/test_workbench.py::test_queue_search_does_not_widen_customer_access` (403) plus the adjuster-gate tests on every workbench route |
| F8 / AC-8.3 | Search box: debounced input, skeleton loading rows, "no matches" empty state with a working clear action | `frontend/src/components/workbench/WorkbenchQueue.test.jsx` — debounce param wiring, clearing drops the param, row-shaped skeletons while loading, no-matches state + clear action |
| F13 / AC-13.1 | Letter templates: the default template — the current decision letter captured at its variable slots — is seeded at startup, and rendering it with the fixture claim's context is byte-equivalent to the letter the pipeline writes today | `backend/tests/test_letter_templates.py::test_default_template_matches_the_pipeline_letter` (byte parity against the fixture decision stage), `::test_default_template_is_seeded_and_idempotent` |
| F13 / AC-13.2 | Merge variables render; unknown or malformed slots render EMPTY with a flagged warning — raw `{{...}}` braces never survive | `test_letter_templates.py` — all six documented variables render (`test_all_documented_merge_variables_render`), whitespace-tolerant slots, unknown → empty + `unknown_variable:` warning, malformed-pair sweep (`test_malformed_slots_never_leave_braces`), context fallbacks |
| F13 / AC-13.3 | Template CRUD is audited; preview renders without persisting; the render feeds the existing PDF letter path | `test_template_crud_is_audited` (before/after snapshots + actor), `test_default_template_cannot_be_deleted` (409, no audit row), `test_preview_renders_without_mutating_or_auditing` (zero mutation/audit/persistence), `test_pdf_export_uses_selected_template` (rendered template body drawn into the PDF; unknown template 404) |

## Feature acceptance — F9 (SLA escalation)

Spec F9 (approved spec artifact). Verified on `feat/sla-escalation`:

- **AC-9.1 — Set exactly once, never cleared:** `backend/tests/test_escalation.py` — repeated sweeps after a breach escalate once and are then no-ops (`test_breach_sets_escalated_at_exactly_once_across_repeated_evaluations`), deciding the claim afterwards leaves the record untouched (`test_escalation_record_survives_deciding_the_claim`), and the pure `escalation_due` never proposes a second stamp on a record-bearing claim. Persistence uses an atomic conditional update (`escalated_at` absent) so concurrent workers race to exactly one stamp.
- **AC-9.2 — Routing:** the bell goes only to the assigned adjuster (`test_assigned_escalation_bell_notifies_only_the_assignee`); unassigned escalations notify nobody and surface solely in the ops analytics escalation count (`test_unassigned_escalation_never_notifies`, `test_analytics_escalation_count_routes_unassigned_claims`) plus the queue badge/filter (`test_queue_escalated_filter_returns_only_escalated_rows`, `WorkbenchQueue.test.jsx` badge + Escalated chip wiring).
- **AC-9.3 — Threshold config:** `SLA_ESCALATION_FACTOR` multiplies the severity's SLA window — default `1.0` escalates at-breach (`test_default_factor_escalates_at_breach`), `1.5` delays escalation past the window (`test_factor_multiplier_delays_escalation_past_the_window`), and zero/negative values fail configuration (`test_zero_or_negative_factor_fails_configuration`).

## Feature acceptance — F10 (claim assignment)

Spec F10 (approved spec artifact). Verified on `feat/claim-assignment`:

- **AC-10.1 — Round-robin assignment with config toggle:** `backend/tests/test_assignment.py` — strict rotation across the active roster (`test_submit_claim_auto_assigns_round_robin`), least-recently-assigned ordering with never-assigned-first and id tiebreaks (pure `pick_next_assignee_id` tests), and `AUTO_ASSIGN=none` leaving claims unassigned without blocking submission (`test_submit_claim_unassigned_when_auto_assign_none`, `test_choose_auto_assignee_none_leaves_claim_unassigned`).
- **AC-10.2 — Audited manual reassignment:** `POST /api/workbench/claims/{claim_id}/assignee` tests — claim update + audit row + `claim_reassigned` event, adjuster auth required, `404` unknown claim/user, `409` inactive/customer target, `422` blank reason.
- **AC-10.3 — Customer portal path unchanged:** `test_status_portal_lookup_hides_assignment_fields` — the public `/api/status/lookup` deny-by-default projection exposes no assignment fields even for claims that carry them; the Mine/Unassigned/All filters are workbench-only.
- **Workload per adjuster:** `backend/tests/test_analytics.py` — `build_workload` counting/order/ties plus the ops payload `workload` group (busiest first, unassigned bucket); frontend `OpsAnalytics.test.jsx` asserts the sixth card, `WorkbenchQueue.test.jsx` the filter chips (param wiring, default All, `aria-pressed`).

## Feature acceptance — F7 (portal settlement visibility)

Spec F7 (approved spec artifact `art_442ZCjeO`). Verified on `feat/portal-settlement`:

- **AC-7.1 — settlement recorded → portal shows the card and milestone 4 completes; before settlement, neither appears:** `backend/tests/test_status_portal.py` — projection before recording (`test_settlement_card_absent_before_settlement`, `test_lookup_omits_settlement_key_before_recording`) and after (`test_settlement_card_and_final_milestone_appear_after_settlement`, `test_lookup_returns_settlement_card_after_recording`); E2E `status-portal.spec.js` "settlement recording surfaces the card and completes the final milestone" (fixture flow to settlement: no card and "3 of 4 milestones complete" before recording, then the adjuster records the settlement and the re-lookup renders the card with the recorded amount and payment-timing line plus "4 of 4 milestones complete"). Milestone 4 completes through the existing `payout_recorded` event timeline (Wave 0 wiring), not a parallel mechanism.
- **AC-7.2 — the card renders only fields that exist on the settlement record — no invented payment-method or fee data:** the projection is deny-by-default (`test_settlement_card_drops_internal_and_future_record_fields`, `test_settlement_card_omitted_when_record_has_no_visible_fields`); RTL `SettlementCard.test.jsx` ("renders no amount or date placeholders when the record lacks them", "never renders payment-method, fee, or reference data even if the payload carried it"); the E2E settlement step additionally asserts the customer page never renders `bank_transfer` or the settlement reference.
- **Dev-mode same-origin fix (same file family):** `StatusPortal.js`, `NotificationBell.js`, and `PortalMessageThread.js` interpolated `import.meta.env.VITE_API_BASE_URL` raw, so an UNSET variable produced literal `undefined/api/…` URLs in dev — the same failure mode `resolveApiBase()` in `src/lib/apiBase.js` already centralized for the SSE client (PR #50). All three now resolve their base through `resolveApiBase()`; compose/CI builds (variable set empty at build time) are unaffected, and the Vitest suite covers both shapes.

## Suite inventory (this branch)

- **Backend (pytest):** 559 tests green (includes `test_llm_fixture.py`, 22 tests, for the deterministic adapter + fault semantics; the F6 deny-by-default projection suite in `test_portal_projection.py`; a login regression test for the pyjwt≥2.13 empty-secret `InvalidKeyError` caught by the first compose run; F8's 11 queue-search tests; F1's email-delivery and access-code-recovery suites; F10's assignment/reassignment/filter/workload tests; F13's 14 letter-template tests — byte parity, rendering safety, audited CRUD, read-only preview, PDF integration; `test_claim_messages.py`, 23 tests, added this wave for adjuster–customer messaging threads, provider-boundary notifications, adjuster bells, rate limits, and access-code scoping; F9 adds the 14 escalation tests in `test_escalation.py`; F7 adds the 6 settlement-projection tests in `test_status_portal.py` — card absent before recording, card + milestone 4 after, deny-by-default field drops, empty-record omission, and endpoint round trips); F11 adds 24 internal-notes tests in `backend/tests/test_notes.py`: CRUD + sanitization, adjuster-gated access, audit trail, mention fan-out idempotency, and the customer-invisibility regression over the deny-by-default projection; the F14 reopen suite `test_reopen.py`, 23 tests, covers the audited reopen flow — both state branches, audit trail, timeline event, and re-decision replacement.
- **Frontend (Vitest/RTL):** 212 tests green in 27 files (adds the workbench/portal message-thread suites, 14 tests, with hostile-string XSS assertions) (`frontend-test` in CI runs the same suite with coverage; F8 adds 4 search-state tests; F10 adds the chip and workload-card tests; F9 adds the badge/filter/round-trip tests; F7 adds the 5-test settlement-card suite plus the milestone-4 timeline assertion); F11 adds NotesPanel coverage: loading/empty/error/retry states, plain-text rendering, composer counter + 4000-cap, mention chips, submit-and-refetch; plus the `ReopenModal` suite (reason-required gating, submit, error state).
- **Playwright E2E (8 spec files, 18 tests, fixture mode):** fnol (3), validation (3), status-portal (3), stp-approval (2), override (3), sse-fallback (1), agent-failure (2, fault-gated: `FIXTURE_FAULT=timeout:intake`), refusal-fallback (1, fault-gated: `FIXTURE_FAULT=refusal:decision`). Serial mode, one worker. Local proof 2026-09-18: 14/14 runnable tests passed on a fresh DB (`claimos_e2e_0918f`) with CI-equivalent rate limits (`LOGIN_RATE_LIMIT=30/minute`, `STATUS_LOOKUP_RATE_LIMIT=10/minute`); the three fault-gated tests passed on dedicated fault-injected stacks (`timeout:intake`, `refusal:decision`) and run in CI's compose job, which recreates the worker per fault phase.
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
| `tc-8-portal-timeline.png`, `tc-8-portal-uniform-error.png`, `tc-8-portal-rate-limited.png`, `tc-8-portal-transparency.png` | AC-8 |
| `tc-8-portal-settlement.png` | F7 (AC-7.1/7.2) |
| `tc-9-workbench-thread.png`, `tc-9-portal-thread.png`, `tc-9-message-send.webm` | AC-14 |
| `tc-14-reopen-workbench.png`, `tc-14-reopen-portal.png` | AC-15 |

Fault-gated flows that skip in the default local run (agent-failure's PROCESSING FAILED surface, refusal retry) are captured by CI's compose job artifacts. Screenshots are the evidence medium throughout: each AC's flow is verified as a sequence of terminal states (blocked form → submitted → decision panel → portal), satisfying the plan's state-pair rule without video.

## Feature wave 2 — customer transparency (F2)

Wave-2 acceptance criteria (Feature F2 spec, `art_442ZCjeO`) are covered on `feat/portal-next-steps`:

| AC | Criterion | Proven by |
|---|---|---|
| AC-2.1 | Non-empty next-step copy for every pipeline stage and terminal state; deny-by-default wire allowlist | `backend/tests/test_status_portal.py::test_next_steps_nonempty_for_every_pipeline_stage` (parametrized over the six `PIPELINE_STAGES`), `::test_next_steps_nonempty_for_terminal_states` (decided/reopened/failed), `::test_projection_allowlist_blocks_internal_fields` (PII/trace fields can never leak), `::test_portal_stage_copy_tracks_pipeline_stages` (copy stays keyed to the real pipeline) |
| AC-2.2 | Honest ETA derived from existing SLA state; field omitted (not empty/null) when SLA state is absent | `::test_eta_present_when_sla_state_exists`, `::test_eta_breached_phrase_names_the_delay_without_a_date`, `::test_eta_absent_omits_field_when_sla_state_is_absent`, `::test_eta_absent_when_nothing_is_pending`, plus API round-trips `::test_lookup_returns_next_steps_and_eta` / `::test_lookup_omits_eta_field_without_sla_state` (exclude_unset wire behavior) |
| AC-2.3 | "What happens next" portal card with loading, error, and empty states | `frontend/src/components/NextStepsCard.test.jsx` (content + ETA chip, chip omitted without ETA, skeleton loading, named error + Retry, instructive empty state); fixture-mode E2E `frontend/e2e/status-portal.spec.js` asserts the card on the decided claim and that no ETA chip renders |

All copy lives in the single `PORTAL_STAGE_COPY` constant (`backend/app/status_portal.py`); no agent/LLM output is ever quoted to the customer.

## Feature wave 2 — workbench saved views + bulk actions (F12)

Wave-2 acceptance criteria (Feature F12 spec, `art_442ZCjeO`) are covered on `feat/workbench-views-bulk`:

| AC | Criterion | Proven by |
|---|---|---|
| AC-12.1 | Saved views: `workbench_views` collection (`{owner_id, name, filters_json, created_at}`) with save/list/apply/delete endpoints, private to their owner | `backend/tests/test_workbench_views_bulk.py::test_save_list_and_apply_view_round_trip`, `::test_view_apply_404s_for_other_owners_and_missing`, `::test_views_reject_unknown_filters_and_blank_names`, `::test_saving_the_same_name_replaces_the_preset`, `::test_views_require_authentication`; RTL: `WorkbenchQueue.test.jsx` "saves the current filters as a named view…", "applies a saved view…", "deletes a saved view…" |
| AC-12.2 | Bulk reassign + flag-for-review over multi-select: N-claim confirmation, one audit entry per touched claim (bulk = a loop of audited single actions), per-claim outcomes with partial failures surfaced, never silently dropped | `backend/tests/test_workbench_views_bulk.py::test_bulk_reassign_is_a_loop_of_per_claim_audits`, `::test_bulk_flag_appends_flags_with_per_claim_audits`, `::test_bulk_partial_failure_reports_per_claim_and_keeps_auditing_rest`, `::test_bulk_duplicate_claim_ids_apply_once`, `::test_bulk_reassign_requires_existing_adjuster_target`, `::test_bulk_rejects_empty_ids_and_missing_reason`, `::test_bulk_endpoint_is_adjuster_gated`; RTL: `WorkbenchQueue.test.jsx` bulk-action block + `BulkActionModal.test.jsx` (confirmation/disabled state, payloads, per-claim failure render) |
| AC-12.3 | Queue surfaces: bulk bar on selection, per-claim review-flag badge, saved-view chips; adjuster-flow dogfood evidence | RTL tests above plus dogfood captures `tc-9-bulk-flag-before.png` (queue + bulk bar on 2-row multi-select), `tc-9-bulk-modal-confirmation.png` ("2 claims will be affected" dialog listing both claim numbers + reason), `tc-9-bulk-flag-after.png` (post-apply queue with per-claim `Review ×1` badges), `tc-10-saved-views.png` (persisted "Elevated claims" chip), `tc-10b-view-applied.png` (chip applied: SEVERITY filter set from the view, queue reduced to the elevated claim) — against the tested head SHA. Live-database verification on the fixture stack: both bulk-flagged claims carry their own `flags` entry (`reason`, `flagged_by`, `flagged_at`) and `audit_log` holds two separate `flag_for_review` entries (one per claim, never one opaque write). Sandbox limitation: ffmpeg is unavailable, so the planned short WebM of the bulk interaction is replaced by the before/confirmation/after screenshot sequence above; the modal disabled-state and apply click are additionally covered by `BulkActionModal.test.jsx` |

F12 adds 13 backend tests (suite: 431 green) and 18 frontend tests (suite: 156 green in 20 files).

## Known gaps / deferred

| Gap | Why | Owner |
|---|---|---|
| AC-12 deployment checklist | Requires live Railway + Atlas M0 credentials (user-scoped secrets) | Repo owner |
| Forced-failure reason omits LLM error class | Failure banner/`failure.reason` names the agent ("Agent ELIGIBILITY_AGENT failed") but not the error class ("LLMTimeout after 3 retries") required by the QA plan's AC-3 shape. Sanitization is intentional (customer-safe surface); the class belongs in the adjuster-visible record. Tracked for the backend wave — not fixable in an E2E PR | Backend wave |
| Customer login lands on "Dashboard stats failed to load" | Customers hitting the ops dashboard (adjuster-only `/api/dashboard/stats`) see an error surface instead of their own landing view. UX defect, product code — tracked separately, not fixable in an E2E PR | Frontend wave |
