# PLAYBOOK — creative-forge

This is the shared Codex/Claude/human procedure and operating contract.

## Operating boundary

Claude/Codex is the researcher, strategist, creative director, transcreator,
editor and visual/performance reviewer. It decides sources, hook/beat
decomposition, hypotheses, concepts, copy, scenes, pacing, visual edits,
cultural fit and what the results mean.

Validators are intentionally narrower. They may enforce app identity,
provenance, rights, schema, hashes, timing, encoding, declared safe zones,
metric arithmetic, manifest coverage and `PAUSED` readback. They must not rate
creative quality, choose a hypothesis, infer cultural meaning or diagnose why
an ad performed.

Paid generation and external writes require human or explicit task
authorization. Activation, budget and spend always require a separate explicit
human confirmation.

**Creative Latitude:** be strict on truth, rights, provenance, claims, spend,
and external state. Be expansive on concepts, hooks, copy, composition,
scenes, pacing, formats, and visual language. A validator may verify what a
concept claims to derive from; it must never decide whether the creative leap
is good enough.

## Canonical 12-stage workflow

1. **Readiness** — confirm app truth, privacy/claim posture, markets, locales,
   destinations, product analytics and the runtime's real capabilities.
2. **Research 360** — gather competitor observations, own results, customer
   insight, trends, reviews, product and store evidence.
3. **Taxonomy** — label lineage/evidence honestly and let the agent decompose
   hook, beats, problem, mechanism, proof, demo, objection and CTA.
4. **Brief** — write audience, objective, `action → expected_result → reason`,
   primary KPI, window, isolated variable and destination.
5. **Concept portfolio** — classify exploration, promising iteration,
   own-winner iteration, format expansion or localization.
6. **Production** — agent writes copy, scenes, shot plan, prompts and edit
   direction using rights-cleared assets.
7. **Localization** — culturally transcreate copy, visuals, VO/captions, app UI
   and destination per market.
8. **Render** — create an atomic, parameterized artifact with tool/input
   versions.
9. **QA** — run technical gates, then let Claude/Codex inspect every image and
   full video; review sound and muted when audio exists, or verify intentional
   silence honestly.
10. **Publish PAUSED** — use a fresh capability receipt, approved
    audience/destination and exact external `PAUSED` readback.
11. **Test** — observe the declared window and attribution contract; do not
    auto-kill or auto-scale.
12. **Learning loop** — bind metrics back to the concept, record the agent's
    interpretation and feed one next variable into the next brief.

### Current capability boundary

The image workflow and local video workflow are executable. Brief, asset and
experiment validators, both video-mining modes, pinned Remotion, atomic render,
render receipts bound to props/engine/output, localized VO with rendered SRT
captions, FFprobe/FFmpeg checks, one native-resolution midpoint frame per
scene, sealed run locks and per-artifact playback receipts are implemented.
The bundled fictional Sunrise Walks en-US proof exercises that path in CI.
`providers.yaml` therefore marks Remotion `production_local`. Meta video
upload/processing/`PAUSED` readback, CPP live state and RevenueCat live state
remain capability-gated. Never improvise a provider schema, external ID or
success receipt.

The following sections document today's image path and the live gates that
remain useful as the broader workflow is implemented.

### Remotion local proof gate

For a fresh checkout or after renderer changes, rerun the proof gate:

```bash
cd remotion
npm ci --ignore-scripts
npm run typecheck
cd ..
python3 scripts/video.py audit-recipe --app sunrise-demo --recipe morning-ritual
python3 scripts/forge.py build-video \
  --app sunrise-demo --recipe morning-ritual --locale en-US --batch-id <batch>
```

Commit the intended code/config first. Playback QA deliberately refuses a dirty
Git worktree, seals the exact commit and clean status, and revalidates both at
approval time. CI runs this same real render + technical QA preparation path.

Then Claude/Codex inspects the complete localized artifact and records the
per-artifact receipt. When audio is present, review sound and muted; when the
declared strategy is intentional silence, verify technical silence and muted
comprehension without pretending an audio track was heard.
`build-video` accepts `--all-markets` only explicitly and renders sequentially.
For VO, each locale declares its own rights-cleared voice asset and SRT;
captions become visible Remotion sequences and both source files enter the run
lock.

