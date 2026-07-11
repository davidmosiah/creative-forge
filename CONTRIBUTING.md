# Contributing

PRs welcome. Two rules gate every merge:

1. **Never weaken a fail-closed gate.** A contribution that makes a validator
   more permissive needs a very good reason and its own tests proving the new
   boundary still blocks fabricated state.
2. **All tests green** (`python3 -m pytest` and `cd remotion && npm run
   typecheck`) with the fictional `sunrise-demo` app only — PRs must not
   introduce real product data, real ad IDs, or real research.
