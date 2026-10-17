# Configuration

All configuration lives in **`.env`** in the project root. Create it by copying
`.env.example`. `.env` is gitignored: **never commit it**, because it holds your API key.

After editing `.env`, **restart the server** (`Ctrl+C`, then `python serve.py`).
Settings are read once at start-up.

---

## Switching the LLM provider or API key

The app talks to the LLM through the standard **OpenAI chat-completions API**, so any
OpenAI-compatible service works. Only these three lines matter:

```ini
LLM_API_KEY=your-key
LLM_MODEL=model-name
LLM_BASE_URL=https://provider-endpoint/v1     # leave empty for OpenAI itself
```

No code changes are needed: the client is built from these values in
`app/orchestration/graph.py` (`_llm()`), which reads them via `app/core/config.py`.

### Ready-made settings

| Provider | `LLM_MODEL` | `LLM_BASE_URL` | Get a key |
|---|---|---|---|
| **DeepSeek** (default; what the project was tested with) | `deepseek-chat` | `https://api.deepseek.com` | platform.deepseek.com |
| **OpenAI** | `gpt-4o-mini` (cheap) or `gpt-4o` | *(empty)* | platform.openai.com |
| **Qwen / DashScope** (international) | `qwen-plus` | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` | Alibaba Cloud Model Studio |
| **OpenRouter** | e.g. `openai/gpt-4o-mini` | `https://openrouter.ai/api/v1` | openrouter.ai |
| **Ollama** (local, free) | e.g. `qwen2.5:7b` | `http://localhost:11434/v1` | no key; set `LLM_API_KEY=ollama` |
| Any vLLM / LM Studio server | your model name | your server's `/v1` URL | — |

### What the model must support

- Plain chat completions with `system` + `user` messages, `temperature=0`, and streaming
  (`stream=True`). No tools or function calling, and no JSON mode.
- Good instruction-following for **both English and Chinese**. The intent parser asks
  for strict JSON in plain text. Small local models (≤ 7B) may misparse complex requests;
  `deepseek-chat` and `gpt-4o-mini` work well.
- **Reasoning models** that reject `temperature` (e.g. some `o1`/`o3` variants) are **not
  supported** without a code change.

### Checking the key works

Start the server and ask something. If the key or model is wrong, the chat shows the
provider's error message (e.g. `AuthenticationError ... 401`, `model not found`).
Search, maps, details and all calculations keep working without an LLM. Only
understanding new questions and writing explanations need it.

### Cost

One question uses two LLM calls: parsing the request and writing the explanation.
That is roughly 3–6k tokens, a fraction of a cent with `deepseek-chat` or `gpt-4o-mini`.

---

## All settings

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `DB_DSN` | **yes** | `postgresql://capstone:capstone@localhost:15432/capstone` | Postgres connection. Must match `db/docker-compose.yml` (host port 15432). |
| `EMBED_MODEL` | **yes** | `nomic-ai/nomic-embed-text-v1.5` | Sentence-embedding model for semantic search. **Keep this value**: the seed's stored embeddings were made with it (768 dimensions). Changing it means re-running `pipeline/load_properties.py`. |
| `LLM_API_KEY` | for chat | — | Your provider key. |
| `LLM_MODEL` | for chat | — | Model name at that provider. |
| `LLM_BASE_URL` | no | OpenAI | OpenAI-compatible endpoint. |
| `OPEX_RATE` | no | `0.28` | Operating expenses as a share of annual rent, between 0 and 1 (industry range 0.25–0.30). |
| `OTHER_ACQUISITION_COSTS` | no | `2000` | AUD acquisition costs other than stamp duty (conveyancing, inspection). |

The app refuses to start if `DB_DSN` or `EMBED_MODEL` is missing: configuration is
never guessed. LLM settings are only checked when the LLM is first used.

### About the two assumptions

The dataset has no operating-cost data, so NOI, cap rate and ROI rest on `OPEX_RATE` and
`OTHER_ACQUISITION_COSTS`. They are **assumptions, and the app says so** next to every
number built on them. Users can also change them live in the property detail panel
(*Purchase cost* / *Rental analysis*). Those edits apply to the whole server until
restart.

Stamp duty is **not** an assumption. It is computed exactly from the Victorian rate table
(`app/analytics/formulas.py`, `stamp_duty_vic`).

---

## Ports

| What | Default | Change it |
|---|---|---|
| Web app | 8000 | `python serve.py --port=8010` |
| Postgres (host side) | 15432 | `db/docker-compose.yml` **and** `DB_DSN` in `.env` (keep them equal) |

On Windows, if a port fails to bind for no obvious reason, it may be in a reserved range.
See [TROUBLESHOOTING.md](TROUBLESHOOTING.md#port-already-in-use--refused).
