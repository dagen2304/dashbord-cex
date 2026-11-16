import os
import re

directory = r'/app/app/services'

for filename in os.listdir(directory):
    if filename.endswith('_collector.py'):
        filepath = os.path.join(directory, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Replace _TIMEOUT = X with _TIMEOUT = 60
        content = re.sub(r'(_TIMEOUT\s*=\s*)\d+', r'\g<1>60', content)
        content = re.sub(r'(_COMMAND_TIMEOUT\s*=\s*)\d+', r'\g<1>60', content)
        
        # Replace hardcoded timeout=10, timeout=15, timeout=5 with timeout=60
        content = re.sub(r'(timeout\s*=\s*)\d+', r'\g<1>60', content)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f'Updated {filename}')

# Also update scheduler.py global timeout
scheduler_path = r'/app/app/tasks/scheduler.py'
if os.path.exists(scheduler_path):
    with open(scheduler_path, 'r', encoding='utf-8') as f:
        content = f.read()
    content = re.sub(r'(timeout\s*=\s*)120', r'\g<1>300', content)
    with open(scheduler_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print('Updated scheduler.py')
