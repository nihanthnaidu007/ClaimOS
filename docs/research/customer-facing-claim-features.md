# Customer-Side Claims Feature Research

**Purpose:** inform ClaimOS's next feature wave. ClaimOS's customer portal today is status-only (claim-number + access-code lookup, milestone timeline, decision-letter PDF). This paper catalogs what claimants get from digital-first insurers, major carriers, and claims platforms in 2026, marks each feature table-stakes vs. differentiator, maps ClaimOS's current surface against the catalog, and ranks the top 10 features to build next.

**Load-bearing assumption:** this research assumes richer *claimant-facing* self-service is the right next wave for ClaimOS — that buyers value it enough to fund it. Confirming evidence: J.D. Power ties fully digital claim journeys to the highest satisfaction and 86% renewal intent among "very easy" journeys (sources S20, S22). Break signal: if ClaimOS's next customers are adjuster-outsourcing operations rather than consumer-facing carriers, the adjuster-productivity workstream (tracked separately) should outrank this list. Adjacent explanations this catalog skips: portal trust may be capped more by pipeline accuracy/latency than by feature count, and adoption may hinge on guest access (no-login friction) rather than feature depth. Both are priced into the ranking below.

**Method:** web research (Perplexity Sonar, retrieved 2026-09-18) across insurer and vendor product pages, an FY2025 10-K, J.D. Power 2023–2025 studies, and Deloitte's 2025 digital insurance maturity research; plus a direct code inspection of ClaimOS (routes, routers, schemas) the same day. ClaimOS claims are from the repo, not the web. Every external claim carries a source marker [S#] resolved at the end.

---

## The short version

The market's claimant experience has converged on a standard arc: file yourself in minutes, capture photos when prompted, watch the claim move stage by stage, get asked for exactly what's missing, sign and get paid digitally. Lemonade shows the ceiling: as of December 31, 2025, 96% of its first-notice-of-loss events are taken by its AI claims agent with no human involvement and about 55% of claims run fully automated end-to-end [S5]. Incumbents (GEICO, Progressive, Allstate, USAA) deliver the same arc inside mature portals [S10][S14][S17][S23]. The industry's biggest measured gap is not having digital features but keeping customers *informed and unblocked*: insurers deliver adequate digital updates only 22% of the time, and one in three claimants still picks up the phone after receiving a digital update [S20][S38][S50][S22].

That gap is ClaimOS's opening. Its pipeline already emits the granular stage data, citations, traces, and milestone events that customer-facing transparency features need; what's missing is almost entirely a presentation-and-permissions layer, which prices most of the top 10 at small-to-medium build effort.

## Feature catalog (2026)

Quick-scan matrix, then detail per feature. Complexity is effort to build into ClaimOS's React 19 + Vite frontend and FastAPI/MongoDB backend: **S** ≤ 1 sprint, **M** 1–2 sprints, **L** multi-sprint or external integration.

| # | Feature | Maturity 2026 | Claimant impact | Build |
|---|---|---|---|---|
| 1 | Self-service FNOL (web/app, guest-capable) | Table stakes | High — starts the clock in minutes | M |
| 2 | Guided photo/video capture | Table stakes | High — better evidence, fewer callbacks | M |
| 3 | AI damage assessment from photos | Differentiator | High — instant estimates, faster payment | L |
| 4 | Real-time status with granular stages | Expectation is table stakes; execution is a differentiator | Very high — the #1 satisfaction driver | S–M |
| 5 | ETA / what-happens-next guidance | Differentiator | High — cuts anxiety and calls | S |
| 6 | Document checklists & requested-item tracking | Differentiator | Very high — kills the main offline loop | M |
| 7 | Adjuster messaging/chat | Table stakes (trending) | High — replaces phone tag | M |
| 8 | E-signature of forms/releases | Differentiator | Medium — unblocks STP | M |
| 9 | Payout tracking + payment-method choice | Differentiator (Allstate leads) | Very high at the moment that matters most | S (view) / L (rails) |
| 10 | Repair-shop selection & coordination | Table stakes in auto | Medium-high for auto claims | L |
| 11 | Notification preference center | Table stakes expectation | Medium | S–M |
| 12 | AI/decision transparency for claimants | Differentiator (rare) | High — trust at the decision moment | S–M |
| 13 | Instant payout for simple claims | Differentiator | High — Lemonade's signature move | L |

### 1. Self-service FNOL (web/app, guest-capable)

