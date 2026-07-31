from pathlib import Path


p = Path("/root/lobster-agent.js")
s = p.read_text()
old = '''  req.on('error', () => {});
  req.write(body); req.end();
  req.write(body); req.end();
  return true;
}
'''
new = '''  req.on('error', () => {});
  req.write(body); req.end();
  return true;
}
'''
if old not in s:
    raise SystemExit("double send block not found")
p.write_text(s.replace(old, new, 1))
