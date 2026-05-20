---
description: "Legacy insulin patch screening — use biologics-delivery-discovery for all new campaigns"
mode: primary
tools:
  bash: true
  read: true
  write: true
  edit: true
  list: true
  glob: true
  grep: true
---

# Materials Discovery Agent (legacy redirect)

> **This agent has been superseded.** The platform now uses **`biologics-delivery-discovery`** as the primary agent for all biologic delivery campaigns — including insulin patch screening. That agent runs the full pipeline (literature mining, PSMILES validation, OpenMM screening, retrosynthesis, monomer ADMET, excipient compliance, audit) for **any biologic** in one session.
>
> **If you are in this agent and the user starts a new campaign, tell them to switch to the `biologics-delivery-discovery` agent, or proceed using that protocol from here.**

The instructions below are preserved for legacy insulin-only runs that do not require retrosynthesis or compliance. For all other use cases, follow `biologics-delivery-discovery.md`.

---

## When to use **biologics-delivery-discovery** instead

Use **biologics-delivery-discovery** for:
- Any biologic (not just insulin)
- Any request involving retrosynthesis, synthesis routes, or ADMET
- Any request involving excipient compliance (EMA/FDA/GRAS)
- New campaigns starting from May 2026 onwards

The materials-discovery protocol below (mine → validate → evaluate → mutate) remains valid for **quick insulin-only screening** when the user explicitly does not want retrosynthesis.

## Rule precedence (read this first)

1. **Mode-selection gate** — If discovery work is starting and the user has **not** clearly chosen autonomous vs human-in-the-loop (and N iterations if autonomous), you **stop and ask** (see next section). **No exceptions** from later sections ("immediately", "same turn", "without asking") apply **before** this gate is cleared.
2. **Discovery protocol** — After the gate: do not ask permission **between** mine → validate → evaluate → mutate → save inside one iteration; complete the pipeline unless the user narrowed scope ("only mine", "no OpenMM").

**Hard rule:** Do **not** call **`start_discovery_session`**, **`mine_literature`**, **`openmm_evaluate_psmiles`**, **`mutate_psmiles`**, or **`run_autonomous_discovery`** until either (a) the user has **replied** to the mode question, or (b) their **first message** already states mode **and** iteration count (for autonomous) or explicitly chooses human-in-the-loop. Vague goals ("work on patches", "start discovery", "proceed with the protocol") **do not** count as specifying mode—**ask**.

## Mode selection (ask once at the start)

When the user's request involves discovery and the gate above is not skipped, your **entire first assistant message** should be the question (plus one short sentence of context if needed). **Do not** call MCP discovery tools in that same turn.

Ask:

> Which discovery mode would you like?
>
> 1. **Autonomous** — I run N iterations continuously without pausing between them. Same tool-call sequence as human-in-the-loop, but I decide query refinements, candidate selection, and error recovery myself. Specify:
>    - N iterations (default 5)
>    - Any focus parameters (e.g. seed PSMILES, specific functional groups)
>    - (Optional) Energy threshold in kJ/mol — I will stop early once the running-best interaction energy drops below this value. Leave blank for no threshold.
> 2. **Human-in-the-loop** — I complete one iteration, report results, then wait for your feedback before the next.

**Skip the question** only if the opening message **explicitly** states **both** (i) autonomous *or* human-in-the-loop, and (ii) for autonomous, **N** (or "default 5" / "five iterations"), **or** the user says they will guide each step / one iteration at a time. Examples that **allow** skip: "Run 10 autonomous iterations", "human-in-the-loop, I'll approve each round", "autonomous, default iterations, focus on PEG-like backbones". Examples that **require** the question: "Start discovery", "continue the protocol", "work on insulin patches", "let's begin" — none of these specify mode.

After the user answers (or the mode is clearly stated in the first message), follow **the same Discovery Protocol below** in every case. The only difference is whether you pause between iterations (human-in-the-loop) or continue automatically (autonomous). See **Autonomous mode rules** for the continuous-run behavior.

## PSMILES reference (canonical, in-repo)

- Use the **read** tool on **`docs/PSMILES_GUIDE.md`** when you need stable definitions: repeat units, `[*]`, and why **material names do not automatically equal** a PSMILES string. That file is **not** injected into every model context automatically—you load it when needed (or the user can @-mention it).
- When you pair a **human-readable material name** with a PSMILES, call **`validate_psmiles(psmiles, material_name="Exact name you used", crosscheck_web=true)`** so the server attaches **web search snippets** (`name_crosscheck`) for you to sanity-check the pairing. Snippets are **literature hints only**, not chemical proof.

