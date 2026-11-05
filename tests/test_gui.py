"""UI state tests without a display or Google connection."""
import unittest
from unittest.mock import MagicMock, patch
from doctors_list.gui import App, summary_text
from doctors_list import writer
from doctors_list.master import reconcile
from fixtures import NOW
from test_writer import MemoryBackend, input_candidate


class Value:
    def __init__(self, value=None): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


class GuiTests(unittest.TestCase):
    def setUp(self):
        b = MemoryBackend()
        self.plan = writer.prepare(b, [input_candidate()], reconcile(b.snapshot)[0], NOW)
        self.app = App.__new__(App)
        a = self.app
        a.plan = None; a.busy = False; a.single_writer = Value(False); a.real_reviewed = Value(False)
        a.apply_button = MagicMock(); a.buttons = [a.apply_button]; a.checks = []
        a.table = MagicMock(); a.summary = Value(); a.diff = MagicMock(); a.status = Value()
        a.config_path = Value('unused'); a.root = MagicMock(); a.run = MagicMock()

    def test_start_readonly_and_approval_controls(self):
        a = self.app
        a.refresh_buttons(); a.apply_button.configure.assert_called_with(state='disabled')
        a.plan = self.plan; a.real_reviewed.set(True)
        a.refresh_buttons(); a.apply_button.configure.assert_called_with(state='disabled')
        a.single_writer.set(True)
        a.refresh_buttons(); a.apply_button.configure.assert_called_with(state='normal')
        a.busy = True
        a.refresh_buttons(); a.apply_button.configure.assert_called_with(state='disabled')

    def test_file_change_discards_approval(self):
        a = self.app
        a.plan = self.plan; a.single_writer.set(True); a.real_reviewed.set(True)
        a.invalidate()
        self.assertIsNone(a.plan); self.assertFalse(a.single_writer.get()); self.assertFalse(a.real_reviewed.get())

    def test_cancelled_confirmation_never_starts_work(self):
        a = self.app
        a.plan = self.plan; a.single_writer.set(True); a.real_reviewed.set(True)
        with patch('doctors_list.gui._config', return_value={}), patch('doctors_list.gui.simpledialog.askstring', return_value=None):
            a.apply()
        a.run.assert_not_called()

    def test_report_display_shows_rows_diff_unknown_people_and_sheets(self):
        report = self.plan['payload']['report']
        self.app.display(report)
        self.assertIn('不明', self.app.summary.get())
        self.assertIn('実際の追加: 医師0', self.app.summary.get())
        text = self.app.diff.insert.call_args.args[1]
        self.assertIn('追加予定の行 3', text)
        self.assertIn('LP・小冊子', text)
        self.assertIn('G:', text)
        self.assertEqual(len(self.app.records), 1)

    def test_no_production_success_on_dryrun(self):
        self.assertIn('未書き込み', summary_text(self.plan['payload']['report']))


if __name__ == '__main__': unittest.main()