**What it is:** the claimant reports the loss themselves in a guided flow — no call, no adjuster needed to open the claim — and gets a claim reference immediately. **Who offers it:** GEICO (report online or in-app in as little as 2 minutes) [S10]; Progressive (report in the app, claim number issued on filing) [S15][S14]; Allstate (file in Allstate Mobile or My Account, plus a guest account to track an existing claim without a login) [S19][S21]; USAA (report on usaa.com or the USAA app) [S23]; Lemonade (tap "File a Claim," AI Jim takes over) [S1]; Hippo captures FNOL through a 24/7 conversational AI agent that structures claim data in real time and routes claims [S7]. **Maturity:** table stakes — every major carrier and all three digital-first insurers studied offer it. **Impact:** first-minutes experience sets the tone; J.D. Power finds satisfaction highest when the journey is digital from first notice through settlement [S20]. **Build:** M — ClaimOS has a resumable FNOL wizard and draft persistence but the submit endpoint requires an authenticated user; a guest path needs policy lookup, abuse controls (rate limits already exist), and an access-code handoff.

### 2. Guided photo/video capture

**What it is:** the app walks the claimant through taking the *right* photos — diagrams to tag damage, corner shots plus close-ups, retakes before submit — and video prompts ("explain what happened in ~1 minute"). **Who offers it:** Lemonade's interactive car diagram where you tap damaged parts, then guided close-ups [S3]; GEICO's guided photo prompts "when requested" plus upload of photos/videos/documents [S10]; Progressive's Guided Photo process [S16]; Snapsheet's "Intelligent Photo" step-by-step guidance from driveway, shop, salvage yard, or roadside [S30]; Allstate's QuickFoto Claim for drivable-car damage [S40]. Hippo adds virtual inspections via video calls or uploaded media when physical access is limited [S8]. **Maturity:** guided capture is table stakes; the AI layer on top (next feature) is not. **Impact:** cleaner inputs mean fewer "we need better photos" loops — a top driver of the offline callbacks J.D. Power measures [S22]. **Build:** M — client-side guidance UX plus the existing document store; no CV required to start.

### 3. AI damage assessment from photos

**What it is:** computer vision turns claimant photos into severity reads, estimates, or total-loss calls. **Who offers it:** Tractable markets pixel-level damage assessment and photo-to-estimate in minutes, describing ~$7B in claims processed annually and writing estimate lines into carrier estimating systems (CCC, Mitchell, Audatex, and Xactimate for property) [S34][S35]; USAA uses ML with Google Cloud for near-real-time damage estimates from images [S25]; GEICO's photo-inspection tool returns a damage estimate in about 20 minutes [S12]; CCC's shops get automatic total-loss prediction once enough photos are captured [S45]. **Maturity:** differentiator — guided capture is common, but photo-to-estimate AI is concentrated in specialists. **Impact:** high on time-to-resolution; it is what makes Lemonade's seconds-long payouts possible [S1][S5]. **Build:** L for credible CV; out of scope for ClaimOS's current wave but its pipeline's DOCUMENT stage is a natural integration point later.

### 4. Real-time status with granular stages

**What it is:** claimants see a live, stage-by-stage view — filed, under review, estimate ready, payment sent — not a single "processing" label. **Who offers it:** GEICO ("track your claim from start to finish") [S10]; Progressive (real-time status incl. repair/inspection status, total-loss status, payment overview) [S15]; Lemonade claimants track receipt, information requests, review status, and settlement timing in the app [S2]; Hippo tracks current and past claims in the Hippo Home App or Portal [S6]; USAA's My Claims Center [S23]; Allstate's MyClaim tracking [S18]; Guidewire ClaimCenter ships claimant self-service with "claim status transparency" [S26], and carriers have deployed GDPR-compliant self-service claim portals on Guidewire CustomerEngage that measurably raised customer transparency [S29]; Duck Creek's policyholder portal executes claim actions in real time against core systems [S28]. **Maturity:** the *expectation* is table stakes; *execution* is a differentiator — insurers deliver adequate digital updates only 22% of the time, and in-app updates (the highest-satisfaction channel) reach only 36% of auto and 31% of home claimants [S20][S38]. **Impact:** the single strongest satisfaction lever in the studies [S20][S22]. **Build:** S–M — ClaimOS's six pipeline stages and SSE/milestone events already produce the data; this is a portal surface over `StatusLookupResponse`.

### 5. ETA / what-happens-next guidance

