# Narrator model benchmark — lane status summarization

_Generated 2026-05-23T22:16:22Z_

Task: compress a real agent's digested session into a 1-line advisory status. Inputs are real captured transcripts; the prompt is the production narrator prompt.

## Performance (per model)

| Model | load | infer wall | tok/s | peak GPU | avg GPU | peak mem | max sent | avg words |
|---|---|---|---|---|---|---|---|---|
| gemma-e2b | 1.0s | 1.43s | 161 | 88% | 85% | 3279 MB | 6 ⚠ | 14 |
| gemma-e4b | 1.0s | 2.27s | 106 | 100% | 86% | 5313 MB | 5 ⚠ | 16 |
| gemma3-4b | 1.0s | 1.42s | 142 | 93% | 89% | 3473 MB | 4 ⚠ | 11 |
| granite-h-1b | 0.5s | 1.16s | 182 | 94% | 90% | 1422 MB | 3 | 10 |
| lfm2-2.6b | 0.5s | 1.28s | 225 | 95% | 88% | 1821 MB | 6 ⚠ | 15 |
| qwen3-1.7b | 0.5s | 0.79s | 272 | 89% | 72% | 2074 MB | 6 ⚠ | 14 |
| qwen3-4b-2507 | 0.5s | 1.59s | 152 | 95% | 89% | 3660 MB | 7 ⚠ | 14 |
| smollm3-3b | 0.5s | 1.06s | 187 | 91% | 88% | 2538 MB | 2 | 15 |

_Wall = total time for all fixtures (excl. load + warmup). tok/s = llama.cpp eval rate. GPU via ioreg Device Utilization %. mem = peak llama-server RSS (unified memory). Sampled solo (no competing load)._

## Narratives per lane (judge fluency + fidelity)

### Lane ARCHITECT  ·  8 events  ·  session 00e0d14a

- **gemma-e2b** (0.15s): Read launch-ARCHITECT.md and is assessing the current mission state by listing files in the mission directory.
- **gemma-e4b** (0.20s): Read launch-ARCHITECT.md and is running a command to list the mission directory contents.
- **gemma3-4b** (0.12s): Read launch-ARCHITECT.md and is listing the mission directory.
- **granite-h-1b** (0.11s): Read launch-ARCHITECT.md and checked mission directory; awaiting further instructions.
- **lfm2-2.6b** (0.09s): Read launch-ARCHITECT.md and is now assessing mission state files in parallel.
- **qwen3-1.7b** (0.08s): Read launch-ARCHITECT.md to understand the pre-bound launch for ARCHITECT lane.
- **qwen3-4b-2507** (0.13s): Read launch-ARCHITECT.md and is listing mission directory files to assess current state.
- **smollm3-3b** (0.11s): Read launch-ARCHITECT.md to understand the current iteration and assess the mission state.

### Lane AUDIT  ·  34 events  ·  session 117d4835

- **gemma-e2b** (0.35s): Read STATUS.md, TASKS.md, MISSION.md, and .mission-events to assess the current state of the v9.4 dogfood run.
- **gemma-e4b** (0.38s): Ran a directory listing in the runs/ folder to inspect the queue directory and bootstrap scripts.
- **gemma3-4b** (0.31s): Read mission, tasks, and run files to assess the fresh v9.4 dogfood run.
- **granite-h-1b** (0.32s): The audit run is a fresh v9.4 dogfood initiation with unclaimed lanes, awaiting user direction.
- **lfm2-2.6b** (0.31s): Read TASKS.md, STATUS.md, MISSION.md, and .mission-events to verify the fresh v9.4 dogfood run state and pipeline readiness.
- **qwen3-1.7b** (0.18s): Read STATUS.md, TASKS.md, MISSION.md, .mission-events, and README.md to understand the current state of the audit.
- **qwen3-4b-2507** (0.41s): Readed STATUS.md, TASKS.md, MISSION.md, README.md, and .mission-events; inspected runs directory. Current run is in INIT phase with all lanes unclaimed.
- **smollm3-3b** (0.20s): Inspecting the project-level trackers and run-level state to orient itself on the current audit phase.

### Lane BACKEND  ·  5 events  ·  session a3bbb58c

