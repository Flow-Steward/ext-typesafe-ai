# TypeSafe AI (JEV) for Flow Steward

Universal JEV decisions through standard **project connections**, with an
importable support-request classification example. No chat model or additional
LLM is needed by the example.

## Set up

1. Install the release ZIP through **Extensions → Install from file** or the
   Flow Steward extension catalog.
2. Open **TypeSafe AI → Connections**, choose a project, give the connection a
   name and enter a key from [TypeSafe Console](https://console.typesafe.ai/).
   The key is stored in that project's encrypted connection secrets. Create as
   many independent connections as needed, including several in one project.
3. Choose a default model (`jev-latest` initially) and a timeout of 1–60 seconds.
4. Use **Test connection**. It authenticates with `GET /v1/models`; it does not
   send a decision request. Package health alone does not verify credentials.
5. Download the separate workflow YAML asset from the release, or use
   [`examples/jev_support_triage-1.0.1.yaml`](examples/jev_support_triage-1.0.1.yaml), and import it.
   Map the TypeSafe extension dependency and the `jev-example` connection
   dependency to your installed extension and chosen project connection.
6. In the imported `jev_evaluate` child, verify that **Evaluate with connection key**
   selects the intended connection. Enable the child and then the parent after
   validation. Run `jev_support_triage` with a `message`.

The example uses the saved project connection's encrypted key. Import only
requires the normal dependency mapping and enablement; no step parameters or
secret references need editing after import.

The example ships disabled, carries its child workflow and contains no API key,
configured secret default or machine-specific install ID. Importing it does not
make an API call. **Test Run can call TypeSafe and consume provider credits.**

## Universal connector

**Evaluate with JEV** takes the selected connection and:

| Input | Meaning |
| --- | --- |
| `state` | Text, an object or an array to evaluate. |
| `questions` | Named `choice`, `score` or `noul` questions. |
| `model` | Optional override of the connection's default model. |
| `api_key` | Optional workflow secret input overriding the connection's key for this invocation. |

It returns `model`, `answers` and `usage` using the
[official TypeSafe API contract](https://docs.typesafe.ai/api). A Choice returns
`choice`, `confidence` and `probabilities`; a Score returns `score`, `confidence`,
`legend` and `probabilities`; a Noul returns a yes probability in `noul`.
Noul probability and Choice/Score confidence are different quantities.

The endpoint is fixed to `https://api.typesafe.ai/v1/systemone`. Redirects are
refused. No key can redirect a request to another host. The connector has no
third-party runtime dependencies and does not retry requests internally.
Authentication, rate limit, timeout, TLS, transport and malformed-response errors
use stable error codes. Raw provider error bodies and credentials are never
returned. Requests are bounded to 2 MiB and responses to 4 MiB; image/Box
questions and compressed responses are outside this version's contract.

### Workflow secret defaults

The connector accepts an optional `api_key` mapped from a workflow input of
type `secret`. An advanced workflow can declare that input, configure its
encrypted secret default in the editor and map `state.inputs.api_key` into the
connector. Flow Steward resolves the opaque reference at its authorized
connector boundary; never pass the key through Template or an agent prompt.

The released example uses only the saved connection and has no secret override
input. Some Flow Steward v2 importers redact credential-named mappings even when
their values are references. Keeping that optional advanced setup outside the
base example lets it import without repairing mappings. Do not add a literal
key to YAML, an operation parameter, a text input or source control.

## Classification example

`jev_support_triage` prepares three questions in one place and calls the reusable
`jev_evaluate` child:

- **Choice:** `billing`, `technical`, `sales` or `general`.
- **Score:** urgency on four descriptive levels, from routine to critical.
- **Noul:** whether a human is required.

The graph returns `manual_review` when category or urgency confidence is below
`min_confidence` (default `0.7`), when the human-required probability is at least
`human_threshold` (default `0.8`), or when the category is unrecognized. Otherwise
it returns the selected category as `routing_key`.

Outputs are `classification`, `routing_key`, `needs_review`, `evaluated_model`
and `usage`. **Manual review is a routing recommendation:** this example creates
no ticket, approval task or notification. Connect your existing human-handling
workflow to that outcome if required.

The thresholds are illustrative. Calibrate them on labelled examples from your
own requests before acting automatically. Pin a model version when thresholds
must remain stable. The example explicitly passes `jev-latest`; change the
parent's model mapping to pin another version. The child's `model` input has no
fixed default, so callers can supply it.
The child workflow's field-based input takes a `state`
object; wrap text as `{"message": "..."}`. Direct connector calls support all
three TypeSafe state shapes (string, object and array).

## Why an extension

[JEV is not a chat-completion model](https://docs.typesafe.ai/introduction/coding-agents)
and cannot be used as the LLM behind an agent through Add model. A direct HTTP
workflow can call its API with a project secret. The target Flow Steward
HTTP-step implementation currently does not resolve workflow-input secret
references for Bearer auth; connector dispatch already does. This extension
reuses that boundary and the normal project connection UI. Classification,
thresholds and routing remain editable workflow configuration.

## Development and verification

Use the target Flow Steward **Development source environment** and its public
Docker-backed tooling, following its canonical `AGENTS.md` and
`docs/standards/*`. Put this repository in the canonical folder
`extensions/flowsteward_typesafe_ai/`:

```sh
./flow-steward extensions validate flowsteward.typesafe-ai --root extensions --health
./flow-steward extensions test flowsteward.typesafe-ai --root extensions --non-interactive
./flow-steward extensions bundle flowsteward.typesafe-ai --root extensions --output-dir dist/extensions --non-interactive --confirm
```

`tests/test_jev.py` verifies wire requests, all three state shapes, mixed question
types, connection selection, optional key overrides, safe errors and response
validation without real provider I/O. `tests/platform_contracts.py` is an
integration suite for the matching Flow Steward source checkout: it uses the
actual manifest compiler, bundle importer, workflow hub and secret boundary.
These platform tests require the source checkout's Python environment; they are
not standalone tests of this zero-dependency runtime.

Live authentication and inference require your own key. A passing mocked
transport test does not claim live JEV verification.

JEV HTTP 400 and 422 validation rejections are definitive, non-retryable
failures. They follow the workflow's configured error edges (`provider_failed`
in the evaluation child, then `classification_failed` in the triage example)
without an operation-recovery approval. Timeouts and other uncertain failures
retain the host's conservative recovery policy. Error details contain only
the HTTP status, never provider response bodies or authorization headers.

Releases use the standard Flow Steward catalog archive format. The runtime ZIP
excludes development tooling, tests and examples; the workflow example is a
separate release asset, and importing it remains an explicit operator action.
Publish a version
tag only after local verification; then register this repository in
`Flow-Steward/extensions/extensions/flowsteward.typesafe-ai.yaml` and regenerate
that repository's catalog using its existing publisher.

## Live acceptance

On 2026-10-16, the JEV connector and classification graph were
verified on a local Flow Steward v2 installation using a saved project
connection. A synthetic HTTP 500 production outage completed the whole parent
and child flow in 3 seconds: model `jev-1.13.0`, category `technical` with
confidence `1.0`, urgency `3` with confidence `1.0`, `routing_key: technical`,
`needs_review: false`. The connector's optional workflow-key override has automated coverage;
it was not exercised with a real credential in this acceptance run.

The core UI masks `input_tokens` and `output_tokens` as `[REDACTED]` and does not
add this extension call to its model-cost total. A displayed zero cost does not
mean the provider request was free.
