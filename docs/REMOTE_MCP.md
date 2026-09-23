# Remote Biologix MCP on Modal

The deployment builds the repository `Dockerfile` directly on Modal with
`SLIM=0` (the Dockerfile default). AiZynthFinder models, ZINC stock, the
Molport cache, precursor records, ADMET weights, OpenMM, AmberTools, Packmol,
RDKit, OpenFF, PyMOL, and biologic PDB inputs are therefore part of the image.
Only `/app/runs` and `/app/papers` are persistent Modal Volumes; `/app/data`
must remain the image layer so a Volume cannot hide the baked databases.

## Create the bearer token

Generate the token locally and create the required Modal Secret:

```bash
export BIOLOGIX_MCP_TOKEN="$(openssl rand -hex 32)"
modal secret create biologix-mcp-secrets \
  BIOLOGIX_MCP_TOKEN="$BIOLOGIX_MCP_TOKEN"
```

The token is an application password. It is not a Modal token or an Anthropic
API key. Keep it in a password manager or secret store. Do not commit it.

Claude Web also needs a separate approval credential and OAuth storage key.
Generate both locally, store both in the same password manager, and create the
dedicated secret:

```bash
export BIOLOGIX_OAUTH_APPROVAL_TOKEN="$(openssl rand -hex 32)"
export BIOLOGIX_OAUTH_STORAGE_KEY="$(openssl rand -hex 32)"
modal secret create biologix-mcp-auth \
  BIOLOGIX_OAUTH_APPROVAL_TOKEN="$BIOLOGIX_OAUTH_APPROVAL_TOKEN" \
  BIOLOGIX_OAUTH_STORAGE_KEY="$BIOLOGIX_OAUTH_STORAGE_KEY"
```

`BIOLOGIX_OAUTH_APPROVAL_TOKEN` is the private Biologix access token entered
only on `/oauth/approve`. `BIOLOGIX_OAUTH_STORAGE_KEY` encrypts registered
clients, pending requests, authorization codes, and token hashes in
`/app/runs/.oauth/state.enc`. Neither value is an Anthropic credential.

Optional provider keys for PaperQA or Asta can be added to the same Modal
Secret through the Modal dashboard. If recreating the secret with `--force`,
include every key because replacement removes omitted values:

- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `OPENROUTER_API_KEY`
- `ASTA_API_KEY`
- `SEMANTIC_SCHOLAR_API_KEY`

## Deploy

From the repository root:

```bash
scripts/deploy_modal.sh
```

It runs `modal deploy modal_app.py` and then `modal run modal_app.py::verify_runtime`
(pass `--skip-verify` to deploy only). Modal prints the HTTPS endpoint for the `serve` function. The MCP URL is that
endpoint with `/mcp` appended:

```bash
export BIOLOGIX_MCP_URL="https://<workspace>--biologix-mcp-serve.modal.run/mcp"
```

The image has two parts. The base image (conda environment, AiZynthFinder
models, precursor database, ADMET environment) is built from the Dockerfile with a
context that holds only its inputs: `Dockerfile`, `environment-simulation.yml`,
`pyproject.toml`, `README.md`, the install scripts, `extern/`, `data/`, `docker/`,
and the RetroSynthesisAgent bootstrap module. Modal caches a Dockerfile build as
one layer keyed on that whole context, so the base rebuilds (about an hour) only
when one of those inputs changes. The project source (`src/python`, `scripts`,
`biologix_ai_mcp_server.py`) is added when each container starts, so a code change
deploys in minutes. Because the image verifier needs that source, the base build
skips it (`VERIFY_IMAGE=0`) and `deploy_modal.sh` runs it after deploying; local
`docker build` still verifies during the build.

## Connect Claude Web

The server supports OAuth 2.1 Dynamic Client Registration (DCR), PKCE, rotating
refresh tokens, and RFC 9728 protected-resource discovery.

1. In Claude Web, add a custom connector.
2. Enter only the deployed MCP URL ending in `/mcp`.
3. Leave the OAuth Client ID field empty. DCR creates the client automatically.
4. Choose **Connect**. Claude redirects the browser to the Biologix
   `/oauth/approve` page.
5. Enter `BIOLOGIX_OAUTH_APPROVAL_TOKEN` and approve the request.

The approval page redirects only to the callback URL registered by that DCR
client. Access tokens expire after one hour. Each refresh token lasts 30 days,
is one-time-use, and is replaced whenever Claude refreshes the connection.

## Connect ChatGPT

