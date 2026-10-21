import json,sys
for l in open(sys.argv[1]+'.server.jsonl'):
    d=json.loads(l)
    if d.get('kind')=='generate':
        print(d['index'], sorted(f['name'] for t in d['request'].get('tools',[]) for f in t.get('functionDeclarations',[])))
        break
