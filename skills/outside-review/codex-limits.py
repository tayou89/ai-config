"""Print Codex's plan and used percentage per limit window without spending usage.

Asks Codex's own `codex app-server` for `account/rateLimits/read`, the same read
the usage panel uses; no credentials are read. Prints one JSON line such as
{"plan": "plus", "five_hour": 0, "seven_day": 100, "seven_day_resets": "...", "running_reviews": 1}
or {"error": "...", "running_reviews": 1} and exits 0 either way, so a caller can decide what to do.
`running_reviews` counts the `codex exec` processes running on this machine whose brief starts
with `Budget reservation: <id>` for a review reservation in the budget ledger, and read-only ones
without such a line, since implementation and research runs must not hold reviews back.
"""
import datetime
import json
import os
import re
import select
import shutil
import subprocess
import sys
import time

WINDOW_KIND = {300: 'five_hour', 10080: 'seven_day'}
TIMEOUT_SECONDS = 20
INITIALIZE_ID = 1
LIMITS_ID = 2
LEDGER_FILE = os.path.expanduser('~/.claude/codex-budget/ledger.jsonl')
REVIEW_KIND = 'review'
RESERVE_EVENT = 'reserve'
EVENT_KEY = 'event'
IDENTIFIER_KEY = 'identifier'
KIND_KEY = 'kind'
OPTIONS_END = '--'
PS_LINE_BREAK = '\\012'
RESERVATION_LINE = re.compile(r'Budget reservation:\s*(\d{8}-[0-9a-f]{8})')


def send(process, message):
    process.stdin.write(json.dumps(message) + '\n')
    process.stdin.flush()


def read_limits(codex):
    process = subprocess.Popen([codex, 'app-server'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True, bufsize=1)

    try:
        send(process, {'id': INITIALIZE_ID, 'method': 'initialize',
                       'params': {'clientInfo': {'name': 'outside-review', 'version': '1'}}})
        deadline = time.time() + TIMEOUT_SECONDS

        while time.time() < deadline:
            if not select.select([process.stdout], [], [], 0.5)[0]:
                continue
            line = process.stdout.readline()

            if not line:
                break
            message = json.loads(line)

            if message.get('id') == INITIALIZE_ID:
                send(process, {'method': 'initialized'})
                send(process, {'id': LIMITS_ID, 'method': 'account/rateLimits/read',
                               'params': {'excludeResetCreditDetails': True}})
            elif message.get('id') == LIMITS_ID:
                return message
    finally:
        process.terminate()
    return None


def summarize(answer):
    if answer is None:
        return {'error': 'Codex did not answer in time'}
    elif 'error' in answer:
        return {'error': 'Codex could not read its limits'}
    else:
        rate = (answer.get('result') or {}).get('rateLimits') or {}
        summary = {'plan': rate.get('planType') or rate.get('plan_type')}

        for key in ('primary', 'secondary'):
            window = rate.get(key) or {}
            kind = WINDOW_KIND.get(window.get('windowDurationMins', window.get('window_minutes')))
            used = window.get('usedPercent', window.get('used_percent'))
            resets = window.get('resetsAt', window.get('resets_at'))

            if kind and used is not None:
                summary[kind] = used
                if resets:
                    summary[kind + '_resets'] = datetime.datetime.fromtimestamp(resets).astimezone().isoformat(timespec='minutes')
        return summary


def read_reservation_kinds(path=LEDGER_FILE):
    kinds = {}

    try:
        with open(path, encoding='utf-8') as handle:
            lines = handle.read().splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get(EVENT_KEY) == RESERVE_EVENT and IDENTIFIER_KEY in record:
            kinds[str(record[IDENTIFIER_KEY])] = str(record.get(KIND_KEY, ''))
    return kinds


def read_reservation_id(arguments):
    """Return the id on the first line of the brief after `--`, where `ps` shows line breaks as `\\012`."""
    if OPTIONS_END in arguments:
        brief = ' '.join(arguments[arguments.index(OPTIONS_END) + 1:]).replace(PS_LINE_BREAK, '\n')
        lines = [line.strip() for line in brief.splitlines() if line.strip()]
        found = RESERVATION_LINE.fullmatch(lines[0]) if lines else None

        return found.group(1) if found else ''
    else:
        return ''


def is_review_process(arguments, kinds=None):
    is_codex_exec = len(arguments) > 1 and os.path.basename(arguments[0]) == 'codex' and arguments[1] == 'exec'
    kind = (kinds or {}).get(read_reservation_id(arguments))

    if is_codex_exec and kind:
        return kind == REVIEW_KIND
    elif is_codex_exec:
        return is_read_only(arguments)
    else:
        return False


def is_read_only(arguments):
    pairs = zip(arguments, arguments[1:])

    return '--sandbox=read-only' in arguments or any(
        flag in ('-s', '--sandbox') and value == 'read-only' for flag, value in pairs
    )


def count_running_reviews():
    listing = subprocess.run(['ps', '-axww', '-o', 'args='], capture_output=True, text=True).stdout
    kinds = read_reservation_kinds()

    return sum(1 for line in listing.splitlines() if is_review_process(line.split(), kinds))


def main():
    codex = shutil.which('codex')

    if codex:
        summary = summarize(read_limits(codex))
    else:
        summary = {'error': 'codex not found'}
    summary['running_reviews'] = count_running_reviews()
    print(json.dumps(summary))
    return 0


if __name__ == '__main__':
    sys.exit(main())
