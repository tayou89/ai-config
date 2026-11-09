"""Write a Claude transcript as plain conversation text for a reviewer.

Keeps user and assistant text and one line per tool call. Tool results, images,
system reminders, and meta messages become markers that give their size and the
original line number, so the reviewer opens the original JSONL line only when it
needs that detail. Thinking blocks are left out. Prints one JSON line with the
sizes. Usage: transcript-text.py <transcript.jsonl> <output.md>
"""
import json
import os
import re
import sys

CONVERSATION_TYPES = ('user', 'assistant')
REMINDER_PATTERN = re.compile(r'<system-reminder>.*?</system-reminder>', re.DOTALL)
TOOL_INPUT_KEYS = ('command', 'file_path', 'pattern', 'url', 'query', 'prompt', 'description')
TOOL_INPUT_LIMIT = 200
USAGE = 'usage: transcript-text.py <transcript.jsonl> <output.md>'


def parse_entry(line):
    try:
        return json.loads(line)
    except ValueError:
        return None


def replace_reminders(text):
    return REMINDER_PATTERN.sub(lambda match: f'[system reminder: {len(match.group(0)):,} chars]', text)


def describe_tool_use(block):
    tool_input = block.get('input') or {}
    detail = next((str(tool_input[key]) for key in TOOL_INPUT_KEYS if tool_input.get(key)), '')

    return f'[tool call {block.get("name")}: {detail[:TOOL_INPUT_LIMIT]}]'


def render_block(block, line_number):
    kind = block.get('type')

    if kind == 'text':
        return replace_reminders(block.get('text', ''))
    elif kind == 'tool_use':
        return describe_tool_use(block)
    elif kind == 'tool_result':
        size = len(json.dumps(block.get('content'), ensure_ascii=False))

        return f'[tool result: {size:,} chars, original line {line_number}]'
    elif kind == 'image':
        return f'[image, original line {line_number}]'
    else:
        return ''


def render_body(entry, line_number):
    content = (entry.get('message') or {}).get('content')

    if entry.get('isMeta'):
        return f'[meta message: {len(json.dumps(content, ensure_ascii=False)):,} chars, original line {line_number}]'
    elif isinstance(content, str):
        return replace_reminders(content)
    else:
        return '\n'.join(part for part in (render_block(block, line_number) for block in content or []) if part.strip())


def find_speaker(entry):
    content = (entry.get('message') or {}).get('content')
    is_tool_result_only = isinstance(content, list) and all(block.get('type') == 'tool_result' for block in content)

    return 'assistant' if is_tool_result_only else entry.get('type')


def convert_transcript(source, target):
    speaker, entry_count = None, 0

    with open(source, errors='replace') as reader, open(target, 'w') as writer:
        for line_number, line in enumerate(reader, start=1):
            entry = parse_entry(line)

            if entry and entry.get('type') in CONVERSATION_TYPES:
                body = render_body(entry, line_number)

                if body:
                    current = find_speaker(entry)

                    if current != speaker:
                        writer.write(f'\n## L{line_number} {current} {entry.get("timestamp", "")}\n')
                        speaker = current
                    writer.write(body + '\n')
                    entry_count += 1
    return entry_count


def main():
    if len(sys.argv) == 3:
        source, target = sys.argv[1], sys.argv[2]
        entry_count = convert_transcript(source, target)
        summary = {'source_bytes': os.path.getsize(source), 'text_bytes': os.path.getsize(target), 'entries': entry_count}

        print(json.dumps(summary))
        return 0
    else:
        print(USAGE, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
