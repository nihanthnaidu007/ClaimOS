# ClaimOS user guide

ClaimOS takes an insurance claim from submission to decision and shows you every step of the machine's reasoning. A claim enters through a form, runs through five software agents in sequence, and comes out with a verdict, a payout figure, a written letter to the customer, and a complete trace you can read line by line. If the system is confident and the risk is low, it finalizes the claim automatically. If anything looks risky, it hands the claim to a human with its reasons attached. You are never guessing why the machine decided something — the reasons are the product.

This guide has two parts: one for adjusters working claims in the console, and one for customers checking on a claim.

---

## For adjusters: the console walkthrough

This part is written for a first-time insurance operations hire. No prior claims experience is assumed; where the industry has a jargon word, we use the plain term the product uses.

### The five pipeline stages

Every claim walks the same path. The stage names appear in the console, so they are worth memorizing on day one:

1. **Intake & Validation** — checks the form is complete and plausible: a real date, a positive amount, a description long enough to be meaningful.
2. **Policy Verification** — looks the policy number up in the database and checks coverage: is the policy active, is this type of incident covered, how much of the claimed amount survives the coverage limit and the deductible.
3. **Document Analysis** — reads the pasted or attached evidence, extracts the facts, and judges how consistent the story is (consistent, partially consistent, or contradicting, plus any red flags).
4. **Eligibility & Risk** — turns everything so far into a risk score from 0–100 and a recommendation. The score is computed by fixed arithmetic in code, not by the AI: the same inputs always produce the same number.
5. **Decision & Communication** — issues the verdict, sets the payout amount, and writes the customer letter.

Two stages can stop the pipeline early. If Intake finds the submission invalid, the pipeline halts and the claim is recorded with the reason — nothing vanishes. If Policy can't find the policy number, or the policy is expired or suspended, the pipeline halts there with the reason on the record. A halted claim is data, not a lost claim.

A deterministic fraud cross-check runs alongside the pipeline: every claim carries an incident fingerprint, and a claim that closely resembles an earlier one is flagged with a severity and an explanation, adding points to the risk score and a badge on the workbench.

### How the risk score works (and when you'll see it)

The Eligibility stage adds fixed points for each risk factor on top of a small base score (5–15 points depending on claim size relative to the coverage limit): +40 for an expired or suspended policy, +35 for an incident type the policy doesn't cover, +25 when the claim exceeds available coverage, +25 when the policy has had 3 or more claims in the past 12 months, +20 when document analysis contradicts the story (+10 when it partially matches), +8 per document red flag (capped at 24), and a fraud cross-check bump of +20/+10/+5 by flag severity (capped at 40). The total is capped at 100.

The routing follows the score: **0–29 recommends auto-approve, 30–69 recommends escalation to a human, 70–100 recommends auto-reject.** Two conditions force auto-reject regardless of the number: the incident type is not covered, or the policy was expired or suspended on the incident date.

When the system finalizes a claim automatically, it is because severity was low, its confidence was high (0.85 or above on the default gate), and eligibility came back clean. When any of those legs fails, the claim escalates to the review queue with the reason recorded. The full five-agent trace is attached either way — a claim a machine finalized shows the same reasoning you would have reviewed yourself.

### The screens

**Dashboard.** Your landing view. It shows the total claim count, how many were approved, rejected, under review, or pending, total approved payout, the average risk score across scored claims, and the count of active policies, with the five most recent claims listed underneath. The numbers refetch automatically about once a minute; the "Updated Ns ago" marker tells you how fresh the view is, and a manual refresh button is beside it.

**New Claim.** The submission form, laid out as a wizard: policy number, incident details, amount, description in your own words. Type a policy number and the holder's details render from the live lookup after two or three characters — holder name, coverage limit, deductible, policy status — so you can confirm you have the right policy before you go further. The form validates as you leave each field: the incident date needs the `YYYY-MM-DD` shape, the claimed amount must be greater than zero (the system accepts up to $5,000,000), and the description needs at least 20 characters so the Document agent has something real to read. Contact email is optional but must be a valid shape when present.

The wizard saves a draft as you go, quietly. Leave halfway and come back, and it offers your draft back with the time it was saved; you decide whether to continue or discard it. Drafts survive reloads and different sessions on the same account, and discarding one is permanent — the server copy goes with it.

**Claim view and pipeline traces.** Opening a claim shows the five stages and their state. While a claim is running, the stage rows stream live: each stage flips from running to complete as its `agent_start` and `agent_complete` events arrive, with the agent's reasoning text, the tools it called, and its duration in milliseconds. A connection chip in the header tells you the stream's health — Live, Reconnecting, or Offline with the last update time. If the connection drops mid-run and comes back, the board replays what it missed from the stored event log and lands on the true current state; you never need to refresh to catch up. Completed stages stay rendered even if a later stage errors — a red stage never erases the green ones above it.

The claim also shows its processing runs: each attempt, when it started and finished, and why it failed or escalated if it did. A claim whose worker was interrupted resumes from its last completed stage rather than starting over, so an attempt that took two tries shows both — and what the second try picked up.

**Documents and evidence.** Adjusters can attach files to a claim — PDF, PNG, or JPEG, up to 10 MiB each. Every upload is hashed (SHA-256), listed on the claim with who uploaded it and when, and announced on the claim's event log, so anyone reading the timeline sees the evidence arrive. Re-uploading identical content does not create duplicates.