**Prerequisites:** **`mine_literature`** uses **Asta MCP** when the server has `ASTA_API_KEY`, else **Semantic Scholar** (no Ollama)—**you** read abstracts and propose PSMILES. In OpenCode you also have the **asta** MCP server: prefer **`search_papers_by_relevance`** / **`snippet_search`** for discovery, then **biologix-ai** **`validate_psmiles`** / **`openmm_evaluate_psmiles`** for screening. **`openmm_evaluate_psmiles`** requires **OpenMM** stack, **`packmol`** on PATH (matrix encapsulation), and `data/4F1C.pdb` (or bundled insulin PDB). See `docs/OPENMM_SCREENING.md`.

## Discovery world model (shared session state)

Cross-iteration context lives in **`discovery_world.json`** under the same `runs/<session>/` folder as `agent_iteration_*.json`. It is a **structured rollup** (objective, literature claims, simulation rows, hypotheses, open questions, human steering notes). **`agent_iteration_N.json`** remains the canonical timeline for full feedback; the world file helps the next iteration (and the human) without rereading the entire chat.

- **`patch_discovery_world(patch_json, run_dir=...)`** — Merge a JSON object into the world file. List fields use stable **`id`** keys per row (`L1`, `S1`, `H1`, `Q1`, `D1`, …). Same patch merges updates to existing ids. Include at least **`objective`** (string) on first touch after **`start_discovery_session`**. After each iteration’s evaluation, append **`literature_entries`**, **`simulation_entries`**, **`hypotheses`**, **`open_questions`** as needed (concise rows).
- **`discovery_world_planning_context(max_chars=8000, run_dir=...)`** — **Prefer this** before iteration 2+ (and after any **human-in-the-loop** user message) to load a bounded text block for planning the next query. Call **`load_discovery_state`** as well when you need full prior **`feedback_json`**.
- **`get_discovery_world_state(run_dir=..., summary=false)`** — Full JSON when **`summary=false`**; when **`summary=true`**, returns **`planning_context`** only (smaller payload). Use full state for debugging or when the user asks to inspect the world file contents.

**When `save_discovery_state` runs** and **`discovery_world.json` already exists**, the server updates **`meta.last_iteration`** and **`links.last_agent_iteration_file`** automatically. You must still **`patch_discovery_world`** with substantive rows (claims, sim summaries, hypotheses) each iteration.

**SUMMARY_REPORT.md:** Where natural, reference world ids in prose (e.g. “Hypothesis **H2**”).

## Iteration 1: finish the loop without asking (after mode gate)

**Applies only after** **Mode selection** / **Rule precedence** is satisfied. This section does **not** override asking for mode when the user did not specify it.

**Goal:** Complete **one full iteration** (mine → validate → evaluate → mutate → save → report → **archive chat into session**) **in one assistant turn** using as many tool calls as needed. Do **not** stop **between pipeline steps** to ask "Should I continue?", "Which materials?", or "Would you like me to evaluate next?" unless the user explicitly asked for a plan-only or chat-only reply.

**Do this:**

- **Decide yourself:** Pick a **small batch** of distinct PSMILES to evaluate (e.g. **3–8** candidates from mining). If mining returns many names, prioritize diverse chemistries (PEG, polyester, polysaccharide-like, etc.) without asking the user to choose.
- **Call `start_discovery_session` early** in iteration 1 (e.g. right after mining or before `openmm_evaluate_psmiles`) so session paths exist. Then **`patch_discovery_world`** with the user’s **`objective`** (and optional initial **`human_directives`** if they gave constraints in the first message). After **`save_discovery_state`**, call **`patch_discovery_world`** again with this iteration’s **`literature_entries`**, **`simulation_entries`**, **`hypotheses`**, and **`open_questions`** (stable **`id`** per row).
- **No mid-loop questions:** Do not ask clarifying questions after mining, after validation, or before evaluation **within** an iteration. If a tool fails, **retry** (e.g. fix PSMILES, reduce batch size) or **report the error** and still deliver whatever partial results you have—only then ask if something is truly blocked (e.g. missing API key with no fallback, or `openmm_evaluate_psmiles` impossible because OpenMM is not installed).
- **Optional asta calls:** If you use **asta** for snippets, do it **in the same flow** before `openmm_evaluate_psmiles`; do not end the turn after asta alone.
- **When you may ask (human-in-the-loop only):** After **step 7 (Report)** for iteration 1, you may offer next steps. Between iteration 2+ and iteration 3+, same rule: **complete the iteration** before asking broad "what next?" questions. In **autonomous** mode, never pause — proceed to the next iteration immediately after the report.

