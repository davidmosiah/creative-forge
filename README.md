# creative-forge

**An agent-driven paid-creative workflow that refuses to lie to you.**

creative-forge turns competitor research into localized, QA-sealed ad
creatives — and treats every external claim as untrusted until a live
readback proves it. It was built to be operated by AI agents (Claude, Codex,
or any CLI agent) with a human owner holding the only keys that matter:
activation, budget, and spend.

```
readiness → research 360 → taxonomy → brief → concept portfolio → production
→ localization → render → QA → publish PAUSED → test → learning loop
```

## Why it exists

Most creative pipelines optimize for output volume. This one optimizes for
**truth under automation**:

- **Swipe fidelity is enforced, not suggested.** A creative that doesn't
  derive from cited, recorded competitor research is blocked at preflight —
  structure is copied, art and words are always your own.
- **Fail-closed everywhere.** Missing rights, stale research, placeholder
  copy, locale drift, or a changed byte after QA approval = hard stop.
- **A local receipt never proves external state.** Publishing requires a
  fresh capability receipt, a live destination readback, and a byte-canonical
  `PAUSED` readback envelope from the ad platform. An `ACTIVE` response
  cannot be masked by local bookkeeping.
- **Ads are born PAUSED.** Activation, budget and spend always require a
  separate, explicit human confirmation. The engine cannot spend money.
- **Honest metrics.** Missing data is `insufficient_data`, never zero. ROAS
  is never invented. Video experiments carry hook/hold rates so a losing ad
  tells you *where* it lost.

## Quickstart (fictional demo app included)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cd remotion && npm ci --ignore-scripts && npm run typecheck && cd ..

# Validate everything about the demo app (research, claims, locales, rights)
python3 scripts/forge.py preflight --app sunrise-demo

# Render the full creative matrix + QA report + contact sheets
python3 scripts/forge.py build --app sunrise-demo --batch-id demo-001 --jobs 2

# After a human/agent ACTUALLY inspects the contact sheets:
python3 scripts/qa.py approve --report qa/sunrise-demo/demo-001/report.json \
  --reviewer you --confirm-all

# Static dashboard of everything the sealed artifacts prove
python3 scripts/dashboard.py --app sunrise-demo --open
```

`sunrise-demo` is a fictional product with fictional research so the entire
pipeline runs end to end out of the box. To onboard a real app, copy the
`apps/sunrise-demo.yaml` shape and replace every fictional fact with a real,
evidenced one — the validators will tell you exactly what's missing.

## What's inside

| Piece | What it does |
|---|---|
| `scripts/forge.py` | preflight / build / build-video / prepare-publish |
| `scripts/qa.py`, `video_qa.py` | sealed QA: hashes, dimensions, safe zones, per-artifact visual approval |
| `scripts/research.py`, `video_mining.py` | competitor research contracts (structure only, never media reuse) |
| `scripts/audiences.py` | fail-closed audience plans (no sensitive-interest targeting) |
| `scripts/publish.py` | PAUSED-only manifests, capability receipts, canonical readback envelopes |
| `scripts/experiments.py` | learning loop: metrics provenance, hook/hold rates, next-brief binding |
| `scripts/host_assets.py` | content-hash hosting + live byte-for-byte verification |
| `scripts/dashboard.py` | static evidence viewer (reads sealed artifacts only) |
| `remotion/` | generic video composition (story/portrait/square, mute-safe) |
| `templates/` | HTML creative templates with per-field char limits |

## Design rules that will not change

1. Competitors' **public** signals only; their art, media, voices and copy
   are never reused.
2. No religious or sensitive-interest ad targeting — context lives in the
   creative, language and country.
3. The agent authors hypotheses, copy and scenes; validators only enforce
   identity, provenance, rights, schema, timing and arithmetic.
4. Every bound on coverage is logged — silent truncation is treated as lying.

## License

AGPL-3.0. If you run a modified version as a service, share your changes.
