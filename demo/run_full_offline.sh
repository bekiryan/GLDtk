#!/bin/zsh
# Full manual rebuild (no 5-minute cap) at human pace, then checked for playability and composed
# against the recorded AI run in demo/out/ai.  Fully offline: no LLM, no server, no internet.
# Keeps the Mac awake (caffeinate) and retries the recording if LDtk dies.  Output: demo/out_full/.
#
#   nohup demo/run_full_offline.sh >/dev/null 2>&1 &      # then: tail -f demo/out_full/run.log
set -u
cd "$(dirname "$0")/.."
OUT=$PWD/demo/out_full
TARGET=$PWD/demo/out/ai/target.ldtk
mkdir -p $OUT
ln -sfn ../out/ai $OUT/ai
LOG=$OUT/run.log
echo "start $(date)" > $LOG

for attempt in 1 2 3; do
  echo "manual recording, attempt $attempt, $(date)" >> $LOG
  rm -rf $OUT/manual
  if caffeinate -dimsu node demo/record_manual.js $TARGET $OUT/manual --budget=0 >> $LOG 2>&1; then
    ok=1; break
  fi
  ok=0; sleep 30
done
[ "$ok" = 1 ] || { echo "FAILED: manual recording did not finish, $(date)" >> $LOG; exit 1; }

echo "compare with the target" >> $LOG
.venv/bin/python demo/compare.py $TARGET $OUT/manual/crypt.ldtk >> $LOG 2>&1
echo "playability check" >> $LOG
.venv/bin/python demo/verify.py $OUT/manual/crypt.ldtk --rules-from $TARGET --json $OUT/manual/verify.json >> $LOG 2>&1
echo "compose" >> $LOG
.venv/bin/python demo/compose.py --out $OUT >> $LOG 2>&1 && echo "DONE $(date): $OUT/comparison.mp4" >> $LOG
