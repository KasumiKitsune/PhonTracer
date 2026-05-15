import os

with open('modules/app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
skip_next = False
for i, line in enumerate(lines):
    if line.strip() == "self.last_params = {":
        new_lines.extend([
            "        self.last_params = {\n",
        ])
    elif line.strip() == "'pitch_ceiling': 600":
        new_lines.extend([
            "            'pitch_ceiling': 600\n",
            "        }\n",
            "        \n",
            "        # Shared ProcessPoolExecutor for performance optimization\n",
            "        max_workers = min(os.cpu_count() or 4, 8)\n",
            "        self.executor = concurrent.futures.ProcessPoolExecutor(max_workers=max_workers)\n",
            "        \n",
            "        def on_closing():\n",
            "            self.executor.shutdown(wait=False)\n",
            "            self.root.destroy()\n",
            "        self.root.protocol(\"WM_DELETE_WINDOW\", on_closing)\n"
        ])
        skip_next = True
    elif skip_next and line.strip() == "}":
        skip_next = False
    elif "with concurrent.futures.ProcessPoolExecutor(max_workers=1) as executor:" in line:
        pass
    elif "future = executor.submit(check_audio_segments, path)" in line:
        new_lines.append(line.replace("executor.submit", "self.executor.submit"))
    elif "with concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8)) as executor:" in line:
        pass
    elif "f = executor.submit(" in line:
        new_lines.append(line.replace("executor.submit", "self.executor.submit").replace("                    ", "                "))
    elif "executor.shutdown(wait=False)" in line:
        pass
    elif "futures = {executor.submit(batch_process_worker, p, params, trim): p for p in paths_to_process}" in line:
        new_lines.append(line.replace("executor.submit", "self.executor.submit").replace("                ", "            "))
    elif "executor = concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8))" in line:
        pass
    elif "futures[executor.submit(batch_process_worker, p, params, trim)] = i" in line:
        new_lines.append(line.replace("executor.submit", "self.executor.submit").replace("                    ", "                "))
    elif "futures[executor.submit(batch_process_worker, path, params, trim)] = i" in line:
         new_lines.append(line.replace("executor.submit", "self.executor.submit").replace("                        ", "                    "))
    else:
        new_lines.append(line)

with open('modules/app.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
