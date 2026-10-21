import json,sys,shutil,os
name=sys.argv[1]; args=json.loads(sys.argv[2]); cwd=sys.argv[3] if len(sys.argv)>3 else "/workspace/work/h11-probe/wt"
home=f"/workspace/work/h11-probe/home-{name}"; shutil.rmtree(home,ignore_errors=True); os.makedirs(home+"/.gemini")
shutil.copy("home1/.gemini/settings.json",home+"/.gemini/settings.json")
c={"home":home,"cwd":cwd,"cli":"/workspace/work/h11-gemcli/node_modules/@google/gemini-cli/bundle/gemini.js","args":["-o","stream-json","-m","gemini-2.5-flash","--skip-trust"]+args,"timeout":90}
json.dump(c,open("probe_env.json","w"))