ChatGPT discovers OAuth from `GET /.well-known/oauth-protected-resource` and
registers as a public client (`token_endpoint_auth_method: none`) with PKCE.
The authorization-server metadata advertises `none` alongside the confidential
client methods Claude uses.

1. In ChatGPT, add a custom MCP connector.
2. Enter the deployed MCP URL ending in `/mcp`.
3. Finish the approval page with `BIOLOGIX_OAUTH_APPROVAL_TOKEN`.

## Connect Claude Code

Choose one configuration method.

### User-scoped command

This stores the resolved header in the user's Claude configuration, outside the
repository:

```bash
claude mcp add --transport http --scope user biologix \
  "$BIOLOGIX_MCP_URL" \
  --header "Authorization: Bearer $BIOLOGIX_MCP_TOKEN"
```

### Project `.mcp.json`

The committed `.mcp.json` contains placeholders only. Export both variables
before starting Claude Code:

```bash
export BIOLOGIX_MCP_URL="https://<workspace>--biologix-mcp-serve.modal.run/mcp"
export BIOLOGIX_MCP_TOKEN="<retrieve-from-your-secret-store>"
claude mcp list
claude
```

Claude Code expands `${BIOLOGIX_MCP_URL}` and `${BIOLOGIX_MCP_TOKEN}` at
runtime. If either variable is absent, `claude mcp list` reports a
missing-variable warning and the server will not authenticate.

## Rotation and revocation

- Rotate only the approval token by updating `biologix-mcp-auth` with a new
  `BIOLOGIX_OAUTH_APPROVAL_TOKEN` and the existing storage key, then redeploy.
  Existing Claude Web sessions remain valid.
- OAuth clients can revoke an individual access token or refresh token through
  the advertised `/revoke` endpoint.
- To revoke every OAuth client and session, remove
  `.oauth/state.enc` from the `biologix-mcp-runs` Volume, generate a new storage
  key and approval token, replace `biologix-mcp-auth`, and redeploy. Rotating the
  storage key without removing the old encrypted file deliberately fails
  closed; it does not silently discard registrations.
- Rotate `BIOLOGIX_MCP_TOKEN` separately in `biologix-mcp-secrets` when the
  Claude Code bearer credential must be revoked.

## Onboarding behavior

ChatGPT uses about the first 512 characters of the MCP `instructions` field, and
Claude Web does not use that field. The remote server therefore sends a short
directive there: ask for the biologic and the polymer (or `"suggest"`), call
`begin_biologix_discovery` first, follow `next_required_tool`, and speak to the
user only when `user_stop_allowed` is true. `begin_biologix_discovery` returns
the full protocol (`src/python/biologix_ai/protocol/PROTOCOL.md`), and every
tool result carries a `protocol` envelope whose `step_instructions` quote the
protocol section for the next step, so clients that ignore server instructions
(Claude Web, Grok) still get each rule when they need it. The repository's
`CLAUDE.md` is a developer guide, not the agent prompt.

The server also refuses a tool call that skips a step and names the required
next tool. Reconnect the connector after a deploy so a new chat loads the
directive.

### Project instructions

ChatGPT and Claude project instructions are the one system-prompt slot those
products offer. Pasting the directive there is optional; the server enforces
the same order without it.

```text
Biologix runs one fixed discovery pipeline. If the user has not given a biologic (name or PDB ID) and a polymer target or "suggest", ask only for those and wait. Then call begin_biologix_discovery before any other Biologix tool; it returns the full protocol. Call one Biologix tool at a time. Every result names next_required_tool: call it next. Do not stop, summarize, or ask the user until user_stop_allowed is true or a tool fails. Write PSMILES yourself. Never invent simulation results.
```

## Compute: CPU and GPU from one URL

The web function `serve` never runs OpenMM itself. `openmm_evaluate_psmiles`
takes `compute="cpu"` or `compute="gpu"` and calls the matching Modal function:

| Function | Hardware | OpenMM platform |
|---|---|---|
| `openmm_worker_cpu` | 8 vCPU, 16 GB | CPU, 8 threads |
| `openmm_worker_gpu` | `BIOLOGIX_GPU` (default `L4`), 4 vCPU | CUDA, mixed precision |

An empty `compute` uses `BIOLOGIX_DEFAULT_COMPUTE`. When a target is large (a full
IgG, above `BIOLOGIX_LARGE_TARGET_ATOMS`, default 15,000 atoms), the gate puts
`compute="gpu"` in `next_arguments`. A GPU request that cannot create a CUDA
context fails with the exact error; it never runs on CPU silently. Every
outcome records `openmm_platform` and `compute`.

