with open('modules/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the else statements that lost their indentation
content = content.replace('\n                        mic_s, mic_e, raw_s, raw_e = self._microscopic_vowel_nucleus(\n', '\n                    else:\n                        mic_s, mic_e, raw_s, raw_e = self._microscopic_vowel_nucleus(\n')
content = content.replace('\n                    # 音频段不够了，标记为缺失\n', '\n                else:\n                    # 音频段不够了，标记为缺失\n')
content = content.replace('\n                            tasks.append({\'word\': word, \'group\': grp[\'group\'], \'missing\': True})\n                        word_idx += 1\n', '\n                        else:\n                            tasks.append({\'word\': word, \'group\': grp[\'group\'], \'missing\': True})\n                        word_idx += 1\n')
content = content.replace('\n                        tasks.append({\'word\': word, \'group\': grp[\'group\'], \'missing\': True})\n            \n', '\n                    else:\n                        tasks.append({\'word\': word, \'group\': grp[\'group\'], \'missing\': True})\n            \n')
content = content.replace('\n                        tasks[idx][\'missing\'] = True # fallback\n', '\n                    else:\n                        tasks[idx][\'missing\'] = True # fallback\n')
content = content.replace('\n                        iid = self.tree_panel.tree.insert(gid, tk.END, text=res[\'word\'] + " (缺失)", tags=(\'item\',))\n', '\n                    else:\n                        iid = self.tree_panel.tree.insert(gid, tk.END, text=res[\'word\'] + " (缺失)", tags=(\'item\',))\n')
content = content.replace('\n                self.root.after(0, lambda: self.set_progress(1.0))\n\n            def finalize():\n', '\n            else:\n                self.root.after(0, lambda: self.set_progress(1.0))\n\n            def finalize():\n')
content = content.replace('\n                            tasks.append({\'word\': word, \'group\': group_name, \'missing\': True})\n                path_idx = 0\n', '\n                        else:\n                            tasks.append({\'word\': word, \'group\': group_name, \'missing\': True})\n            else:\n                path_idx = 0\n')
content = content.replace('\n                            tasks.append({\'word\': word, \'group\': group_name, \'missing\': True})\n\n            results = [None] * len(tasks)\n', '\n                        else:\n                            tasks.append({\'word\': word, \'group\': group_name, \'missing\': True})\n\n            results = [None] * len(tasks)\n')
content = content.replace('\n                        futures[self.executor.submit(batch_process_worker, path, params, trim)] = i\n            \n', '\n                    else:\n                        futures[self.executor.submit(batch_process_worker, path, params, trim)] = i\n            \n')
content = content.replace('\n                self.root.after(0, lambda: self.set_progress(1.0))\n\n            def finalize():\n', '\n            else:\n                self.root.after(0, lambda: self.set_progress(1.0))\n\n            def finalize():\n')
content = content.replace('\n                        suffix = " (未匹配)" if match_mode == \'fuzzy\' else " (缺失)"\n', '\n                    else:\n                        suffix = " (未匹配)" if match_mode == \'fuzzy\' else " (缺失)"\n')
content = content.replace('\n            if mode == \'long\': self.process_long_with_wordlist(raw_text)\n', '\n            if mode == \'long\': self.process_long_with_wordlist(raw_text)\n            else: self.process_batch_with_wordlist(raw_text, match_mode=match_mode_var.get())\n')

with open('modules/app.py', 'w', encoding='utf-8') as f:
    f.write(content)