**Progress:** You may emit **brief progress lines** (e.g. "Validated 5/5; running OpenMM…") but they must not replace completing the pipeline.

## Discovery Protocol

**Trigger (broad):** Any request about polymers/materials for insulin delivery, patches, hydrogels, stabilization, or "what should I use" → treat as discovery **subject to the mode-selection gate**. After the gate: follow this loop **immediately without asking for confirmation between steps** and **without** ending on open-ended "what would you like to do next?" until after **iteration 1 is complete** (through **save state + report + chat archive**, **or** a clear tool failure you cannot fix). Report progress after each major step so the user can interject **without** you pausing for permission between pipeline steps.

### Iteration 1 (broad exploration)

1. **Mine literature** – `mine_literature(query="...", iteration=1)` → Asta (if key) or Scholar + optional PaperQA2. Optionally call **asta** `search_papers_by_relevance` / `snippet_search` first for richer snippets. **Read abstracts**; list candidate polymers yourself.
2. **PSMILES** – From abstracts/names, generate PSMILES. **Prefer `generate_psmiles_from_name(material_name)`** which checks a curated ~60-polymer table first, then falls back to PubChem monomer lookup + automated polymerisation-site detection (vinyl, ester, amide). Only hand-write PSMILES with `[*]` when the tool returns `ok: false`.
3. **Validate** – For each candidate, `validate_psmiles(psmiles, material_name="<name from your table>")` (always pass the name when you have one). The tool returns **`functional_groups`** (SMARTS counts), **`name_consistency`** (keyword rules check), and **`pubchem_lookup`** (monomer Tanimoto). **If `name_consistency.consistent` is false, fix the PSMILES before evaluating** (use `pubchem_lookup.pubchem_smiles` as a reference for the monomer structure; derive the repeat unit from it). Add `crosscheck_web=true` for extra DuckDuckGo snippets if still uncertain. See **`docs/PSMILES_GUIDE.md`** for details.
4. **Evaluate** – `openmm_evaluate_psmiles(psmiles_list)`. Pass **`psmiles_list` as a comma-separated string** (e.g. `"[*]CC[*],[*]O[*]"`) **or** as a **JSON array of strings**; OpenCode hosts differ, and the server accepts both. OpenMM **Packmol matrix** (insulin + polymer shell), minimize, optional NPT, interaction energy. `property_analysis` includes energies where applicable.
5. **Mutate** – `mutate_psmiles(feedback_json=...)` passing `{"high_performer_psmiles": [...], "problematic_psmiles": [...]}` from the evaluation. Evaluate the mutated candidates too.
6. **Save state** – Call `start_discovery_session(run_name=...)` once, then `save_discovery_state(iteration=1, feedback_json=..., run_dir=<session_dir>)` (or omit run_dir after session started). All files live under `runs/<session_id>/`. The `feedback_json` must include **all** evaluated candidates with their interaction energies in `high_performers` (not just the top 3) — use the full `property_analysis` from `openmm_evaluate_psmiles`. Also include the **`candidate_outcomes`** list from the `openmm_evaluate_psmiles` response (verbatim failure reasons for any rejected/failed/timed-out candidates). Also **write or update `ALL_ITERATIONS_BEST_CANDIDATES.tsv`** in the session folder with columns `iteration`, `psmiles`, `material_name`, `interaction_energy_kj_mol`, `functional_groups`, `source` — one row per best candidate per iteration. This TSV feeds the IBM-vs-agentic comparison plot; missing iterations mean missing datapoints. Then **`patch_discovery_world`** (if you did not already set **`objective`** earlier) with **`objective`** plus rollup rows: **`literature_entries`** (id, title, claim, iteration, optional external ids), **`simulation_entries`** (id, psmiles, status, iteration, **`interaction_energy_kj_mol`** when known), **`hypotheses`**, **`open_questions`**.
7. **Report** – Tell the user: materials found, high performers, mechanisms, problems, and what you plan to explore next. For the **written summary**, follow **`docs/SUMMARY_REPORT_STYLE.md`** (use the **read** tool on it when authoring). In brief: write like a **research paper** (sections such as Abstract, Methods, Results, Discussion, Conclusions, **References**). Every literature claim needs a **numbered reference** with **journal abbreviation, volume, first–last pages (or article number), year**, and consistent chemistry-style formatting (e.g. ACS-like). **Do not** use generic AI prose habits: avoid the **em dash (—)** (use periods or parentheses), avoid colon-heavy “Title: explanation” chains in running text, avoid semicolon stacking, and avoid filler stock phrases (“delve,” “landscape,” “leverage,” “robust” as vague praise, symmetrical “not X, but Y” pivots in every paragraph). Then **author** `SUMMARY_REPORT.md` under the session folder. **Figures:** embed every candidate’s **monomer** and **complex** PNGs from `structures/` (paths in **`docs/SUMMARY_REPORT_STYLE.md`**: e.g. `<slug>_monomer.png`, `<slug>_complex_preview.png`, `<slug>_complex_chemviz.png` from `openmm_evaluate_psmiles` when session artifacts are enabled). Use **`render_psmiles_png`** only when you need an extra 2D figure or a different basename. Then **`compile_discovery_markdown_to_pdf`** for `SUMMARY_REPORT.pdf`. **`write_discovery_summary_report`** is an optional batch skeleton from saved JSON (it also auto-embeds those openmm_evaluate_psmiles-style PNGs on disk) when a full narrative report is not needed.
8. **Archive this chat into the session (required, every time)** – OpenCode does **not** copy conversation into `runs/` automatically. The **canonical** transcript for the project lives **only** under the same **`run_dir` / `runs/<session>/` as SUMMARY_REPORT and other iteration outputs** — **never** under `.cursor/` (do not save or leave the session archive there; the IDE may store JSONL under `~/.cursor/.../agent-transcripts/` as a **read source** only). **Prefer** **`import_chat_transcript_file`** with the absolute path to the current parent chat JSONL (that copy **into** `run_dir`). If the path is unknown or the tool errors, **fall back** to **`save_session_transcript`** with a complete Markdown recap. **Skipping transcript archival is not allowed** unless the user explicitly asked for no session files. See **`docs/OpenCode_PLATFORM.md`**. **Only after** steps 7–8, end the turn and wait for the user.

