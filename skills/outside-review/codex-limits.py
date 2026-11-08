"""Print Codex's plan and used percentage per limit window without spending usage.

Asks Codex's own `codex app-server` for `account/rateLimits/read`, the same read
the usage panel uses; no credentials are read. Prints one JSON line such as
{"plan": "plus", "five_hour": 0, "seven_day": 100, "seven_day_resets": "..."}
or {"error": "..."} and exits 0 either way, so a caller can decide what to do.
"""
import datetime
import json
import select
import shutil
import subprocess
import sys
import time

WINDOW_KIND = {300: 'five_hour', 10080: 'seven_day'}
TIMEOUT_SECONDS = 20
INITIALIZE_ID = 1
LIMITS_ID = 2


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


def main():
    codex = shutil.which('codex')

    if codex:
        print(json.dumps(summarize(read_limits(codex))))
    else:
        print(json.dumps({'error': 'codex not found'}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
