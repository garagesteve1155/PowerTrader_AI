import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.abspath(os.path.join(BASE_DIR, '..'))
TF_CHOICES = ['1hour', '2hour', '4hour', '8hour', '12hour', '1day', '1week']

created = []
visited = []

for entry in os.listdir(BASE_DIR):
    p = os.path.join(BASE_DIR, entry)
    if not os.path.isdir(p):
        continue
    # skip common non-coin dirs
    if entry.startswith('.') or entry in ('hub_data', 'scripts', '__pycache__'):
        continue
    visited.append(p)

# include base dir (BTC main folder)
visited.insert(0, BASE_DIR)

for folder in visited:
    try:
        for tf in TF_CHOICES:
            fn = os.path.join(folder, f'neural_perfect_threshold_{tf}.txt')
            if not os.path.isfile(fn):
                with open(fn, 'w', encoding='utf-8') as f:
                    f.write('0')
                created.append(fn)
    except Exception as e:
        print('ERROR for', folder, e)

print(f'Visited {len(visited)} folders; created {len(created)} files')
for c in created:
    print(c)

if created:
    exit(0)
else:
    exit(0)
