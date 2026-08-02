import sys, re, json, collections
sys.path.insert(0, 'gen')
from antlr4 import InputStream, CommonTokenStream
from antlr4.error.ErrorListener import ErrorListener
from KqlLexer import KqlLexer
from KqlParser import KqlParser
from pathlib import Path

FENCE = re.compile(r"^```[kK]usto\s*$(.*?)^```\s*$", re.M | re.S)

class Collect(ErrorListener):
    def __init__(self): self.errors=[]
    def syntaxError(self, r,s,line,col,msg,e): self.errors.append((line,col,msg))

def parse(q):
    lex=KqlLexer(InputStream(q)); lex.removeErrorListeners()
    le=Collect(); lex.addErrorListener(le)
    p=KqlParser(CommonTokenStream(lex)); p.removeErrorListeners()
    pe=Collect(); p.addErrorListener(pe)
    try: p.top()
    except Exception as ex: return [(0,0,f"EXCEPTION {type(ex).__name__}: {ex}")]
    return le.errors+pe.errors

qdir=Path(sys.argv[1])/"data-explorer/kusto/query"
total=ok=0; failures=[]
for md in sorted(qdir.glob("*.md")):
    for m in FENCE.finditer(md.read_text(encoding="utf-8", errors="replace")):
        block=m.group(1).strip()
        if not block: continue
        total+=1
        errs=parse(block)
        if not errs: ok+=1
        else: failures.append((md.name, block, errs[0]))

print(f"corpus blocks parsed: {ok}/{total}  ({100*ok/total:.1f}%)")
print(f"failures: {len(failures)}\n")
msgs=collections.Counter()
for name, blk, (l,c,msg) in failures:
    key=re.sub(r"'[^']*'","'X'",msg)[:80]
    msgs[key]+=1
print("top failure messages:")
for msg,n in msgs.most_common(12): print(f"  {n:>4}  {msg}")
print("\nsample failing pages:")
for name,blk,(l,c,msg) in failures[:8]:
    first=[ln for ln in blk.splitlines() if ln.strip()][:1]
    print(f"  {name:<38} {str(first[0])[:60] if first else ''}")
json.dump([{"page":n,"err":m[2],"snippet":b[:200]} for n,b,m in failures], open("parse_failures.json","w"), indent=1)
