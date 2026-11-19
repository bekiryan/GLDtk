# GLDtk evaluation report

## Summary

| provider/model | prompts | fidelity | playable | LLM calls | attempts | patches | re-prompts | filled | layout retries | repairs | wall mean | wall p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gemini/gemini-3.7-flash | 6 | 0.93 | 100% (6/6) | 7 | 1.17 | 0.83 | 0.00 | 0.00 | 0.00 | 0.00 | 0.69s | 2.37s |

- **gemini/gemini-3.7-flash** fidelity items: satisfied 29, approximated 4, impossible 0, missing 0, unchecked 2; failures: none; expectation failures: 0

## Difficulty by section label

| provider/model | label | sections | mean score | min | max |
|---|---|---|---|---|---|
| gemini/gemini-3.7-flash | easy | 4 | 4.9 | 0.6 | 6.7 |
| gemini/gemini-3.7-flash | medium | 8 | 2.5 | 0.6 | 5.4 |
| gemini/gemini-3.7-flash | hard | 5 | 6.5 | 3.9 | 8.4 |

## Per prompt

| prompt | tags | status | fidelity | calls | wall | notes |
|---|---|---|---|---|---|---|
| structure-crypt | multi, horizontal, structure, enemy, boss | ok | 0.94 | 1 | 0.1s |  |
| structure-labyrinth | single, horizontal, structure, pickup | ok | 0.75 | 1 | 0.1s |  |
| structure-tower | multi, vertical, structure | ok | 1.00 | 2 | 2.4s |  |
| structure-hub-key | single, horizontal, structure, key | ok | 1.00 | 1 | 0.0s |  |
| structure-mixed-outdoor | multi, horizontal, structure, hazard | ok | 1.00 | 1 | 0.0s |  |
| v4-mission-metroidvania | multi, horizontal, v4, mission, key, structure | ok | 0.90 | 1 | 1.5s |  |

## Worst requirements

| category | requirement | seen | missing | approximated | impossible |
|---|---|---|---|---|---|
| layout | the labyrinth has several dead ends. | 1 | 0 | 1 | 0 |
| layout | the level is built as a hedge maze labyrinth. | 1 | 0 | 1 | 0 |
| other | the prompt asks for a key that opens a lock. | 1 | 0 | 1 | 0 |
| section | there is a small labyrinth of catacombs. | 1 | 0 | 1 | 0 |
