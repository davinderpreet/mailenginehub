#!/bin/bash
# Quick WIP save — run after every significant change to prevent loss on session crash
# Usage: bash save.sh "what you just did"

cd "$(dirname "$0")"

MSG="${1:-WIP save}"

# Stage everything except secrets
git add -A
git reset -- .env credentials* *.pem 2>/dev/null || true

if git diff --cached --quiet 2>/dev/null; then
    echo "Nothing to save."
else
    git commit -m "$MSG"
    echo "Saved: $MSG"
fi