## 0. Discover capabilities

Inspect the current agent's real tools. Record availability for PostHog MCP,
Meta Ads MCP, Higgsfield MCP/CLI and Remotion. Never assume a connector from a
previous run. `providers.yaml` defines preferred/fallback providers. Record
the exact agent, provider, checked time, tool names and relevant schemas with
no secrets. Missing capability blocks only its stage.

## 1. Research competitors

Use Meta Ads MCP Ad Library search when available; use the browser otherwise.
For each relevant creative record advertiser, market, active start, verified
format, hook, angle, source URL and evidence level in
`swipe/<app>/competitors.yaml`.

Long runtime is only `longevity_proxy`. Use `performance_data` only when actual
spend/result metrics exist. Use explicit lineage:
`competitor_pattern | own_winner | customer_insight | trend | exploratory`.
Only `own_winner` with performance data is a winner. Never copy literal
competitor art, assets, video, frames, voice, music or copy.

For public video, Claude/Codex watches and writes a concise derived beat
timeline; the validator only checks URL/timestamps/completeness. Do not download
or version competitor media. Keep `format: unknown` when it is not verified;
a downstream video-specific use must block. Licensed/owned video needs source,
hash, commercial/derivative rights and applicable releases before it can be a
production asset. Identifiable people require a hashed signed-release evidence
file. Keep contracts/releases under the ignored `assets/<app>/rights/` path;
the tracked registry stores only path, kind and digest, and the QA lock seals
the local bytes. The implemented audit is:

`python3 scripts/video_mining.py --app <app>`

```bash
python3 scripts/research.py --app <app>
```

## 2. Refresh app and market signals

Read the app's real `.lproj`/localization resources and Fastlane storefront
metadata independently. Refresh `signals/<app>.yaml` through PostHog MCP using
project, time window, app name, `not_testflight` and `not_emulator`. Add ASA/Meta
spend only from verified sources; without it, ROAS remains unavailable.

```bash
python3 scripts/locales.py --app <app>
python3 scripts/signals.py --app <app>
```

## 3. Turn evidence into agent-authored recipes

Every concept declares one `lineage_ref` inside its own `research_refs`. That
anchors the idea's provenance. A recipe may independently select an
`execution_ref` from its `research_refs` for a format or audiovisual pattern;
the two references may have different lineages. This lets an own-winner thesis
use an observed video grammar without relabeling either source. A production
recipe contains:

```yaml
template: pain-headline-cta
format: square
swiped_from: "StrideCo (fictional) ES — pain to peace"
research_refs: [demo-0000000000000001]
execution_ref: demo-0000000000000001
claims_used: [daily_ritual, daily_free]
target_markets: [br, mexico, spain]
ad_copy:
  pt: {primary_text: "...", headline: "...", description: "..."}
  es: {primary_text: "...", headline: "...", description: "..."}
locales:
  pt: { pain: "...", headline: "...", headline_accent: "...", sub: "...", cta: "..." }
  es: { pain: "...", headline: "...", headline_accent: "...", sub: "...", cta: "..." }
  it: { pain: "...", headline: "...", headline_accent: "...", sub: "...", cta: "..." }
  pl: { pain: "...", headline: "...", headline_accent: "...", sub: "...", cta: "..." }
  en: { pain: "...", headline: "...", headline_accent: "...", sub: "...", cta: "..." }
market_overrides:
  mexico:
    copy: { pain: "...", headline: "...", headline_accent: "...", sub: "...", cta: "..." }
    ad_copy: {primary_text: "...", headline: "...", description: "..."}
  spain:
    copy: { pain: "...", headline: "...", headline_accent: "...", sub: "...", cta: "..." }
    ad_copy: {primary_text: "...", headline: "...", description: "..."}
```

`target_markets` limits render and QA for this recipe. Use transcreation for
every targeted market. If two targets share one base `copy_language`, both
must declare full `market_overrides` keyed by market ID; the engine never
silently treats Mexico and Spain as one market. When
`require_native_market_copy: true`, fallback is a hard error. Review templates
require a genuine review plus verifiable source URL. Drafts live under
`recipes/<app>/drafts/` and never enter default batches.
Each market ID must use a unique `storefront_locale`; duplicate storefront
locales are blocked because output and QA paths use that canonical locale.

