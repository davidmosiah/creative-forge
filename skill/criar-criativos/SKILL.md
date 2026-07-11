---
name: criar-criativos
description: Use when Codex or Claude receives a request to research, generate, localize, quality-check, or publish paid-ad creatives for a configured app.
---

# Criar Criativos

## Overview

Use the same `creative-forge` checkout from Codex and Claude. The repository is
the source of truth; MCPs are optional runtime providers, never hidden state.

```bash
export CREATIVE_FORGE_ROOT="${CREATIVE_FORGE_ROOT:-$HOME/creative-forge}"
cd "$CREATIVE_FORGE_ROOT"
```

For workflow rationale, current gaps and direct sources, read

## Agent-driven boundary

Claude/Codex decides research, hook/beat decomposition, hypothesis, concepts,
copy, cultural transcreation, scenes, shot plan, pacing, visual edit, visual
QA and performance interpretation. Deterministic validators are limited to app
identity, provenance, rights, schema, hashes, timing, encoding, declared safe
zones, metric arithmetic, manifest coverage and `PAUSED` readback.

Do not ask a validator to score taste, creative quality, cultural fit or why a
metric moved. Put those decisions and rationale in agent receipts. Paid
generation and external writes require human or explicit task authorization;
activation, budget and spend require a separate explicit human confirmation.

## Capability discovery

At the start of every run, inspect the tools actually available to the current
agent. Look specifically for PostHog MCP, Meta Ads MCP, Higgsfield and Remotion.
Do not infer availability from documentation and **não invente** tool names,
schemas, flags, IDs, publication results, or performance data.

- PostHog MCP refreshes `signals/<app>.yaml` with production-only filters.
- Meta Ads MCP mines the Ad Library and publishes only through a manifest.
- Higgsfield MCP is preferred; `scripts/higgsfield.sh` is the verified CLI fallback.
- Video mining, local Remotion render, FFprobe/FFmpeg audit, sealed run locks,
  per-artifact agent playback receipts and the experiment contract are
  implemented. Remotion is `production_local`; Meta video upload/processing/
  `PAUSED` readback is a separate unproven capability and remains blocked.

The current Higgsfield CLI adapter is read-only for schema discovery and cost
estimation. Paid `generate` is fail-closed because the quote does not impose a
provider-side ceiling or reconcile actual cost. Do not bypass that block. A
future paid path needs both guarantees, checkpointed retry safety and a new
review; rights promotion remains a separate asset-registry gate.

Before Meta publication, write a local **capability receipt** containing agent,
checked timestamp, provider, exact available tool names and the distinct live
readback tool. No tokens. It must be less than 60 minutes old. Also collect a
fresh readiness bundle covering the selected destination/CPP plus every
app-configured runtime gate used by the campaign (for example app events and
attribution mapping). Local JSON validation never replaces those live queries.

## Canonical workflow

Use the same stages for every app:

`readiness → research 360 → taxonomy → brief → concept portfolio → production → localization → render → QA → PAUSED → test → learning loop`

Stop at the last stage supported by the current repository and discovered
providers. A target contract is not a runtime capability.

For Remotion, run the exact install/typecheck/audit/render/QA proof gate in
`PLAYBOOK.md`. Inspect the complete artifact; review sound and muted when audio
exists, or verify intentional silence honestly when it does not.

## Current executable video path

1. Audit the agent-authored pattern and localized recipe:

   ```bash
   python3 scripts/video_mining.py --app <slug> --json
   python3 scripts/video.py audit-recipe --app <slug> --recipe <recipe>
   ```

2. Render one requested market and create its sealed playback report in one
   bounded command:

   ```bash
   python3 scripts/forge.py build-video \
     --app <slug> --recipe <recipe> --locale <locale> --batch-id <batch>
   ```

3. Claude/Codex must inspect the complete localized timeline and record the
   actual review. Use the `artifact_key` printed in the report; never approve
   from technical probes alone:

   ```bash
   python3 scripts/video_qa.py approve \
     --report qa/<slug>/<batch>/<locale>/<recipe>/playback-report.json \
     --artifact-key <key> --reviewer <codex|claude> \
     --notes "what was actually inspected" --confirm-all
   python3 scripts/video_qa.py status \
     --report qa/<slug>/<batch>/<locale>/<recipe>/playback-report.json
   ```

Use `--all-markets` only when the task actually authorizes the full localization
matrix. A browser-observed competitor pattern informs structure only; it never
becomes a Remotion asset. Voiceover and SRT captions are per locale; the
renderer places captions on-screen and the QA lock seals both files.

## Current executable image path

