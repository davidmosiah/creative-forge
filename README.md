<h1 align="center">creative-forge</h1>

<div align="center">
  <img src="docs/images/banner.svg" alt="creative-forge — Paid ads by AI agents. Receipts by design." width="85%" />
</div>

<h3 align="center">
  The agent-driven paid-creative workflow that refuses to lie.<br>
  Competitor research &rarr; localized creatives &rarr; sealed QA &rarr; ads born PAUSED &mdash; with receipts at every step.
</h3>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/LICENSE-AGPL--3.0-22C55E?style=for-the-badge&labelColor=0F172A" alt="License AGPL-3.0" /></a>
  <a href="#-quality-bar"><img src="https://img.shields.io/badge/TESTS-284_passing-16A34A?style=for-the-badge&labelColor=0F172A" alt="284 tests" /></a>
  <a href="#-see-it-run"><img src="https://img.shields.io/badge/DEMO-runs_out_of_the_box-F59E0B?style=for-the-badge&labelColor=0F172A" alt="Demo included" /></a>
  <a href="https://www.remotion.dev"><img src="https://img.shields.io/badge/VIDEO-Remotion-7C3AED?style=for-the-badge&labelColor=0F172A" alt="Remotion video" /></a>
  <img src="https://img.shields.io/badge/BUILT_FOR-AI_agents-0EA5A3?style=for-the-badge&labelColor=0F172A" alt="Built for AI agents" />
</p>

<p align="center">
  <strong>What is this?</strong> A production creative pipeline where <strong>AI agents do the creative and strategic work</strong> (research, hypotheses, copy, scenes, QA judgment) and <strong>fail-closed validators enforce the truth</strong> (provenance, rights, hashes, timing, PAUSED-only publishing). The human holds the only keys that matter: <strong>activation, budget, spend</strong>.
</p>

---

## Front door

