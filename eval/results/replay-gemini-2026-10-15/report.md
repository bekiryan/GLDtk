# GLDtk evaluation report

## Summary

| provider/model | prompts | fidelity | playable | LLM calls | attempts | patches | re-prompts | filled | layout retries | repairs | wall mean | wall p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gemini/default | 8 | 0.98 | 100% (8/8) | 9 | 1.12 | 1.88 | 0.00 | 0.00 | 0.00 | 0.00 | 0.82s | 2.05s |

- **gemini/default** fidelity items: satisfied 27, approximated 0, impossible 0, missing 1, unchecked 2; failures: none; expectation failures: 0

## Difficulty by section label

| provider/model | label | sections | mean score | min | max |
|---|---|---|---|---|---|
| gemini/default | easy | 4 | 1.9 | 1.4 | 2.4 |
| gemini/default | medium | 6 | 2.4 | 1.0 | 3.7 |
| gemini/default | hard | 2 | 4.6 | 4.4 | 4.7 |

## Per prompt

| prompt | tags | status | fidelity | calls | wall | notes |
|---|---|---|---|---|---|---|
| single-forest | single, horizontal, simple | ok | 1.00 | 1 | 0.1s |  |
| single-cave-hazards | single, horizontal, hazard | ok | 1.00 | 1 | 0.1s |  |
| two-sections | multi, horizontal, pickup, enemy, interactive | ok | 1.00 | 1 | 0.1s |  |
| three-sections | multi, horizontal, enemy, checkpoint, pickup | ok | 1.00 | 1 | 1.6s |  |
| vertical-tower | single, vertical | ok | 1.00 | 2 | 2.0s |  |
| vertical-shaft-multi | multi, vertical, pickup | ok | 0.83 | 1 | 2.0s |  |
| enemy-walker | single, enemy, entity-kinds | ok | 1.00 | 1 | 0.3s |  |
| enemy-flyer-turret | single, enemy, entity-kinds | ok | 1.00 | 1 | 0.4s |  |

## Worst requirements

| category | requirement | seen | missing | approximated | impossible |
|---|---|---|---|---|---|
| layout | the prompt asks for a shaft section. | 1 | 1 | 0 | 0 |
