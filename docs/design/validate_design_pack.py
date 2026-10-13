"""Check design files and synthetic examples only; no product or network execution."""
from pathlib import Path
import json, os, re, sys, tomllib
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
ROOT=Path(__file__).resolve().parent
# Private strings that must never appear in public docs live outside this repo.
_deny=os.environ.get('METAMUSE_PRIVATE_DENYLIST')
DENY=[l.strip() for l in Path(_deny).read_text(encoding='utf-8').splitlines() if l.strip() and not l.startswith('#')] if _deny else []
if not _deny: print('note: METAMUSE_PRIVATE_DENYLIST not set; private-string check skipped',file=sys.stderr)
load=lambda p:json.loads((ROOT/p).read_text(encoding='utf-8'))
tools=load('contracts/mcp-tools.json')['tools']
assert len(tools)==15 and len({x['name'] for x in tools})==15
by_name={x['name']:x for x in tools}
for t in tools:
 Draft202012Validator.check_schema(t['inputSchema'])
 Draft202012Validator.check_schema(t['outputSchema'])
call=load('examples/tool-call.json');result=load('examples/tool-result.json')
for s,d in [(by_name[call['tool']]['inputSchema'],call['arguments']),(by_name[call['tool']]['outputSchema'],result)]:
 Draft202012Validator(s,format_checker=FormatChecker()).validate(d)
negatives=0
for s,d in [(by_name[call['tool']]['inputSchema'],{**call['arguments'],'approved':True}),
            (by_name[call['tool']]['outputSchema'],{**result,'status':'error'})]:
 try: Draft202012Validator(s).validate(d)
 except ValidationError: negatives+=1
 else: raise AssertionError('Invalid fixture accepted')
assert len(load('contracts/requirements.json')['requirements'])==32
assert len(load('contracts/backlog.json')['tasks'])==22
ats=load('contracts/acceptance-cases.json')['cases']
assert len(ats)==54 and all(x['execution_status']=='not_run' for x in ats)
links=0
for p in ROOT.rglob('*.md'):
 text=p.read_text(encoding='utf-8');assert text.count('```')%2==0,p
 for target in re.findall(r'\]\(([^)]+)\)',text):
  if '://' in target or target.startswith('#'):continue
  assert (p.parent/target.split('#',1)[0]).exists(),(p,target)
  links+=1
 for secret in DENY:
  assert secret not in text,(p,'private deny-list entry found')
with (ROOT/'examples/metamuse.example.toml').open('rb') as f: cfg=tomllib.load(f)
assert cfg['app']['network_mode']=='offline'
assert cfg['muse']['enabled']==cfg['publishing']['enabled']==False
for p in ROOT.rglob('*.json'):json.loads(p.read_text(encoding='utf-8'))
report={'scope':'design files and synthetic contract examples only','result':'passed',
'requirements':32,'backlog_tasks':22,'specified_acceptance_cases':54,
'product_acceptance_cases_executed':0,'mcp_tool_designs':15,
'positive_contract_checks':2,'negative_contract_checks':negatives,'local_links_checked':links,
'live_tests_run':0,'network_operations':0,
'limitations':['No application runtime is included.','Detailed per-tool result schemas must be generated from implementation models.','This is a rebuilt handoff, not a byte-identical restoration of the unavailable prior ZIP.']}
(ROOT/'DESIGN_VALIDATION_REPORT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