| | |
|---|---|
| 🏃 **Run it in 3 commands** | [Quick start](#-quick-start) — the fictional demo app renders real, QA'd creatives out of the box |
| 🧭 **Understand the design** | [Why fail-closed?](#-why-fail-closed) · [The 12-stage loop](#-the-12-stage-loop) |
| 🧾 **See the receipts** | [What "proof" means here](#-the-receipts) — capability, readiness, PAUSED readback envelopes |
| 🛠 **Onboard your real app** | [docs/GETTING-STARTED.md](docs/GETTING-STARTED.md) — from `apps/your-app.yaml` to your first sealed batch |
| 📖 **Operator handbook** | [PLAYBOOK.md](PLAYBOOK.md) — the full operating contract, stage by stage |

---

## 🎬 See it run

```bash
python3 scripts/forge.py build --app sunrise-demo --batch-id demo-001 --jobs 2
```

<div align="center">
  <img src="docs/images/demo-contact-sheet.png" alt="QA contact sheet rendered by the demo app: two localized creatives (en-US and es-MX) for the fictional Sunrise Walks app" width="85%" />
</div>

That's the **actual QA contact sheet** the demo produces on a fresh clone: one concept, two markets (`en-US`, `es-MX`), transcreated copy (not translated), brand palette from config, safe zones checked, every PNG hashed and sealed. No accounts, no credentials, no network writes.

---

## 🚀 Quick start

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cd remotion && npm ci --ignore-scripts && npm run typecheck && cd ..

# 1. Validate everything (research, claims, rights, locales, audiences)
python3 scripts/forge.py preflight --app sunrise-demo

# 2. Render the creative matrix + QA report + contact sheets
python3 scripts/forge.py build --app sunrise-demo --batch-id demo-001 --jobs 2

# 3. A human or agent ACTUALLY looks at the sheets, then seals approval
python3 scripts/qa.py approve --report qa/sunrise-demo/demo-001/report.json \
  --reviewer you --confirm-all

# 4. Static dashboard of everything the sealed artifacts prove
python3 scripts/dashboard.py --app sunrise-demo --open

# Bonus: a 15s mute-safe video, rendered by Remotion with agent-authored scenes
python3 scripts/forge.py build-video --app sunrise-demo \
  --recipe morning-ritual --locale en-US --batch-id demo-001
```

`sunrise-demo` is a **fictional product with fictional research** so the whole
pipeline runs end to end with zero real data. Every fictional file says so.

---

## 🧨 Why fail-closed?

Everyone building with agents hits the same wall: the agent says
*"campaign published!"* — and nothing was published. It reports metrics that
don't exist. It would happily spend your budget at 3am.

creative-forge inverts the trust model. **Agents author; validators verify;
humans authorize.**

| The agent decides | The validators enforce | Only a human may |
|---|---|---|
| research angles & hypotheses | provenance: every creative cites recorded research | activate an ad |
| copy, scenes, pacing, cultural fit | rights: source, hash, commercial + derivative scope | set or change budget |
| what a metric result *means* | schema, char limits, safe zones, encoding, timing | spend money |
| QA judgment (it must actually look) | sealed approvals — one changed byte voids them | waive a physical check |

Three rules with no exceptions:

1. **Swipe fidelity.** A creative that doesn't derive from cited competitor
   research is blocked at preflight. Structure is copied — art and words are
   always your own.
2. **A local receipt never proves external state.** Publishing needs a fresh
   capability receipt, a live destination readback, and a byte-canonical
   `PAUSED` readback envelope from the platform. An `ACTIVE` provider response
   cannot be masked by local bookkeeping.
3. **Honest metrics.** Missing data is `insufficient_data`, never zero. ROAS
   is never invented. Video results carry hook/hold rates so a losing ad tells
   you *where* it lost (the first 3 seconds vs. the body).

---

## 🔁 The 12-stage loop

```mermaid
flowchart LR
    A[1 Readiness] --> B[2 Research 360]
    B --> C[3 Taxonomy]
    C --> D[4 Brief]
    D --> E[5 Concepts]
    E --> F[6 Production]
    F --> G[7 Localization]
    G --> H[8 Render]
    H --> I[9 Sealed QA]
    I --> J[10 Publish PAUSED]
    J --> K[11 Test window]
    K --> L[12 Learning loop]
    L -->|next brief| D
```

Every stage has a validator; every hand-off is an artifact on disk you can
audit later. The full contract lives in [PLAYBOOK.md](PLAYBOOK.md).

---

## 🧾 The receipts

The publish path is where agent pipelines usually lie, so it's the most
defended surface here. Creating one ad requires, in order:

1. **Capability receipt** (&lt; 60 min old) naming the real discovered platform
   tools — including a readback tool that is *not* a create tool.
2. **Readiness receipts** from live checks: the store destination is up, the
   platform has received app events, attribution is mapped. Raw responses are
   stored with their sha256.
3. A **manifest** bound to the sealed QA matrix — one ad per concept, capped
   per ad set, `PAUSED` hardcoded, `activation_allowed: false`.
4. A **byte-canonical readback envelope** per ad, cross-binding provider, tool,
   timestamp, `PAUSED`, all platform IDs and the artifact hash:

```json
{"binding": {"ad_id": "…", "artifact_sha256": "…", "status": "PAUSED", "…": "…"},
 "provider_response": {"id": "…", "status": "PAUSED", "…": "…"},
 "schema": "creative-forge/meta-ad-readback@1", "tool": "…", "observed_at": "…"}
```

`scripts/publish.py verify-receipt` re-validates the whole chain — and even
then, a verified receipt only proves the *audit record*: a `DONE` claim
requires the live readback observed in the current run.

---

## 🗂 What's inside

| Piece | What it does |
|---|---|
| `scripts/forge.py` | `preflight` / `build` / `build-video` / `prepare-publish` |
| `scripts/qa.py` · `video_qa.py` | sealed QA: hashes, dimensions, safe zones, per-artifact visual approval |
| `scripts/research.py` · `video_mining.py` | research contracts — structure only, never media reuse |
| `scripts/audiences.py` | fail-closed audience plans (no sensitive-interest targeting, ever) |
| `scripts/publish.py` | PAUSED-only manifests, capability receipts, canonical readback envelopes |
| `scripts/experiments.py` | learning loop: metrics provenance, hook/hold rates, next-brief binding |
| `scripts/host_assets.py` | content-hash static hosting + live byte-for-byte URL verification |
| `scripts/dashboard.py` | static evidence viewer — reads sealed artifacts only, invents nothing |
| `remotion/` | generic video composition (story · portrait · square), mute-safe by contract |
| `templates/image/*` | HTML creative templates with per-field char limits and declared safe zones |
| `apps/sunrise-demo.yaml` | the fictional demo app — copy its shape to onboard a real one |

## ✅ Quality bar

- **284 tests** (`python3 -m pytest`) — validators, receipts, hardening, contracts
- **Typechecked video** (`cd remotion && npm run typecheck`)
- **CI** runs the full suite *plus a real Remotion render* with sealed QA prep
- Fictional demo data only; the repo ships **zero** real product or ad-account data

## 🧭 Design rules that will not change

1. Competitors' **public** signals only; their art, media, voices and copy are never reused.
2. No religious or sensitive-interest ad targeting — context lives in the creative, language and country.
3. Agents author; validators enforce; humans authorize activation/budget/spend.
4. Every bound on coverage is logged — silent truncation is treated as lying.

## 🤝 Contributing & security

PRs welcome — read [CONTRIBUTING.md](CONTRIBUTING.md) first (rule #1: never
weaken a fail-closed gate). Vulnerabilities: use GitHub's private vulnerability
reporting — see [SECURITY.md](SECURITY.md).

## 📄 License

[AGPL-3.0](LICENSE). Run a modified version as a service? Share your changes.
