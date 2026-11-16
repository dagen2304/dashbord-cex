import os
import re

directory = 'frontend/src'

replacements = {
    '#d4af37': '#FF7900',   # Gold -> Orange Brand
    '#f97316': '#F16E00',   # Old Warning Orange -> Orange Accessible
    'bg-[#131318]': 'bg-theme-popover',
    'bg-[#0c0c0e]': 'bg-theme-surface',
    'bg-[#030303]': 'bg-theme-base',
    'bg-[#1b1b1f]': 'bg-theme-popover',
    'border-[#131318]': 'border-theme-popover',
    'text-[#131318]': 'text-theme-popover'
}

def process_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as file:
        content = file.read()
        
    new_content = content
    for old, new in replacements.items():
        # Case insensitive replace for hex codes
        pattern = re.compile(re.escape(old), re.IGNORECASE)
        new_content = pattern.sub(new, new_content)
        
    if content != new_content:
        with open(filepath, 'w', encoding='utf-8') as file:
            file.write(new_content)
        print(f"Updated {filepath}")

for root, _, files in os.walk(directory):
    for file in files:
        if file.endswith(('.tsx', '.ts', '.jsx', '.js', '.css')):
            process_file(os.path.join(root, file))

print("Replacement complete.")
