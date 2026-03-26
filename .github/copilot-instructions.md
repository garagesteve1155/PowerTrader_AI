<!-- Bootstrap Copilot instructions for the PowerTrader_AI repository -->
# Copilot / AI assistant instructions for this repository

Purpose
-------
This file gives quick, high-signal guidance for AI assistants (Copilot-style) working in this repository. It is intentionally short and links to authoritative docs rather than duplicating them.

Quick Start
-----------
- Install dependencies: `python -m pip install -r requirements.txt`
- Run the application (local): `python pt_hub.py`
- Run simulator tests: `python run_pytests.py`
- Docker: build `docker build -t powertrader_ai:latest .` and run `docker run --rm -it powertrader_ai:latest` (or `docker compose up --build`)

Key files and docs (links)
-------------------------
- Agent customization: [.github/agents/powertrader.agent.md](.github/agents/powertrader.agent.md)
- Entrypoints: [pt_hub.py](pt_hub.py#L1), [pt_thinker.py](pt_thinker.py#L1), [pt_trader.py](pt_trader.py#L1)
- Exchange layer: [pt_exchange_api.py](pt_exchange_api.py#L1), [pt_kucoin_simulator.py](pt_kucoin_simulator.py#L1)
- Tests: [tests/test_kucoin_simulator.py](tests/test_kucoin_simulator.py#L1)
- Run tests helper: [run_pytests.py](run_pytests.py#L1)
- Architecture overview: [document_pack/architecture_powertrader.md](document_pack/architecture_powertrader.md)
- Docker and compose: [Dockerfile](Dockerfile#L1), [docker-compose.yml](docker-compose.yml#L1)

Conventions & important notes
-----------------------------
- Secrets: the repo currently documents using plaintext `r_key.txt` / `r_secret.txt` files. Prefer environment variables for new work and avoid committing secrets.
- IPC / persistence: the code uses filesystem artifacts (JSON/text files under `autoresearch_runs/`, etc.) for coordination between processes. Be mindful when changing file formats.
- Dependency note: some docs reference pinning `setuptools==81.0.0`; leave that alone unless you verify the impact across platforms.

How the assistant should act
---------------------------
- Link, don't embed: prefer adding links to canonical docs in `document_pack/` instead of copying large sections.
- Be conservative with edits: make minimal, focused changes; run tests locally (`python run_pytests.py`) before proposing broad refactors.
- When suggesting changes that affect security (secrets handling) or ops (Docker images), include migration steps and a rollback plan.

Suggested example prompts
-------------------------
- "Run the simulator unit tests and report failures; suggest a minimal fix." 
- "Create a unit test for `pt_trader.py` that exercises DCA entry logic for small balances." 
- "Summarize the architecture and list three low-risk refactor opportunities." 

Next agent customizations to consider
-----------------------------------
- CI helper agent: create an agent that knows how to run `run_pytests.py`, build the Docker image, and validate container behavior.
- Security checklist hook: add an instruction or GitHub Action that warns about committing `r_key.txt`-style files.
- Docs sync tool: generate short READMEs for top-level modules (`pt_trader.py`, `pt_thinker.py`) linking to `document_pack` details.

How to update this file
-----------------------
Keep this file concise. If you need to add larger guidance, create files under `.github/agents/` (follow the existing `powertrader.agent.md` style) and link to them here.

Contact
-------
If unsure, open an issue or ask in the repository PR describing the proposed change and its risk.
