import re

with open('modules/app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
skip_next = False
for i, line in enumerate(lines):
    if line.strip() == "'pitch_ceiling': 600":
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
        new_lines.append(line.replace("executor.submit", "self.executor.submit").replace("                        ", "                    "))
    elif "with concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8)) as executor:" in line:
        pass
    elif "f = executor.submit(" in line:
        new_lines.append(line.replace("executor.submit", "self.executor.submit").replace("                            ", "                        "))
    elif "futures = {executor.submit(batch_process_worker, p, params, trim): p for p in paths_to_process}" in line:
        new_lines.append(line.replace("executor.submit", "self.executor.submit").replace("                ", "            "))
    elif "executor = concurrent.futures.ProcessPoolExecutor(max_workers=min(os.cpu_count() or 4, 8))" in line:
        pass
    elif "futures[executor.submit(batch_process_worker, p, params, trim)] = i" in line:
        new_lines.append(line.replace("executor.submit", "self.executor.submit").replace("                    ", "                "))
    elif "futures[executor.submit(batch_process_worker, path, params, trim)] = i" in line:
        new_lines.append(line.replace("executor.submit", "self.executor.submit").replace("                        ", "                    "))
    elif "executor.shutdown(wait=False)" in line:
        pass
    else:
        new_lines.append(line)

# Since we're removing `with` blocks we need to dedent the contents
content = "".join(new_lines)
# We will just write it and check syntax. If there's syntax error due to indent, we'll revert app.py as the previous optimizations to audio_core, spectrogram_panel, and data_utils are already significant and safe.
with open('modules/app.py', 'w', encoding='utf-8') as f:
    f.write(content)
