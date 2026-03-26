import subprocess, sys, importlib.util
if not importlib.util.find_spec('pytest'):
    print('Installing pytest')
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--upgrade', 'pip'])
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pytest'])
else:
    print('pytest already installed')
print('\\n--- RUNNING TESTS ---')
rc = subprocess.call([sys.executable, '-m', 'pytest', '-q', 'tests/test_kucoin_simulator.py'])
print('\\n--- TESTS EXIT CODE:', rc, '---')
sys.exit(rc)