The concept provenance gate is lineage-aware:

- `competitor_pattern` requires a traceable competitor anchor, but does not
  force the recipe to copy its structure;
- `own_winner` requires a real own-performance anchor with metrics;
- `customer_insight` and `trend` require an anchor with that honest lineage;
- `exploratory` may anchor any traceable evidence and make an original leap.

The recipe's execution gate is separate. `execution_ref` may select a
format/audiovisual reference from another lineage. Structural fidelity is
required only when that explicitly selected execution record is
`competitor_pattern`; omitting `execution_ref` declares an original execution,
so template, composition, hook, and pacing remain agent decisions.
For video, an original execution also omits structural `references`; when an
`execution_ref` is selected, the inline `references` list covers exactly that
verified audiovisual pattern, not every source behind the concept.
Claims, rights, locales, and provenance remain hard gates for every lineage.

Higgsfield lifestyle/video shots may use the MCP only when the active agent can
prove a provider-enforced cost ceiling and reconcile the actual job cost. The
current CLI adapter is deliberately read-only: it discovers the live model
schema and estimates cost without creating a job. A short-lived quote can be
saved for planning, but it is an estimate — not authorization or a guarantee:

```bash
python3 scripts/higgsfield_video.py cost \
  --model <discovered-model> --params <params.json> \
  --max-cost <authorized-cap> --cost-unit credits \
  --quote-out runs/<app>/<batch>/higgsfield-quote.json
```

`generate` currently blocks before any paid provider call, even with
`--confirm-spend`. If a future provider exposes an enforceable cap plus actual
cost reconciliation, re-enable only with checkpoint/retry tests and a new
review. Any generated output would still need a separate asset-registry entry
with hashed rights evidence and explicit paid-ad/platform scope. Remotion local
rendering is production-proven; its Meta publish path remains blocked.

## 4. Preflight and build

```bash
python3 scripts/forge.py preflight --app <app>
python3 scripts/forge.py build --app <app> --batch-id <id> --jobs 4
python3 scripts/forge.py build-video \
  --app <app> --recipe <recipe> --locale <market-id-or-storefront-locale> --batch-id <id>
```

Preflight blocks stale/missing research, stale/missing signals, locale drift,
unknown research references, unsupported claims, missing assets, placeholders,
CTA policy errors and incomplete copy languages. Rendering is atomic: a failed or
timed-out Chrome process cannot reuse a stale PNG.
On macOS, Chrome launches are serialized by default for reliability; only set
`CREATIVE_FORGE_CHROME_MAX_PARALLEL` after proving parallel renders locally.

## 5. Automated and visual QA

`build` creates a QA report and contact sheets. Use each sheet only as an
index, then open every original PNG at native resolution. Inspect copy,
language, spelling, readability, wrapping, contrast, image artifacts/anatomy,
truthfulness, brand consistency, platform safe zones and lineage fidelity.
For competitor-pattern execution, that includes structural fidelity; for
original execution, it means fidelity to the agent-authored hypothesis and
concept anchor, not similarity to a competitor.

Only after real inspection:

```bash
python3 scripts/qa.py status --report <report.json>
python3 scripts/qa.py approve --report <report.json> --reviewer <agent> \
  --review-file <qa-review.json>
python3 scripts/qa.py status --report <report.json>
```

`qa-review.json` lists every required check plus one non-empty note for every
`artifact_key`. Reviewer, timestamp, checks, notes, dimensions, paths and
hashes are sealed; changing any of them invalidates approval.

```json
{
  "checks": ["copy_correct", "readable", "imagery_consistent", "claims_truthful", "safe_zones", "no_artifacts", "lineage_fidelity"],
  "artifact_reviews": [
    {"artifact_key": "<from qa.py status>", "notes": "What was inspected in the original PNG."}
  ]
}
```

Changing one PNG invalidates approval.

For video, the same rule becomes per-artifact playback QA: inspect the whole
file plus every sealed native-resolution scene frame and, when audio exists,
review with sound and muted; inspect captions, hook, rhythm, scene
transitions, anatomy/artifacts, copy, truth, cultural fit and platform UI
occlusion. FFprobe/FFmpeg may validate timing/encoding/black or silent segments;
they do not approve aesthetics or meaning.