1. Run the fail-closed preflight:

   ```bash
   python3 scripts/forge.py preflight --app <slug>
   ```

2. Refresh stale research with Meta Ads MCP `ads_library_search` when available,
   otherwise use the browser. Save structured evidence to
   `swipe/<slug>/competitors.yaml`. Label lineage honestly as
   `competitor_pattern`, `own_winner`, `customer_insight`, `trend` or
   `exploratory`. Longevity and observed competitor ads are evidence, never
   proven ROAS; only `own_winner` with performance data is a winner.
3. Refresh stale analytics through PostHog MCP. Keep `not_testflight`,
   `not_emulator`, project, window and app filters in `signals/<slug>.yaml`.
   Without spend/revenue, report that ROAS is unavailable.
4. For the current image path, adapt a cited competitor pattern's structure,
   never its art, media or literal copy. Every recipe needs `research_refs`,
   evidence-backed `claims_used`, explicit target markets/platforms, on-canvas
   copy and off-canvas `ad_copy` for every targeted `copy_language`. The
   recipe's `target_markets` is the render/QA boundary. If two targets share a
   language, write complete `market_overrides.<market_id>.copy` and `.ad_copy`
   for each; never collapse Mexico and Spain into one generic Spanish asset.
   Reviews require a real source URL. Broader lineage production uses the
   implemented brief/concept/asset contract rather than bypassing preflight.
5. Generate the full matrix:

   ```bash
   python3 scripts/forge.py build --app <slug> --batch-id <id> --jobs 4
   ```

6. Open and inspect every generated **contact sheets** file. Check copy language,
   spelling, readability, contrast, image anatomy/artifacts, truthfulness, brand,
   Story/Reels safe zones, and evidence fidelity — each creative must visibly
   follow the cited competitor pattern's structure/angle from
   `swipe/<app>/competitors.yaml`
   (structure copied, art and words ours; off-strategy creatives are blocked).
   Only after inspection:

   ```bash
   python3 scripts/qa.py approve --report <report.json> --reviewer <codex|claude> --confirm-all
   python3 scripts/qa.py status --report <report.json>
   ```

7. Validate the audience plan; publish targets ONE approved audience from
   `audiences/<app>.yaml` (cold = broad by country; never religious/sensitive
   interest segmentation — the creative + language carry the context):

   ```bash
   python3 scripts/audiences.py --app <slug>
   ```

8. If Meta Ads MCP exists, create the `PAUSED` manifest (it inherits the
   audience's market and only includes that market's creatives):

   ```bash
   python3 scripts/forge.py prepare-publish --qa-report <report.json> \
     --capabilities <capabilities.json> --account-id <id> \
     --campaign-id <id> --ad-set-id <id> --audience-id <audience> \
     --readiness-receipt <readiness.json> \
     --out <manifest.json>
   ```

   Discover each Meta tool schema, execute every manifest item as `PAUSED`, then
   call the exact readback tool named by the manifest. Save returned creative/ad
   IDs, artifact hash, account/campaign/ad-set IDs and observed time. Store the
   structured result in the byte-canonical
   `creative-forge/meta-ad-readback@1` envelope under ignored
   `runs/live-readbacks/`; its normalized provider result must itself match the
   IDs and `PAUSED` binding. Then bind its relative path and real sha256. Opaque
   or cross-bound JSON, symlinks, escapes, mutations and duplicate IDs fail.
   Then run
   `scripts/publish.py verify-receipt`.
   That validates the record but cannot substitute for the live read. Reuse each
   deterministic name/item key on retries. Missing MCP/schema/readback means
   BLOCKED, never a simulated success.

## Hard gates

- Never publish placeholders, fabricated reviews, stale signals, changed QA
  files, unresolved locales, or unsupported claims.
- Never reuse competitor video, frames, logos, music, voice or copy. A public
  ad/Top Ads URL is research, not a reuse license. `reference_only` assets are
  forbidden in output.
- Never call an unverified format video. Keep `unknown` and block any
  video-specific downstream use until verified.
- Do not claim CPP, RevenueCat events, Remotion license eligibility, Meta video
  upload/processing or provider availability without a fresh live receipt.
- For video, Claude/Codex must inspect each localized artifact and create the
  sealed per-artifact receipt; codec/timing checks cannot replace visual/
  cultural QA. When audio exists, review sound and muted. For intentional
  silence, verify silence and muted comprehension without a false audio claim.
- É proibido: nunca ativar campaign/ad set/ad e nunca mudar orçamento ou gasto. Isso
  requires a new explicit user confirmation after the paused receipt is verified.
- Report `DONE` or `BLOCKED` with evidence, commands, exact counts and external IDs.
