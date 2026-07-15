# Getting started — onboarding a real app

The demo (`sunrise-demo`) exists so you can watch the machine run. This guide
takes you from that to **your** app producing sealed, publishable creatives.

The pipeline is fail-closed on purpose: at every step below, the honest move
is to let a validator block you and then fix the input — never to loosen the
validator. If you find yourself editing a gate, read
[CONTRIBUTING.md](../CONTRIBUTING.md) first.

## 0. Prerequisites

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cd remotion && npm ci --ignore-scripts && cd ..   # only needed for video
```

Run the demo once end to end (`README → Quick start`) so you know what a
green run looks like before you introduce your own variables.

## 1. Describe your app — `apps/<slug>.yaml`

Copy the demo and edit every section. It is the single source of truth the
validators check everything else against:

```bash
cp apps/sunrise-demo.yaml apps/my-app.yaml
```

| Section | What it controls | Fail-closed behavior |
|---|---|---|
| `store` | real App Store id + URL | publish readiness does a **live** readback of this URL |
| `palette` / `fonts` / `assets` | brand system injected into every template | renders never hardcode colors |
| `voice` | per-language anchors, **banned phrases**, approved CTAs | copy containing a banned phrase fails preflight |
| `claims` | every marketing claim → evidence file + `verified_at` | a recipe using an undeclared claim is blocked |
| `locales.markets` | market → language, storefront, currency | a recipe targeting an unlisted market is blocked |
| `policy` | sensitive context, targeting limits, human-confirmation rules | `allow_sensitive_interest_targeting` is expected to stay `false` |
| `publish` | format, per-ad-set cap, static asset hosting | manifests exceeding the cap are refused |
| `testing_policy` | budgets, windows, guardrails for the learning loop | recommendations outside guardrails are flagged |

The two sections people get wrong:

- **`claims`** — "free daily content", "no account", "syncs with X" are
  claims. Each needs an `evidence` pointer to something in *your* repo
  (source file, product doc) plus a `verified_at` date. If you can't point at
  evidence, you can't say it in an ad. That's the feature.
- **Asset rights** — every image/SVG you reference must appear in
  `assets/<slug>/registry.yaml` with source, sha256, and a rights block
  (`status: cleared`, commercial + derivative scope). Unregistered assets
  don't render.

## 2. Record real research — `swipe/<slug>/competitors.yaml`

This is the pipeline's foundation and the one stage that cannot be faked:
**a creative without a traceable evidence anchor does not build.** That anchor
may be competitor structure, your own performance, customer insight, a trend,
or evidence supporting an exploratory concept.

Go to the [Meta Ad Library](https://www.facebook.com/ads/library/), find
competitors that have kept the same ads running for months (longevity ≈
validation), and record the *pattern*, never the media:

```yaml
- id: my-app-0001
  competitor: BigRival
  observed_at: "2026-07-01"
  source_url: "https://www.facebook.com/ads/library/?id=..."
  evidence_level: observed        # you actually saw it — no hearsay
  pattern:
    structure: "pain question → product moment → CTA"
    angle: relief
    why_it_works: "names the 7am anxiety moment before selling anything"
```

Rules the validators enforce: `source_url` must be a real platform domain,
`observed_at` can't be in the future, and video recipes may only cite
patterns that are themselves **verified video** entries. Structure is
swipeable; art, words, voices and footage are not.

## 3. Brief and recipes

A brief (`briefs/<slug>/…yaml`) turns research into named concepts with a
hypothesis each. A recipe (`recipes/<slug>/…yaml`) turns one concept into a
buildable creative:

```yaml
template: pain-headline-cta          # must exist in templates/image/
concept_id: morning-relief           # must exist in the brief
research_refs: [my-app-0001]         # must exist in your swipe file
execution_ref: my-app-0001           # optional format/structure anchor
claims_used: [daily_free, no_account]  # must be declared in apps/my-app.yaml
target_markets: [us, mexico]         # must be declared in locales.markets
locales:
  en: { pain: "...", headline: "...", sub: "...", cta: "Download free" }
  es: { ... }                        # transcreate — don't translate