- **gemma-e2b** (0.20s): Attempted to find launch-BACKEND.md in the project, but the tool use was rejected.
- **gemma-e4b** (0.30s): Tried to find launch-BACKEND.md using find, but the operator rejected the tool use.
- **gemma3-4b** (0.21s): Tried to locate launch-BACKEND.md, but the operator rejected the tool use.
- **granite-h-1b** (0.15s): Read launch-BACKEND.md but rejected the tool; paused awaiting further instructions.
- **lfm2-2.6b** (0.18s): Read launch-BACKEND.md from the specified path but the tool use was rejected; awaiting operator input.
- **qwen3-1.7b** (0.13s): Ran a find to locate launch-BACKEND.md, but the operator rejected the command; now paused awaiting direction.
- **qwen3-4b-2507** (0.22s): Tried to locate launch-BACKEND.md, but the operator rejected the tool use; now paused awaiting direction.
- **smollm3-3b** (0.15s): Operator rejected the tool use, so the agent is now paused awaiting further instructions.

### Lane FRONTEND  ·  13 events  ·  session 314fd996

- **gemma-e2b** (0.24s): Read launch-FRONTEND.md and STATUS.md, and is checking applier liveness heartbeat and mission events.
- **gemma-e4b** (0.38s): Read launch-FRONTEND.md, STATUS.md, and is running tail commands on mission event and applier files.
- **gemma3-4b** (0.24s): Read launch-FRONTEND.md and STATUS.md to prepare for the mission iteration.
- **granite-h-1b** (0.19s): Read launch-FRONTEND.md and STATUS.md to understand the mission loop.
- **lfm2-2.6b** (0.27s): Agent-2512 is inspecting the launch-FRONTEND.md and reading the status file to analyze the mission state of the FRONTEND lane.
- **qwen3-1.7b** (0.12s): Read launch-FRONTEND.md and STATUS.md to understand the mission state and workflow.
- **qwen3-4b-2507** (0.24s): Read launch-FRONTEND.md, STATUS.md, and checked app heartbeat and mission events for activity.
- **smollm3-3b** (0.20s): Reading the launch-FRONTEND.md file to understand the mission worker loop and the current lane status.

### Lane META  ·  22 events  ·  session 15e30973

- **gemma-e2b** (0.26s): Read README.md, MISSION.md, and TASKS.md, and checked .mission-events and STATUS.md.
- **gemma-e4b** (0.62s): Read README.md, MISSION.md, and TASKS.md, then ran Bash(cd /Users/dave/Documents/Projects/megalodon && ls -la | hea...) to check the mission root directory structure.
- **gemma3-4b** (0.30s): Read launch-META.md, MISSION.md, TASKS.md, and mission events to understand the mission state.
- **granite-h-1b** (0.17s): Checked mission root directory; no .mission-events file found.
- **lfm2-2.6b** (0.23s): Read README.md, MISSION.md, and .mission-events; currently checking mission root directory structure for setup details.
- **qwen3-1.7b** (0.14s): Read README.md, MISSION.md, TASKS.md, and check the mission root directory to understand the setup.
- **qwen3-4b-2507** (0.28s): Read README.md, MISSION.md, TASKS.md, and STATUS.md; attempted to inspect mission root directory structure.
- **smollm3-3b** (0.21s): Inspecting the mission protocol, mission files, and current state to understand the mission setup and execution flow.

### Lane TEST  ·  37 events  ·  session 9c069b4f

- **gemma-e2b** (0.23s): Checked run directory files and queue contents, found pending rejected, and confirmed a fresh applier heartbeat.
- **gemma-e4b** (0.39s): Listed files in the run directory, read TASKS.md and MISSION.md, and ran ls on the queue directory.
- **gemma3-4b** (0.24s): Checked run directory, TASKS.md, and heartbeat to assess the test environment.
- **granite-h-1b** (0.22s): Checked run directory, TASKS.md, and heartbeat; no scripts found, applier heartbeat fresh.
- **lfm2-2.6b** (0.21s): Checked queue and scripts directory; heartbeat is fresh but applier heartbeat rejected; awaiting script availability.
- **qwen3-1.7b** (0.14s): Checked files in the run directory and read TASKS.md, MISSION.md to understand the run's state.
- **qwen3-4b-2507** (0.29s): Listed run directory files, read TASKS.md, MISSION.md, and heartbeat.txt; checking scripts in run directory.
- **smollm3-3b** (0.20s): Checking the applier heartbeat and queue structure before claiming tasks in the freshly scaffolded run directory.

