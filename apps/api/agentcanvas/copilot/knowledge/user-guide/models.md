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
| OpenAI | `openai:gpt-5.5` | `OPENAI_API_KEY` |
| Azure OpenAI | `azure_openai:<deployment>` | `AZURE_OPENAI_API_KEY` (plus endpoint and API version) |
| Google Gemini | `google_genai:gemini-2.5-flash` | `GOOGLE_API_KEY` |
| Mistral AI | `mistralai:mistral-large-latest` | `MISTRAL_API_KEY` |
| Groq | `groq:llama-3.3-70b-versatile` | `GROQ_API_KEY` |
| DeepSeek | `deepseek:deepseek-chat` | `DEEPSEEK_API_KEY` |
| xAI | `xai:grok-4` | `XAI_API_KEY` |
| Any OpenAI-compatible API (OpenRouter, Together, Fireworks, a gateway…) | `openai_compatible:<model>` | `OPENAI_COMPATIBLE_API_KEY`, or a secret you pick; a **Base URL** is required |

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
server and load or pull the model before running a workflow. Local models vary in how well they call tools and
produce structured output: if an agent misbehaves, try a larger model.

When AgentCanvas runs in Docker, `localhost` is the container itself: local servers are reached through
`host.docker.internal` automatically (`AGENTCANVAS_LOCAL_MODELS_HOST`).

Any other provider LangChain's `init_chat_model` supports can be used by its prefix too (for example
`bedrock_converse:`), once its integration package is installed on the server; the editor then warns that the
model is not in the catalog (`W010`).

## Where a model's API key comes from

For each AI model block, the first of these that exists is used:

1. The **API key (secret reference)** field of that AI model block: pick any project secret by name.
2. The **project secret named after the provider**, for example `OPENAI_API_KEY`. The first-run screen saves the key
   you enter under this name; you can also add it on the **Workspace** page under **Secrets**.
3. The **server's environment variable** of the same name (set when the server was started).

So one key entered on the first-run screen serves every model of that provider in the project, and a single model
can still use another key (a second account, a customer's key) through its own secret reference.

Keys are stored encrypted, never shown again, and masked as `***` in run events and traces. Exported projects read
them from environment variables with the same names (listed in the export's `.env.example`).

## Backup models and reusing answers

Two settings of an **AI model** block form its model gateway. They work on every framework (LangGraph, the
OpenAI Agents SDK, CrewAI and Google ADK) and for every step and agent the block powers.

- **Backup models:** when the block's model fails (the provider is down, out of quota, too slow for the block's
  timeout), the same request goes to each backup in order and the first answer wins. A streamed answer fails over
  only before its first word. Each backup uses its provider's own key (the project secret or server variable named
  after the provider, see above) and the block's temperature, output limit and timeout; it does not take the block's
  address or key. Pick backups from other providers, so one outage does not stop both (the editor warns about a
  backup that repeats the model, W016). Cost is counted for the model that answered.
- **Reuse answers for (s):** an identical request (same messages, tools and settings) within that many seconds gets
  the stored answer without calling the provider, at no cost. Good for steps whose answer should not change, such as
  classifying or extracting; leave it empty for creative writing. Cached answers live in the server's memory, per
  project, up to 1,000 of them, and are gone after a server restart.

The run panel and the workflow's **Spending** count cached answers and answers from backups. Exported LangGraph
projects keep both settings; OpenAI Agents SDK exports keep the backup models but not the cache; CrewAI and Google ADK exports keep neither. The older **Backup
models** add-on block (`mw.model_fallback`) still works for agents on LangGraph.

## The first-run screen

Open it from **Choose a model** on Home (until setup is done) or **Settings › Models**.

1. Pick a provider and a model. For a local server or an OpenAI-compatible API, **Find models on the server** lists
   the models it offers (LM Studio lists the models you downloaded; Ollama the ones you pulled) so you can pick one
   instead of typing its name.
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
server variable), the models a local server (Ollama, LM Studio, vLLM, llama.cpp) lists while it runs, and the
project's own OpenAI-compatible model. Knowledge embeddings list only models that embed text: the built-in one,
OpenAI's and Mistral's and Gemini's embedding models when their key is set, and local models whose names say they
embed (`nomic-embed-text`, `mxbai-embed-large`, `bge-m3`, …). A chat model such as `gpt-oss` cannot embed, so to
use LM Studio or Ollama for knowledge, download an embedding model there too.
