# Vendored: avoid-ai-writing detector

`patterns.js` is the deterministic detector from
https://github.com/conorbronsdon/avoid-ai-writing (MIT, Copyright (c) 2026 Conor
Bronsdon; see `LICENSE`), copied unchanged at the commit in `VERSION`.
`run_detector.js` is ours: it reads `{text, contextMode, sourceMode}` as JSON on
stdin and prints the detector's `analyzeText` result.

Update by copying `detector/patterns.js` from the upstream repository again.