Video approval uses a separate review JSON with `notes` plus the complete
`PLAYBACK_CHECKS` list, passed through `--review-file`. The contact sheet and
scene frames help inspection; only watching the complete MP4 proves rhythm,
transitions, sound intent, and full-timeline behavior.

## 6. Audience plan, then publish as PAUSED

Every publish targets one approved audience from `audiences/<app>.yaml`
(hypothesis, funnel stage, market, data provenance, confidence, approval).
Cold = broad by country/language; warm/high-intent = own-data custom audiences;
lookalike = own seed ≥ 100. Never religious/sensitive interest segmentation —
the creative + language + country carry the context. Validate with:

```bash
python3 scripts/audiences.py --app <app>
```

Discover exact Meta Ads MCP schemas and create a capability receipt that names
a real read-only tool distinct from every create/update/delete/write action.
Query the live destination (default
App Store page, CPP or landing page) and save `readiness.json` with response
digest. Save the exact raw response under ignored `runs/live-readbacks/`; every
receipt must bind its repository-relative `response_path` and real sha256.
Symlinks, absolute/escaping paths and post-receipt mutations are rejected.
Include each additional stage gate required by the app config, such as
Meta app events and attribution mapping; all receipts expire after 60 minutes. Generate a manifest only after
QA passes. It inherits the audience's market, localized ad copy and exact
destination, and only includes that market's creatives:

```bash
python3 scripts/forge.py prepare-publish \
  --qa-report <report.json> --capabilities <capabilities.json> \
  --account-id <id> --campaign-id <id> --ad-set-id <id> \
  --audience-id <audience> \
  --readiness-receipt readiness.json \
  --out <manifest.json>
```

