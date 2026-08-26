# AGENTS.md — VentureCore AI

Instructions for coding agents working in this repository. Read this fully before any task.

---

## What this project is

VentureCore AI is a business intelligence platform for small businesses. AI agents analyse a
company's inventory, sales, and finance data and produce recommendations and executive briefings.

**Current state:** data arrives via CSV/Excel upload. Analysis runs on demand when a user asks a
question in the UI. Single organisation per account. Deployed on Render.

**Where it is going:** a live-data product that ingests from external APIs (Shopify, Stripe,
accounting systems), runs analysis on a schedule, pushes proactive alerts, and turns every
recommendation into an executable action. It is an intelligence layer on top of the customer's
existing systems — **not** a replacement ledger and **not** a system of record.

Every change should move toward that end state or at minimum not move away from it.

---

## How to work

**Explore before you change.** Read the existing code and match it. Do not introduce a new
framework, ORM, test runner, state manager, or project structure because you prefer it. If the
repo uses a pattern, follow that pattern even if you would have chosen differently.

**Never assume the stack.** Detect it from the manifests and lockfiles actually present in the
repo. If something you need is genuinely absent, say so and propose the smallest addition rather
than installing several packages.

**Small, reviewable diffs.** One task at a time. Do not opportunistically refactor unrelated code,
reformat files you did not otherwise change, or fix unrelated bugs you notice — mention them in
your summary instead and let a human decide.

**Stop and ask** when a task requires deleting user data, a schema change with no safe migration
path, adding a paid third-party service, or a change touching more than roughly fifteen files.
Report the blocker rather than guessing.

**Verify your work.** Run the test suite and the linter before reporting done. If either fails,
the task is not done. If there is no test suite yet, add tests for the code you wrote.

**Report honestly.** If you could not complete part of a task, say exactly which part and why.
Never describe something as working that you have not run. Do not report partial work as complete.

---

## Non-negotiable architecture rules

These four exist because retrofitting them later is a rewrite. They apply to every change, whether
or not the task mentions them.

### 1. Multi-tenancy on every table

Every domain table carries a non-nullable `organization_id` foreign key. Every read filters by it.
Every write sets it. There is currently one organisation and its id is always `1` — that does not
make the column optional. A query without an org filter is a bug even when it returns correct
results today.

### 2. Money as integers, always with a currency

Monetary amounts are stored as integers in the currency's minor unit (cents, sen), in a column
named with a `_minor` suffix, alongside an ISO-4217 `currency` column. Never floats, never
`Decimal` in the database, never a bare amount without its currency. Convert to display format at
the presentation layer only.

### 3. The schema is a sync target, not a source of truth

Domain tables that hold business data carry `source` (e.g. `csv_import`, `shopify`, `manual`),
`external_id` (nullable), and `last_synced_at`. Records are upserted on `(source, external_id)`
where an external id exists. This holds now, while the only source is CSV import, so that adding a
real connector later is additive rather than a migration.

### 4. No business logic inside prompts

Agents call typed tools; tools contain the logic. SQL, arithmetic, and business rules live in
ordinary tested functions. A prompt may describe what a tool does and when to use it — it must
never contain a query, a formula, or a threshold. If you find yourself writing SQL into a prompt
string, extract it into a tool instead.

---

## Correctness rules for AI features

**The model orchestrates; code calculates.** Any number shown to a user comes from deterministic
code — a SQL aggregate or a dataframe operation — never from the model's own arithmetic. The model
decides which tool to call and how to explain the result. It does not add numbers up.

**Every figure is traceable.** A computed figure carries the identifiers of the rows it was derived
from, so the UI can show its workings. Do not return a bare number from a tool.

**Tools are typed and validated.** Every tool has an explicit input schema, validates its arguments
before touching the database, and returns a structured result. Writes are transactional with a
rollback path. A tool called with bad arguments returns a structured error the agent can recover
from — it does not raise into the request handler.

**Tools that write are idempotent** where the operation allows it, keyed so a retry does not
double-create.

---

## Constraints

**Zero budget.** Free tiers only. Do not add any service that requires payment or a credit card.
Do not add a paid API, a paid database, a paid monitoring service, or a paid CI runner. If a task
seems to need one, stop and report it.

**Free-tier awareness.** Assume strict LLM rate limits and daily caps. Cache aggressively. Never
call a model in a loop over rows. Batch where possible. Anything a user sees repeatedly should be
computed once and stored, not regenerated per page load.

**No secrets in the repo.** Configuration comes from environment variables. Add new variables to
`.env.example` with a comment, never a real value. Never commit an API key, connection string, or
token, and never print one in a log line.

---

## Out of scope — do not build

Do not add these, even if a task seems adjacent to them:

- Billing, subscriptions, pricing tiers, Stripe checkout
- Marketing pages, SEO, landing page copy
- Mobile apps
- New feature modules or agents beyond what a task explicitly asks for
- Country-specific tax or e-invoicing logic
- Any authentication provider swap

The product surface is being narrowed, not widened. If a task tempts you to add a feature, don't.

---

## Definition of done

A task is complete when all of the following are true:

- The stated acceptance criteria are met
- Tests exist for new logic and the full suite passes
- The linter and type checker pass
- No secrets, no debug prints, no commented-out code left behind
- `.env.example` and the README are updated if configuration or setup changed
- Your summary states what changed, what you verified by running it, and anything left undone
