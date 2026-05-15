with open('modules/app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

import re

for i, line in enumerate(lines):
    # Fix the missing else part block indentations that caused the error
    if "tasks.append({'word': word, 'group': grp['group'], 'missing': True})" in line and "else:" in lines[i-1]:
        print(f"Line {i}: {lines[i-1]}")
        print(f"Line {i+1}: {line}")
