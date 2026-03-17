#!/bin/bash
# ============================================================
#  MailEngineHub Deploy Script
#  Deploys local git repo → VPS (with safety checks)
# ============================================================

set -e

VPS_HOST="root@mailenginehub.com"
VPS_PATH="/var/www/mailengine"
SSH_KEY="$HOME/.ssh/mailengine_vps"
SSH="ssh -i $SSH_KEY $VPS_HOST"
SCP="scp -i $SSH_KEY"
SERVICE="mailengine"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "========================================"
echo "  MailEngineHub Deploy"
echo "========================================"

# ── Step 1: Pre-flight checks ──────────────
echo -e "\n${YELLOW}[1/6] Pre-flight checks...${NC}"

# Must be in git repo
if ! git rev-parse --is-inside-work-tree > /dev/null 2>&1; then
    echo -e "${RED}ERROR: Not in a git repository. Run from mailenginehub-repo/${NC}"
    exit 1
fi

# Must have clean working tree (everything committed)
if [ -n "$(git status --porcelain)" ]; then
    echo -e "${RED}ERROR: Uncommitted changes detected. Commit first!${NC}"
    echo "  Run: git add -A && git commit -m 'your message'"
    git status --short
    exit 1
fi

LOCAL_COMMIT=$(git rev-parse --short HEAD)
LOCAL_MSG=$(git log -1 --format='%s')
echo -e "  Local commit: ${GREEN}$LOCAL_COMMIT${NC} — $LOCAL_MSG"

# ── Step 2: Snapshot VPS state before deploy ──
echo -e "\n${YELLOW}[2/6] Snapshotting VPS state (git commit on VPS)...${NC}"
$SSH "cd $VPS_PATH && git add -A && git diff --cached --quiet || git commit -m 'Auto-snapshot before deploy $LOCAL_COMMIT' 2>/dev/null" || true
VPS_COMMIT=$($SSH "cd $VPS_PATH && git rev-parse --short HEAD")
echo -e "  VPS snapshot: ${GREEN}$VPS_COMMIT${NC}"

# ── Step 3: Copy files to VPS ─────────────
echo -e "\n${YELLOW}[3/6] Deploying files to VPS...${NC}"

# Core Python files
for f in app.py database.py block_registry.py studio_routes.py studio_skills.py \
         ai_engine.py ai_content.py ai_provider.py delivery_engine.py email_sender.py \
         email_shell.py email_templates.py next_best_message.py knowledge_scraper.py \
         template_studio.py learning_engine.py learning_config.py outcome_tracker.py \
         strategy_optimizer.py system_map_data.py campaign_planner.py campaign_preflight.py \
         profit_engine.py discount_engine.py discount_codes.py condition_engine.py \
         cascade.py customer_intelligence.py data_enrichment.py identity_resolution.py \
         shopify_sync.py shopify_products.py shopify_enrichment.py activity_sync.py \
         action_ledger.py token_utils.py health_check.py watchdog.py run.py; do
    if [ -f "$f" ]; then
        $SCP "$f" "$VPS_HOST:$VPS_PATH/$f"
    fi
done

# Templates
$SCP templates/*.html "$VPS_HOST:$VPS_PATH/templates/"
if [ -d "templates/studio" ]; then
    $SCP templates/studio/*.html "$VPS_HOST:$VPS_PATH/templates/studio/"
fi

# Static files
if [ -d "static" ]; then
    $SCP -r static/ "$VPS_HOST:$VPS_PATH/static/"
fi

echo -e "  ${GREEN}Files deployed.${NC}"

# ── Step 4: Commit on VPS ─────────────────
echo -e "\n${YELLOW}[4/6] Committing deploy on VPS...${NC}"
$SSH "cd $VPS_PATH && git add -A && git commit -m 'Deploy $LOCAL_COMMIT: $LOCAL_MSG'"
echo -e "  ${GREEN}VPS commit created.${NC}"

# ── Step 5: Restart service ───────────────
echo -e "\n${YELLOW}[5/6] Restarting $SERVICE...${NC}"
$SSH "systemctl restart $SERVICE"
sleep 3
STATUS=$($SSH "systemctl is-active $SERVICE")
if [ "$STATUS" = "active" ]; then
    echo -e "  ${GREEN}Service is running.${NC}"
else
    echo -e "  ${RED}SERVICE FAILED! Rolling back...${NC}"
    $SSH "cd $VPS_PATH && git checkout $VPS_COMMIT -- . && systemctl restart $SERVICE"
    echo -e "  ${YELLOW}Rolled back to $VPS_COMMIT${NC}"
    exit 1
fi

# ── Step 6: Smoke test ────────────────────
echo -e "\n${YELLOW}[6/6] Smoke test (checking key routes)...${NC}"
FAILED=0
for route in / /contacts /settings /ai-engine /studio /warmup /templates /flows /profiles /sent-emails /campaign-planner /profits /learning /audit /system-map; do
    CODE=$($SSH "curl -s -o /dev/null -w '%{http_code}' -L -u admin:DavinderS@1993 http://localhost:5000$route")
    if [ "$CODE" = "200" ] || [ "$CODE" = "302" ]; then
        echo -e "  ${GREEN}$CODE${NC} $route"
    else
        echo -e "  ${RED}$CODE${NC} $route"
        FAILED=1
    fi
done

if [ "$FAILED" = "1" ]; then
    echo -e "\n${RED}⚠ Some routes failed! Check logs with:${NC}"
    echo "  ssh -i ~/.ssh/mailengine_vps root@mailenginehub.com 'journalctl -u mailengine -n 50'"
    echo -e "\n${YELLOW}To rollback:${NC}"
    echo "  ssh -i ~/.ssh/mailengine_vps root@mailenginehub.com 'cd /var/www/mailengine && git checkout $VPS_COMMIT -- . && systemctl restart mailengine'"
else
    echo -e "\n${GREEN}✓ Deploy successful!${NC}"
    echo "  Local:  $LOCAL_COMMIT — $LOCAL_MSG"
    echo "  VPS:    $($SSH "cd $VPS_PATH && git rev-parse --short HEAD")"
fi
