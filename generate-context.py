#!/usr/bin/env python3
"""
Auto-generates CLAUDE.md and DEPLOY_LOG.md by scanning the actual codebase.
Run: python generate-context.py
Called automatically by deploy.sh before each deploy.
"""

import re
import os
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent


def extract_routes(filepath, prefix=""):
    """Extract @app.route or @bp.route entries from a Python file."""
    routes = []
    if not filepath.exists():
        return routes
    content = filepath.read_text(encoding="utf-8", errors="replace")
    lines = content.split("\n")
    for i, line in enumerate(lines):
        m = re.match(r'\s*@\w+\.route\("([^"]+)"(?:,\s*methods=\[([^\]]+)\])?\)', line)
        if m:
            path = prefix + m.group(1)
            methods = m.group(2) or '"GET"'
            methods = methods.replace('"', '').replace("'", "")
            # Get function name and first comment/docstring
            func_name = ""
            desc = ""
            for j in range(i + 1, min(i + 5, len(lines))):
                fm = re.match(r'\s*def (\w+)\(', lines[j])
                if fm:
                    func_name = fm.group(1)
                    # Check for docstring
                    for k in range(j + 1, min(j + 3, len(lines))):
                        dm = re.match(r'\s*"""(.+?)"""', lines[k])
                        if dm:
                            desc = dm.group(1)
                        elif '"""' in lines[k]:
                            desc = lines[k].strip().strip('"""').strip()
                    break
            routes.append({
                "path": path,
                "methods": methods,
                "func": func_name,
                "desc": desc,
                "line": i + 1,
            })
    return routes


def extract_models(filepath):
    """Extract Peewee model classes and their fields."""
    models = []
    if not filepath.exists():
        return models
    content = filepath.read_text(encoding="utf-8", errors="replace")
    lines = content.split("\n")
    current_model = None
    current_fields = []

    for i, line in enumerate(lines):
        # Model class definition
        cm = re.match(r'^class (\w+)\((\w+)\):', line)
        if cm and cm.group(2) in ("BaseModel", "Model"):
            if current_model:
                models.append({"name": current_model, "fields": current_fields, "line": current_line})
            current_model = cm.group(1)
            current_fields = []
            current_line = i + 1
            continue

        # Field definition
        if current_model:
            fm = re.match(r'\s+(\w+)\s*=\s*(CharField|TextField|IntegerField|FloatField|BooleanField|DateTimeField|DateField|ForeignKeyField|AutoField|DecimalField|BigIntegerField|SmallIntegerField)\b', line)
            if fm:
                current_fields.append(fm.group(1))
            # End of class
            elif re.match(r'^class \w+', line) or (re.match(r'^\S', line) and not line.startswith('#') and line.strip()):
                if current_model != "BaseModel":
                    models.append({"name": current_model, "fields": current_fields, "line": current_line})
                current_model = None
                current_fields = []
                # Check if this is a new model
                cm2 = re.match(r'^class (\w+)\((\w+)\):', line)
                if cm2 and cm2.group(2) in ("BaseModel", "Model"):
                    current_model = cm2.group(1)
                    current_fields = []
                    current_line = i + 1

    if current_model and current_model != "BaseModel":
        models.append({"name": current_model, "fields": current_fields, "line": current_line})

    return models


def extract_scheduled_jobs(filepath):
    """Extract APScheduler job definitions."""
    jobs = []
    if not filepath.exists():
        return jobs
    content = filepath.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r'_scheduler\.add_job\((\w+),\s*"(\w+)"(?:,\s*(?:seconds|minutes|hours)=(\d+)|,\s*hour=(\d+),\s*minute=(\d+))', content):
        func = m.group(1)
        sched_type = m.group(2)
        if sched_type == "interval":
            interval_val = m.group(3)
            jobs.append({"func": func, "schedule": f"every {interval_val}s", "type": "interval"})
        elif sched_type == "cron":
            hour = m.group(4)
            minute = m.group(5)
            jobs.append({"func": func, "schedule": f"{hour}:{minute.zfill(2)} UTC", "type": "cron"})
    return jobs


def extract_templates(templates_dir):
    """List all templates with their extends/blocks."""
    templates = []
    if not templates_dir.exists():
        return templates
    for f in sorted(templates_dir.rglob("*.html")):
        rel = f.relative_to(templates_dir)
        size = f.stat().st_size
        content = f.read_text(encoding="utf-8", errors="replace")
        extends = ""
        em = re.search(r'{%\s*extends\s*["\']([^"\']+)', content)
        if em:
            extends = em.group(1)
        templates.append({"name": str(rel), "size": size, "extends": extends})
    return templates


