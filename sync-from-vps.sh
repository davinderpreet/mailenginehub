#!/bin/bash
# ============================================================
#  MailEngineHub Sync-from-VPS Script
#  Pulls VPS state → local git repo (for when VPS was edited directly)
# ============================================================

set -e

VPS_HOST="root@mailenginehub.com"
VPS_PATH="/var/www/mailengine"
SSH_KEY="$HOME/.ssh/mailengine_vps"
SSH="ssh -i $SSH_KEY $VPS_HOST"
SCP="scp -i $SSH_KEY"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "========================================"
echo "  MailEngineHub Sync from VPS"
echo "========================================"

# Must be in git repo
if ! git rev-parse --is-inside-work-tree > /dev/null 2>&1; then
    echo -e "${RED}ERROR: Not in a git repository.${NC}"
    exit 1
fi

# ── Step 1: Commit VPS state first ────────
echo -e "\n${YELLOW}[1/3] Snapshotting VPS state...${NC}"
$SSH "cd $VPS_PATH && git add -A && git diff --cached --quiet || git commit -m 'Snapshot before sync-to-local'" || true

# ── Step 2: Pull key files from VPS ──────
echo -e "\n${YELLOW}[2/3] Pulling files from VPS...${NC}"

# Python files
for f in $($SSH "ls $VPS_PATH/*.py" | xargs -n1 basename); do
    # Skip one-off scripts
    case "$f" in
        patch_*|phase1_*|i_*|i4_*|i9_*|fix_*|check_*|show_*|investigate_*|diagnose_*|test_*|backfill_*|inspect_*)
            continue ;;
    esac
    $SCP "$VPS_HOST:$VPS_PATH/$f" "./$f" 2>/dev/null && echo "  ← $f" || true
done

# Templates
mkdir -p templates/studio
$SCP "$VPS_HOST:$VPS_PATH/templates/*.html" "./templates/" 2>/dev/null && echo "  ← templates/*.html"
$SCP "$VPS_HOST:$VPS_PATH/templates/studio/*.html" "./templates/studio/" 2>/dev/null && echo "  ← templates/studio/*.html"

# Static
if $SSH "[ -d $VPS_PATH/static ]" 2>/dev/null; then
    mkdir -p static
    $SCP -r "$VPS_HOST:$VPS_PATH/static/" "./static/" 2>/dev/null && echo "  ← static/"
fi

# ── Step 3: Show what changed ─────────────
echo -e "\n${YELLOW}[3/3] Changes pulled from VPS:${NC}"
git diff --stat
echo ""

CHANGED=$(git status --porcelain | wc -l)
if [ "$CHANGED" -gt 0 ]; then
    echo -e "${GREEN}$CHANGED files changed. Review and commit:${NC}"
    echo "  git add -A && git commit -m 'Sync from VPS: <describe changes>'"
else
    echo -e "${GREEN}No changes — local and VPS are in sync.${NC}"
fi