```

The concept also declares `lineage` and `lineage_ref`. `execution_ref` belongs
to the recipe and may point to another lineage. Only an execution reference
classified as `competitor_pattern` adds `swiped_from` and must match the
template's `swipe_angles`; omit it for an original execution. This separation
keeps the concept's evidence honest while allowing a different visual grammar.
Per-field character limits remain technical fit constraints, not taste scores.
Video follows the same rule: an original execution has no `execution_ref` and
no structural `references`; when one is selected, `references` covers exactly
that verified audiovisual pattern. Concept research remains independent.

## 4. Build and seal

```bash
python3 scripts/forge.py preflight --app my-app          # whole-config validation
python3 scripts/forge.py build --app my-app --batch-id 001 --jobs 4
```

`build` renders the full matrix (recipe × market × format), hashes every PNG,
and writes a QA report + contact sheets under `qa/my-app/001/`. On macOS, the
renderer serializes full Chrome launches by default; override
`CREATIVE_FORGE_CHROME_MAX_PARALLEL` only after a local parallel smoke test.

Then a human
— or an agent that uses the sheets as an index and **opens every original
PNG at full resolution** — records notes by `artifact_key` and approves:

```bash
python3 scripts/qa.py approve --report qa/my-app/001/report.json \
  --reviewer your-name --review-file qa-review.json
```

The approval seals the batch: report digest + input digest. Change one byte
of a rendered PNG, a recipe, or the app config and the seal is void — you
rebuild and re-approve. There is no way to "fix one small thing" after
approval, by design.

Video is the same loop with a physical-check contract on top
(`build-video` → `video_qa.py`): mute-safe captions, safe zones, duration,
and a required human/agent viewing of the actual MP4.

## 5. Audiences

```bash
python3 scripts/audiences.py --app my-app
```

Audience plans (`audiences/<slug>.yaml`) are deliberately boring: broad
targeting, country + language + OS, optimization event. Sensitive-interest
targeting (religion, health, politics…) is refused regardless of what the
platform UI would let you do — the *creative* carries the context instead.

## 6. Publish — PAUSED, with receipts

This stage needs your ad-platform tooling connected (e.g. a Meta Ads MCP).
The contract, in order — each step blocks the next:

1. `capabilities` receipt: discover the platform tools you actually have,
   fresh within 60 minutes.
2. Readiness receipts: live checks that the store page is up, app events are
   arriving, attribution is mapped. Raw responses stored with sha256.
3. `forge.py prepare-publish`: manifest bound to your sealed QA matrix —
   `PAUSED` hardcoded, `activation_allowed: false`.
4. Create ads from the manifest, then **read each one back** and store the
   byte-canonical readback envelope proving `PAUSED`.
5. `scripts/publish.py verify-receipt` re-validates the whole chain.

Activation, budget changes, and spend are yours alone. The pipeline will
prepare everything and then stop — that's it working, not failing.

## 7. Learn — `scripts/experiments.py`

After your test window (72h default), record results with provenance: which
tool reported which metric, when. Missing data is `insufficient_data`, never
zero. Video metrics carry hook rate (3s views / impressions) and hold rate
(thruplay / 3s views), so a loser tells you whether the first 3 seconds or
the body failed. The verdict binds into your next brief — the loop closes.

## Troubleshooting the right way

| Symptom | It's telling you |
|---|---|
| `preflight` rejects a claim | add evidence to `apps/<slug>.yaml` or cut the claim from the copy |
| recipe blocked for research | record the pattern you're actually swiping in `swipe/` first |
| QA seal voided | you edited an input after approval — rebuild, re-approve |
| publish refuses to run | a readiness receipt is stale or a live readback failed; fix the real-world thing |
| a metric shows `insufficient_data` | it *is* insufficient — wait or widen the window, don't guess |

If a gate seems wrong rather than inconvenient, open an issue — with the
failing artifact attached.