**What it is:** each stage says what happens next, what the insurer needs from the claimant, and roughly when. **Who offers it:** thin public coverage — Guidewire's injured-worker template (track status, payments, and recovery milestones) [S27] and Duck Creek's "contextual communication" positioning [S28] gesture at it; Deloitte finds customers expect instant status updates and contextual communication as baseline [S39]. **Maturity:** differentiator — rarely shipped well. **Impact:** high; unmet-information loops are the measured satisfaction killer [S22]. **Build:** S — stage descriptors plus simple timing bands (e.g., "estimates usually within 2 business days") from ClaimOS's analytics; no new infrastructure.

### 6. Document checklists & requested-item tracking

**What it is:** a claim-type-driven checklist of required documents where the claimant sees per-item state — needed / received / approved — and uploads against it. **Who offers it:** weakest coverage among the catalog in carrier marketing; closest analogs are claims-tooling checklists (Claimable: checklists, reminders, notifications [S44]) and GEICO's "take guided photos when requested and upload any documents needed" [S10]; adjuster-side platforms track requests but rarely expose them to claimants. **Maturity:** differentiator — visibly under-served relative to its impact. **Impact:** very high; J.D. Power finds one-third of claimants still contact the insurer after a digital update, dropping satisfaction by 78 points (on a 1,000-point scale) — missing-documents loops are the classic cause [S22]. **Build:** M — new requested-items collection, customer-scoped upload endpoint (documents today are adjuster-gated), checklist UI.

### 7. Adjuster messaging/chat

**What it is:** claimants message their adjuster — and see who their adjuster is — inside the claim view. **Who offers it:** Progressive ("send a message to your claims rep" from the claim status view) [S15]; GEICO texts adjuster contact details and status [S13]; USAA's My Claims Center supports contacting the adjuster [S23]; Guidewire's self-service includes communicating with adjusters [S26]. **Maturity:** table stakes trending — present across incumbents and platforms. **Impact:** high on trust and cycle time; it is the alternative to the phone calls the studies penalize. **Build:** M — a messages collection, customer/adjuster scopes, and the existing SSE/polling fallback for delivery.

### 8. E-signature of claim forms and releases

**What it is:** claimants sign attestations, forms, and settlement releases digitally, with an audit trail. **Who offers it:** Lemonade (claimants digitally sign a "Pledge of Honor" mid-flow) [S4]; claims-vendor tooling (Regure: send-from-claim-file e-signature with real-time signing status and tamper-evident audit trails [S42]); Document eSign for claim forms and settlement offers [S47]. Incumbents reference e-sign inside digital claim flows but market it lightly. **Maturity:** differentiator at claimant level. **Impact:** medium-high — it removes a paper stop that blocks straight-through processing. **Build:** M — embedded signature capture with a stored, hashed audit record; heavier (L) only if a DocuSign-class provider is required.

### 9. Payout/settlement tracking + payment-method choice

**What it is:** claimants see settlement amount and status, choose the rail, and watch the money move. **Who offers it:** Allstate publishes timing by method — debit card in minutes, Zelle in about 2 hours, direct deposit in 2–5 business days, check up to 2 weeks — with method selection in My Account and tracking in the app [S17]; USAA settles to the claimant or a third party and supports digital-wallet payments [S23][S24]; payment vendors productize the choice: Snapsheet Payments (push-to-card, ACH, wallets, unified transaction tracking) [S31], DisburseCloud (8 modalities incl. instant deposit, Venmo, PayPal, Zelle) [S43], Paymentus (payout status via automated SMS/email) [S41]. **Maturity:** differentiator — Allstate is the benchmark; most portals stop at "payment issued." **Impact:** very high — the payout moment is the emotional peak of the claim; ambiguity there erases goodwill earned earlier. **Build:** S for a tracking view over ClaimOS's recorded settlements; L for real payment rails (out of scope for now — ClaimOS records settlements but does not move money).

### 10. Repair-shop selection & coordination

**What it is:** claimants find/choose network shops and watch repair progress, sometimes with photos. **Who offers it:** Allstate (find vetted Good Hands Repair Network shops in the app) [S19]; Progressive (network shop chosen → Progressive pays the shop directly) [S16]; Guidewire self-service includes selecting preferred repair vendors by location [S26]; CCC's Carwise gives customers repair status plus photos of the repair in progress [S36]. **Maturity:** table stakes in auto claims; differentiator in property. **Impact:** medium-high for auto books; irrelevant for theft/weather-only books. **Build:** L for real network integration; M for a manual-entry status feed.