Choose these when deploying:

```bash
BIOLOGIX_GPU=L4 BIOLOGIX_DEFAULT_COMPUTE=cpu setsid nohup modal deploy modal_app.py > /tmp/modal_deploy.log 2>&1 &
```

`BIOLOGIX_MODAL_APP=biologix-mcp-gpu` deploys a second, independent app with its
own URL, for example one whose default compute is `gpu`. Workers receive their
inputs and return their artifacts in the call, so they share no Volume with the
web container.

## Long calls

Remote clients time out long tool calls. A call that takes longer than
`BIOLOGIX_TOOL_WAIT_S` (240 s) keeps running and returns
`{"status": "running", "job_id": ...}`; its envelope names
`await_biologix_job`, which waits again and returns the finished result with
the normal envelope. While a job runs, other pipeline calls from the same client
return `JOB_RUNNING`. Other clients are unaffected: state, the `MCP_BUSY` lock,
and jobs are all per MCP session. `scaledown_window=900` keeps the web
container alive between waits. Set `BIOLOGIX_MIN_CONTAINERS=1` to avoid cold
starts, at the cost of one always-on container.

## Biologic targets

`resolve_biologic_target` accepts a name (`semaglutide`), `PDB:chains`
(`4ZGM:B`), a bare PDB ID (biological assembly 1), `uniprot:ACCESSION[:a-b]`
(AlphaFold DB), or `sequence:ONE_LETTER` (ESMFold, at most 400 residues). It
prepares the structure with PDBFixer, runs the AMBER14 check the simulation will
run, caches the result under `/app/runs/.structures/`, and lists every
substitution and removal. The summary report's "Target structure" section
repeats them. When resolution fails, the agent retries with a more specific form
twice before the pipeline stops.

## Budgets

Per iteration the gate carries three candidates through OpenMM and three
through retrosynthesis. These are compute budgets, not scientific limits: set
`BIOLOGIX_MAX_OPENMM_CANDIDATES` and `BIOLOGIX_MAX_RETRO_TARGETS` before
deploying to carry more. A run costs roughly one GPU-minute per OpenMM
candidate on an L4, more for a large target.

Screening only marks a candidate `fail` when the tools cannot use the
structure. An ADMET alert is measured on a methyl-capped monomer proxy rather
than on the polymer, so it is a `warning` with a `disposition_reason` and the
candidate still reaches simulation, with the alert reported beside the result.
Charged repeat units are supported. A zwitterion (sulfobetaine,
phosphorylcholine) is net neutral and needs nothing. A polyelectrolyte is packed
with Na+ or Cl- counterions so the polymer matrix is neutral and its PME energy
carries no neutralising-background artefact; the OpenMM result reports them
under `counterions`. The protein keeps its own net charge, as it does in every
run, so existing results stay comparable. `BIOLOGIX_AI_OPENMM_NEUTRALIZE=no`
refuses a charged matrix instead of packing ions.

## Other clients

`clients/` holds generated setup for Cursor, the Claude Code plugin,
Antigravity, ChatGPT, and Grok (`python scripts/build_client_adapters.py`).
`biologix_runtime_status` reports Packmol, OpenMM platforms, AiZynthFinder,
ADMET, and compute from any client, and `GET /healthz` is an unauthenticated
liveness probe.

## Verification

```bash
modal run modal_app.py::verify_runtime
modal run modal_app.py::verify_gpu
curl -s https://<workspace>--biologix-mcp-serve.modal.run/healthz
claude mcp get biologix
```

`verify_gpu` must report `"name": "CUDA"` and a completed insulin outcome.

The image build fails unless all scientific assets pass deep validation:

- all three AiZynthFinder ONNX models load and both template tables are readable
- the ZINC HDF5 stock contains at least one million records
- the Molport pickle contains at least one million InChIKeys
- manual and SMiPoly precursor tiers meet their minimum record counts
- bundled biologic PDB files contain coordinate records
- RetroSynAgent builds a session-derived polymer route and AiZynthFinder
  attempts and solves its monomer route
- ADMET-AI returns predictions from its isolated environment

An HTTP request without a token must receive `401 Unauthorized` with an RFC
9728 `resource_metadata` challenge. Verify DCR, approval, PKCE exchange, refresh,
and revoke behavior before connecting Claude Web. The existing bearer token
must still authenticate Claude Code. A connected client can then call a cheap
tool such as `validate_psmiles`.