**Document requests.** For documents you need *from the claimant*, open the case and use the "Documents we need" checklist. Request a document by name ("Repair estimate") with an optional note describing what a good upload looks like ("Signed, itemized, on shop letterhead") — the note is what the claimant will read, so make it concrete. Each request shows a status chip: Requested (waiting on the claimant), Received, or Waived. Only requested items can be changed: edit the wording, or waive it — with a reason for the audit log — when the outcome no longer needs it; once received or waived, a request is settled history. Titles are capped at 120 characters and descriptions at 2,000, validation happens as you leave the field, and every create, edit, and waive is written to the audit trail with your name on it.

**Evidence pack.** Every claim with a decision has a downloadable PDF: the verdict box, the payout, the risk factors, and the letter body, with the reasoning trace and the claim's event history. It is the artifact you hand to a reviewer, a manager, or the customer.

**Claim history.** Every claim in the system, newest first, filterable. Auto-finalized claims live here too — the workbench queue shows only what needs a human.

**Policy lookup.** Search policies by number, holder name, or policy type. "No policy matches that number. Check the number and try again" is the whole empty state — check the number, try again.

**Notifications.** The bell in the header shows your unread count. You are notified about milestones on claims where you are the contact — submission, decision, and the like — and marking a notification read is one click. You only ever see your own.

**Workbench.** The adjuster's review surface — see the next section.

### Reviewing, approving, and overriding

The workbench queue lists every claim that needs a human: pending, under review, or escalated. Each row shows its SLA age — how long the claim has been waiting against its severity's target (72 hours for low severity, 24 for elevated, by default) — and turns amber at three-quarters of the target, red when the target is breached. Rows can be filtered by status and severity, sorted by age, severity, or risk, and the queue updates on its own; there is no refresh button because there is nothing to refresh.

Open a case and you get the whole picture on one page: the summary assembled from the stored agent traces (recommendation, confidence, risk, coverage math, document findings), the live event timeline, the decision letter as written, and the audit history. None of this view calls the AI — it is read straight from what the pipeline recorded, so it is instant and identical every time you open it.

To override the system's recommendation, you enter a decision, an optional payout, and a **required reason**. The system refuses an override without a reason — the request bounces back with "A reason is required to override" and nothing changes. A claim that is already decided cannot be overridden; re-open it first. With a reason, the claim moves to `overridden`, the decision and payout update, and the override is written to an append-only audit log: who did it, what changed before and after, and why, in one entry nobody can quietly edit. The reason you type appears on the claim's timeline where the next person to touch the claim will read it.

**Settlements.** When a claim is settled, the settlement facts — amount, method, reference — are recorded on the claim, stamped with who recorded them, and written to the same audit log. ClaimOS records settlements; it does not move money, and a claim records at most one settlement.

**Ops analytics.** The ops page answers "how are we doing?" from the same durable data: decision cycle times at the 50th/90th/95th percentile, the straight-through rate (how many decided claims finalized with no human touch), the fraud flag rate, the decision mix, and SLA breaches by severity. Every figure is computed from stored claim timelines — nothing is hand-entered, so nothing can drift from reality.

### What claim statuses mean

`pending` (queued; agents have not started or are between stages) · `under_review` (mid-run) · `escalated` (needs a human; the escalation reason is on the claim) · `auto_approved` (finalized by the system, no human action) · `approved` / `rejected` (decided after review) · `overridden` (a human changed the machine's decision, reason on file) · `failed` (the pipeline hit an unrecoverable error — the reason names what went wrong, and the claim's run history shows the attempts) · `settled` (payment recorded). The dashboard's coarse buckets group these for at-a-glance counting, and every transition lands on the claim's event timeline.

---

## For customers: checking on a claim

**File a claim.** Start from "File a claim". The wizard asks what happened in plain steps — your policy number, the date and type of incident, the amount, and a description in your own words. It saves a draft as you go: every step change and every few seconds of typing, quietly, with a "Draft saved 14:02" note. If you stop halfway and come back, it offers your draft back with the time it was saved, and you decide whether to continue or discard. Fields validate as you leave them, so a mistyped date is caught while the field is still in front of you, not after you submit. When you submit, you get a claim number and an access code — keep the access code; it is how you check on your claim. If you gave us an email address, the access code is also sent to that address, so the confirmation email is a safe place to keep it.

**Check your claim's status.** Open the status page and enter your claim number with the access code from your confirmation. You will see a timeline of what has happened — received, checked against your policy, reviewed, decided — and the page updates on its own; there is no need to refresh. The portal shows only what is yours to see: your first name, the claim's status and milestones, and the outcome once decided — never amounts, policy numbers, or contact details. If the claim number or code is wrong, the page says so in one way, every time — it never reveals whether a claim number exists. Too many lookups from one connection in a short window are rate-limited, to protect your claim's privacy.

**Lost your access code?** Use the recovery form on the status page: enter your claim number and the email address you gave when filing, and a fresh code is emailed to that address (the old one stops working the moment a new one is sent). If the claim number and email don't match what's on file, the form gives the same answer either way — it never reveals whether a claim exists. A few recovery requests per minute are all one connection can make, so the form can't be used to send emails to someone else's address.

**Get your decision.** When a decision is reached you get a notification, and the decision letter is downloadable as a PDF — the same document the adjuster sees: the verdict, the payout figure, and the next steps, including how to appeal a rejection (30 days, in writing).