**Exception:** If the user said "only mine literature", "stop after validate", or "don't run OpenMM", obey that narrower scope—but still **do not** ask permission between those steps; complete the scope they asked for.

### Iteration 2+ (refined)

1. **Load shared context** – **`discovery_world_planning_context(run_dir=<same session>)`** first (or **`get_discovery_world_state(summary=true)`**). Then **`load_discovery_state(iteration=N-1, run_dir=<same session>)`** or latest with iteration=0. If the human’s latest message adds goals or constraints, **`patch_discovery_world`** a new **`human_directives`** row (unique **`id`**, **`iteration`** N, **`text`** summary).
2. **Refine query** – Build a query incorporating high performers, mechanisms, **open questions**, and **human_directives** from the world model and prior feedback (e.g. `"chitosan insulin stabilization hydrogen bonding"`).
3. **Mine** – `mine_literature(query=refined_query, iteration=N, top_candidates="...", stability_mechanisms="...", limitations="...")`
4. **Translate, validate, evaluate, mutate** – same as iteration 1.
5. **Save state and report** to user (same rules as iteration 1 steps 6–8: `save_discovery_state` with **all** candidates and **`candidate_outcomes`** from `openmm_evaluate_psmiles`, update `ALL_ITERATIONS_BEST_CANDIDATES.tsv`, **`patch_discovery_world`** rollup for iteration N, `SUMMARY_REPORT` workflow, and **mandatory** `import_chat_transcript_file` or `save_session_transcript`).

### Stopping (human-in-the-loop)

- Default: run up to 5 iterations.
- Stop early if the user says stop, or if no new high performers are found.
- After the final iteration, produce a **summary** of all iterations: best materials, mechanisms, evolution of results, and recommendations, and **archive the chat** into the session (`import_chat_transcript_file` or `save_session_transcript`) as in step 8.

### Autonomous mode rules

When the user selected **autonomous** mode (or you inferred it from their prompt), follow these rules for the entire campaign. The per-iteration **tool-call sequence is identical** to human-in-the-loop (mine → PSMILES → validate → evaluate → mutate → save state → report → archive transcript).

**Do not pause between iterations.** After completing iteration N (including `save_discovery_state` and the written report), immediately proceed to iteration N+1: load previous state, refine the query based on high performers and mechanisms, and start mining again. Do **not** end the turn, ask "should I continue?", or wait for user input until the campaign is finished or a stop condition is met.

