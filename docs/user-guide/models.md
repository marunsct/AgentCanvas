# Models and API keys

AgentCanvas works with any major model provider and with models running on your own computer. You choose a
default model once on the first-run screen, and any **AI model** block on the canvas can use a different one.

![Choosing a model](img/setup.png)

## Supported providers

A model is written `provider:model`. Type it in a AI model block's **Model** field (suggestions are listed), or pick it
in **Settings › Models**.

| Provider | Example model id | API key (project secret or server variable) |
|---|---|---|
| Anthropic | `anthropic:claude-sonnet-5-5` | `ANTHROPIC_API_KEY` |
| OpenAI | `openai:gpt-6.1-sol` | `OPENAI_API_KEY` |
| Azure OpenAI | `azure_openai:<deployment>` | `AZURE_OPENAI_API_KEY` (plus endpoint and API version) |
| Google Gemini | `google_genai:gemini-3.6-flash` | `GOOGLE_API_KEY` |
| Mistral AI | `mistralai:mistral-large-latest` | `MISTRAL_API_KEY` |
| Groq | `groq:llama-3.3-70b-versatile` | `GROQ_API_KEY` |
| DeepSeek | `deepseek:deepseek-v4-pro` | `DEEPSEEK_API_KEY` |
| xAI | `xai:grok-4.7` | `XAI_API_KEY` |
| Any OpenAI-compatible API (OpenRouter, Together, Fireworks, a gateway…) | `openai_compatible:<model>` | `OPENAI_COMPATIBLE_API_KEY`, or a secret you pick; a **Base URL** is required |
| AWS Bedrock (your AWS account) | `bedrock_converse:eu.anthropic.claude-sonnet-5-5` | no key: AWS credentials and a **Region**, see [below](#models-in-your-own-cloud-account) |
| Google Vertex AI, Gemini (your Google Cloud project) | `google_vertexai:gemini-3.8-flash` | no key: Google Cloud credentials, a **Region** and a project |
| Google Vertex AI, Claude (your Google Cloud project) | `google_anthropic_vertex:claude-sonnet-5-5` | no key: Google Cloud credentials, a **Region** and a project |

### Models on your computer

| Server | Example model id | Default address |
|---|---|---|
| [Ollama](https://ollama.com) | `ollama:llama3.3`, `ollama:qwen3`, `ollama:gpt-oss:20b` | `http://localhost:11434` |
| [LM Studio](https://lmstudio.ai) | `lmstudio:<model loaded in LM Studio>` | `http://localhost:1234/v1` |
| [vLLM](https://docs.vllm.ai) | `vllm:<served model name>` | `http://localhost:8000/v1` |
| [llama.cpp server](https://github.com/ggml-org/llama.cpp) | `llamacpp:<model>` | `http://localhost:8080/v1` |

Local models need no key unless the server requires one: LM Studio with **Require authentication** on in its
server settings (paste one of its API tokens), vLLM or llama.cpp started with `--api-key`. Such a key is the project
secret `LMSTUDIO_API_KEY`, `VLLM_API_KEY` or `LLAMACPP_API_KEY` (or the server variable of that name), and the
first-run screen stores it for you. Set **Base URL** only when the server is not on its default address (the vLLM default
port 8000 is also the AgentCanvas API's in development, so run vLLM on another port and set the Base URL). Start the
server and load or pull the model before running a workflow.

The catalog lists the current models with their prices (the Claude and OpenAI ones), so costs and cost budgets count
them; a model the provider has announced it will stop serving shows its date and the editor warns (W023), and it is
no longer offered for new choices. Claude Haiku 5.5 is the quick, low-cost model the templates use.

Current Claude models (Opus 5.5, Sonnet 5.5, Haiku 5.5, Fable 5.1) and reasoning models such as GPT-5 do not take
a **Temperature** (or top_p, top_k): the provider refuses a request that sets one, so a run leaves it out and the
editor says so (W020). To make answers more varied or more exact, say so in the prompt.

A stored key goes only where it was stored for: a provider's key to that provider, a server's key to the address saved
with it. A model whose **Base URL** points anywhere else gets a key only when its own **API key** setting names one
(a secret, chosen on purpose), so a workflow cannot send the project's keys to a server of its choosing. Local models vary in how well they call tools and
produce structured output: if an agent misbehaves, try a larger model.

When AgentCanvas runs in Docker, `localhost` is the container itself: local servers are reached through
`host.docker.internal` automatically (`AGENTCANVAS_LOCAL_MODELS_HOST`).

Any other provider LangChain's `init_chat_model` supports can be used by its prefix too, once its integration
package is installed on the server; the editor then warns that the model is not in the catalog (`W010`).

## Models in your own cloud account

AWS Bedrock and Google Vertex AI run the models in your organisation's own cloud account: the calls are billed
there, fall under the data-processing terms you have with that cloud, can stay on its private network, and are
allowed or denied by its access rules (IAM) instead of an API key.

| Provider | Model ids in the catalog | Integration on the server |
|---|---|---|
| AWS Bedrock | Claude Opus, Sonnet and Haiku 5.5, Amazon Nova 2 Lite and Nova Pro, Llama 4 Maverick, Mistral Large 3, for example `bedrock_converse:eu.anthropic.claude-sonnet-5-5` | `langchain-aws` |
| Google Vertex AI, Gemini | `google_vertexai:gemini-3.8-flash`, `google_vertexai:gemini-3.6-flash` | `langchain-google-genai` (always installed) |
| Google Vertex AI, Claude | `google_anthropic_vertex:claude-opus-5-5`, `…claude-sonnet-5-5`, `…claude-haiku-5-5` | `langchain-google-vertexai` |

The API image includes all three (an operator can leave the AWS and Claude-on-Vertex ones out: see
[Deploy and operate](deploy.md#option-1-docker-compose-on-one-host)). Any other Bedrock model or inference
profile id works too, for example an application inference profile ARN for cost tracking; the editor then warns
that it is not in the catalog (`W010`), and an operator can price it with `AGENTCANVAS_MODEL_PRICES`.

### Region: where your prompts go

The AI model block's **Region** is where the prompts and answers are sent and processed: `eu-central-1`
(Frankfurt) on AWS; `europe-west4` (Netherlands), `eu` (any EU region), `us` or `global` on Google Cloud. Pick the
region your data may be processed in. Without one, the block uses the server's default (`AWS_REGION` or
`AWS_DEFAULT_REGION`; `GOOGLE_CLOUD_LOCATION`), and without either the editor reports `E024` and runs stop with
the same message. The region must be a region name: anything else is refused, because it becomes part of the
address the signed request goes to.

On Bedrock, the model id says where a request may be served from, within the region you call:

- `eu.…`, `us.…`, `au.…`, `jp.…`: cross-region inference that stays in that geography (an EU profile called
  from `eu-central-1` may serve the request in Frankfurt, Stockholm, Milan, Spain, Ireland or Paris).
- `global.…`: any AWS region in the world, for the most capacity, at a lower price (Claude: 10% less).
- A model id without a prefix (`mistral.mistral-large-3-675b-instruct`): in the region you call only, where the
  model offers it.

On Google Cloud, a region (`europe-west4`) keeps the request there, `eu` or `us` within that geography, and
`global` anywhere. Current Claude models on Vertex AI take `global`, `eu` or `us` only.

The catalog prices each Bedrock routing choice separately (an EU or US profile of Claude costs 10% more than the
global one). On Vertex AI the price depends on the region, which is not part of the model id, so the catalog
records the price of a regional or multi-region endpoint, the higher one: cost budgets err on the safe side, and a
`global` endpoint is billed 10% less than they count. Prices were checked on 2026-11-10 against the AWS Bedrock and
Google Cloud pricing pages and Anthropic's; Gemini is recorded at its 2027 price (double the introductory one).
Your own negotiated prices: `AGENTCANVAS_MODEL_PRICES`.

### Credentials

For each AI model block, the first of these that exists is used. A run signs its calls with it; nothing is stored
in the workflow.

1. **AWS Bedrock:** the block's **API key** secret (a Bedrock API key), else the project secret
   `AWS_BEARER_TOKEN_BEDROCK`, else the project secrets `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (and
   `AWS_SESSION_TOKEN` for temporary keys; one without the other is refused, not mixed with the server's).
   **Google Vertex AI:** the project secret `GOOGLE_SERVICE_ACCOUNT_KEY`, the contents of a service account's JSON
   key file. Only a service account key is taken: other credential files (external accounts, workload identity
   federation configurations) name files, addresses or commands on the server, and are refused.
2. **The server's own identity:** on AWS, an IAM role for the API's service account (IRSA or EKS Pod Identity),
   an ECS task role, an EC2 instance role, or `AWS_*` variables; on Google Cloud, Workload Identity on GKE, the
   service account of the VM or Cloud Run service, or a key file in `GOOGLE_APPLICATION_CREDENTIALS`. This needs no
   secret at all, and the keys never leave the cloud. Every project on the server can use it, so give that identity
   access to the models you want offered, and nothing else.

In the **hosted edition**, the server's identity is the operator's, not a tenant's: models in your cloud account use
only the project's own secrets, and a run without them stops with a message naming them.

The project for Vertex AI is the block's **Google Cloud project**, else the project of the service account key,
else the server's `GOOGLE_CLOUD_PROJECT`. The identity needs the Vertex AI User role
(`roles/aiplatform.user`) on it. On AWS, it needs `bedrock:InvokeModel` and
`bedrock:InvokeModelWithResponseStream` on the models (and, for a cross-region profile, on the profile and the
models in each region it may route to), and the models enabled in the Bedrock console.

A block of these providers reaches the provider at its own address: a **Base URL** is refused (`E024`). For private
networking, create a VPC interface endpoint (AWS PrivateLink) for `bedrock-runtime`, or a Private Service Connect
endpoint for Vertex AI, with private DNS: the provider's own address then resolves to it.

Backup models of the same cloud keep the block's region and project; a backup of another provider uses its own
settings, so its prompts go where that provider runs: list only backups you accept for this data. **Use the
cheapest of** never picks a model of another provider for a block that names a region, nor a Bedrock model that may
serve requests from more places than the block's own (a `global.` profile in place of an `eu.` one), so it never
moves prompts out of where the block keeps them. Agents designed for other frameworks (CrewAI, Google ADK, the
OpenAI Agents SDK and the others) reach these models through AgentCanvas, like every catalog model, so the same
credentials and region apply.

## Where a model's API key comes from

For each AI model block, the first of these that exists is used:

1. The **API key (secret reference)** field of that AI model block: pick any project secret by name.
2. The **project secret named after the provider**, for example `OPENAI_API_KEY`. The first-run screen saves the key
   you enter under this name; you can also add it under **Settings › Secrets**.
3. The **server's environment variable** of the same name (set when the server was started).

So one key entered on the first-run screen serves every model of that provider in the project, and a single model
can still use another key (a second account, a customer's key) through its own secret reference.

Keys are stored encrypted, never shown again, and masked as `***` in run events and traces. Exported projects read
them from environment variables with the same names (listed in the export's `.env.example`).

## Backup models and reusing answers

Three settings of an **AI model** block form its model gateway. They work on every framework (LangGraph, the
OpenAI Agents SDK, CrewAI and Google ADK) and for every step and agent the block powers.

- **Backup models:** when the block's model fails (the provider is down, out of quota, too slow for the block's
  timeout), the same request goes to each backup in order and the first answer wins. A streamed answer fails over
  only before its first word. Each backup uses its provider's own key (the project secret or server variable named
  after the provider, see above) and the block's temperature, output limit and timeout; it does not take the block's
  address or key. Pick backups from other providers, so one outage does not stop both (the editor warns about a
  backup that repeats the model, W016). Cost is counted for the model that answered.
- **Use the cheapest of** (optional): a list of models. At the start of every run the block uses the cheapest of
  them that is *ready* and *allowed*, instead of the model at the top, which stays the choice when none qualifies.
  Ready: the provider has a key here (a project secret or server variable), or the block's own key or address is for
  that provider; a retired model is not ready, and a local model is never chosen (a local server may be down: list
  it among the backup models). A winner from another provider uses that provider's own key and address, like a
  backup model: the block's own key and address are never sent to it. Allowed: swapping it in breaks no [policy](accounts.md) of your organisation that
  the block's own model does not already break (policies that apply to every environment). Cheapest means the
  catalog price, input plus output per million tokens; a model with no price is used only when no priced model
  qualifies, because a cost budget cannot count its calls. It works with the backup models, not instead of them:
  the winner is the block's model for the run and the backups still catch its failures (the winner is dropped from
  them if it was listed). The run's events carry the choice (`ac.route`: the model chosen and why each other
  candidate was not), and the draft keeps both settings. The editor warns about a model listed twice, a local one
  or one with no price (W024). Exported projects always use the model at the top.
- **Reuse answers for (s):** an identical request (same messages, tools and settings) within that many seconds gets
  the stored answer without calling the provider, at no cost. Good for steps whose answer should not change, such as
  classifying or extracting; leave it empty for creative writing. Cached answers are kept in the database, per
  project, up to 1,000 of them: every server shares them, and they survive a restart. (A project exported as code
  keeps them in the memory of the exported process.)

The run panel and the workflow's **Spending** count cached answers and answers from backups. Exported LangGraph
projects keep the backup models and the cache settings; OpenAI Agents SDK exports keep the backup models but not the cache; CrewAI and Google ADK exports keep neither. The older **Backup
models** add-on block (`mw.model_fallback`) still works for agents on LangGraph.

## The first-run screen

Open it from **Choose a model** on Home (until setup is done) or **Settings › Models**.

1. Pick a provider and a model. For a local server or an OpenAI-compatible API, **Find models on the server** lists
   the models it offers (LM Studio lists the models you downloaded; Ollama the ones you pulled) so you can pick one
   instead of typing its name. The hosted service asks only servers at public addresses: it has no local server to
   ask, and an internal or private address is refused.
2. Enter the API key (for a local server, only if it requires one), or the server address for a local server on a
   non-default port.
3. **Test connection** sends one short message with exactly these settings and shows the reply or the error.
   Nothing is saved yet.
4. **Save** stores the key as a project secret and the model as the project's **default model**. Workflows you
   create from templates afterwards use it instead of the template's own models (their other settings, such as
   temperature, are kept). Existing workflows don't change.

**Not now, hide the reminder** hides the prompt on Workflows; templates then keep their own models.

## Checks in the editor

| Code | Meaning |
|---|---|
| `W010` | The model is not in the catalog, so its tool calling and structured-output support are not checked. It still runs. |
| `W011` | The provider's integration is not installed on this server. Install the package named in the message. |
| `E017` | An OpenAI-compatible model has no Base URL. |
| `E024` | An AWS Bedrock or Google Vertex AI model has no region (neither the block's nor a server default), a region that is not one, or a Base URL. |
| `W028` | An AWS Bedrock or Google Vertex AI model has no credentials in sight: no project secrets for it, and the server shows no cloud identity (in the hosted edition, only the project secrets count). On AWS an instance role is seen from the machine itself; if your server has one the editor does not see, runs still use it. |
| `W029` | A Region or Google Cloud project is set on a model of another provider: it is not used. |

Under the Model field, a hint names the provider, where its key comes from, and its default address.

## LLM judges and simulated users

Evaluators that use a model (LLM judge, pairwise judge) and simulated users accept any catalog model or any
`provider:model` of a known provider, local ones included, so you can evaluate with a model on your own machine.
They use the project's keys the same way.

## Which model is used, when nothing names one

**Settings › Models › By purpose** has one rule for every place a model is chosen: what names its model uses it;
otherwise the project's default for that purpose (judge evaluators, simulated users, knowledge embeddings); otherwise
the project's model; otherwise nothing, and the page or the error says what to choose. The table shows, for each
purpose, the model it comes to and why. A default is applied when something is saved, so a judge evaluator created
without a model keeps the model it got even if the default changes later; edit the evaluator to change it.

Each choice lists only models that can answer here: hosted models whose provider key is set (a project secret or a
server variable), AWS Bedrock and Vertex AI models with credentials (project secrets, or the server's identity
outside the hosted edition) when the server has a default region (`AWS_REGION`, `GOOGLE_CLOUD_LOCATION`: a menu
sets no region of its own), the models a local server (Ollama, LM Studio, vLLM, llama.cpp) lists while it runs, and the
project's own OpenAI-compatible model. Knowledge embeddings list only models that embed text: the built-in one,
OpenAI's and Mistral's and Gemini's embedding models when their key is set, and local models whose names say they
embed (`nomic-embed-text`, `mxbai-embed-large`, `bge-m3`, …). A chat model such as `gpt-oss` cannot embed, so to
use LM Studio or Ollama for knowledge, download an embedding model there too.
