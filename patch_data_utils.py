with open('modules/data_utils.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('import os', 'import os\nfrom typing import List, Dict, Optional, Any, Tuple')

content = content.replace('def parse_wordlist(raw_text):', 'def parse_wordlist(raw_text: str) -> Tuple[List[Dict[str, Any]], List[str]]:')
content = content.replace('def fuzzy_match_word_to_path(word, available_paths, used_indices=None):', 'def fuzzy_match_word_to_path(word: str, available_paths: List[str], used_indices: Optional[List[int]] = None) -> Optional[int]:')
content = content.replace('def get_export_text_for_item(item, real_index, num_points):', 'def get_export_text_for_item(item: Dict[str, Any], real_index: int, num_points: int) -> str:')

with open('modules/data_utils.py', 'w', encoding='utf-8') as f:
    f.write(content)