### 11. Notification preference center

**What it is:** claimants choose channels (email/SMS/push) and frequency for claim updates. **Who offers it:** GEICO's opt-in text alerts for status, adjuster contact, and rental info [S13]; Paymentus-style automated SMS/email payout notifications [S41]; J.D. Power observes customers expect to choose the channel, and notes most still get updates by email/phone/text rather than in-app [S20]. **Maturity:** table-stakes expectation, differentiator in execution. **Impact:** medium — compounds the value of every other notification feature. **Build:** S–M — a preferences document per user plus a filter in the existing milestone fanout (whose provider boundary already anticipates SMS/push drivers).

### 12. AI/decision transparency for claimants

**What it is:** plain-language explanation of what was decided, what evidence and rules drove it, and when a human reviews. **Who offers it:** Lemonade is the reference — its public claims page says "our AI runs dozens of anti-fraud algorithms" and hands complex claims to humans [S1], and it publicly discloses automation metrics (96% AI-handled FNOL, ~55% fully automated) [S5]; it also published a policy statement that it does not build AI that denies claims using physical or personal features [S46]. Root's Claims AI is a platform offering — reasoned approve/reject recommendations for claims handlers — not a claimant-facing explanation [S9]. No major carrier markets model-level explanations to claimants. **Maturity:** differentiator — rare, and growing in importance as AI-settled claims attract scrutiny. **Impact:** high at the decision moment, exactly where denial anxiety peaks. **Build:** S–M — ClaimOS already stores per-stage traces and citations; this is a templated, plain-language rendering of data that exists.

### 13. Instant payout for simple claims

**What it is:** low-severity claims approved and paid in the same session. **Who offers it:** Lemonade — instantly approved claims are paid "in seconds" [S1]; its public record is a 3-second settlement [S48][S49]; GEICO's photo-estimate path issues payment in as little as one business day [S11]; Allstate's debit-card payout lands in minutes [S17]. **Maturity:** differentiator; requires AI assessment (feature 3) plus instant rails. **Impact:** high where severity mix allows; ClaimOS's STP gate (confidence threshold, low-severity type/amount rules) already models this decision. **Build:** L — depends on payment rails and CV; the decisioning half exists.

## What ClaimOS already has (verified in code, 2026-09-18)

Inspected: `frontend/src/App.js` routes, `backend/server.py` router wiring, `backend/app/status_routes.py`, `backend/app/claims_routes.py`, `backend/app/fnol_drafts.py`, `backend/app/notifications/`, `backend/app/schemas.py`, `frontend/src/components/StatusPortal.js`.

| Catalog feature | ClaimOS today |
|---|---|
| Self-service FNOL | Partial — resumable FNOL wizard with draft persistence, but submit (`POST /api/claims`) requires authentication; no guest flow |
| Guided photo capture | No — attachments upload against a claim in the console; no guided claimant capture |
| AI damage assessment | No photo CV; deterministic fraud rules + STP gates exist on structured data |
| Real-time granular status | **Yes, strongest asset** — public `/api/status/lookup` (claim number + access code) returns status, `currentStage`, decision outcome, and milestones; live polling on the portal while the timeline is incomplete |
| ETA / what-happens-next | No — stages are shown but not explained or timed |
| Document checklists / requested items | No — document endpoints are adjuster-gated (`require_adjuster`); customers cannot upload or see requested-item state |
| Adjuster messaging | No |
| E-signature | No |
| Payout tracking | Partial — settlements are recorded and surface in decision letters; no payment-method choice or payout timeline (ClaimOS does not move money) |
| Repair coordination | No |
| Notification preferences | Partial — milestone email fanout to the claim contact via a pluggable provider boundary, plus an in-app notification center (list/mark-read) for authenticated customers; no preference controls, no SMS/push drivers |
| Decision transparency | Partial for adjusters (traces, citations, evidence packs — all adjuster-gated); nothing rendered for claimants |
| Guest access | **Yes** — the status portal's claim-number + access-code pattern, with a deliberately masked payload (no amounts or policy numbers) |

## Ranked top-10 customer features for ClaimOS

Ranked by claimant-trust and time-to-resolution impact per unit of build cost; numbers refer to the catalog.