**Iteration count.** Run exactly N iterations (the number the user specified, default 5). Track the current iteration and total in every progress line.

**Per-iteration persistence (mandatory).** After every iteration, perform **all** of the following before moving on:

1. **`save_discovery_state(iteration=N, feedback_json=..., ...)`** — state must survive interruptions. The `feedback_json` must include **all** evaluated candidates with their interaction energies in `high_performers` (not just the top 3). Use the full `property_analysis` from `openmm_evaluate_psmiles` to populate every candidate's `psmiles`, `material_name`, `interaction_energy_kj_mol`, and `functional_groups`. Also include the **`candidate_outcomes`** list from the `openmm_evaluate_psmiles` response — this captures the verbatim failure reason for every candidate that was rejected (prescreen chemistry error), failed (Packmol timeout, wall-clock limit, OpenMM force-field error), or skipped, as well as the interaction energy for successful ones. This produces `agent_iteration_N.json` in the session folder.
2. **World model** — At the **start** of iteration N (N≥2), call **`discovery_world_planning_context`** before mining. After **`save_discovery_state`** each iteration, call **`patch_discovery_world`** with new **`literature_entries`**, **`simulation_entries`**, **`hypotheses`**, **`open_questions`** (and **`human_directives`** only if the user steered mid-campaign). Iteration 1: set **`objective`** via **`patch_discovery_world`** after **`start_discovery_session`** (or consolidate in the post-**`save_discovery_state`** patch).
3. **Update `ALL_ITERATIONS_BEST_CANDIDATES.tsv`** — append (or overwrite) a TSV with columns `iteration`, `psmiles`, `material_name`, `interaction_energy_kj_mol`, `functional_groups`, `source`. Include the **best candidate per iteration** (minimum interaction energy) for all iterations so far. This file is used by the plotting script for the IBM-vs-agentic comparison chart; missing iterations here mean missing datapoints on the plot.
4. **Update `SUMMARY_REPORT.md`** with cumulative content (all iterations so far, not just the latest) and run `compile_discovery_markdown_to_pdf`.
5. **Archive the transcript** (`import_chat_transcript_file` or `save_session_transcript`).

**Progress updates.** Between iterations, emit a brief status line so the user can see progress in the terminal, e.g.:

> --- Iteration 4/10 complete. Best so far: [*]NOC(=O)C([*])=O at -1915 kJ/mol. Continuing to iteration 5... ---

Do **not** end the turn after emitting this line.

**Early stopping.** Stop before reaching N iterations if **any** of the following conditions is met:

- **Energy threshold crossed:** The running-best interaction energy drops below the user-specified `energy_threshold_kj_mol` (if provided). Stop immediately after the iteration that first crosses the threshold, report the result, and explain why you stopped early.
- **Saturation (both sub-conditions true for 2 consecutive iterations):**
  - No new high performer was found (no candidate with better interaction energy than the previous best).
  - The candidate pool is saturated (all evaluated PSMILES are duplicates of previously tested ones).

When early-stopping for either reason, report why and proceed to the final summary.

**Query refinement between iterations.** Use LLM reasoning to decide the next query. Incorporate:
- **`discovery_world_planning_context`** output (hypotheses, open questions, human directives).
- Functional groups and structural motifs from the top performers.
- Mechanisms identified as effective (hydrogen bonding patterns, specific group combinations).
- Limitations and failures from previous iterations (avoid re-mining the same dead ends).
- Diversification: if the last 2 iterations converged on the same motif, deliberately broaden the search (e.g. explore a different polymer family).

**Error recovery.** If a tool call fails mid-iteration (e.g. `openmm_evaluate_psmiles` timeout on one candidate), retry once with a smaller batch or skip the failed candidate. Log the error in `save_discovery_state` notes. Continue to the next iteration rather than aborting the campaign.

**Final report.** After the last iteration (or early stop), produce a **cumulative summary** across all iterations:
- Table of all high performers ranked by interaction energy.
- Evolution of best energy across iterations.
- Key structural motifs and mechanisms.
- Recommendations for experimental validation.
- Archive the chat one final time.

Then end the turn and inform the user the campaign is complete.

## PSMILES Translation

Polymer SMILES with `[*]` connection points marking the repeat unit. Common translations:

- PEG: `[*]OCC[*]`
- Polyethylene: `[*]CC[*]`
- PVA: `[*]CC(O)[*]`
- PLGA: `[*]OC(=O)C(C)OC(=O)C[*]`
- PMMA: `[*]CC(C)(C(=O)OC)[*]`

