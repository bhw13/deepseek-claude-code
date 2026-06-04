<div align="center">

# 🤖 DeepSeek Claude Code

Run Claude Code in your terminal on **DeepSeek** — one command (`ds`) that routes Claude Code's Opus/Sonnet/Haiku model names to DeepSeek models, with per-tier reasoning effort.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)
[![Python 3.14](https://img.shields.io/badge/python-3.14-3776ab.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json&style=for-the-badge)](https://github.com/astral-sh/uv)
[![Type checking: Ty](https://img.shields.io/badge/type%20checking-ty-ffcc00.svg?style=for-the-badge)](https://pypi.org/project/ty/)
[![Code style: Ruff](https://img.shields.io/badge/code%20formatting-ruff-f5a623.svg?style=for-the-badge)](https://github.com/astral-sh/ruff)

[Quick Start](#quick-start) · [How `ds` maps models](#how-ds-maps-models) · [Codespaces](#run-in-a-github-codespace) · [How it works](#how-it-works) · [Development](#development)

</div>

> **Credit:** Built on [**free-claude-code**](https://github.com/Alishahryar1/free-claude-code) by [Alishahryar1](https://github.com/Alishahryar1) — a general Anthropic-compatible proxy that supports many providers plus Discord/Telegram and voice integrations. This repo is a thin fork that focuses it on **DeepSeek** and adds the one-command `ds` launcher with per-tier effort routing. For the full provider list and those extra integrations, see the upstream project.

<div align="center">
  <img src="assets/pic.png" alt="DeepSeek Claude Code in action" width="700">
</div>

## What it does

Claude Code talks to the Anthropic Messages API. This runs a small local proxy that intercepts those calls and forwards them to DeepSeek's Anthropic-compatible endpoint — translating Claude Code's model names to DeepSeek models and streaming the responses back in the shape Claude Code expects. You keep the Claude Code experience; inference runs on DeepSeek.

The `ds` command bundles it into one step: it starts the proxy (routed to DeepSeek) and launches Claude Code, then shuts the proxy down when you exit.

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

`ds` starts the DeepSeek-routed proxy and launches Claude Code together, then stops the proxy when you exit. To get a global `ds` command (no `uv run` prefix), install it once with `uv tool install .`.

## How `ds` maps models

`deepseek-v4-max` doesn't exist, so Opus and Sonnet both use `deepseek-v4-pro` and are differentiated by reasoning effort (forwarded to DeepSeek as `output_config.effort`):

| Claude Code tier | DeepSeek model      | Effort |
| ---------------- | ------------------- | ------ |
| Opus             | `deepseek-v4-pro`   | `max`  |
| Sonnet           | `deepseek-v4-pro`   | `high` |
| Haiku            | `deepseek-v4-flash` | `high` |

Tune effort per tier with `MODEL_EFFORT` / `MODEL_OPUS_EFFORT` / `MODEL_SONNET_EFFORT` / `MODEL_HAIKU_EFFORT` (`low` | `medium` | `high` | `xhigh` | `max`); a blank tier inherits `MODEL_EFFORT`. The proxy writes its logs to `~/.fcc/logs/server.log`, so server output never corrupts the Claude Code TUI.

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

- `ds`: starts the DeepSeek-routed proxy (per-tier effort mapping) and launches Claude Code, then stops the proxy on exit.
- `fcc-server`: starts the proxy on its own.
- `fcc-claude`: launches Claude Code against an already-running proxy.
- `fcc-deepseek`: starts the proxy with `MODEL=deepseek/deepseek-chat`.
- `fcc-init`: optional scaffold for `~/.fcc/.env`; prefer the Admin UI for normal configuration.

## Credit & License

This is a DeepSeek-focused fork of [**free-claude-code**](https://github.com/Alishahryar1/free-claude-code) by [Alishahryar1](https://github.com/Alishahryar1) — the underlying proxy, provider transports, and integrations are their work. Licensed under the MIT License; see [LICENSE](LICENSE).
