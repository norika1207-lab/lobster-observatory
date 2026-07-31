#!/bin/bash
set -u

# Force Claude LLM — local mode generates repeated titles that get blocked by dedup
export LOBSTER_POSTER_LLM=local # disabled Claude spend by emergency stop

# Moltbook applies a global posting cooldown. Run posters through a single
# scheduler so 21 lobsters do not wake up together and waste generations on 429s.
# Updated 2026-05-12: Added 11 new lobsters (apprentices + elder, indices 11-21)
POSTERS=(
  "lobster-poster.js:/tmp/lobster.log"
  "lobster-poster-2.js:/tmp/lobster-2.log"
  "lobster-poster-3.js:/tmp/lobster-3.log"
  "lobster-poster-4.js:/tmp/lobster-4.log"
  "lobster-poster-5.js:/tmp/lobster-5.log"
  "lobster-poster-6.js:/tmp/lobster-6.log"
  "lobster-poster-7.js:/tmp/lobster-7.log"
  "lobster-poster-8.js:/tmp/lobster-8.log"
  "lobster-poster-9.js:/tmp/lobster-9.log"
  "lobster-poster-10.js:/tmp/lobster-10.log"
  "lobster-poster-11.js:/tmp/lobster-11.log"
  "lobster-poster-12.js:/tmp/lobster-12.log"
  "lobster-poster-13.js:/tmp/lobster-13.log"
  "lobster-poster-14.js:/tmp/lobster-14.log"
  "lobster-poster-15.js:/tmp/lobster-15.log"
  "lobster-poster-16.js:/tmp/lobster-16.log"
  "lobster-poster-17.js:/tmp/lobster-17.log"
  "lobster-poster-18.js:/tmp/lobster-18.log"
  "lobster-poster-19.js:/tmp/lobster-19.log"
  "lobster-poster-20.js:/tmp/lobster-20.log"
  "lobster-poster-21.js:/tmp/lobster-21.log"
)

MIN_COOLDOWN=160
ROUND_PAUSE=8

echo "Sequential lobster poster scheduler starting at $(date -Is)"
echo "Posters: ${#POSTERS[@]} · min cooldown ${MIN_COOLDOWN}s"

while true; do
  for entry in "${POSTERS[@]}"; do
    script="${entry%%:*}"
    log="${entry#*:}"
    echo "[$(date -Is)] running ${script}" | tee -a "$log"
    output=$(node "/root/${script}" 2>&1)
    printf '%s\n' "$output" | tee -a "$log"

    retry=$(printf '%s\n' "$output" | grep -o 'retry in [0-9]*s' | tail -1 | grep -o '[0-9]*' || true)
    if [ -n "$retry" ]; then
      sleep_for=$((retry + 5))
      echo "[$(date -Is)] ${script} hit cooldown; sleeping ${sleep_for}s" | tee -a "$log"
      sleep "$sleep_for"
    else
      echo "[$(date -Is)] ${script} completed; sleeping ${MIN_COOLDOWN}s" | tee -a "$log"
      sleep "$MIN_COOLDOWN"
    fi
  done
  sleep "$ROUND_PAUSE"
done
