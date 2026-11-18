#!/usr/bin/env python3
import json, sys, subprocess, os, datetime, shutil, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

data = json.load(sys.stdin)

# User email + login method from OAuth account
user_email = ''
login_method = ''
try:
    config_dir = os.environ.get('CLAUDE_CONFIG_DIR', os.path.expanduser('~/.claude'))
    with open(os.path.join(os.path.dirname(config_dir), '.claude.json'), encoding='utf-8') as f:
        claude_cfg = json.load(f)
        oauth = claude_cfg.get('oauthAccount', {})
        user_email = oauth.get('emailAddress', '')
        billing = oauth.get('billingType', '')
        if billing == 'stripe_subscription':
            login_method = 'Subscription'
        elif oauth:
            login_method = 'Claude'
except Exception:
    pass

# Override with 3rd-party / API key if detected
if os.environ.get('CLAUDE_CODE_USE_BEDROCK'):
    login_method = 'Bedrock'
elif os.environ.get('CLAUDE_CODE_USE_VERTEX'):
    login_method = 'Vertex'
elif os.environ.get('CLAUDE_CODE_USE_FOUNDRY'):
    login_method = 'Foundry'
elif os.environ.get('ANTHROPIC_API_KEY'):
    login_method = 'API Console'

# Basic info
model = data.get('model', {}).get('display_name', '')
cwd = data.get('workspace', {}).get('current_dir', '') or data.get('cwd', '')
directory = os.path.basename(cwd) if cwd else ''
cost = data.get('cost', {}).get('total_cost_usd', 0) or 0
pct = int(data.get('context_window', {}).get('used_percentage', 0) or 0)
duration_ms = data.get('cost', {}).get('total_duration_ms', 0) or 0
total_in = data.get('context_window', {}).get('total_input_tokens', 0) or 0
total_out = data.get('context_window', {}).get('total_output_tokens', 0) or 0

# Cache
cur = data.get('context_window', {}).get('current_usage', {}) or {}
cache_read = cur.get('cache_read_input_tokens', 0) or 0
cache_create = cur.get('cache_creation_input_tokens', 0) or 0

# Colors
CYAN, GREEN, YELLOW, RED, DIM, RESET = '\033[36m', '\033[32m', '\033[33m', '\033[31m', '\033[90m', '\033[0m'

# Context progress bar
bar_color = RED if pct >= 80 else YELLOW if pct >= 50 else GREEN
filled = pct * 10 // 100
bar = '\u2588' * filled + '\u2591' * (10 - filled)

# Duration
mins, secs = duration_ms // 60000, (duration_ms % 60000) // 1000

# Git: find executable cross-platform
git_cmd = shutil.which('git')
if not git_cmd:
    for p in ['/usr/local/bin/git', '/usr/bin/git', '/bin/git']:
        if os.path.isfile(p):
            git_cmd = p
            break

# Git branch + detailed status
branch = ''
if git_cmd and cwd:
    try:
        branch_name = subprocess.check_output(
            [git_cmd, '-C', cwd, '--no-optional-locks', 'branch', '--show-current'],
            text=True, stderr=subprocess.DEVNULL
        ).strip()
        branch = f' | 🌿 {GREEN}{branch_name}{RESET}' if branch_name else ''
        porcelain = subprocess.check_output(
            [git_cmd, '-C', cwd, '--no-optional-locks', 'status', '--porcelain'],
            text=True, stderr=subprocess.DEVNULL
        ).strip()
        if porcelain and branch:
            staged = modified = untracked = 0
            for line in porcelain.splitlines():
                if len(line) >= 2:
                    x, y = line[0], line[1]
                    if x == '?':
                        untracked += 1
                    else:
                        if x in 'MADRC':
                            staged += 1
                        if y in 'MD':
                            modified += 1
            parts = []
            if staged: parts.append(f'{GREEN}+{staged}{RESET}')
            if modified: parts.append(f'{YELLOW}~{modified}{RESET}')
            if untracked: parts.append(f'{RED}?{untracked}{RESET}')
            if parts:
                branch += ' ' + ' '.join(parts)
            else:
                branch += f' {RED}*{RESET}'
    except Exception:
        branch = ''

# Rate limits
rl = data.get('rate_limits', {})
rate_parts = []
for key, label in [('five_hour', '5h'), ('seven_day', '7d')]:
    obj = rl.get(key)
    if obj:
        rpct = int(obj.get('used_percentage', 0))
        ts = obj.get('resets_at')
        rc = RED if rpct >= 80 else YELLOW if rpct >= 50 else DIM
        if ts and ts > 0:
            reset = datetime.datetime.fromtimestamp(ts).strftime('%H:%M')
            rate_parts.append(f'{rc}[{label}: {rpct}% -> {reset}]{RESET}')
        else:
            rate_parts.append(f'{rc}[{label}: {rpct}%]{RESET}')
rate_str = ' '.join(rate_parts)

# Line 1: user email + login method, directory, branch + git status
method_str = f' {DIM}({login_method}){RESET}' if login_method else ''
user_str = f'👤 {DIM}{user_email}{RESET}{method_str}' if user_email else (method_str if login_method else '')
print(f'{user_str} | 📁 {directory}{branch}' if user_str else f'📁 {directory}{branch}')

# Line 2: model, context bar, tokens, cost, duration, rate limits
def fmt_tokens(n):
    if n >= 1_000_000: return f'{n/1_000_000:.1f}M'
    if n >= 1_000: return f'{n/1_000:.0f}k'
    return str(n)

cache_total = cache_read + cache_create
cache_pct = int(cache_read * 100 / cache_total) if cache_total > 0 else 0
cache_str = f' C:{cache_pct}%' if cache_total > 0 else ''

line2 = f'{CYAN}[{model}]{RESET} {bar_color}{bar}{RESET} {pct}% | 🔤 {DIM}in:{fmt_tokens(total_in)} out:{fmt_tokens(total_out)}{cache_str}{RESET} | 💰 {YELLOW}${cost:.2f}{RESET} | ⏱️ {mins}m{secs}s'
if rate_str:
    line2 += f' | {rate_str}'
print(line2)