The Meta Ads MCP has no image-upload tool: `ads_create_creative` needs a public
`image_url` (or a pre-uploaded `image_hash`). Host the approved PNGs (e.g. the
app site's `/ads/` folder) at publish time; receipts stay bound by sha256.

Create each creative/ad with `PAUSED`, call the bound readback tool and preserve
its structured result in a byte-canonical
`creative-forge/meta-ad-readback@1` envelope. It must cross-bind provider/tool,
time, `PAUSED`, item, account/campaign/ad-set, creative/ad and artifact hash.
The normalized provider result inside the envelope must repeat and agree with
those fields; contradictory `ACTIVE` or unrelated responses fail:

```bash
python3 scripts/publish.py verify-receipt --app <app> --manifest <manifest.json> --receipt <receipt.json>
```

Use the deterministic `creative_name`, `ad_name` and `item_key` from the manifest
to avoid duplicate retries. Every receipt item must explicitly return
`status: PAUSED`. The local verifier validates the receipt structure and
binding; it cannot prove a provider call happened. A `DONE` claim therefore
requires the live readback observed in the current run. Never enable delivery,
budget or spend without a new explicit user confirmation.

Video remains blocked unless capability discovery proves upload, processing,
creative/ad creation and readback for that exact media path. Image capability
does not imply video capability.

The manifest ships **one ad per `(concept_id, variant_id)` pair** in the app's
`publish.primary_format`, capped by `max_ads_per_ad_set`. Multiple formats of a
variant are collapsed; distinct variants are never silently discarded.

## 7. iOS go checklist (verified facts, 2026-07)

Before the FIRST app-install campaign for an iOS app, verify — sources are
Meta's own help center and RevenueCat docs, adversarially verified 2026-07-09:

- **App active in Meta Events Manager**: Meta must have received events in the
  last 90 days via Facebook SDK, Conversions API, App Events API or an MMP
  ([Meta app activity requirements](https://www.facebook.com/business/help/670955636925518)).
  No events = fix this first.
- **SKAdNetwork**: app-side SKAN compatibility is required even with
  server-to-server events and no SDK; send the iOS version with app events
  ([Meta iOS campaign guidance](https://www.facebook.com/business/help/2750680505215705)).
  SKAN *event configuration* in
  Events Manager is NOT needed when optimizing only for installs; it is needed
  to optimize/report post-install events. AEM is the fallback path if SKAN
  can't be configured (no SKAN reporting).
- **Meta SDK**: iOS 14+ campaigns via SDK require Facebook SDK >= 16.2.1
  (Limited Login + SKAN 4.0). **RevenueCat's Meta integration REQUIRES the Meta
  SDK running in the app** — S2S alone doesn't unlock it — and by default only
  forwards events when ATT is authorized, unless the dashboard override "Send
  events when ATT consent is not authorized" is enabled
  ([RevenueCat Meta Ads integration](https://www.revenuecat.com/docs/integrations/attribution/meta-ads)).
  Events map to
  StartTrial / Subscribe.
- **Advantage+ creative enhancements**: some are ON by default and vary by
  format/placement; opt out per enhancement in Ads Manager before launch if
  the creative must render exactly as QA'd
  ([Meta Advantage+ creative guidance](https://www.facebook.com/business/help/297506218282224)).
- **Page**: ads run from a Page; a brand-new empty Page hurts trust — give it
  identity and a few posts before the go.

## 8. Test, interpret, recommend (the loop after PAUSED)

Structure (verified sources): Phiture treats each value proposition as its own
ad group with an explicit hypothesis (action / result / reason). AppAgent's
2024 deck reports a 1:10 creative win ratio and separates hook, basic and
advanced iterations. Treat those as portfolio context, never as a universal
threshold. A round with no winner is a learning result, not a pipeline failure.

Operational defaults (ours, not platform facts, editable in
`apps/<app>.yaml → testing_policy`):
the Sunrise Walks config currently declares 72h, spend/CPI, CTR, frequency and
bounded scaling contexts. These are operator defaults, not sourced platform constants. The agent interprets them alongside sample quality and
feeds results back into
`signals/<app>.yaml` (paid section) so the next batch is chosen by data.

These thresholds generate recommendations only. They never pause, activate,
kill, scale or change budget automatically.

## 9. Rights, localization and destination continuity

Every output asset needs source/provenance, SHA-256, rights class, derivative
permission and paid-ad scope covering every recipe `target_platforms` value.
`reference_only` is forbidden in outputs. Commissioned, licensed and generated
assets additionally need a local hashed
contract/license/terms snapshot plus explicit paid-ad and platform scope.
Music, footage, fonts, voices, likenesses, reviews and quotations are separate
rights surfaces; clearance on one platform is not assumed portable.

Keep market, copy language, app locale, storefront locale and destination as
separate fields. Transcreate rather than translate literally, but do not change
product truth. If a concept depends on an Apple Custom Product Page, bind its
approved ID/URL and locale; missing or unapproved CPP is a blocker, not a silent
fallback to the default page.

## 10. Observability contract

The target lineage is:

`app_slug → brief_id → concept_id → variant_id → locale/format → artifact_sha256 → item_key → platform creative/ad IDs → metric window → decision receipt → next brief`

Store currency, attribution window, spend, revenue and sample status with each
result. Validators recalculate CTR/CVR/CPI/CPA/ROAS only from complete inputs;
missing data is `insufficient_data`, never zero. Video experiments additionally
carry `video_3s_views` and `video_thruplay_views` (Meta: 3-second plays /
ThruPlay), from which hook rate (3s ÷ impressions) and hold rate (ThruPlay ÷
3s) are derived — they say whether a losing video failed at the hook or in the
body, which decides whether the next iteration is a cheap first-3-seconds swap
or a new concept. Image experiments omit both fields and keep the original
contract. Claude/Codex interprets the
result and states what would falsify the next recommendation.

Record the immutable result with:

```bash
python3 scripts/experiments.py \
  --file <result.yaml> --manifest <exact-manifest.json> \
  --publish-receipt <exact-paused-receipt.json> \
  --metrics-source <normalized-insights-envelope.json> \
  --evidence-root <root-containing-receipt-response-paths> --app <slug>
```

The validator reuses the complete publish verifier in historical mode (only age
expiry is disabled) and requires a
`creative-forge/meta-insights-readback@1` source. Its tool, exact metrics,
date/currency/attribution window, item, app, brief, concept, variant, artifact
and every Meta account/campaign/ad-set/creative/ad ID are cross-bound. A
`final` decision must resolve `agent_decision.next_brief_ref` under the same
app. The contract is not end-to-end proven until a real paid result supplies
the platform IDs/metrics and feeds that next brief.
