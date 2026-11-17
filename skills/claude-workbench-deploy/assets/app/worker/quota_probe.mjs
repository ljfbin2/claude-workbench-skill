// Official SDK control request only: an empty prompt stream creates no turn.
import { readFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
const cli = process.argv[2];
const pkgRoot = process.env.WEB_AI_JS_SDK_DIR;
const sdkVersion=process.env.WEB_AI_QUOTA_SDK_VERSION;
const cliVersion=process.env.WEB_AI_QUOTA_CLI_VERSION;
if(!pkgRoot||!sdkVersion||!cliVersion)process.exit(2);
const pkg = JSON.parse(await readFile(`${pkgRoot}/package.json`, 'utf8'));
if (pkg.name !== '@anthropic-ai/claude-agent-sdk' || pkg.version !== sdkVersion) process.exit(2);
const version = execFileSync(cli, ['--version'], {timeout: 1200, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore']}).trim();
if (version.split(/\s+/)[0] !== cliVersion) process.exit(2);
const { query } = await import(`${pkgRoot}/sdk.mjs`);
async function* empty() {}
const q = query({prompt: empty(), options: {pathToClaudeCodeExecutable: cli, tools: [], mcpServers: {}, strictMcpConfig: true, settingSources: [], persistSession: false, extraArgs: {'safe-mode': null, 'no-chrome': null}}});
try {
  const response = await q.usage_EXPERIMENTAL_MAY_CHANGE_DO_NOT_RELY_ON_THIS_API_YET({skipBehaviors: true});
  const rate = response?.rate_limits;
  const scope = x => {
    if (!x || typeof x !== 'object' || Array.isArray(x)) return x;
    const m = x.model;
    const model = m && typeof m === 'object' && !Array.isArray(m) ? {display_name:m.display_name, ...(Object.keys(m).some(k => k !== 'display_name' && !(k === 'id' && m[k] === null)) ? {__unknown:true} : {})} : m;
    return {model, surface:x.surface == null ? null : true, ...(Object.keys(x).some(k => k !== 'model' && k !== 'surface') ? {__unknown:true} : {})};
  };
  // Whitelist fields; no account data, messages, raw logs, or session usage.
  const number = x => typeof x === 'number' && Number.isFinite(x) ? x : null;
  const date = x => typeof x === 'string' && x.length <= 64 ? x : null;
  const fable = x => typeof x === 'string' && /^(?:claude[ -]+)?fable$/i.test(x.trim());
  const limits = Array.isArray(rate?.limits) && rate.limits.length <= 64 ? rate.limits.filter(r => r?.kind === 'session' || r?.kind === 'weekly_all' || (r?.kind === 'weekly_scoped' && fable(r?.scope?.model?.display_name))).map(r => r && typeof r === 'object' ? {kind:r.kind, scope:scope(r.scope), percent:number(r.percent), resets_at:date(r.resets_at)} : null) : null;
  const models = Array.isArray(rate?.model_scoped) && rate.model_scoped.length <= 64 ? rate.model_scoped.filter(r => fable(r?.display_name)).map(r => r && typeof r === 'object' ? {display_name:r.display_name, utilization:number(r.utilization), resets_at:date(r.resets_at)} : null) : null;
  process.stdout.write(JSON.stringify({rate_limits_available:response?.rate_limits_available === true, rate_limits:{limits, model_scoped:models}}));
} finally { q.close(); }