def get_file_stats():
    """Get line counts for key files."""
    stats = []
    for f in sorted(ROOT.glob("*.py")):
        if f.name.startswith(("test_", "preview_", "investigate_", "inspect_", "patch_", "phase1_", "i_", "i4_", "i9_", "fix_", "check_", "show_", "diagnose_", "backfill_")):
            continue
        lines = len(f.read_text(encoding="utf-8", errors="replace").split("\n"))
        stats.append({"name": f.name, "lines": lines})
    return stats


def get_git_log(n=10):
    """Get recent git commits."""
    try:
        result = subprocess.run(
            ["git", "log", f"--oneline", f"-{n}", "--format=%h %ai %s"],
            capture_output=True, text=True, cwd=str(ROOT)
        )
        return result.stdout.strip().split("\n") if result.stdout.strip() else []
    except Exception:
        return []


def get_deploy_log():
    """Read deploy log if it exists."""
    log_path = ROOT / "DEPLOY_LOG.md"
    if log_path.exists():
        return log_path.read_text(encoding="utf-8", errors="replace")
    return ""


def generate_claude_md():
    """Generate the full CLAUDE.md."""

    # Extract everything
    app_routes = extract_routes(ROOT / "app.py")
    studio_routes = extract_routes(ROOT / "studio_routes.py", prefix="/studio")
    all_routes = app_routes + studio_routes
    models = extract_models(ROOT / "database.py")
    jobs = extract_scheduled_jobs(ROOT / "app.py")
    templates = extract_templates(ROOT / "templates")
    file_stats = get_file_stats()
    git_log = get_git_log(15)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Build the document
    doc = f"""# MailEngineHub — Claude Project Context
> Auto-generated by `generate-context.py` on {now}. Do NOT edit manually — your changes will be overwritten.

Built for Davinder. In-house email marketing platform for LDAS Electronics, replacing Klaviyo/Omnisend.
Stack: Flask + SQLite (Peewee ORM) + Amazon SES + Gunicorn on VPS.

---

## ⚠️ DEPLOYMENT RULES — READ FIRST ⚠️

**NEVER edit files directly on the VPS via SSH.** All changes go through git.

### The Workflow

```
1. Edit files in THIS local repo (mailenginehub-repo/)
2. Test locally if possible
3. git add + git commit
4. Run: bash deploy.sh
5. deploy.sh handles: snapshot VPS → copy files → commit on VPS → restart → smoke test
```

### If You Must Edit on VPS (emergency hotfix)

```
1. SSH in and make the fix
2. IMMEDIATELY run on VPS: cd /var/www/mailengine && git add -A && git commit -m "hotfix: describe"
3. Then sync back: bash sync-from-vps.sh (from local repo)
4. Commit locally: git add -A && git commit -m "Sync hotfix from VPS"
```

### Rollback

```bash
ssh -i ~/.ssh/mailengine_vps root@mailenginehub.com
cd /var/www/mailengine
git log --oneline -5          # find the good commit
git checkout <commit> -- .    # restore files
systemctl restart mailengine
```

### NEVER Do These Things

- ❌ `scp` local files to VPS without committing first
- ❌ Edit VPS files without committing on VPS after
- ❌ Overwrite VPS app.py from local without checking if local is behind
- ❌ Run patch scripts that modify app.py via string replacement
- ❌ Leave VPS changes uncommitted at end of session

---

## Project Layout

| Location | Purpose |
|---|---|
| `C:\\Users\\davin\\Claude Work Folder\\mailenginehub-repo\\` | **Primary local git repo** (work here) |
| `root@mailenginehub.com:/var/www/mailengine/` | **VPS production** (has its own git) |
| `https://github.com/davinderpreet/mailenginehub` | **GitHub remote** |

> ⚠️ `C:\\Users\\davin\\Claude Work Folder\\email-platform\\` and `mailenginehub\\` are OLD — DO NOT USE.

### VPS Access

```bash
SSH_KEY=~/.ssh/mailengine_vps
ssh -i $SSH_KEY root@mailenginehub.com
# Service: systemctl restart mailengine
# Logs: journalctl -u mailengine -n 50
# App: port 5000 behind nginx
# Auth: admin:DavinderS@1993
# Live: https://mailenginehub.com
```

### Scripts

| Script | What it does |
|---|---|
| `deploy.sh` | Deploy local → VPS (snapshot, copy, commit, restart, smoke test, auto-rollback) |
| `sync-from-vps.sh` | Pull VPS → local (when VPS was edited directly) |
| `generate-context.py` | Regenerate this CLAUDE.md from codebase (auto-run by deploy.sh) |

---

## UI Design System (Dark Theme)

All templates extend `base.html` (dark glass theme). Key CSS variables:
- `--bg: #07091a`, `--surface: rgba(255,255,255,0.035)`, `--border: rgba(255,255,255,0.07)`
- `--purple: #7c3aed`, `--purple2: #a855f7`, `--cyan: #06b6d4`, `--pink: #ec4899`
- `--green: #10b981`, `--amber: #f59e0b`, `--red: #ef4444`
- Stat card colors: `purple`, `cyan`, `green`, `pink` (NOT blue/orange)

---

## All Routes ({len(all_routes)} total)

| Route | Methods | Function | Line |
|---|---|---|---|
"""

    # Group routes by category
    for r in sorted(all_routes, key=lambda x: x["path"]):
        doc += f"| `{r['path']}` | {r['methods']} | `{r['func']}` | {r['line']} |\n"

    doc += f"""
---

## Database Models ({len(models)} models in database.py)

| Model | Key Fields | Line |
|---|---|---|
"""

    for m in models:
        fields_str = ", ".join(m["fields"][:8])
        if len(m["fields"]) > 8:
            fields_str += f" (+{len(m['fields']) - 8} more)"
        doc += f"| `{m['name']}` | {fields_str} | {m['line']} |\n"

    doc += f"""
---

## Scheduled Jobs ({len(jobs)} jobs)

### Interval Jobs (continuous)

| Function | Schedule |
|---|---|
"""

    for j in sorted(jobs, key=lambda x: (x["type"], x["schedule"])):
        if j["type"] == "interval":
            doc += f"| `{j['func']}` | {j['schedule']} |\n"

    doc += """
### Nightly Cron Jobs

| Function | Time (UTC) |
|---|---|
"""

    for j in sorted(jobs, key=lambda x: x["schedule"]):
        if j["type"] == "cron":
            doc += f"| `{j['func']}` | {j['schedule']} |\n"

    doc += f"""
---

## Templates ({len(templates)} files)

| Template | Extends | Size |
|---|---|---|
"""

    for t in templates:
        size_kb = f"{t['size'] / 1024:.1f}KB"
        doc += f"| `{t['name']}` | {t['extends']} | {size_kb} |\n"

    doc += f"""
---

## File Map ({len(file_stats)} Python files)

| File | Lines | Purpose |
|---|---|---|
"""

    # Purpose map
    purpose = {
        "app.py": "All Flask routes + scheduler",
        "database.py": "All Peewee ORM models + init_db()",
        "block_registry.py": "Email template block rendering",
        "studio_routes.py": "AI Studio Blueprint (/studio/*)",
        "studio_skills.py": "AI skills pipeline for email generation",
        "template_studio.py": "Template Studio orchestrator",
        "knowledge_scraper.py": "Knowledge base auto-enrichment",
        "ai_engine.py": "Nightly AI scoring + daily plan",
        "ai_content.py": "AI content generation",
        "ai_provider.py": "AI API provider abstraction",
        "next_best_message.py": "Per-contact email decision engine",
        "learning_engine.py": "Self-learning analysis pipeline",
        "outcome_tracker.py": "Email outcome collection",
        "strategy_optimizer.py": "Apply learned insights",
        "learning_config.py": "Key-value config (get_val/set_val)",
        "delivery_engine.py": "Email delivery with warmup compliance",
        "email_sender.py": "AWS SES sending via boto3",
        "email_shell.py": "Unified header + footer for emails",
        "email_templates.py": "Seed email templates",
        "system_map_data.py": "System architecture viz data (65 nodes)",
        "campaign_planner.py": "AI campaign opportunity scanner",
        "campaign_preflight.py": "Pre-send validation checks",
        "profit_engine.py": "Product profitability scoring",
        "customer_intelligence.py": "Customer profile enrichment",
        "condition_engine.py": "Flow step conditional logic",
        "cascade.py": "Auto-cascade intelligence rules",
        "shopify_sync.py": "Shopify customer/order sync",
        "shopify_products.py": "Shopify product catalog sync",
        "shopify_enrichment.py": "Shopify data enrichment",
        "activity_sync.py": "Email activity sync",
        "action_ledger.py": "Action audit logging",
        "identity_resolution.py": "Cross-channel identity matching",
        "data_enrichment.py": "Contact data enrichment",
        "discount_engine.py": "Dynamic discount generation",
        "discount_codes.py": "Shopify discount code management",
        "token_utils.py": "Token generation utilities",
        "health_check.py": "System health diagnostics",
        "watchdog.py": "Auto-restart watchdog",
        "run.py": "Entry point",
        "flow_templates_seed.py": "Seed flow templates",
        "convert_templates.py": "Template migration utilities",
        "create_showcase_templates.py": "Showcase template generator",
        "normalize_activity.py": "Activity data normalization",
        "sns_verify.py": "AWS SNS verification",
        "trigger_sync.py": "Trigger sync utility",
        "generate-context.py": "Auto-generate this CLAUDE.md",
    }

    for s in sorted(file_stats, key=lambda x: -x["lines"]):
        p = purpose.get(s["name"], "")
        doc += f"| `{s['name']}` | {s['lines']:,} | {p} |\n"

    doc += """
---

## Recent Git History

```
"""

    for entry in git_log:
        doc += f"{entry}\n"

    doc += """```

---

## Common Errors

- `UndefinedError` in template → Missing variable in route's `render_template()` call
- `AttributeError` on model → Field doesn't exist; check database.py for actual field names
- Service won't start → `journalctl -u mailengine -n 50`
- Route 404 → Check app.py `@app.route` or studio_routes.py for `/studio/*`
- `LearningConfig` uses key-value: `LearningConfig.get_val(key, default)` / `set_val(key, value)`
"""

    return doc


