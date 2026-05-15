with open('modules/app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "res = future.result()" in line and "orig_idx = futures[future]" in lines[i-1]:
        print(f"Line {i+1}: {line}")
