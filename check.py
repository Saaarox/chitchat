import ast, sys
files = [
    'config.py', 'bot.py', 'handlers/protection.py',
    'handlers/analytics.py', 'handlers/moderation.py',
    'services/scheduler.py', 'services/cas.py',
]
errors = []
for f in files:
    try:
        ast.parse(open(f).read())
    except SyntaxError as e:
        errors.append(f'{f}: {e}')
if errors:
    print('SYNTAX ERRORS:')
    for e in errors: print(e)
    sys.exit(1)
else:
    print('ALL FILES SYNTAX OK')