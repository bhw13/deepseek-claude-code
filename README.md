<div align="center">

# 🤖 DeepSeek Claude Code

Run Claude Code in your terminal on **DeepSeek** — one command (`ds`) that routes Claude Code's Opus/Sonnet/Haiku model names to DeepSeek models, with per-tier reasoning effort.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)
[![Python 3.14](https://img.shields.io/badge/python-3.14-3776ab.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json&style=for-the-badge)](https://github.com/astral-sh/uv)
[![Type checking: Ty](https://img.shields.io/badge/type%20checking-ty-ffcc00.svg?style=for-the-badge)](https://pypi.org/project/ty/)
[![Code style: Ruff](https://img.shields.io/badge/code%20formatting-ruff-f5a623.svg?style=for-the-badge)](https://github.com/astral-sh/ruff)

[Quick Start](#quick-start) · [How `ds` maps models](#how-ds-maps-models) · [Shared context](#shared-context-across-terminals) · [Codespaces](#run-in-a-github-codespace) · [How it works](#how-it-works) · [Development](#development)

</div>

> **Credit:** Built on [**free-claude-code**](https://github.com/Alishahryar1/free-claude-code) by [Alishahryar1](https://github.com/Alishahryar1) — a general Anthropic-compatible proxy that supports many providers plus Discord/Telegram and voice integrations. This repo is a thin fork that focuses it on **DeepSeek** and adds the one-command `ds` launcher with per-tier effort routing. For the full provider list and those extra integrations, see the upstream project.

<div align="center">
  <img src="assets/pic.png" alt="DeepSeek Claude Code in action" width="700">
</div>

## What it does

Claude Code talks to the Anthropic Messages API. This runs a small local proxy that intercepts those calls and forwards them to DeepSeek's Anthropic-compatible endpoint — translating Claude Code's model names to DeepSeek models and streaming the responses back in the shape Claude Code expects. You keep the Claude Code experience; inference runs on DeepSeek.

The `ds` command bundles it into one step: it starts the proxy (routed to DeepSeek) and launches Claude Code, then shuts the proxy down when you exit. Open several sessions at once — `ds` for DeepSeek, plain `claude` for real Claude — and they automatically share what each terminal is working on — see [Shared context across terminals](#shared-context-across-terminals).

## Quick Start

### 1. Prerequisites

- [uv](https://github.com/astral-sh/uv) and Python 3.14 — after installing uv, run `uv python install 3.14.0`.
- The Claude Code CLI — `npm install -g @anthropic-ai/claude-code`.
- A DeepSeek API key — [platform.deepseek.com/api_keys](https://platform.deepseek.com/api_keys).

### 2. Get the code

```bash
git clone https://github.com/bhw13/deepseek-claude-code.git
cd deepseek-claude-code
uv sync
```

### 3. Add your DeepSeek key

Start the proxy once and open the Admin UI it prints:

```bash
uv run fcc-server
```

```text
INFO:     Admin UI: http://127.0.0.1:8082/admin (local-only)
```

Paste your key into `DEEPSEEK_API_KEY`, then click **Validate** → **Apply**. (Alternatively, put `DEEPSEEK_API_KEY=...` in a local `.env` — it is gitignored and never committed.)

<div align="center">
  <img src="assets/admin-page.png" alt="Local admin UI for proxy settings" width="700">
</div>

### 4. Run it

```bash
uv run ds
```

`ds` starts the DeepSeek-routed proxy and launches Claude Code together, then stops the proxy when you exit.

For a global install (no `uv run` prefix), run `uv tool install .` once. That puts `ds` on your `PATH`:

| Command | What it does |
| ------- | ------------ |
| `ds`    | Start the DeepSeek-routed proxy **and** launch Claude Code; stop the proxy on exit. |

Use `ds` to run Claude Code on DeepSeek through the proxy; run `claude` directly when you want real Claude on your own account instead. Either way, run several at once — any mix of `ds` and plain `claude` sessions — and they automatically [share context](#shared-context-across-terminals).

## How `ds` maps models

Opus and Sonnet both map to `deepseek-v4-pro`and are differentiated by reasoning effort, while Haiku maps to 'deepseek-v4-flash' (forwarded to DeepSeek as `output_config.effort`):

| Claude Code tier | DeepSeek model      | Effort |
| ---------------- | ------------------- | ------ |
| Opus             | `deepseek-v4-pro`   | `max`  |
| Sonnet           | `deepseek-v4-pro`   | `high` |
| Haiku            | `deepseek-v4-flash` | `high` |

Tune effort per tier with `MODEL_EFFORT` / `MODEL_OPUS_EFFORT` / `MODEL_SONNET_EFFORT` / `MODEL_HAIKU_EFFORT` (`low` | `medium` | `high` | `xhigh` | `max`); a blank tier inherits `MODEL_EFFORT`. The proxy writes its logs to `~/.fcc/logs/server.log`, so server output never corrupts the Claude Code TUI.

## Shared context across terminals

Run more than one session at once — any mix of `ds` and plain `claude` terminals — and they automatically share what each is working on. A `UserPromptSubmit` hook (registered in `~/.claude/settings.json` the first time you run `ds`, or via `fcc-install-hooks`) records each session's latest prompt to a small file under `~/.fcc/run/` and surfaces the others' prompts back so the models stay consistent across terminals — independent of where inference runs, so DeepSeek (`ds`) and real-Claude (plain `claude`) sessions all share the same context.

- **Automatic.** Each fresh prompt is published to the other live sessions and surfaced to them as read-only background context — no commands, no copy-paste.
- **Ephemeral by design.** The note file lives under `~/.fcc/run/` and is deleted when the last session ends, so context never persists across runs and can't accumulate or bloat.
- **Bounded, so it won't degrade output.** One overwritten note per session, idle notes expire, and only a small, recent set is injected — keeping shared context fresh instead of growing into noise that distracts the model.
- **Invisible solo.** With a single terminal there are no peers, so nothing is added to your prompts and behavior is unchanged.

Tune or disable it with the `SHARED_CONTEXT_*` settings (see [`.env.example`](.env.example)); set `SHARED_CONTEXT_ENABLED=false` to turn it off entirely. Remove the hook with `fcc-uninstall-hooks` (re-add with `fcc-install-hooks`).

## Run in a GitHub Codespace

The proxy reads `DEEPSEEK_API_KEY` from the environment, and GitHub Codespaces injects **Codespaces secrets** as environment variables — so `ds` works in a Codespace with no `.env` file.

**1. Add your key as a Codespaces secret**

On GitHub: **Settings → Secrets and variables → Codespaces → New secret** (your account, scoped to this repo) or the repo's **Settings → Secrets and variables → Codespaces**.

- **Name:** `DEEPSEEK_API_KEY`
- **Value:** your key from [platform.deepseek.com/api_keys](https://platform.deepseek.com/api_keys)

Use the **Codespaces** tab, not **Actions** — they are separate stores. The secret is encrypted and never committed; cloning the repo never exposes it.

**2. Open a Codespace and run `ds`**

From the repo: **Code ▸ Codespaces ▸ Create codespace on main**. Then in the Codespace terminal:

```bash
uv sync                                   # install the proxy + deps
npm install -g @anthropic-ai/claude-code  # install the Claude Code CLI
uv run ds                                 # start the DeepSeek proxy + Claude Code
```

`ds` picks up `DEEPSEEK_API_KEY` from the Codespaces environment automatically.

## How It Works

<div align="center">
  <img src="assets/how-it-works.svg" alt="DeepSeek Claude Code request flow" width="900">
</div>

Diagram source: [`assets/how-it-works.mmd`](assets/how-it-works.mmd).

- FastAPI exposes Anthropic-compatible routes such as `/v1/messages`, `/v1/messages/count_tokens`, and `/v1/models`.
- Model routing resolves the incoming Claude model name (Opus/Sonnet/Haiku) to the configured DeepSeek model and effort.
- DeepSeek is reached through its Anthropic-compatible Messages endpoint; the proxy normalizes thinking blocks, tool calls, token-usage metadata, and provider errors into the shape Claude Code expects.
- Trivial Claude Code probes are answered locally to save latency and quota.
- Concurrent local sessions share a short note of what each is working on via a Claude Code `UserPromptSubmit` hook and an ephemeral `~/.fcc/run/` file, injected as read-only background context (see [Shared context across terminals](#shared-context-across-terminals)).

## Development

### Project structure

```text
deepseek-claude-code/
├── server.py              # ASGI entry point
├── api/                   # FastAPI routes, service layer, model routing
├── core/                  # Shared Anthropic protocol helpers and SSE utilities
├── providers/             # Provider transports (DeepSeek and others), registry
├── cli/                   # Package entry points (ds, fcc-server, …)
├── config/                # Settings, provider catalog, logging
└── tests/                 # Unit and contract tests
```

### Commands

```bash
uv run ruff format
uv run ruff check
uv run ty check
uv run pytest
```

Run them in that order before pushing.

### Package scripts

- `ds`: starts the DeepSeek-routed proxy (per-tier effort mapping) and launches Claude Code, then stops the proxy on exit. If a proxy is already running it is reused.
- `fcc-server`: starts the proxy on its own.
- `fcc-deepseek`: starts the proxy with `MODEL=deepseek/deepseek-chat`.
- `fcc-init`: optional scaffold for `~/.fcc/.env`; prefer the Admin UI for normal configuration.
- `fcc-context-hook`: the `UserPromptSubmit`/`SessionEnd` hook backing [shared context](#shared-context-across-terminals); invoked by Claude Code, not run directly.
- `fcc-install-hooks` / `fcc-uninstall-hooks`: register or remove the shared-context hook in `~/.claude/settings.json` (`ds` auto-registers it on launch; run `fcc-install-hooks` once if you only ever use plain `claude`).

## Credit & License

This is a DeepSeek-focused fork of [**free-claude-code**](https://github.com/Alishahryar1/free-claude-code) by [Alishahryar1](https://github.com/Alishahryar1) — the underlying proxy, provider transports, and integrations are their work. Licensed under the MIT License; see [LICENSE](LICENSE).