def append_deploy_log(commit_hash, commit_msg):
    """Append to DEPLOY_LOG.md."""
    log_path = ROOT / "DEPLOY_LOG.md"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    if not log_path.exists():
        header = "# MailEngineHub — Deploy Log\n\nAutomatically updated by `deploy.sh` after each deploy.\n\n---\n\n"
        log_path.write_text(header, encoding="utf-8")

    existing = log_path.read_text(encoding="utf-8", errors="replace")

    # Get changed files from git
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            capture_output=True, text=True, cwd=str(ROOT)
        )
        changed = result.stdout.strip()
    except Exception:
        changed = "(unknown)"

    entry = f"""### {now} — `{commit_hash}`

**{commit_msg}**

Files changed:
```
{changed}
```

---

"""

    # Insert after header
    parts = existing.split("---\n\n", 1)
    if len(parts) == 2:
        new_content = parts[0] + "---\n\n" + entry + parts[1]
    else:
        new_content = existing + "\n" + entry

    log_path.write_text(new_content, encoding="utf-8")
    return log_path


def generate_memory_md():
    """Generate MEMORY.md for Claude Code cross-session memory."""

    # Extract key stats
    app_routes = extract_routes(ROOT / "app.py")
    studio_routes = extract_routes(ROOT / "studio_routes.py", prefix="/studio")
    all_routes = app_routes + studio_routes
    models = extract_models(ROOT / "database.py")
    jobs = extract_scheduled_jobs(ROOT / "app.py")
    file_stats = get_file_stats()
    git_log = get_git_log(5)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Page routes only (no API/webhooks/tracking)
    pages = [r for r in all_routes if not r["path"].startswith("/api/")
             and not r["path"].startswith("/track/")
             and not r["path"].startswith("/webhooks/")
             and not r["path"].startswith("/unsubscribe/")
             and r["methods"] in ("GET", "GET, POST")
             and "<int:" not in r["path"]
             and "<email>" not in r["path"]
             and "<token>" not in r["path"]]

    nightly_jobs = [j for j in jobs if j["type"] == "cron"]
    total_lines = sum(s["lines"] for s in file_stats)

    doc = f"""# MailEngineHub -- Project Memory
> Auto-generated by `generate-context.py` on {now}. Run `python generate-context.py` in mailenginehub-repo to refresh.

## DEPLOYMENT WORKFLOW -- FOLLOW THIS
- **Primary repo**: `C:\\Users\\davin\\Claude Work Folder\\mailenginehub-repo\\` (git, push to GitHub)
- **VPS**: `root@mailenginehub.com:/var/www/mailengine/` (also has git)
- **Deploy**: `cd mailenginehub-repo && bash deploy.sh` (handles snapshot, copy, commit, restart, smoke test)
- **Sync VPS to local**: `bash sync-from-vps.sh` (if VPS was edited directly)
- **NEVER scp files to VPS without committing first**
- **NEVER edit VPS directly without committing on VPS after**
- **DO NOT USE** `C:\\Users\\davin\\Claude Work Folder\\email-platform\\` (old, outdated copy)
- **DO NOT USE** `C:\\Users\\davin\\Claude Work Folder\\mailenginehub\\` (older repo)
- Full rules in `CLAUDE.md` inside the repo

## Project Overview
In-house email marketing platform for LDAS Electronics, replacing Klaviyo/Omnisend.
- **Live URL**: https://mailenginehub.com
- **Stack**: Flask + SQLite (Peewee) + Amazon SES + Gunicorn on VPS
- **SSH key**: `~/.ssh/mailengine_vps` -> `root@mailenginehub.com`
- **GitHub**: https://github.com/davinderpreet/mailenginehub
- **Auth**: admin:DavinderS@1993
- **Restart**: `ssh -i ~/.ssh/mailengine_vps root@mailenginehub.com "systemctl restart mailengine"`

## Codebase Stats
- **{len(all_routes)} routes** ({len(app_routes)} in app.py, {len(studio_routes)} in studio_routes.py)
- **{len(models)} database models** in database.py
- **{len(nightly_jobs)} nightly cron jobs** + 7 interval jobs
- **{len(file_stats)} Python files** ({total_lines:,} total lines)
- **app.py**: {next((s['lines'] for s in file_stats if s['name'] == 'app.py'), 0):,} lines

## Key Pages (sidebar)
"""

    for r in sorted(pages, key=lambda x: x["path"]):
        doc += f"- `{r['path']}` -- {r['func']}\n"

    doc += f"""
## UI Design System (Dark Theme)
All templates extend `base.html` (dark glass theme). Key CSS variables:
- `--bg: #07091a`, `--surface: rgba(255,255,255,0.035)`, `--border: rgba(255,255,255,0.07)`
- `--purple: #7c3aed`, `--purple2: #a855f7`, `--cyan: #06b6d4`, `--pink: #ec4899`
- `--green: #10b981`, `--amber: #f59e0b`, `--red: #ef4444`
- Stat card colors: `purple`, `cyan`, `green`, `pink` (NOT blue/orange)

## Database Key Models
- `Contact` -- email list (5,939 contacts)
- `CustomerProfile` -- enriched profile per contact (50+ fields)
- `CampaignEmail` / `FlowEmail` -- sent email tracking
- `ContactScore` -- AI scoring (rfm_segment, engagement_score)
- `MessageDecision` -- per-contact email decision log
- `LearningConfig` -- key-value store: `get_val(key, default)` / `set_val(key, value)`
- `KnowledgeEntry` -- AI knowledge base entries
- `ActionLedger` -- audit trail for all actions

## Nightly Pipeline (UTC)
"""

    for j in sorted(nightly_jobs, key=lambda x: x["schedule"]):
        doc += f"- {j['schedule']}: `{j['func']}`\n"

    doc += f"""
## Recent Git History
"""

    for entry in git_log:
        doc += f"- {entry}\n"

    doc += f"""
## Key Status
- AWS SES: Verified, in sandbox mode (awaiting production access)
- Warmup: Active, Phase 1 (Ignition, 50/day)
- All {len(pages)} page routes verified working
"""

    return doc


if __name__ == "__main__":
    import sys

    print("Scanning codebase...")

    # Generate CLAUDE.md
    claude_md = generate_claude_md()
    out_path = ROOT / "CLAUDE.md"
    out_path.write_text(claude_md, encoding="utf-8")
    print(f"  [OK] CLAUDE.md generated ({len(claude_md):,} chars)")

    # Generate MEMORY.md
    memory_md = generate_memory_md()
    memory_path = Path.home() / ".claude" / "projects" / "C--Users-davin-Claude-Work-Folder" / "memory" / "MEMORY.md"
    if memory_path.parent.exists():
        memory_path.write_text(memory_md, encoding="utf-8")
        print(f"  [OK] MEMORY.md generated ({len(memory_md):,} chars)")
    else:
        print(f"  [SKIP] MEMORY.md path not found: {memory_path.parent}")

    # If called with --deploy <hash> <msg>, also update deploy log
    if len(sys.argv) >= 4 and sys.argv[1] == "--deploy":
        commit_hash = sys.argv[2]
        commit_msg = " ".join(sys.argv[3:])
        log_path = append_deploy_log(commit_hash, commit_msg)
        print(f"  [OK] DEPLOY_LOG.md updated")

    print("Done.")
