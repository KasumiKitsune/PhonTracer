from pathlib import Path

from modules.wordlist_editor import RoundedGroupList, VisualWordlistEditor
from modules.wordlist_v2 import AI_REVIEW_STATUS, DEFAULT_REVIEW_STATUS


class FakeButton:
    def __init__(self):
        self.configures = []

    def configure(self, **kwargs):
        self.configures.append(kwargs)


def test_editor_button_omits_none_width(monkeypatch):
    captured = {}

    def fake_button(parent, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("modules.wordlist_editor.ctk.CTkButton", fake_button)

    editor = VisualWordlistEditor.__new__(VisualWordlistEditor)
    editor.colors = {
        "primary": "#2563EB",
        "primary_hover": "#1D4ED8",
        "text_soft": "#334155",
        "danger": "#EF4444",
    }
    editor.font_small = None

    VisualWordlistEditor._button(editor, object(), "添加组", lambda: None, "secondary")

    assert "width" not in captured


def test_editor_does_not_call_scrollable_grid_propagate_with_argument():
    source = Path("modules/wordlist_editor.py").read_text(encoding="utf-8")
    assert "right.grid_propagate(False)" not in source


def test_editor_uses_custom_list_and_scrollbars():
    source = Path("modules/wordlist_editor.py").read_text(encoding="utf-8")
    assert "tk.Listbox(" not in source
    assert "ttk.Scrollbar" not in source
    assert "RoundedGroupList" in source
    assert "CTkScrollbar" in source


def test_group_list_programmatic_selection_is_silent(monkeypatch):
    monkeypatch.setattr("modules.wordlist_editor.ctk.CTkFont", lambda **_kwargs: "font")

    group_list = RoundedGroupList.__new__(RoundedGroupList)
    group_list.colors = {
        "primary": "#2563EB",
        "primary_hover": "#1D4ED8",
        "text": "#17202A",
    }
    group_list.font_family = "Microsoft YaHei"
    group_list.buttons = [FakeButton()]
    group_list._selected_index = None
    callback_count = {"value": 0}

    def on_select(_event):
        callback_count["value"] += 1

    group_list._select_callback = on_select

    group_list.selection_set(0, notify=False)
    assert callback_count["value"] == 0

    group_list.selection_set(0, notify=True)
    assert callback_count["value"] == 1


def test_group_refresh_uses_silent_selection():
    source = Path("modules/wordlist_editor.py").read_text(encoding="utf-8")
    assert "selection_set(self.current_group_index, notify=False)" in source


class RecursiveTree:
    def __init__(self, editor):
        self.editor = editor
        self.selection_calls = 0

    def get_children(self):
        return ("0",)

    def selection_set(self, _iid):
        self.selection_calls += 1
        self.editor._on_item_select()

    def focus(self, _iid):
        return None

    def see(self, _iid):
        return None

    def selection(self):
        return ("0",)


def test_item_programmatic_selection_is_silent():
    editor = VisualWordlistEditor.__new__(VisualWordlistEditor)
    editor._refreshing = False
    editor.item_tree = RecursiveTree(editor)
    editor._sync_from_fields = lambda: (_ for _ in ()).throw(AssertionError("静默选择不应同步字段"))
    editor._refresh_item_row_styles = lambda: None

    VisualWordlistEditor._select_item(editor, 0, notify=False)

    assert editor.item_tree.selection_calls == 0
    assert editor._refreshing is False


def test_wordlist_context_menu_matches_project_tree_style():
    source = Path("modules/wordlist_editor.py").read_text(encoding="utf-8")
    assert "font=(\"Microsoft YaHei\", 10)" in source
    assert "activebackground=\"#3B82F6\"" in source
    assert "activeforeground=\"#FFFFFF\"" in source
    assert "relief=\"solid\"" in source
    assert "self.item_tree.bind(\"<Button-3>\", self._show_item_context_menu)" in source
    assert "self.group_list.bind(\"<Button-3>\", self._show_group_context_menu)" in source


def test_toolkit_wordlist_editor_removes_intro_card():
    source = Path("toolkit.py").read_text(encoding="utf-8")
    assert "常用编辑在下方标题栏完成" not in source


def test_main_import_routes_to_legacy_dialog():
    source = Path("modules/app.py").read_text(encoding="utf-8")
    assert "from .wordlist_editor import VisualWordlistEditor" not in source
    assert "return self.open_text_dialog_legacy(mode)" in source
    assert "导入高级字表" in source
    assert "btn_import_v2 = ctk.CTkButton(btn_row" in source


def test_toolkit_owns_wordlist_agent_prompt():
    source = Path("toolkit.py").read_text(encoding="utf-8")
    prompt_source = Path("modules/wordlist_v2.py").read_text(encoding="utf-8")
    assert "复制 Agent 提示词" in source
    assert "def copy_wordlist_agent_prompt" in source
    assert "ADVANCED_WORDLIST_AGENT_PROMPT" in source
    assert "组名,组备注,组标签,词项,词项备注,标签,别名,复核状态" in prompt_source
    assert "第一阶段：先提问，不要直接生成字表" in prompt_source
    assert "标签设计规则" in prompt_source
    assert "质量检查规则" in prompt_source
    assert "拼音,声调,韵母" not in prompt_source
    assert "tone:T1" not in prompt_source


def test_wordlist_table_hides_empty_optional_columns():
    editor = VisualWordlistEditor.__new__(VisualWordlistEditor)

    assert VisualWordlistEditor._visible_item_columns(editor, [
        {"label": "妈", "note": "", "tags": [], "meta": {}, "metadata_source": DEFAULT_REVIEW_STATUS}
    ]) == ("label", "note", "tags")

    assert VisualWordlistEditor._visible_item_columns(editor, [
        {"label": "妈", "note": "", "tags": ["阴平"], "aliases": ["ma"], "meta": {"声调": "1"}, "metadata_source": AI_REVIEW_STATUS}
    ]) == ("label", "note", "tags", "aliases", "source")


def test_wordlist_editor_moves_frequent_actions_to_footer():
    source = Path("modules/wordlist_editor.py").read_text(encoding="utf-8")
    assert "footer_actions = [" in source
    assert "(\"打开 .ptwl\", self.load_ptwl_dialog" in source
    assert "(\"保存\", self.save_ptwl_dialog" in source
    assert "(\"检查\", self.check_document" in source
    assert "bottom_actions=[" in Path("toolkit.py").read_text(encoding="utf-8")


def test_wordlist_editor_localizes_tags_and_cell_editing():
    source = Path("modules/wordlist_editor.py").read_text(encoding="utf-8")
    assert '"tags": "标签"' in source
    assert '"组标签"' in source
    assert '"词项标签"' in source
    assert "vowel:a" not in source
    assert "role:target" not in source
    assert "set:main" not in source
    assert "self.item_tree.bind(\"<Double-1>\", self._start_cell_edit)" in source
    assert "def _set_item_field" in source


def test_group_selection_does_not_refresh_back_to_old_index():
    source = Path("modules/wordlist_editor.py").read_text(encoding="utf-8")
    assert "def _sync_from_fields(self, emit=True, refresh_ui=True)" in source
    assert "self._sync_from_fields(emit=False, refresh_ui=False)" in source
    assert "self.current_group_index = target_group_index" in source
    assert "self._refresh_groups(keep_selection=True)" in source


def test_sidebar_scroll_speed_is_fast():
    source = Path("modules/wordlist_editor.py").read_text(encoding="utf-8")
    assert "yview_scroll(step * 12, \"units\")" in source
    assert "yview_scroll(step * 56, \"units\")" in source


def test_wordlist_editor_hides_phonology_fields_from_ui():
    source = Path("modules/wordlist_editor.py").read_text(encoding="utf-8")
    assert '"pinyin": "拼音"' not in source
    assert '"tone": "声调"' not in source
    assert '"rhyme": "韵母"' not in source
    assert 'self._property_entry(right, "拼音"' not in source
    assert 'self._property_entry(right, "声调"' not in source
    assert 'self._property_entry(right, "韵母"' not in source
