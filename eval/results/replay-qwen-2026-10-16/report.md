# GLDtk evaluation report

## Summary

| provider/model | prompts | fidelity | playable | LLM calls | attempts | patches | re-prompts | filled | layout retries | repairs | wall mean | wall p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ollama/qwen2.5:14b | 6 | 0.93 | 83% (5/6) | 10 | 1.00 | 3.33 | 0.00 | 0.17 | 0.00 | 0.17 | 0.99s | 3.17s |

- **ollama/qwen2.5:14b** fidelity items: satisfied 15, approximated 3, impossible 0, missing 0, unchecked 11; failures: replay_missing x1; expectation failures: 2

## Difficulty by section label

| provider/model | label | sections | mean score | min | max |
|---|---|---|---|---|---|
| ollama/qwen2.5:14b | easy | 3 | 4.1 | 2.1 | 5.8 |
| ollama/qwen2.5:14b | medium | 7 | 4.7 | 2.1 | 8.3 |
| ollama/qwen2.5:14b | hard | 8 | 5.8 | 0.6 | 8.5 |

## Per prompt

| prompt | tags | status | fidelity | calls | wall | notes |
|---|---|---|---|---|---|---|
| structure-crypt | multi, horizontal, structure, enemy, boss | ok | 1.00 | 2 | 0.4s | no Interactive entity |
| structure-labyrinth | single, horizontal, structure, pickup | replay_missing | - | 4 | 3.2s |  |
| structure-tower | multi, vertical, structure | ok | 1.00 | 1 | 0.6s |  |
| structure-hub-key | single, horizontal, structure, key | ok | 0.75 | 1 | 0.1s | no hub section |
| structure-mixed-outdoor | multi, horizontal, structure, hazard | ok | 1.00 | 1 | 0.2s |  |
| v4-mission-metroidvania | multi, horizontal, v4, mission, key, structure | ok | 0.90 | 1 | 1.5s |  |

## Worst requirements

| category | requirement | seen | missing | approximated | impossible |
|---|---|---|---|---|---|
| layout | the exit room has a locked door. | 1 | 0 | 1 | 0 |
| entity | the hidden side room contains a key. | 1 | 0 | 1 | 0 |
| other | the prompt asks for a key that opens a lock. | 1 | 0 | 1 | 0 |
