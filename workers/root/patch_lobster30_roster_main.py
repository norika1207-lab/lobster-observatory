from pathlib import Path

p = Path("/opt/battle_arena/app/main.py")
s = p.read_text(encoding="utf-8")

old_app = '''APPRENTICE_LOBSTERS = {"emberling", "sparkrill", "gauntlet", "rushfin", "nullpinch", "brineledger", "omenwake", "mirrorcalm", "flarecub", "basaltrook"}
MENTOR_TO_APPRENTICE = {"norika_oda":"emberling", "clawtrix":"sparkrill", "pincerbot":"gauntlet", "ragclaw":"rushfin", "snaplex":"nullpinch", "hexclaw":"brineledger", "vortexiq":"omenwake", "prismbit":"mirrorcalm", "blazepaw":"flarecub", "stonefang":"basaltrook"}
APPRENTICE_TO_MENTOR = {v:k for k,v in MENTOR_TO_APPRENTICE.items()}
'''
new_app = '''APPRENTICE_LOBSTERS = {"emberling", "sparkrill", "gauntlet", "rushfin", "nullpinch", "brineledger", "omenwake", "mirrorcalm", "flarecub", "basaltrook"}
MENTOR_TO_APPRENTICE = {"norika_oda":"emberling", "clawtrix":"sparkrill", "pincerbot":"gauntlet", "ragclaw":"rushfin", "snaplex":"nullpinch", "hexclaw":"brineledger", "vortexiq":"omenwake", "prismbit":"mirrorcalm", "blazepaw":"flarecub", "stonefang":"basaltrook"}
APPRENTICE_TO_MENTOR = {v:k for k,v in MENTOR_TO_APPRENTICE.items()}
LOBSTER_THIRTY = tuple(sorted(OLD_GUARD_LOBSTERS | APPRENTICE_LOBSTERS | TRANSFER_LOBSTERS))
LOBSTER_RUNTIME_NAMES = tuple(sorted(set(LOBSTER_THIRTY) | {"elder"}))
_LOBSTER_NAMES = set(LOBSTER_RUNTIME_NAMES)
'''
if "LOBSTER_THIRTY =" not in s:
    if old_app not in s:
        raise SystemExit("apprentice anchor not found")
    s = s.replace(old_app, new_app, 1)

repls = {
    '_RESEARCH_PERSONAS = {"norika_oda", "clawtrix", "pincerbot", "ragclaw", "snaplex", "hexclaw", "vortexiq", "prismbit", "blazepaw", "stonefang"}': '_RESEARCH_PERSONAS = set(LOBSTER_THIRTY)',
    'agents = ["stonefang","snaplex","vortexiq","clawtrix","pincerbot","hexclaw","norika_oda","prismbit","blazepaw","ragclaw"]': 'agents = list(LOBSTER_THIRTY)',
    'personas = ["stonefang","snaplex","vortexiq","clawtrix","pincerbot","hexclaw","norika_oda","prismbit","blazepaw","ragclaw","emberling","sparkrill","gauntlet","rushfin","nullpinch","brineledger","omenwake","mirrorcalm","flarecub","basaltrook"]': 'personas = list(LOBSTER_THIRTY)',
    'allowed = {"hexclaw","pincerbot","norika_oda","vortexiq","snaplex","prismbit","clawtrix","ragclaw","blazepaw","stonefang"}': 'allowed = set(LOBSTER_THIRTY)',
    'personas = ["hexclaw","pincerbot","norika_oda","vortexiq","snaplex","prismbit","clawtrix","ragclaw","blazepaw","stonefang"]': 'personas = list(LOBSTER_THIRTY)',
}
changed = []
for old, new in repls.items():
    n = s.count(old)
    if n:
        s = s.replace(old, new)
        changed.append((old[:60], n))

p.write_text(s, encoding="utf-8")
print({"changed": changed, "has_lobster_thirty": "LOBSTER_THIRTY =" in s})
