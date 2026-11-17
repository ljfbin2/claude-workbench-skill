"""Obtain Claude's model candidates using its official Python SDK.

This process initializes the CLI without submitting a prompt or a turn. It only
prints a narrow, sanitized projection of the SDK's initialization response.
"""
import asyncio
import json
import re
import sys

EFFORTS = frozenset(('low', 'medium', 'high', 'xhigh', 'max'))
# Anthropic's CLI model documentation: Fable 5.2 requires v2.1.321.
MINIMUM_CLI_VERSION = {'claude-fable-5-2': (2, 1, 321)}


def normalize_models(items, version):
    if not isinstance(items, list):
        raise ValueError('model list is unavailable')
    match = re.search(r'\b(\d+)\.(\d+)\.(\d+)\b', version or '')
    parsed_version = tuple(map(int, match.groups())) if match else None
    result, seen = [], set()
    for item in items[:100]:
        if not isinstance(item, dict):
            continue
        value = item.get('value')
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,120}', value) or value in seen:
            continue
        seen.add(value)
        label = item.get('displayName')
        label = label[:160] if isinstance(label, str) and label.strip() else value
        resolved = item.get('resolvedModel')
        resolved = resolved[:120] if isinstance(resolved, str) else None
        raw_efforts = item.get('supportedEffortLevels')
        efforts = list(dict.fromkeys(x for x in raw_efforts if isinstance(x, str) and x in EFFORTS)) if isinstance(raw_efforts, list) else None
        minimum = MINIMUM_CLI_VERSION.get(resolved or value)
        selectable = minimum is None or (parsed_version is not None and parsed_version >= minimum)
        reason = None
        if not selectable:
            reason = '\u9700\u8981 Claude Code 2.1.321 \u6216\u66f4\u65b0\u7248\u672c' if parsed_version else '\u65e0\u6cd5\u786e\u8ba4 Claude Code \u7248\u672c'
        result.append({'value': value, 'displayName': label, 'resolvedModel': resolved, 'supportedEffortLevels': efforts, 'selectable': selectable, 'unavailableReason': reason})
    return result


async def _get(cli, cwd):
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
    options = ClaudeAgentOptions(cli_path=cli, cwd=cwd, setting_sources=[], strict_mcp_config=True, tools=[], max_turns=0)
    async with ClaudeSDKClient(options=options) as client:
        info = await client.get_server_info()
        if not isinstance(info, dict):
            raise ValueError('SDK initialization unavailable')
        return normalize_models(info.get('models'), str(info.get('claude_code_version') or ''))


def main():
    if len(sys.argv) != 3:
        raise ValueError('invalid arguments')
    models = asyncio.run(asyncio.wait_for(_get(sys.argv[1], sys.argv[2]), timeout=20))
    if not models:
        raise ValueError('empty model list')
    print(json.dumps({'models': models}, ensure_ascii=True, separators=(',', ':')))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Do not expose SDK stderr, environment, account details, or credentials.
        sys.exit(1)