1. **Granular status timeline with what-happens-next guidance (features 4+5)** — the market's biggest measured gap (adequate digital updates only 22% of the time [S20]) meets ClaimOS's existing milestones and stage events; mostly a portal surface over data that already exists.
2. **Document checklists & requested-item tracking with customer upload (feature 6)** — attacks the one-third-still-call failure mode [S22]; needs one new collection and a customer-scoped upload endpoint.
3. **Adjuster–customer messaging (feature 7)** — closes the remaining offline loop; reuses SSE and the notification fanout.
4. **Decision transparency for claimants (feature 12)** — renders the traces and citations ClaimOS already stores; a differentiator no major carrier offers and the most on-brand feature for a glass-box platform.
5. **Guided photo capture (feature 2)** — matches the standard intake UX across GEICO, Progressive, Allstate, CCC, and Snapsheet; improves evidence quality feeding the DOCUMENT stage.
6. **Payout & settlement tracking view (feature 9)** — a read-only settlement/method/status view over recorded settlements is cheap now and the natural seam for payment rails later.
7. **Guest self-service FNOL (feature 1)** — open the existing resumable wizard to claimants with policy lookup and access-code handoff; converts the wizard from console-only to the market-standard entry point.
8. **Notification preference center (feature 11)** — small build on the existing fanout and provider boundary; compounds every other feature's notification value.
9. **E-signature for attestations and releases (feature 8)** — removes the last paper stop from straight-through processing; medium build with an embedded signature and audit trail.
10. **Repair/vendor coordination (feature 10)** — valuable for auto books (CCC's Carwise sets the expectation [S36]) but priced last because real integration is heavy; a manual status feed is the affordable first step.

## Sources

Retrieved 2026-09-18 via Perplexity Sonar web research; ClaimOS claims verified by direct code inspection the same day.

- [S1] Lemonade — "How Lemonade's Tech-Powered Claims Work" — https://www.lemonade.com/claims
- [S2] Lemonade — "How to File a Renters Insurance Claim" — https://www.lemonade.com/renters/explained/how-to-file-a-renters-insurance-claim/
- [S3] Lemonade — "Reporting Your Car's Damage Using the Lemonade App" — https://www.lemonade.com/car/explained/taking-pictures-for-car-insurance/
- [S4] Lemonade UK — "Making an Insurance Claim" — https://www.lemonade.com/uk/home-insurance/explained/making-an-insurance-claim/
- [S5] Lemonade FY2025 Form 10-K (via StockTitan) — https://www.stocktitan.net/sec-filings/LMND/10-k-lemonade-inc-files-annual-report-33aac5b74a32.html
- [S6] Hippo — Claims — https://www.hippo.com/claim
- [S7] Claims Journal — "Hippo Announces Rollout of AI-Driven Claims Workflow" (2026-04-10) — https://www.claimsjournal.com/news/national/2026/04/10/336821.htm
- [S8] Techedge AI — "Hippo Deploys AI-Powered Voice Agent Clara…" (2026-04-09) — https://techedgeai.com/hippo-deploys-ai-powered-voice-agent-clara-to-digitize-homeowner-claims-targeting-70-online-filings/
- [S9] Root — "Claims AI" (Platform) — https://rootplatform.com/claims-ai
- [S10] GEICO — Claims Center — https://www.geico.com/claims/
- [S11] GEICO — Easy Photo Estimate — https://www.geico.com/web-and-mobile/mobile-apps/easy-photo-estimate/
- [S12] GEICO — Mobile Apps — https://www.geico.com/web-and-mobile/mobile-apps/
- [S13] GEICO — Text Messages (claims alerts) — https://www.geico.com/web-and-mobile/mobile-apps/text-alerts/
- [S14] Progressive — Claims FAQ — https://www.progressive.com/claims/faq/
- [S15] Progressive — Online Claims FAQ — https://www.progressive.com/claims/faq/how-to-report-a-claim/
- [S16] Progressive — Car Repair Estimates FAQ — https://www.progressive.com/claims/faq/repair-estimates/
- [S17] Allstate — Claim Payments — https://www.allstate.com/claims/file-track/claim-payments
- [S18] Allstate — Photo Claims — https://www.allstate.com/claims/auto-motorcycle/photos
- [S19] Allstate — Allstate Mobile App — https://www.allstate.com/help-support/allstate-mobile
- [S20] J.D. Power — 2025 U.S. Claims Digital Experience Study press release — https://www.jdpower.com/business/press-releases/2025-us-claims-digital-experience-study/
- [S21] Allstate — Claims Help (guest account tracking) — https://www.allstate.com/help-support/claims
- [S22] J.D. Power — 2023 U.S. Claims Digital Experience Study press release — https://www.jdpower.com/business/press-releases/2023-us-claims-digital-experience-study/
- [S23] USAA — Claims Center — https://www.usaa.com/support/insurance/claims/
- [S24] USAA — Mobile App — https://www.usaa.com/mobile-app/
- [S25] Google Cloud — "USAA and Google Cloud Work Together to Speed Auto Claims" — https://cloud.google.com/blog/topics/customers/usaa-and-google-cloud-work-together-to-speed-auto-claims
- [S26] Guidewire — ClaimCenter data sheet — https://assets.ctfassets.net/vdinc3339dpx/2oSVq3NzzMyIJVp1mh1UCw/0a5a2bf7d9f98fa037c22250ff3825d6/DS_Guidewire_ClaimCenter_EN_2301.pdf
- [S27] Guidewire — Cloud Platform releases (injured-worker self-service template) — https://www.guidewire.com/products/technology/guidewire-cloud-platform-releases
- [S28] Duck Creek — Policyholder portal — https://www.duckcreek.com/product/duck-creek-policyholder/
- [S29] Hexaware — Guidewire CustomerEngage claims-portal case study — https://hexaware.com/case-study/how-hexaware-transformed-claims-processing-for-a-leading-nordic-insurer-with-guidewire-customerengage/
- [S30] Snapsheet — Appraisals — https://www.snapsheetclaims.com/products/insurance-appraisals
- [S31] Snapsheet — Claims Payments — https://www.snapsheetclaims.com/products/payments
- [S32] CCC Mobile – Quick Estimate (Google Play) — https://play.google.com/store/apps/details?id=com.cccis.quickest&hl=en_US
- [S33] CCC — Quick Estimate Mobile Application (PDF) — https://help.cccis.com/learning/mobile/insurance/quickest/quickestimatemobileapplication.pdf
- [S34] Tractable — company site — https://tractable.ai/
- [S35] Teardown — Tractable profile — https://www.teardown.ai/companies/tractable
- [S36] CCC — Carwise launch — https://www.cccis.com/news-and-insights/posts/ccc-information-services-inc-launches-carwise-solution-2/
- [S37] J.D. Power — 2025 U.S. Auto Claims Satisfaction Study press release — https://www.jdpower.com/business/press-releases/2025-us-auto-claims-satisfaction-study/
- [S38] Insurance Journal — "JD Power: Full Digital Claims Process Drives High Customer Satisfaction" (2025-12-09) — https://www.insurancejournal.com/news/national/2025/12/09/850297.htm
- [S39] Deloitte — Digital Insurance Maturity 2025 — https://www.deloitte.com/content/dam/assets-zone2/ce/en/docs/services/consulting/2025/2025%20digital%20insurance%20maturity%20report.pdf
- [S40] Allstate — QuickFoto Claim (content hub) — https://delivery.contenthub.allstate.com/api/public/content/cb309eb1742c46b6b2ad2e6e02b948cf?v=06983da2
- [S41] Paymentus — Insurance Disbursements — https://www.paymentus.com/insurance/insurance-disbursements/
- [S42] Regure — Insurance Claims E-Signature — https://www.getregure.com/platform/e-signatures/
- [S43] DisburseCloud — Claims Payments — https://www.disbursecloud.com/claims-payments/
- [S44] F6S — Claimable (claims document management listing) — https://www.f6s.com/software/category/claims-document-management
- [S45] CCC — Total Loss Prediction (help center) — https://cccis.zendesk.com/hc/en-us/articles/32282989575316-Total-Loss-Prediction
- [S46] Lemonade — "Lemonade's Claim Automation" (policy statement) — https://www.lemonade.com/blog/lemonades-claim-automation/
- [S47] Document eSign — Electronic Signature for Insurance — https://documentesign.com/solutions/electronic-signature-for-insurance
- [S48] AI Use Case Hub — Lemonade claims-automation case page (secondary) — https://www.aiusecasehub.com/case/lemonade-accelerates-claims-processing-with-ai-automation
- [S49] Penny Pincher — "AI-Powered Car Insurance Claims" (secondary) — https://pennypincher.com/articles/insurance/auto/ai-powered-car-insurance-claims-automation-2026
- [S50] Program Business — J.D. Power digital claims study summary (secondary) — https://programbusiness.com/news/digital-claims-raise-satisfaction-but-customers-still-switch-channels-j-d-power-study-shows/