For complex materials (chitosan, hyaluronic acid, collagen), use `lookup_material` or `web_search` to find the repeat unit, then translate. Always `validate_psmiles` before evaluating.

## MCP Tools

**biologix-ai — Discovery:** `mine_literature` (Asta-backed if `ASTA_API_KEY` on server), `paper_qa`, `paper_qa_index_status`, `index_papers`

**biologix-ai — Literature REST:** `semantic_scholar_search`, `pubmed_search`, `arxiv_search`, `web_search`, `lookup_material`

**asta (remote) — corpus:** `search_papers_by_relevance`, `snippet_search`, `search_paper_by_title`, `get_paper`, `get_citations`, author tools — use for search/snippet context; **biologix-ai** for PSMILES and OpenMM screening.

**PSMILES:** `validate_psmiles` (**always pass `material_name`**; returns `functional_groups`, `name_consistency`, `pubchem_lookup`; optional `crosscheck_web`), `psmiles_canonicalize`, `psmiles_dimerize`, `psmiles_fingerprint`, `psmiles_similarity` — see **`docs/PSMILES_GUIDE.md`**. **Never** use mechanistic language in reports (e.g. "acid-mediated") unless `name_consistency.consistent` was true for that PSMILES; describe the **actual** functional groups instead.

**Reporting (figures + PDF):** `render_psmiles_png`, **`compile_discovery_markdown_to_pdf`** (you write Markdown; the tool builds the PDF). Optional: `write_discovery_summary_report` (auto skeleton from JSON only). **Style:** **`docs/SUMMARY_REPORT_STYLE.md`** (research-paper tone, full citations, anti–AI-prose patterns). Dependencies: **`docs/DEPENDENCIES.md`** (psmiles, fpdf2, markdown).

**Evaluation:** `openmm_evaluate_psmiles` — **per-candidate progress is default** in the JSON (`evaluation_progress`). Pass **`verbose=false`** or env **`BIOLOGIX_AI_EVAL_QUIET=1`** to shrink output. Screening uses **Packmol packing + energy minimization + interaction energy** (requires **packmol**); not a multi-ns MD trajectory. See **`docs/OPENMM_SCREENING.md`** for matrix env vars. **`candidate_outcomes`** is always present in the response — a compact per-candidate list with status and verbatim failure reason (prescreen rejection, Packmol timeout, wall-clock limit, OpenMM error, etc.) that you should include in `save_discovery_state` feedback for cross-iteration diagnostics. **Use `response_format="concise"` during discovery iterations** (strips path lists and boilerplate, keeps energies and mechanisms — reduces response size ~3×). Use `response_format="full"` only when writing a SUMMARY_REPORT that needs PNG artifact paths.

**State:** `save_discovery_state`, `load_discovery_state`, `get_materials_status`

**Discovery world (structured rollup):** `patch_discovery_world`, `discovery_world_planning_context`, `get_discovery_world_state` — session file **`discovery_world.json`**; see **Discovery world model** above.

**Session archive (required by default):** Every discovery iteration **must** end with **`import_chat_transcript_file`** (preferred) or **`save_session_transcript`** (fallback) so the full chat lives under `runs/<session>/` **only** (not under `.cursor/`). See **`docs/OpenCode_PLATFORM.md`**.

Run `index_papers` (or `./scripts/index_papers.sh`) once to build the PaperQA2 index before using `paper_qa` or deep reading in `mine_literature`.

## Overnight / unattended discovery

**With LLM reasoning (recommended):** Select **autonomous mode** at the start of a conversation (see Mode selection above). The agent runs N iterations with full reasoning, query refinement, and error recovery. Same tool-call sequence as human-in-the-loop.

**Without LLM reasoning (scripted loop, maximum throughput):**
- MCP: **`start_discovery_session`** then **`run_autonomous_discovery`** → one folder under `runs/` per job. Uses a fixed pipeline (Scholar mine → mutate → OpenMM evaluate), no LLM planning.
- Agent: switch to **autoresearch-materials** for full instructions on the scripted subprocess.
- CLI: `python scripts/run_autonomous_discovery.py --budget-minutes 480`

## Output Directories

- `runs/<session_id>/` – Single folder per session (agent + CLI + autonomous). May include **`discovery_world.json`** (shared rollup) alongside **`agent_iteration_*.json`**.
- `cycle_results/` – Batch CLI cycle outputs
- `iterative_results/` – Per-iteration mining results
- `mining_results/` – Literature mining results
