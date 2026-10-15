# GLDtk evaluation report

## Summary

| provider/model | prompts | fidelity | playable | LLM calls | attempts | patches | re-prompts | filled | layout retries | repairs | wall mean | wall p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gemini/default | 8 | 1.00 | 100% (8/8) | 9 | 1.12 | 0.12 | 0.00 | 0.00 | 0.00 | 0.00 | 110.04s | 274.12s |

- **gemini/default** fidelity items: satisfied 27, approximated 0, impossible 0, missing 0, unchecked 2; failures: none; expectation failures: 0

## Per prompt

| prompt | tags | status | fidelity | calls | wall | notes |
|---|---|---|---|---|---|---|
| single-forest | single, horizontal, simple | ok | 1.00 | 1 | 185.4s |  |
| single-cave-hazards | single, horizontal, hazard | ok | 1.00 | 1 | 132.4s |  |
| two-sections | multi, horizontal, pickup, enemy, interactive | ok | 1.00 | 1 | 102.3s |  |
| three-sections | multi, horizontal, enemy, checkpoint, pickup | ok | 1.00 | 1 | 117.5s |  |
| vertical-tower | single, vertical | ok | 1.00 | 2 | 274.1s |  |
| vertical-shaft-multi | multi, vertical, pickup | ok | 1.00 | 1 | 39.5s |  |
| enemy-walker | single, enemy, entity-kinds | ok | 1.00 | 1 | 15.3s |  |
| enemy-flyer-turret | single, enemy, entity-kinds | ok | 1.00 | 1 | 13.9s |  |

## Worst requirements

None.
